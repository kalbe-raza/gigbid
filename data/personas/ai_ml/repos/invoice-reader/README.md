# invoice-reader

Invoice field extraction. Built 2022. (Synthetic portfolio repository.)

## Architecture
Deskewed scans, ran OCR, then a small layout model labelled totals, dates and tax IDs.

## Tech
- Computer Vision
- PyTorch
- OCR
- Python
- Tesseract
- OpenCV
- Docker

## Layout
- `main.py`
- `ocr.py`
- `preprocess.py`
- `Dockerfile`

## Running
```bash
cp .env.example .env
make run
```
