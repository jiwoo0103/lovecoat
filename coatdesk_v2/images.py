from __future__ import annotations

import base64
import json
import threading
from collections import OrderedDict

from PIL.ImageQt import ImageQt
from PySide6.QtCore import QSize, QUrl
from PySide6.QtGui import QColor, QImage
from PySide6.QtQuick import QQuickImageProvider

from coatdesk.photos import read_image
from .domain import photo_path


def image_url(root, relative, roi=None):
    if not relative:
        return ""
    try:
        stamp = photo_path(root, relative).stat().st_mtime_ns
    except OSError:
        stamp = 0
    if roi is None:
        url = QUrl.fromLocalFile(str(photo_path(root, relative)))
        url.setFragment(str(stamp))
        return url.toString()
    payload = json.dumps([root, relative, roi, stamp]).encode()
    return "image://photos/" + base64.urlsafe_b64encode(payload).decode()


class Photos(QQuickImageProvider):
    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self.cache = OrderedDict()
        self.bytes = 0
        self.lock = threading.Lock()
        self.limit = 64 * 1024 * 1024

    def requestImage(self, identifier, size, requested_size):
        pixels = max(120, min(2400, max(requested_size.width(), requested_size.height(), 640)))
        key = (identifier, pixels)
        with self.lock:
            cached = self.cache.get(key)
            if cached is not None:
                self.cache.move_to_end(key)
                size.setWidth(cached.width())
                size.setHeight(cached.height())
                return cached
        try:
            root, relative, roi, _ = json.loads(base64.urlsafe_b64decode(identifier))
            image = read_image(str(photo_path(root, relative)), 3000 if roi else pixels)
            if roi:
                x1, y1, x2, y2 = roi
                image = image.crop((int(image.width*x1), int(image.height*y1), int(image.width*x2), int(image.height*y2)))
                image.thumbnail((pixels, pixels))
            result = QImage(ImageQt(image)).copy()
        except Exception:
            result = QImage(QSize(160, 120), QImage.Format.Format_RGB32)
            result.fill(QColor("#e5e7eb"))
        size.setWidth(result.width())
        size.setHeight(result.height())
        with self.lock:
            self.cache[key] = result
            self.bytes += result.sizeInBytes()
            while self.bytes > self.limit and self.cache:
                _, removed = self.cache.popitem(last=False)
                self.bytes -= removed.sizeInBytes()
        return result
