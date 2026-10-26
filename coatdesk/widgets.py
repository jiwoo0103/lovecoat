from __future__ import annotations

from collections import OrderedDict

from PySide6.QtCore import QObject, QRunnable, QRect, QRectF, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QFont, QImage, QImageReader, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QGraphicsScene, QGraphicsView, QLabel,
    QPushButton, QRubberBand, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .domain import Task, boundaries


class JobSignals(QObject):
    done = Signal(object)
    error = Signal(str)
    progress = Signal(str)


class Job(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = JobSignals()

    def run(self):
        try:
            self.signals.done.emit(self.function(self.signals.progress.emit))
        except (ValueError, OSError, RuntimeError) as error:
            self.signals.error.emit(str(error))
        except Exception as error:
            # Third-party errors may embed authenticated requests. Show no raw response.
            status = getattr(getattr(error, "resp", None), "status", None)
            self.signals.error.emit(
                f"작업에 실패했습니다 ({type(error).__name__}"
                f"{f' · HTTP {status}' if status else ''}). 연결과 설정을 확인하세요."
            )


class PhotoGrid(QWidget):
    selected = Signal(int, object)
    opened = Signal(int, str)
    page_requested = Signal(int)

    def __init__(self):
        super().__init__()
        self.setMinimumSize(500, 350)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.drag_start = None
        self.task: Task | None = None
        self.regions = []
        self.cache: OrderedDict[str, QPixmap] = OrderedDict()
        self.pending = set()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(3)

    def set_task(self, task: Task | None):
        self.task = task
        self.update()

    def thumbnail(self, path: str):
        if path in self.cache:
            self.cache.move_to_end(path)
            return self.cache[path]
        if path and path not in self.pending:
            self.pending.add(path)

            def read(_):
                reader = QImageReader(path)
                reader.setAutoTransform(True)
                size = reader.size()
                size.scale(QSize(600, 1000), Qt.AspectRatioMode.KeepAspectRatio)
                reader.setScaledSize(size)
                return path, reader.read()

            job = Job(read)
            job.signals.done.connect(self.loaded)
            job.signals.error.connect(lambda _, p=path: self.loaded((p, QImage())))
            self.pool.start(job)
        return None

    def loaded(self, result):
        path, image = result
        self.pending.discard(path)
        self.cache[path] = QPixmap.fromImage(image)
        while len(self.cache) > 160:
            self.cache.popitem(last=False)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor("#edf1f6"))
        self.regions = []
        if not self.task:
            painter.setPen(QColor("#526175"))
            painter.setFont(QFont("Malgun Gothic", 15))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "사진 폴더를 불러와 작업을 시작하세요\n\n한 벌씩 3장 · 정면만 모아보기 · 번호로 찾기")
            return
        task = self.task
        per_page = task.rows * (3 if task.front_only else 1)
        start = task.page * per_page
        row_height = self.height() / task.rows
        width = self.width() / (3 if task.front_only else 1)
        edges = boundaries(task.garments)
        ordinals = task.ordinals()
        for offset, garment in enumerate(task.garments[start:start + per_page]):
            index = start + offset
            x = offset % 3 * width if task.front_only else 0
            y = (offset // 3 if task.front_only else offset) * row_height
            box = QRectF(x + 5, y + 4, width - 10, row_height - 8)
            chosen = index in task.selected
            boundary = index in edges and not garment.excluded
            background = "#f2f2f2" if garment.excluded else ("#fff1d6" if boundary else "#ffffff")
            border = "#2563eb" if chosen else ("#e6a13b" if boundary else "#d4dce8")
            painter.setBrush(QColor(background))
            painter.setPen(QPen(QColor(border), 3 if chosen else 1))
            painter.drawRoundedRect(box, 7, 7)
            painter.setPen(QColor("#777777" if garment.excluded else "#162a46"))
            painter.setFont(QFont("Malgun Gothic", 9, QFont.Weight.Bold))
            tags = " · ".join(garment.fields.get(k, "") for k in ("gender", "age", "size") if garment.fields.get(k))
            badge = "제외" if garment.excluded else f"#{ordinals.get(garment.code, '')}"
            if index in edges:
                badge += "  ↔ 경계 후보"
            if garment.issues:
                badge += "  ! 사진 확인"
            title = f"{garment.code}  {badge}   {tags}"
            painter.drawText(box.adjusted(10, 3, -8, 0), Qt.AlignmentFlag.AlignTop, title)
            angles = [garment.front] if task.front_only else ["0", "1", "2"]
            for col, angle in enumerate(angles):
                image_box = QRectF(box.x() + col * box.width() / len(angles) + 4,
                                   box.y() + 25, box.width() / len(angles) - 8, box.height() - 31)
                pixmap = self.thumbnail(garment.photos.get(angle, ""))
                if pixmap and not pixmap.isNull():
                    fitted = pixmap.size().scaled(image_box.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio)
                    zoom = task.photo_zoom / 100
                    destination = QRectF(0, 0, fitted.width() * zoom, fitted.height() * zoom)
                    destination.moveCenter(image_box.center())
                    painter.save()
                    painter.setClipRect(image_box)
                    painter.drawPixmap(destination, pixmap, QRectF(pixmap.rect()))
                    painter.restore()
                else:
                    painter.setPen(QColor("#8a98aa"))
                    message = "불러오는 중" if pixmap is None and garment.photos.get(angle) else f"{angle}번 사진 없음"
                    painter.drawText(image_box, Qt.AlignmentFlag.AlignCenter, message)
                self.regions.append((image_box, index, angle))
            self.regions.append((QRectF(box.x(), box.y(), box.width(), 25), index, garment.front))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus()
            self.drag_start = event.position()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.drag_start is None:
            return
        delta = event.position() - self.drag_start
        origin = self.drag_start
        self.drag_start = None
        if abs(delta.x()) >= 70 and abs(delta.x()) > abs(delta.y()) * 1.4:
            self.page_requested.emit(1 if delta.x() < 0 else -1)
            return
        if (event.position() - origin).manhattanLength() > 12:
            return
        for box, index, _ in self.regions:
            if box.contains(event.position()):
                self.selected.emit(index, event.modifiers())
                break

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            self.page_requested.emit(1)
        elif event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self.page_requested.emit(-1)
        else:
            super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.drag_start = None
        for box, index, angle in self.regions:
            if box.contains(event.position()):
                self.opened.emit(index, angle)
                break


class ZoomView(QGraphicsView):
    def __init__(self, pixmap: QPixmap):
        super().__init__()
        scene = QGraphicsScene(self)
        scene.addPixmap(pixmap)
        self.setScene(scene)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#e5e9ee"))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    def wheelEvent(self, event):
        factor = 1.18 if event.angleDelta().y() > 0 else 1 / 1.18
        if .02 < self.transform().m11() * factor < 20:
            self.scale(factor, factor)

    def fit(self):
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)


