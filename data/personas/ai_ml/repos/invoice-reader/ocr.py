import pytesseract
from PIL import Image


def read_page(path: str) -> str:
    return pytesseract.image_to_string(Image.open(path), config="--psm 6")
