"""Exercise theme transitions, retained editing and all styled dialogs in QML."""
from PySide6.QtCore import QMetaObject, QObject, QPoint, QPointF, Qt
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest

from coatdesk_v2.backend import Backend
from test_v2_ui_flow import capture, click, seed_review, visual
from test_v2_ui_flow import ui as ui
from test_v2_workspace import wait_for


def test_icon_only_theme_toggle_animates_persists_and_works_in_dialog(ui):
    backend, window, keys, _ = ui
    backend.recent = backend.repository.recent()
    backend._notify()
    QTest.qWait(100)
    toggle = window.findChild(QObject, "themeToggle")
    symbol = window.findChild(QObject, "themeIcon")
    assert toggle.property("visible") and toggle.property("enabled")
    assert backend.theme == "light" and symbol.property("phase") == 0
    capture(window, "v26-home-light.png")
    click(window, toggle)
    assert backend.theme == "dark"
    assert 0 < symbol.property("phase") < 1
    QTest.qWait(500)
    assert symbol.property("phase") == 1
    assert toggle.property("actionLabel") == "라이트 모드로 전환"
    assert window.property("color").name() == "#191919"
    capture(window, "v26-home-dark.png")
    again = Backend(backend.repository.directory)
    assert again.theme == "dark"
    again.shutdown()
    dialog = window.findChild(QObject, "createDialog")
    QMetaObject.invokeMethod(dialog, "open")
    QTest.qWait(80)
    click(window, window.findChild(QObject, "createDialogThemeToggle"))
    QTest.qWait(500)
    assert dialog.property("opened") and backend.theme == "light"
    capture(window, "v26-create-light.png")
    click(window, window.findChild(QObject, "createDialogThemeToggle"))
    QTest.qWait(500)
    capture(window, "v26-create-dark.png")
    QMetaObject.invokeMethod(dialog, "close")
    backend.openWorkspace(keys[0])
    assert toggle.property("visible") and backend.theme == "dark"
    # Reverse a transition while it is running; settle at the final requested theme.
    click(window, toggle)
    click(window, toggle)
    QTest.qWait(500)
    assert symbol.property("phase") == 1 and backend.theme == "dark"


def test_themed_dialogs_and_pending_fields_keep_actions_visible_at_minimum_size(ui):
    backend, window, keys, _ = ui
    seed_review(backend, keys[0])
    window.resize(1000, 720)
    for theme in ("light", "dark"):
        backend.setTheme(theme)
        QTest.qWait(500)
        capture(window, f"v26-work-{theme}.png")
        pending = visual(window.contentItem(), "card_0001")
        normal = visual(window.contentItem(), "card_0004")
        assert pending.property("color") == normal.property("color")
        assert QQmlExpression(QQmlEngine.contextForObject(pending), pending, "border.width").evaluate()[0] == 2
        assert window.findChild(QObject, "excelExportButton").property("visible")
        for name in ("createDialog", "recentDialog", "sourceDialog", "aiDialog", "viewDialog", "headDialog", "zoomDialog", "transferDialog"):
            dialog = window.findChild(QObject, name)
            QMetaObject.invokeMethod(dialog, "open")
            QTest.qWait(70)
            assert dialog.property("opened")
            assert dialog.property("x") >= 0 and dialog.property("y") >= 0
            assert dialog.property("x") + dialog.property("width") <= window.width()
            assert dialog.property("y") + dialog.property("height") <= window.height()
            capture(window, f"v26-{name}-{theme}.png")
            QMetaObject.invokeMethod(dialog, "close")
        assert window.findChild(QObject, "batchDialog") is None
        assert window.findChild(QObject, "clearSelectionButton") is None


