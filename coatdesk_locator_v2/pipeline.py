from __future__ import annotations

import torch
from PIL import Image

from coatdesk_locator.model import choose_detection
from coatdesk_locator.pipeline import rgb_tensor
from .experiment import SIDE, build_v2


class PlacardLocatorV2:
    def __init__(self,weights,device="cpu"):
        self.device=device
        self.model=build_v2().to(device).eval()
        self.model.load_state_dict(torch.load(weights,map_location=device,weights_only=True))

    @torch.inference_mode()
    def locate(self,photo):
        frame=photo.resize((SIDE,SIDE),Image.Resampling.LANCZOS)
        prediction=self.model([rgb_tensor(frame).to(self.device)])[0]
        scale=prediction["boxes"].new_tensor([photo.width/SIDE,photo.height/SIDE]*2)
        prediction["boxes"]*=scale
        return choose_detection(prediction,photo.width,photo.height)
