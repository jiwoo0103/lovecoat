"""One application-level page key handler, independent of card focus."""
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QGuiApplication


class NavigationKeys(QObject):
    def __init__(self, backend, window):
        super().__init__(window)
        self.backend, self.window = backend, window
        QGuiApplication.instance().installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.KeyPress or event.modifiers() != Qt.KeyboardModifier.NoModifier:
            return False
        direction = {Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1, Qt.Key.Key_PageUp: -1, Qt.Key.Key_PageDown: 1}.get(event.key())
        if direction is None or not self.window.property("pageNavigationEnabled"):
            return False
        if QGuiApplication.focusWindow() not in (None, self.window):
            return False
        focus = self.window.activeFocusItem()
        while focus:
            # QQuickTextInput/TextEdit expose cursorPosition. Closed ComboBoxes do not.
            if focus.metaObject().indexOfProperty("cursorPosition") >= 0:
                return False
            focus = focus.parentItem()
        self.backend.pageBy(direction)
        event.accept()
        return True
