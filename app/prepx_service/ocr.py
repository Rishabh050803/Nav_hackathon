from functools import lru_cache

from PIL import ImageDraw
import pytesseract

from .config import Settings
from .errors import ServiceError
from .languages import LANGUAGES


class LocalOCR:
    def __init__(self, settings: Settings):
        self.settings = settings
        if settings.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd

    def installed(self) -> set[str]:
        try:
            return set(pytesseract.get_languages(config=''))
        except (pytesseract.TesseractNotFoundError, pytesseract.TesseractError, OSError):
            return set()

    def capabilities(self):
        installed = self.installed()
        return [dict(code=code, name=name, tesseract_model=tess, installed=bool(tess and tess in installed),
                     easyocr_model=easy) for code, (name, tess, easy) in LANGUAGES.items()]

    def recognize(self, image, languages: list[str] | None, backend: str = 'tesseract') -> str:
        if self.settings.math_ocr_enabled:
            image = image.copy()
            formulas = formula_regions(image)
            draw = ImageDraw.Draw(image)
            for box, _ in formulas:
                draw.rectangle(box, fill='white')
        else:
            formulas = []
        if backend == 'easyocr':
            if not languages:
                raise ServiceError('EasyOCR requires explicit language hints, e.g. en,kok. Compatible scripts only.')
            codes = [LANGUAGES[c][2] for c in languages]
            if any(c is None for c in codes):
                raise ServiceError('A requested language has no EasyOCR model; try Tesseract.')
            try:
                import numpy as np
                results = easy_reader(tuple(codes)).readtext(np.array(image), detail=1)
                regions = [(float(box[0][0]), float(box[0][1]), float(box[2][1]), text)
                           for box, text, _ in results]
            except ImportError as exc:
                raise ServiceError('Install requirements-ocr-extra.txt to use EasyOCR.', 503) from exc
            except (ValueError, RuntimeError) as exc:
                raise ServiceError('EasyOCR initialization/recognition failed; check compatible language models.', 503) from exc
        else:
            installed = self.installed()
            selected = languages or [code for code, (_, tess, _) in LANGUAGES.items() if tess in installed]
            if not selected:
                raise ServiceError('Install Tesseract and English/Indian traineddata, or select EasyOCR.', 503)
            models = [LANGUAGES[code][1] for code in selected]
            missing = [code for code, model in zip(selected, models) if not model or model not in installed]
            if missing:
                raise ServiceError('Missing or unsupported Tesseract language models: ' + ', '.join(missing), 503)
            try:
                data = pytesseract.image_to_data(image, lang='+'.join(models), config='--psm 3',
                                                timeout=self.settings.ocr_timeout_seconds,
                                                output_type=pytesseract.Output.DICT)
                regions = [(data['left'][i], data['top'][i], data['top'][i] + data['height'][i], text.strip())
                           for i, text in enumerate(data['text']) if text.strip()]
            except (pytesseract.TesseractError, RuntimeError, OSError) as exc:
                raise ServiceError('OCR failed or timed out; check Tesseract and the document quality.', 503) from exc
        regions.extend((box[0], box[1], box[3], '$' + latex + '$') for box, latex in formulas)
        # Restore inline equations beside the OCR tokens on the same baseline.
        lines = []
        for x, top, bottom, text in sorted(regions, key=lambda r: (r[1], r[0])):
            center = (top + bottom) / 2
            for line in reversed(lines):
                if abs(line['center'] - center) <= max(8, (bottom - top) * .45):
                    line['tokens'].append((x, text))
                    break
            else:
                lines.append({'center': center, 'tokens': [(x, text)]})
        return '\n'.join(' '.join(t for _, t in sorted(line['tokens'])) for line in lines)


@lru_cache(maxsize=4)
def easy_reader(codes):
    import easyocr
    return easyocr.Reader(list(codes), gpu=False)


@lru_cache(maxsize=1)
def formula_models():
    try:
        from pix2text.formula_detector import MathFormulaDetector
        from pix2text.latex_ocr import LatexOCR
        return MathFormulaDetector(), LatexOCR()
    except (ImportError, RuntimeError, OSError) as exc:
        raise ServiceError('Math OCR is enabled but Pix2Text/models are unavailable. Install the optional dependencies and models.', 503) from exc


def formula_regions(image):
    detector, recognizer = formula_models()
    try:
        detections = detector(image, resized_shape=768)
        boxes = [(max(0, int(d['box'][0][0])), max(0, int(d['box'][0][1])),
                  min(image.width, int(d['box'][2][0])), min(image.height, int(d['box'][2][1])))
                 for d in detections]
        boxes = [b for b in boxes if b[2] > b[0] and b[3] > b[1]]
        if not boxes:
            return []
        texts = recognizer.recognize([image.crop(box) for box in boxes], batch_size=1)
        if len(texts) != len(boxes):
            raise ServiceError('Formula recognition returned incomplete results.', 502)
        return [(box, item['text']) for box, item in zip(boxes, texts)]
    except ServiceError:
        raise
    except Exception as exc:
        raise ServiceError('Local formula recognition failed.', 503) from exc
