from dataclasses import dataclass
import math

import pymupdf
from PIL import Image

from .config import Settings
from .errors import ServiceError
from .ocr import LocalOCR


@dataclass
class PageText:
    number: int
    text: str
    used_ocr: bool
    has_image: bool = False


@dataclass
class ReadDocument:
    filename: str
    pages: list[PageText]
    warnings: list[str]

    def prompt_text(self):
        return '\n\n'.join(f'--- SOURCE PAGE {p.number} ---\n{p.text}' for p in self.pages)


def inspect_pdf(data: bytes, settings: Settings) -> int:
    if not data.startswith(b'%PDF-'):
        raise ServiceError('The upload is not a PDF.')
    try:
        with pymupdf.open(stream=data, filetype='pdf') as doc:
            if doc.needs_pass:
                raise ServiceError('Password-protected PDFs are not supported.')
            if not 1 <= doc.page_count <= settings.max_pages:
                raise ServiceError(f'Each PDF must contain 1-{settings.max_pages} pages.')
            return doc.page_count
    except ServiceError:
        raise
    except Exception as exc:
        raise ServiceError('PDF is corrupt or unreadable.') from exc


def read_pdf(filename: str, data: bytes, settings: Settings, ocr: LocalOCR,
             languages: list[str] | None, backend='tesseract', force_ocr=False) -> ReadDocument:
    inspect_pdf(data, settings)
    pages, warnings = [], []
    with pymupdf.open(stream=data, filetype='pdf') as doc:
        for number, page in enumerate(doc, 1):
            text = page.get_text('text', sort=True).strip()
            # Image-bearing pages can contain questions absent from the text layer.
            has_image = bool(page.get_image_info())
            use_ocr = force_ocr or has_image or len(text) < 30 or '\ufffd' in text
            if use_ocr:
                area = page.rect.width * page.rect.height
                if area <= 0 or not math.isfinite(area):
                    raise ServiceError(f'Page {number} has invalid dimensions.')
                scale = min(300 / 72, math.sqrt(settings.max_render_pixels / area))
                pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False)
                image = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
                text = ocr.recognize(image, languages, backend)
                if not settings.math_ocr_enabled:
                    warnings.append(f'Page {number}: basic OCR used; complex image equations require MATH_OCR_ENABLED=true.')
            if not text.strip():
                warnings.append(f'Page {number}: no readable text detected; verify the source page.')
            pages.append(PageText(number, text, use_ocr, has_image))
    result = ReadDocument(filename, pages, warnings)
    if not any(p.text.strip() for p in pages):
        raise ServiceError('No readable text was extracted from this PDF.')
    if len(result.prompt_text()) > settings.max_document_chars:
        raise ServiceError('Extracted document exceeds the configured text limit; split the PDF.', 413)
    return result
