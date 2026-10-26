from __future__ import annotations

import numpy as np
import torch
from PIL import Image

from coatdesk_ab.data import SHAPES, SIZES, letterbox
from coatdesk_ab.network import PhotoClassifier
from .dataset import FRAME
from .model import build_detector, choose_detection


def rgb_tensor(image):
    return torch.from_numpy(np.asarray(image).transpose(2, 0, 1).copy()).float() / 255


class PlacardLocator:
    def __init__(self, weights, device="cpu"):
        self.device = device
        self.model = build_detector().to(device).eval()
        self.model.load_state_dict(torch.load(weights, map_location=device, weights_only=True))

    @torch.inference_mode()
    def locate(self, photo):
        frame = photo.resize(FRAME, Image.Resampling.LANCZOS)
        prediction = self.model([rgb_tensor(frame).to(self.device)])[0]
        scale = prediction["boxes"].new_tensor([photo.width / FRAME[0], photo.height / FRAME[1]] * 2)
        prediction["boxes"] = prediction["boxes"] * scale
        return choose_detection(prediction, photo.width, photo.height)


class FrozenPanelClassifiers:
    def __init__(self, directory, device="cpu"):
        self.device = device
        self.models = {}
        for task, classes in (("shape", SHAPES), ("size", SIZES)):
            model = PhotoClassifier(len(classes)).to(device)
            model.load_state_dict(torch.load(directory / f"panel-{task}.pt", map_location=device, weights_only=True))
            self.models[task] = model.eval()

    @torch.inference_mode()
    def predict(self, crop):
        square, _ = letterbox(crop)
        value = rgb_tensor(square).unsqueeze(0).to(self.device)
        value = (value - value.new_tensor([.485, .456, .406])[None, :, None, None]) / value.new_tensor([.229, .224, .225])[None, :, None, None]
        if self.device == "cuda":
            with torch.autocast("cuda", dtype=torch.float16):
                predictions = {k: m(value).float().softmax(1)[0].cpu().tolist() for k, m in self.models.items()}
        else:
            predictions = {k: m(value).softmax(1)[0].tolist() for k, m in self.models.items()}
        return {"shape": SHAPES[int(np.argmax(predictions["shape"]))], "size": SIZES[int(np.argmax(predictions["size"]))],
                "shape_scores": dict(zip(SHAPES, predictions["shape"], strict=True)),
                "size_scores": dict(zip(SIZES, predictions["size"], strict=True))}
