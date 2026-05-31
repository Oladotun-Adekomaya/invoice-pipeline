from pathlib import Path

import pytesseract
from pdf2image import convert_from_path
from PIL import Image

from src.config import settings
from src.observability.logger import get_logger
from src.observability.retry import with_retry

logger = get_logger(__name__)

if settings.tesseract_cmd:
    pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd


def pdf_to_images(path: Path) -> list[Image.Image]:
    logger.info("converting pdf to images", filename=path.name)
    images = convert_from_path(str(path), dpi=300)
    logger.info("pdf converted", filename=path.name, pages=len(images))
    return images


@with_retry(max_attempts=3, wait_min=1.0, retry_on=(Exception,))
def extract_text(file_path: Path) -> str:
    images = pdf_to_images(file_path)

    all_text: list[str] = []
    for i, image in enumerate(images):
        logger.info("running ocr on page", page=i + 1, total=len(images))
        text = pytesseract.image_to_string(image)
        all_text.append(text)

    full_text = "\n".join(all_text)

    logger.info(
        "extraction complete",
        filename=file_path.name,
        pages=len(images),
        characters=len(full_text),
    )

    return full_text