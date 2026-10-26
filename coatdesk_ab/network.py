from __future__ import annotations

import torch
from torch import nn
from torchvision.models import resnet18


class PhotoClassifier(nn.Module):
    """One independent task per model, with learned spatial attention pooling."""

    def __init__(self, classes, base=None):
        super().__init__()
        backbone = resnet18(weights=None)
        if base is not None:
            backbone.load_state_dict(torch.load(base, map_location="cpu", weights_only=True))
        # Keep more spatial detail for small printed characters in whole photos.
        # The same stride-16 architecture is used for all four models.
        backbone.layer4[0].conv1.stride = (1, 1)
        backbone.layer4[0].downsample[0].stride = (1, 1)
        self.features = nn.Sequential(*list(backbone.children())[:-2])
        self.attention = nn.Conv2d(512, 1, 1)
        nn.init.zeros_(self.attention.weight)
        nn.init.zeros_(self.attention.bias)
        self.output = nn.Sequential(nn.Dropout(.15), nn.Linear(512, classes))

    def forward(self, image):
        features = self.features(image)
        attention = self.attention(features).flatten(2).softmax(-1)
        pooled = (features.flatten(2) * attention).sum(-1)
        return self.output(pooled)


def freeze_batch_norm(model):
    for module in model.modules():
        if isinstance(module, nn.BatchNorm2d):
            module.eval()