def show_zoom(parent, pixmap: QPixmap, title: str):
    dialog = QDialog(parent)
    dialog.setWindowTitle(title + " · 휠로 확대, 드래그로 이동")
    dialog.resize(900, 850)
    layout = QVBoxLayout(dialog)
    view = ZoomView(pixmap)
    layout.addWidget(view)
    fit = QPushButton("화면에 맞추기")
    fit.clicked.connect(view.fit)
    layout.addWidget(fit)
    dialog.show()
    view.fit()
    dialog.exec()


class CropPicker(QLabel):
    def __init__(self, image: QImage):
        super().__init__()
        self.original = QPixmap.fromImage(image)
        self.setFixedSize(480, 670)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.display = self.original.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation)
        self.setPixmap(self.display)
        self.offset_x = (self.width() - self.display.width()) / 2
        self.offset_y = (self.height() - self.display.height()) / 2
        self.rubber = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self.origin = None
        self.roi = None

    def mousePressEvent(self, event):
        self.origin = event.position().toPoint()
        self.rubber.setGeometry(QRect(self.origin, QSize()))
        self.rubber.show()

    def mouseMoveEvent(self, event):
        if self.origin is not None:
            self.rubber.setGeometry(QRect(self.origin, event.position().toPoint()).normalized())

    def mouseReleaseEvent(self, event):
        if self.origin is None:
            return
        box = QRect(self.origin, event.position().toPoint()).normalized()
        left = max(0., (box.left() - self.offset_x) / self.display.width())
        top = max(0., (box.top() - self.offset_y) / self.display.height())
        right = min(1., (box.right() - self.offset_x) / self.display.width())
        bottom = min(1., (box.bottom() - self.offset_y) / self.display.height())
        if right - left > .02 and bottom - top > .02:
            self.roi = [left, top, right, bottom]
        self.origin = None


class PreviewDialog(QDialog):
    def __init__(self, parent, title: str, summary: str, headers: list[str], rows: list[list],
                 apply_text: str = "확인 후 실행", allowed: bool = True):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(960, 650)
        layout = QVBoxLayout(self)
        label = QLabel(summary)
        label.setWordWrap(True)
        layout.addWidget(label)
        table = QTableWidget(len(rows), len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                table.setItem(r, c, QTableWidgetItem(str(value)))
        table.horizontalHeader().setStretchLastSection(True)
        table.resizeColumnsToContents()
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("닫기")
        apply = buttons.addButton(apply_text, QDialogButtonBox.ButtonRole.AcceptRole)
        apply.setEnabled(allowed)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