def test_category_filter_restore_legacy_selection_and_last_review_escape(ui):
    backend, window, keys, _ = ui
    seed_review(backend, keys[0])
    backend.repository.update(keys[0], view={"selected": ["0001", "0002"], "class_filters": {"gender": "여성", "size": "L", "age": "성인"}})
    backend.openWorkspace(keys[0])
    assert backend.class_filters["category"] == ""
    assert "selectedCount" not in backend.state and not hasattr(backend, "selected")
    backend.setClassificationFilters("여성", "L", "성인", "코트")
    assert [g.code for g in backend.filtered] == ["0002", "0003"]
    backend.openWorkspace(keys[1])
    backend.openWorkspace(keys[0])
    assert backend.class_filters["category"] == "코트"
    backend.setFilter("확인 필요")
    assert [g.code for g in backend.filtered] == ["0002"]
    backend.confirmGarment("0002")
    QTest.qWait(60)
    assert not backend.filtered
    click(window, visual(window.contentItem(), "resetAllFilters"))
    assert len(backend.filtered) == 4 and backend.filter_name == "전체"
    backend.setField("0003", "size", "S")
    QTest.keyClick(window, Qt.Key_Z, Qt.ControlModifier)
    QTest.qWait(60)
    assert backend.repository.load(keys[0]).garments[2].fields["size"] == "L"
    assert "selected" not in backend.repository.load(keys[0]).view


def test_readable_theme_text_pairs_and_menu_hides_undo(ui):
    backend, window, keys, _ = ui
    backend.openWorkspace(keys[0])
    context = QQmlEngine.contextForObject(window)

    def color(token):
        value, error = QQmlExpression(context, window, "Theme." + token).evaluate()
        assert not error
        return value

    def luminance(value):
        channels = [value.redF(), value.greenF(), value.blueF()]
        linear = [v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4 for v in channels]
        return sum(v*w for v, w in zip(linear, (.2126, .7152, .0722)))

    for theme in ("light", "dark"):
        backend.setTheme(theme)
        QTest.qWait(500)
        for foreground, background in (("text", "surface"), ("secondary", "surface"), ("text", "control"),
                                       ("primaryText", "primary"), ("primaryText", "primaryHover"),
                                       ("primaryText", "exportColor"), ("primaryText", "exportHover"),
                                       ("warningText", "warning"), ("pendingText", "pending"), ("pendingText", "reviewField"),
                                       ("text", "workspaceHeading"),
                                       ("accentText", "surface"), ("accentText", "canvas")):
            a, b = sorted((luminance(color(foreground)), luminance(color(background))))
            assert (b+.05)/(a+.05) >= 4.5, (theme, foreground, background)
        menu = window.findChild(QObject, "workspaceMenu")
        QMetaObject.invokeMethod(menu, "open")
        QTest.qWait(100)
        value, error = QQmlExpression(QQmlEngine.contextForObject(menu), menu,
            "Array.from({length: count}, function(_, i) { return itemAt(i).text || ''; }).filter(function(t) { return !!t; })").evaluate()
        assert not error
        if hasattr(value, "toVariant"):
            value = value.toVariant()
        assert value == ["엑셀로 저장", "보기 설정", "고급 설정", "작업 관리"]
        capture(window, f"v26-menu-{theme}.png")
        QMetaObject.invokeMethod(menu, "close")


def test_home_columns_and_selected_tab_underline_follow_the_view(ui):
    backend, window, keys, _ = ui
    grid = visual(window.contentItem(), "homeWorkspaceGrid")
    for width, columns in ((1000, 3), (1380, 4), (1650, 5)):
        window.resize(width, 780)
        QTest.qWait(70)
        assert grid.property("columns") == columns
    window.resize(1380, 920)
    backend.openWorkspace(keys[0])
    backend.openWorkspace(keys[1])
    QTest.qWait(100)
    for key, other in (keys, keys[::-1]):
        click(window, visual(window.contentItem(), "workspaceTab_" + key))
        wait_for(lambda: not backend.jobs and backend.state["workspaceId"] == key)
        active = visual(window.contentItem(), "workspaceUnderline_" + key)
        inactive = visual(window.contentItem(), "workspaceUnderline_" + other)
        assert active.isVisible() and active.width() > 100 and active.height() == 3
        assert not inactive.isVisible()
    click(window, window.findChild(QObject, "homeButton"))
    assert visual(window.contentItem(), "homeUnderline").isVisible()
    assert all(not visual(window.contentItem(), "workspaceUnderline_" + key).isVisible() for key in keys)


