# loom-inspect

Fabric defect detection on the production line. Built 2023. (Synthetic portfolio repository.)

## Architecture
Collected and labelled 6,400 frames, trained a lightweight detector and exported it for an edge PC beside the loom.

## Tech
- Computer Vision
- YOLO
- PyTorch
- Object Detection
- ONNX
- OpenCV

## Layout
- `main.py`
- `export.py`
- `preprocess.py`

## Running
```bash
cp .env.example .env
make run
```
