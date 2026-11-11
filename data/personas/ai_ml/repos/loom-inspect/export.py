import torch

torch.onnx.export(model, dummy, "detector.onnx", opset_version=17, input_names=["images"])