def test_choice_hover_color_stays_between_idle_and_hover_colors(ui):
    backend, window, keys, _ = ui
    seed_review(backend, keys[0])
    combo = visual(window.contentItem(), "category_0004_combo")
    point = combo.mapToScene(QPointF(combo.width() - 32, 5)).toPoint()
    center = combo.mapToScene(QPointF(combo.width() / 2, combo.height() / 2)).toPoint()
    for theme in ("light", "dark"):
        backend.setTheme(theme)
        QTest.mouseMove(window, QPoint(5, 80))
        QTest.qWait(420)
        idle = window.grabWindow().pixelColor(point).value()
        QTest.mouseMove(window, center)
        assert combo.property("hovered")
        samples = []
        for _ in range(6):
            QTest.qWait(25)
            samples.append(window.grabWindow().pixelColor(point).value())
        QTest.qWait(170)
        hovered = window.grabWindow().pixelColor(point).value()
        assert idle != hovered
        low, high = sorted((idle, hovered))
        assert all(low <= sample <= high for sample in samples), (theme, idle, samples, hovered)


def test_choice_popup_tracks_fields_after_resize(ui):
    backend, window, keys, _ = ui
    seed_review(backend, keys[0])
    for width, height in ((1380, 920), (1920, 1020), (1000, 720), (1380, 920)):
        window.resize(width, height)
        QTest.qWait(100)
        for code in ("0001", "0002"):
            combo = visual(window.contentItem(), f"category_{code}_combo")
            click(window, combo)
            context = QQmlEngine.contextForObject(combo)
            value, error = QQmlExpression(context, combo,
                "popup.contentItem.mapToItem(null, 0, 0).x - popup.padding").evaluate()
            assert not error and window.property("popupCount") == 1
            popup_width, error = QQmlExpression(context, combo, "popup.width").evaluate()
            expected = min(combo.mapToScene(QPointF()).x(), width - popup_width - 8)
            assert not error and abs(value - expected) < 1, (width, code, value)
            QTest.keyClick(window, Qt.Key_Escape)
            QTest.qWait(70)
            assert window.property("popupCount") == 0


def test_layout_preview_resets_without_dimming_or_applying(ui):
    backend, window, keys, _ = ui
    seed_review(backend, keys[0])
    backend.setLayout(2, 2)
    button = window.findChild(QObject, "layoutButton")
    popup = window.findChild(QObject, "layoutPopup")

    def preview():
        return popup.property("previewRows"), popup.property("previewColumns")

    point = QPoint(8, 250)
    QTest.mouseMove(window, point)
    QTest.qWait(170)
    idle = window.grabWindow().pixelColor(point)
    click(window, button)
    assert popup.property("opened") and preview() == (2, 2)
    assert window.grabWindow().pixelColor(point) == idle
    last = visual(window.contentItem(), "layoutCell_15")
    if last is None:
        last = window.findChild(QObject, "layoutCell_15")
    end = last.mapToScene(QPointF(last.width() / 2, last.height() / 2)).toPoint()
    QTest.mouseMove(window, end)
    assert preview() == (4, 4)
    QTest.mouseMove(window, point)
    assert preview() == (2, 2)
    QTest.mouseMove(window, end)
    QTest.keyClick(window, Qt.Key_Escape)
    QTest.qWait(70)
    assert not popup.property("opened")
    click(window, button)
    assert preview() == (2, 2)
    QTest.keyClick(window, Qt.Key_Right)
    assert preview() == (2, 3) and backend.state["gridColumns"] == 2
    QTest.keyClick(window, Qt.Key_Return)
    QTest.qWait(70)
    assert not popup.property("opened") and backend.state["gridColumns"] == 3
