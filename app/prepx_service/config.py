from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / '.env', extra='ignore')
    groq_api_key: str = ''
    groq_model: str = 'openai/gpt-oss-20b'
    tesseract_cmd: str = ''
    math_ocr_enabled: bool = False
    max_files: int = Field(default=5, ge=1, le=20)
    max_pages: int = Field(default=10, ge=1, le=10)
    max_file_mb: int = Field(default=20, ge=1, le=100)
    max_document_chars: int = Field(default=60000, ge=1000, le=100000)
    groq_timeout_seconds: float = Field(default=90, gt=0)
    ocr_timeout_seconds: float = Field(default=90, gt=0)
    max_render_pixels: int = Field(default=20000000, ge=1000000)


@lru_cache
def get_settings() -> Settings:
    return Settings()
