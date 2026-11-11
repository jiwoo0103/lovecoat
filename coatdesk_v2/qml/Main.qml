import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

ApplicationWindow {
    id: window
    width: 1380
    height: 920
    minimumWidth: 1000
    minimumHeight: 720
    visible: true
    readonly property string appName: "외투작업실"
    title: appName + (appState.home ? "" : " · " + appState.name)
    flags: Qt.platform.os === "windows"
           ? Qt.Window | Qt.CustomizeWindowHint | Qt.WindowSystemMenuHint | Qt.WindowMinimizeButtonHint
             | Qt.WindowMaximizeButtonHint | Qt.WindowCloseButtonHint | Qt.ExpandedClientAreaHint | Qt.NoTitleBarBackgroundHint
           : Qt.Window
    readonly property real nativeTitleHeight: SafeArea.margins.top
    color: Theme.canvas
    font.family: "Malgun Gothic"
    font.pixelSize: 14
    property var appState: appBackend.state
    property int popupCount: 0
    property bool pageNavigationEnabled: working && !appState.loadingWorkspace && popupCount === 0 && !classificationPopup.opened && !layoutPopup.opened && !workspaceMenu.opened && !addTabMenu.opened && !createDialog.opened && !recentDialog.opened && !sourceDialog.opened && !aiDialog.opened && !viewDialog.opened && !headDialog.opened && !zoomDialog.opened && !transferDialog.opened
    Component.onCompleted: appBackend.attachWindow(window)
    property bool working: appState.hasWorkspace && !appState.home
    palette.window: Theme.surface
    palette.windowText: Theme.text
    palette.alternateBase: Theme.control
    palette.highlightedText: Theme.primaryText
    palette.toolTipBase: Theme.surface
    palette.toolTipText: Theme.text
    palette.highlight: Theme.primary
    palette.button: Theme.control
    palette.buttonText: Theme.text
    palette.text: Theme.text
    palette.base: Theme.surface
    palette.placeholderText: Theme.secondary
    palette.mid: Theme.border
    palette.dark: Theme.border
    palette.brightText: Theme.primaryText
    onClosing: contentItem.forceActiveFocus()

    function showZoom(code) { appBackend.focusGarment(code); zoomDialog.open() }
    function showHead(code) { appBackend.focusGarment(code); headDialog.open() }
    property var managedWorkspace: ({id: "", name: ""})
    function manageWorkspace(record, action) {
        managedWorkspace = record
        if (action === "rename") { renameField.text = record.name; renameDialog.open() }
        else if (action === "delete") deleteDialog.open()
    }
    function emptyPhotoSummary() {
        var codes = appState.emptyPhotoCodes
        return "사진 없는 폴더 " + codes.length + "개" + (codes.length ? " · " + codes.slice(0, 12).join(", ") + "번" : "") + (codes.length > 12 ? " 외 " + (codes.length-12) + "개" : "")
    }
    function applyClassFilters() {
        appBackend.setClassificationFilters(genderFilter.currentIndex > 0 ? genderFilter.currentText : "",
            sizeFilter.currentIndex > 0 ? sizeFilter.currentText : "",
            ageFilter.currentIndex > 0 ? ageFilter.currentText : "",
            categoryFilter.currentIndex > 0 ? categoryFilter.currentText : "")
    }
    header: Rectangle {
        id: titleBar
        objectName: "titleBar"
        height: window.nativeTitleHeight > 0 ? window.nativeTitleHeight : 36
        color: Theme.canvas
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 14
            // The Windows Qt platform uses three caption buttons, each 1.5 times the caption height.
            anchors.rightMargin: window.nativeTitleHeight > 0 ? Math.floor(window.nativeTitleHeight * 1.5) * 3 + 10 : 12
            spacing: 8
            CoatMark { Layout.preferredWidth: 20; Layout.preferredHeight: 20 }
            Label { text: window.appName; color: Theme.text; font.bold: true }
            Item { Layout.fillWidth: true }
            ThemeToggle { Layout.fillHeight: true }
        }
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.border }
    }
    footer: Rectangle {
        height: 48
        color: Theme.canvas
        RowLayout {
            anchors.fill: parent
            anchors.margins: 6
            spacing: 5
            ActionButton {
                objectName: "homeButton"
                text: "홈"
                iconName: "home"
                flat: true
                implicitWidth: 82
                Layout.fillHeight: true
                focusPolicy: Qt.NoFocus
                enabled: !appState.navigationBlocked
                background: Rectangle {
                    color: parent.hovered ? Theme.control : "transparent"
                }
                Rectangle {
                    objectName: "homeUnderline"
                    anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: 2 }
                    height: 3; radius: 1.5; color: Theme.tabIndicator; visible: appState.home; z: 1
                }
                contentItem: RowLayout {
                    spacing: 8
                    AppIcon { name: "home"; ink: Theme.text }
                    Label { text: "홈"; color: Theme.text; font.bold: appState.home }
                }
                onClicked: appBackend.goHome()
            }
            ListView {
                id: workspaceTabs
                objectName: "workspaceTabs"
                Layout.fillWidth: true
                Layout.fillHeight: true
                orientation: ListView.Horizontal
                spacing: 5
                clip: true
                model: appState.openWorkspaces
                ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AsNeeded; height: 4 }
                delegate: ActionButton {
                    id: tabButton
                    required property var modelData
                    objectName: "workspaceTab_" + modelData.id
                    height: workspaceTabs.height
                    width: Math.min(230, Math.max(140, implicitWidth))
                    text: modelData.name
                    readonly property bool selectedWorkspace: !appState.home && appState.workspaceId === modelData.id
                    focusPolicy: Qt.NoFocus
                    enabled: !appState.navigationBlocked
                    contentItem: RowLayout {
                        spacing: 8
                        Label { Layout.fillWidth: true; text: tabButton.text; color: tabButton.selectedWorkspace ? Theme.text : Theme.secondary; font.bold: tabButton.selectedWorkspace; elide: Text.ElideRight }
                        ActionButton {
                            objectName: "closeWorkspace_" + tabButton.modelData.id
                            text: ""
                            iconName: "close"
                            flat: true
                            Accessible.name: "탭 닫기"
                            font.pixelSize: 18
                            implicitWidth: 32
                            implicitHeight: 32
                            ToolTip.visible: hovered
                            ToolTip.text: "탭 닫기 · 작업 기록은 유지합니다"
                            onClicked: { var key = tabButton.modelData.id; window.contentItem.forceActiveFocus(); appBackend.closeWorkspace(key) }
                        }
                    }
                    background: Rectangle {
                        radius: 6
                        color: tabButton.hovered ? Theme.control : "transparent"
                        border.color: tabButton.visualFocus ? Theme.primary : "transparent"
                    }
                    Rectangle {
                        objectName: "workspaceUnderline_" + tabButton.modelData.id
                        anchors { left: parent.left; right: parent.right; bottom: parent.bottom; leftMargin: 3; rightMargin: 3; bottomMargin: 4 }
                        height: 3; radius: 1.5; color: Theme.tabIndicator; visible: tabButton.selectedWorkspace; z: 1
                    }
                    onClicked: { var key = modelData.id; window.contentItem.forceActiveFocus(); appBackend.requestWorkspace(key) }
                }
            }
            ToolButton {
                objectName: "addWorkspaceTab"
                text: "+"
                font.pixelSize: 24
                implicitWidth: 38
                enabled: !appState.navigationBlocked
                onClicked: addTabMenu.open()
                AppMenu {
                    id: addTabMenu
                    y: -implicitHeight
                    AppMenuItem { objectName: "newWorkspaceTabAction"; text: "새 작업 만들기"; onTriggered: createDialog.open() }
                    AppMenuItem { objectName: "openWorkspaceTabAction"; text: "기존 작업 열기"; onTriggered: recentDialog.open() }
                }
            }
        }
    }
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 10
        RowLayout {
            objectName: "appHeader"
            visible: window.working
            Layout.minimumHeight: 36
            Layout.fillWidth: true
            spacing: 12
            Label { visible: window.working; text: appState.name; font.pixelSize: 21; font.bold: true; color: Theme.text; Layout.maximumWidth: window.width * 0.4; elide: Text.ElideRight }
            Label { visible: window.working; text: appState.date + " · " + appState.counts.total + "벌"; color: Theme.secondary }
            Item { Layout.fillWidth: true }
            Label { visible: window.working && (appState.workspaceLocked || !appState.saved); text: appState.workspaceLocked ? "엑셀 저장 중 · 입력 잠금" : "저장 확인 필요"; color: appState.saved ? Theme.secondary : Theme.pendingText; font.pixelSize: 11 }
            ActionButton {
                visible: window.working
                objectName: "excelExportButton"
                text: "엑셀로 저장"
                iconName: "excel"
                tone: "export"
                enabled: !appState.navigationBlocked
                onClicked: { window.contentItem.forceActiveFocus(); transferDialog.open() }
            }
            ActionButton {
                visible: window.working
                objectName: "workspaceMenuButton"
                implicitWidth: 40
                implicitHeight: 38
                iconName: "more"
                text: ""
                flat: true
                Accessible.name: "작업 메뉴"
                enabled: !appState.navigationBlocked
                onClicked: workspaceMenu.open()
                ToolTip.visible: hovered
                ToolTip.text: "작업 메뉴"
                AppMenu {
                    id: workspaceMenu
                    objectName: "workspaceMenu"
                    x: parent.width - width
                    y: parent.height
                    AppMenuItem { objectName: "excelExportAction"; text: "엑셀로 저장"; onTriggered: { window.contentItem.forceActiveFocus(); transferDialog.open() } }
                    AppMenuSeparator {}
                    AppMenuItem { objectName: "viewSettingsAction"; text: "보기 설정"; onTriggered: viewDialog.open() }
                    AppMenuSeparator {}
                    AppMenu {
                        title: "고급 설정"
                        AppMenuItem { text: "사진 범위 / 에러번호 / 폴더"; onTriggered: sourceDialog.open() }
                        AppMenuItem { text: "분석 기준"; onTriggered: aiDialog.open() }
                        AppMenuItem { text: "작업 전체 다시 분석"; enabled: !appState.busy; onTriggered: appBackend.startAnalysis(true) }
                    }
                    AppMenu {
                        title: "작업 관리"
                        enabled: !appState.busy
                        AppMenuItem { text: "이름 변경"; onTriggered: window.manageWorkspace({id: appState.workspaceId, name: appState.name}, "rename") }
                        AppMenuSeparator {}
                        AppMenuItem { text: "작업 삭제…"; onTriggered: window.manageWorkspace({id: appState.workspaceId, name: appState.name}, "delete") }
                    }
                }
            }
        }
        Rectangle {
            visible: appState.error.length > 0
            Layout.fillWidth: true
            implicitHeight: errorLabel.implicitHeight + 16
            radius: 7
            color: Theme.pending
            RowLayout {
                anchors.fill: parent
                anchors.margins: 8
                Label { id: errorLabel; Layout.fillWidth: true; text: appState.error; wrapMode: Text.Wrap; color: Theme.pendingText }
                ToolButton { text: "닫기"; onClicked: appBackend.dismissError() }
            }
        }
        HomeView {
            objectName: "homeView"
            visible: appState.home
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.topMargin: 4
            controller: appBackend
            appState: window.appState
            onCreateRequested: createDialog.open()
            onManageRequested: function(record, action) { window.manageWorkspace(record, action) }
        }
        RowLayout {
            objectName: "workspaceToolbar"
            visible: window.working
            Layout.fillWidth: true
            spacing: 12
            ActionButton {
                objectName: "analyzeButton"
                iconName: "analysis"
                text: appState.recognizing ? "분석 중단" : "분석"
                implicitHeight: 35
                highlighted: true
                enabled: appState.recognizing || !appState.busy
                ToolTip.visible: hovered && appState.recognizing
                ToolTip.text: appState.analysisWorkspaceName + " 작업의 분석을 중단합니다."
                onClicked: { board.forceActiveFocus(); appState.recognizing ? appBackend.cancelAnalysis() : appBackend.startAnalysis(false) }
            }
            ActionButton {
                objectName: "reviewFilterButton"
                visible: appState.reviewCount > 0
                tone: "warning"
                iconName: "warning"
                text: "확인 필요  " + appState.reviewCount
                implicitHeight: 35
                checkable: true
                checked: appState.filter === "확인 필요"
                enabled: !appState.loadingWorkspace
                onClicked: { board.forceActiveFocus(); appBackend.setFilter(checked ? "확인 필요" : "전체") }
            }
            Item { Layout.fillWidth: true }

            ActionButton {
                objectName: "layoutButton"
                iconName: "grid"
                text: "배치"
                chevron: true
                implicitHeight: 35
                onClicked: layoutPopup.open()
                Popup {
                    id: layoutPopup
                    objectName: "layoutPopup"
                    y: parent.height+5
                    width: 230
                    padding: 14
                    modal: true
                    dim: false
                    property int previewRows: 0
                    property int previewColumns: 0
                    property bool pointerPreview: false
                    function resetPreview() {
                        pointerPreview = false
                        previewRows = appState.gridRows
                        previewColumns = appState.gridColumns
                    }
                    function previewWithKeys(rowStep, columnStep) {
                        pointerPreview = false
                        previewRows = Math.min(4, Math.max(1, previewRows + rowStep))
                        previewColumns = Math.min(4, Math.max(1, previewColumns + columnStep))
                    }
                    onAboutToShow: resetPreview()
                    onOpened: gridPicker.forceActiveFocus()
                    background: Rectangle { color: Theme.surface; radius: 10; border.color: Theme.border }
                    contentItem: ColumnLayout {
                        id: gridPicker
                        focus: true
                        Keys.onLeftPressed: layoutPopup.previewWithKeys(0, -1)
                        Keys.onRightPressed: layoutPopup.previewWithKeys(0, 1)
                        Keys.onUpPressed: layoutPopup.previewWithKeys(-1, 0)
                        Keys.onDownPressed: layoutPopup.previewWithKeys(1, 0)
                        Keys.onReturnPressed: { appBackend.setLayout(layoutPopup.previewRows, layoutPopup.previewColumns); layoutPopup.close() }
                        Label { text: layoutPopup.previewRows + "행 × " + layoutPopup.previewColumns + "열 · " + (layoutPopup.previewRows*layoutPopup.previewColumns) + "벌" }
                        GridLayout {
                            columns: 4
                            columnSpacing: 5
                            rowSpacing: 5
                            HoverHandler {
                                onHoveredChanged: if (!hovered && layoutPopup.pointerPreview) layoutPopup.resetPreview()
                            }
                            Repeater {
                                model: 16
                                ToolButton {
                                    required property int index
                                    objectName: "layoutCell_" + index
                                    implicitWidth: 45
                                    implicitHeight: 36
                                    hoverEnabled: true
                                    onHoveredChanged: if (hovered) {
                                        layoutPopup.pointerPreview = true
                                        layoutPopup.previewRows = Math.floor(index/4)+1
                                        layoutPopup.previewColumns = index%4+1
                                    }
                                    background: Rectangle { radius: 4; color: Math.floor(parent.index/4) < layoutPopup.previewRows && parent.index%4 < layoutPopup.previewColumns ? Theme.primary : Theme.control }
                                    onClicked: { appBackend.setLayout(Math.floor(index/4)+1, index%4+1); layoutPopup.close() }
                                }
                            }
                        }
                        Label { text: "작은 창에서는 카드를 축소합니다"; color: Theme.secondary; font.pixelSize: 11 }
                    }
                }
            }
            AppField {
                id: searchField
                objectName: "garmentSearch"
                placeholderText: "옷 번호 검색"
                text: appState.search
                implicitHeight: 35
                Layout.preferredWidth: window.width < 1100 ? 112 : 155
                enabled: !appState.loadingWorkspace
                onTextEdited: appBackend.setSearch(text)
            }
            ActionButton {
                id: classificationButton
                objectName: "classificationButton"
                property int activeCount: Object.keys(appState.classFilters).filter(function(k) { return !!appState.classFilters[k] }).length
                iconName: "filter"
                text: "분류" + (activeCount ? " · " + activeCount : "")
                chevron: true
                implicitHeight: 35
                enabled: !appState.loadingWorkspace
                onClicked: classificationPopup.open()
                Popup {
                    id: classificationPopup
                    objectName: "classificationPopup"
                    x: parent.width - width
                    y: parent.height + 7
                    width: 310
                    padding: 16
                    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
                    background: Rectangle { color: Theme.surface; radius: 9; border.color: Theme.border }
                    contentItem: ColumnLayout {
                        spacing: 10
                        RowLayout {
                            Label { text: "카테고리"; Layout.preferredWidth: 72 }
                            AppComboBox { id: categoryFilter; objectName: "categoryFilter"; Layout.fillWidth: true; model: ["전체"].concat(appState.options.category); currentIndex: Math.max(0, model.indexOf(appState.classFilters.category)); onActivated: window.applyClassFilters() }
                        }
                        RowLayout {
                            Label { text: "성별"; Layout.preferredWidth: 72 }
                            AppComboBox { id: genderFilter; objectName: "genderFilter"; Layout.fillWidth: true; model: ["전체"].concat(appState.options.gender); currentIndex: Math.max(0, model.indexOf(appState.classFilters.gender)); onActivated: window.applyClassFilters(); delegate: SelectOption { width: genderFilter.width; currentValue: index === genderFilter.currentIndex; highlighted: index === genderFilter.highlightedIndex } }
                        }
                        RowLayout {
                            Label { text: "사이즈"; Layout.preferredWidth: 72 }
                            AppComboBox { id: sizeFilter; objectName: "sizeFilter"; Layout.fillWidth: true; model: ["전체"].concat(appState.options.size); currentIndex: Math.max(0, model.indexOf(appState.classFilters.size)); onActivated: window.applyClassFilters(); delegate: SelectOption { width: sizeFilter.width; currentValue: index === sizeFilter.currentIndex; highlighted: index === sizeFilter.highlightedIndex } }
                        }
                        RowLayout {
                            Label { text: "성인 / 아동"; Layout.preferredWidth: 72 }
                            AppComboBox { id: ageFilter; objectName: "ageFilter"; Layout.fillWidth: true; model: ["전체"].concat(appState.options.age); currentIndex: Math.max(0, model.indexOf(appState.classFilters.age)); onActivated: window.applyClassFilters(); delegate: SelectOption { width: ageFilter.width; currentValue: index === ageFilter.currentIndex; highlighted: index === ageFilter.highlightedIndex } }
                        }
                        RowLayout {
                            Label { text: "선택한 조건을 모두 만족하는 옷"; font.pixelSize: 11; color: Theme.secondary; Layout.fillWidth: true }
                            ToolButton { objectName: "resetClassFilters"; text: "초기화"; onClicked: appBackend.setClassificationFilters("", "", "", "") }
                        }
                    }
                }
            }
        }
        Item {
            id: board
            objectName: "garmentBoard"
            visible: window.working
            Layout.fillWidth: true
            Layout.fillHeight: true
            focus: true
            GridLayout {
                id: garmentGrid
                objectName: "garmentGrid"
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                height: shownRows > 0 ? shownRows * ((board.height - (displayRows-1)*12)/displayRows + 12) - 12 : 0
                columns: appState.gridColumns
                columnSpacing: 12
                rowSpacing: 12
                property int displayRows: appState.gridRows
                property int shownRows: Math.ceil(Math.min(appState.visibleCount, appState.gridRows * columns) / columns)
                Repeater {
                    model: appBackend.pageModel
                    delegate: Item {
                        required property var record
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.preferredWidth: 1
                        Layout.preferredHeight: (board.height - (garmentGrid.displayRows-1)*12)/garmentGrid.displayRows
                        GarmentCard {
                            anchors.fill: parent
                            record: parent.record
                            controller: appBackend
                            fastInput: appState.fastInput
                            review: appState.mode === "review"
                            frontOnly: appState.frontOnly
                            photoSize: appState.photoSize
                            options: appState.options
                            enabled: !appState.loadingWorkspace && !appState.scopeChanging
                            editingEnabled: !appState.workspaceLocked
                            onZoomRequested: function(code) { window.showZoom(code) }
                            onHeadRequested: function(code) { window.showHead(code) }
                        }
                    }
                }
            }
            ColumnLayout {
                anchors.centerIn: parent
                visible: appState.visibleCount === 0
                Label { text: "이 조건에 해당하는 옷이 없습니다."; color: Theme.secondary }
                ActionButton { objectName: "resetAllFilters"; text: "전체 보기"; Layout.alignment: Qt.AlignHCenter; onClicked: appBackend.resetFilters() }
            }
        }
        RowLayout {
            visible: window.working || appState.exporting || appState.recognizing
            Layout.fillWidth: true
            spacing: 8
            ToolButton { visible: window.working; text: "←"; implicitHeight: 29; enabled: appState.page > 1; onClicked: appBackend.pageBy(-1) }
            Label { visible: window.working; text: appState.page + " / " + appState.pages + " 페이지"; color: Theme.secondary; font.pixelSize: 11 }
            ToolButton { visible: window.working; text: "→"; implicitHeight: 29; enabled: appState.page < appState.pages; onClicked: appBackend.pageBy(1) }
            BusyIndicator { running: appState.busy; visible: running; implicitWidth: 22; implicitHeight: 22 }
            Label { Layout.fillWidth: true; text: appState.busy ? appState.message : ""; elide: Text.ElideRight; color: Theme.secondary; font.pixelSize: 11 }
        }
    }
    Shortcut { sequence: "Ctrl+Z"; enabled: window.working && !zoomDialog.opened && !appState.navigationBlocked && !appState.loadingWorkspace && !appState.workspaceLocked; onActivated: appBackend.undo() }
    Shortcut { sequence: "Ctrl+F"; enabled: window.working; onActivated: searchField.forceActiveFocus() }
    SurfaceDialog {
        id: renameDialog
        objectName: "renameDialog"
        title: "작업 이름 변경"
        modal: true
        anchors.centerIn: parent
        width: 440
        standardButtons: Dialog.Ok | Dialog.Cancel
        acceptLabel: "이름 변경"
        acceptEnabled: renameField.text.trim().length > 0
        onAccepted: appBackend.renameWorkspace(window.managedWorkspace.id, renameField.text)
        AppField { id: renameField; objectName: "renameWorkspaceField"; width: parent.width; maximumLength: 120; onAccepted: if (renameDialog.acceptEnabled) renameDialog.accept() }
    }
    SurfaceDialog {
        id: deleteDialog
        objectName: "deleteDialog"
        title: "작업을 삭제할까요?"
        modal: true
        anchors.centerIn: parent
        width: 460
        standardButtons: Dialog.Ok | Dialog.Cancel
        acceptLabel: "작업 삭제"
        destructive: true
        onAccepted: appBackend.deleteWorkspace(window.managedWorkspace.id)
        Label { width: parent.width; text: "‘" + window.managedWorkspace.name + "’의 입력·검수·분석 기록을 삭제합니다. 되돌릴 수 없습니다.\n\n원본 사진과 이미 저장한 엑셀 파일은 유지합니다."; wrapMode: Text.Wrap; color: Theme.secondary }
    }



    SurfaceDialog {
        id: createDialog
        objectName: "createDialog"
        title: "새 작업 만들기"
        modal: true
        anchors.centerIn: parent
        width: 580
        standardButtons: Dialog.Ok | Dialog.Cancel
        acceptLabel: appState.busy ? "처리 중…" : "작업 시작"
        acceptObjectName: "createWorkspaceButton"
        acceptEnabled: workspaceForm.canSubmit
        autoAccept: false
        onPrimaryTriggered: workspaceForm.submit()
        WorkspaceForm { id: workspaceForm; width: parent.width; controller: appBackend; busy: appState.busy; onSubmitted: createDialog.close() }
    }

    SurfaceDialog {
        id: recentDialog
        objectName: "recentDialog"
        title: "최근 작업공간"
        modal: true
        anchors.centerIn: parent
        width: 660
        height: Math.min(560, window.height-100)
        standardButtons: Dialog.Close
        ListView {
            anchors.fill: parent
            clip: true
            spacing: 8
            model: appState.recent
            ScrollBar.vertical: ScrollBar {}
            delegate: ItemDelegate {
                width: ListView.view.width
                height: 77
                text: modelData.name + "  ·  " + modelData.shooting_date + "\n" + modelData.start + "–" + modelData.end + "번  /  " + modelData.count + "벌\n" + modelData.source
                font.pixelSize: 14
                onClicked: { var key = modelData.id; recentDialog.close(); appBackend.requestWorkspace(key) }
            }
            Label { anchors.centerIn: parent; visible: appState.recent.length === 0; text: "저장한 작업공간이 없습니다."; color: Theme.secondary }
        }
    }

    SurfaceDialog {
        id: sourceDialog
        objectName: "sourceDialog"
        title: "사진 범위 · 에러번호 · 사진 폴더"
        modal: true
        anchors.centerIn: parent
        width: 600
        standardButtons: Dialog.Close
        onOpened: { editErrors.text = appState.errors; editRangeStart.text = appState.start; editRangeEnd.text = appState.end }
        Connections { target: appBackend; function onScopeChanged() { sourceDialog.close() } }
        ColumnLayout {
            width: parent.width
            spacing: 12
            RowLayout {
                Layout.fillWidth: true
                ColumnLayout {
                    Layout.fillWidth: true
                    Label { text: "시작 번호" }
                    AppField { id: editRangeStart; objectName: "editRangeStart"; Layout.fillWidth: true; text: appState.start; enabled: !appState.busy; validator: IntValidator { bottom: 0; top: 99999999 } }
                }
                Label { text: "—"; Layout.topMargin: 20 }
                ColumnLayout {
                    Layout.fillWidth: true
                    Label { text: "끝 번호" }
                    AppField { id: editRangeEnd; objectName: "editRangeEnd"; Layout.fillWidth: true; text: appState.end; enabled: !appState.busy; validator: IntValidator { bottom: 0; top: 99999999 } }
                }
            }
            Label { text: "에러번호 · 이 범위에서 제외할 번호" }
            AppField { id: editErrors; objectName: "editErrorNumbers"; Layout.fillWidth: true; placeholderText: "212, 300"; enabled: !appState.busy }
            Label { Layout.fillWidth: true; text: "범위를 줄여도 기존 입력은 보관됩니다. 다시 늘리면 이어서 표시합니다. 범위 밖 에러번호는 함께 수정하세요."; color: Theme.secondary; wrapMode: Text.Wrap }
            Label { Layout.fillWidth: true; text: window.emptyPhotoSummary(); color: Theme.secondary; wrapMode: Text.Wrap }
            Label { text: "다른 작업공간과 이미 저장한 엑셀 파일은 바뀌지 않습니다."; color: Theme.secondary }
            Label { visible: appState.error.length > 0; Layout.fillWidth: true; text: appState.error; color: Theme.pendingText; wrapMode: Text.Wrap }
            ActionButton { objectName: "saveWorkspaceScope"; text: appState.scopeChanging ? "사진 검사 중…" : "사진 범위와 에러번호 저장"; enabled: !appState.busy; highlighted: true; onClicked: appBackend.updateWorkspaceScope(editRangeStart.text, editRangeEnd.text, editErrors.text) }
            Label { Layout.fillWidth: true; text: "사진 위치: " + appState.source; wrapMode: Text.Wrap; topPadding: 18 }
            ActionButton { text: "옮긴 사진 폴더 다시 연결"; enabled: !appState.busy; onClicked: reconnectFolder.open() }
        }
    }
    FolderDialog { id: reconnectFolder; title: "같은 번호 구조의 사진 상위 폴더"; onAccepted: { appBackend.reconnect(selectedFolder.toString()); sourceDialog.close() } }

    SurfaceDialog {
        id: aiDialog
        objectName: "aiDialog"
        title: "모델별 확인 필요 기준"
        modal: true
        anchors.centerIn: parent
        width: 430
        standardButtons: Dialog.Close
        ColumnLayout {
            width: parent.width
            spacing: 12
            Label { Layout.fillWidth: true; text: "후보값을 먼저 입력하고, 기준 미달 항목만 확인이 필요하도록 표시합니다. 점수는 실제 정답 확률이 아닙니다."; wrapMode: Text.Wrap }
            RowLayout { Label { text: "판넬 위치 (%)" } SpinBox { id: panelThreshold; from: 1; to: 100; value: Math.round(appState.aiThresholds.panel * 100) } }
            RowLayout { Label { text: "성별 / 성인·아동 (%)" } SpinBox { id: shapeThreshold; from: 1; to: 100; value: Math.round(appState.aiThresholds.shape * 100) } }
            RowLayout { Label { text: "사이즈 (%)" } SpinBox { id: sizeThreshold; from: 1; to: 100; value: Math.round(appState.aiThresholds.size * 100) } }
            RowLayout { Label { text: "옷 소매 (%)" } SpinBox { id: sleeveThreshold; objectName: "sleeveThreshold"; from: 1; to: 100; value: Math.round(appState.categoryThresholds.sleeve * 100) } }
            Label { text: "옷 기장 기준: 90%"; color: Theme.secondary }
            ActionButton { objectName: "saveAiThresholds"; text: "기준 저장"; enabled: !appState.busy; onClicked: { appBackend.setAiThresholds(panelThreshold.value, shapeThreshold.value, sizeThreshold.value, sleeveThreshold.value); aiDialog.close() } }
        }
    }



    SurfaceDialog {
        id: viewDialog
        objectName: "viewDialog"
        title: "보기 설정"
        modal: true
        anchors.centerIn: parent
        width: 440
        standardButtons: Dialog.Close
        ColumnLayout {
            width: parent.width
            spacing: 12
            Label { text: "현재 배치 " + appState.gridRows + "행 × " + appState.gridColumns + "열" }
            RowLayout {
                Label { text: "사진 보기"; Layout.fillWidth: true }
                ActionButton { objectName: "reviewModeButton"; text: "사진 + 입력"; checkable: true; checked: appState.mode === "review"; onClicked: appBackend.setMode("review") }
                ActionButton { objectName: "photoModeButton"; text: "사진만 보기"; checkable: true; checked: appState.mode === "photos"; onClicked: appBackend.setMode("photos") }
            }
            CheckBox { objectName: "fastInputCheckBox"; text: "빠른 입력"; checked: appState.fastInput; onClicked: appBackend.setFastInput(checked) }
            CheckBox { text: "사진만 볼 때 정면 사진만 표시"; checked: appState.frontOnly; onClicked: appBackend.setFrontOnly(checked) }
            Label { text: "사진 확대 " + appState.photoSize + "%" }
            Slider { Layout.fillWidth: true; from: 100; to: 250; stepSize: 10; value: appState.photoSize; onMoved: appBackend.setPhotoSize(value) }
            Label { Layout.fillWidth: true; text: "한 칸은 옷 한 벌입니다. 사진을 더블클릭하면 확대합니다. 좌우 방향키로 페이지를 이동합니다."; wrapMode: Text.Wrap; color: Theme.secondary }
        }
    }

    SurfaceDialog {
        id: headDialog
        objectName: "headDialog"
        title: "머리 확대 · " + (appState.current.code || "")
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        anchors.centerIn: parent
        width: Math.min(800, window.width-80)
        height: 540
        standardButtons: Dialog.Close
        ColumnLayout {
            anchors.fill: parent
            Label { Layout.fillWidth: true; text: appState.current.note || ""; wrapMode: Text.Wrap }
            Image { source: headDialog.opened ? (appState.current.head || "") : ""; asynchronous: false; fillMode: Image.PreserveAspectFit; sourceSize.width: 1200; Layout.fillWidth: true; Layout.fillHeight: true }
            RowLayout {
                ActionButton { text: "← 이전 옷"; onClicked: appBackend.nextGarment(-1) }
                ActionButton { text: "다음 옷 →"; onClicked: appBackend.nextGarment(1) }
                Item { Layout.fillWidth: true }
                ActionButton { text: "머리 영역 지정"; enabled: !appState.recognizing && !appState.workspaceLocked; onClicked: { headDialog.close(); zoomDialog.cropMode = true; zoomDialog.open() } }
                ActionButton { text: "자동 위치 사용"; enabled: !appState.recognizing && !appState.workspaceLocked; onClicked: appBackend.automaticPanel() }
                ActionButton { objectName: "analyzeCurrentButton"; text: "이 옷만 다시 분석"; enabled: !appState.busy && !!appState.current.code && !appState.current.excluded; onClicked: appBackend.analyzeCurrent() }
            }
        }
    }

    SurfaceDialog {
        id: zoomDialog
        objectName: "zoomDialog"
        title: "사진 상세 · " + (appState.current.code || "")
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        anchors.centerIn: parent
        width: window.width - 50
        height: window.height - 50
        standardButtons: Dialog.Close
        property bool cropMode: false
        property real factor: 1
        property real panX: 0
        property real panY: 0
        property var anglePhotos: []
        property string currentCode: appState.current.code || ""
        property string currentFront: appState.current.front || "0"
        function chooseFront() {
            anglePhotos = appState.current.photos || []
            angleBox.currentIndex = Math.max(0, anglePhotos.findIndex(function(p) { return p.angle === appState.current.front }))
        }
        onCurrentCodeChanged: if (opened) { factor = 1; panX = 0; panY = 0; chooseFront() }
        onCurrentFrontChanged: if (opened) chooseFront()
        onOpened: {
            factor = 1; panX = 0; panY = 0
            chooseFront()
            zoomArea.forceActiveFocus()
        }
        onClosed: cropMode = false
        ColumnLayout {
            anchors.fill: parent
            RowLayout {
                ActionButton { text: "← 이전 옷"; onClicked: appBackend.nextGarment(-1) }
                ActionButton { text: "다음 옷 →"; onClicked: appBackend.nextGarment(1) }
                AppComboBox { id: angleBox; model: zoomDialog.anglePhotos; textRole: "angle"; implicitWidth: 80; delegate: SelectOption { width: angleBox.width; text: modelData.angle; currentValue: index === angleBox.currentIndex; highlighted: index === angleBox.highlightedIndex } }
                ToolButton { text: "머리 확대"; onClicked: { zoomDialog.close(); headDialog.open() } }
                ActionButton { text: "정면으로"; enabled: !appState.recognizing && !appState.workspaceLocked; onClicked: appBackend.setFront(appState.current.code, angleBox.currentText) }
                CheckBox { text: "머리 영역 지정"; checked: zoomDialog.cropMode; enabled: !appState.recognizing && !appState.workspaceLocked; onClicked: { zoomDialog.cropMode = checked; zoomDialog.factor = 1; zoomDialog.panX = 0; zoomDialog.panY = 0 } }
                Item { Layout.fillWidth: true }
                ActionButton { text: "전체 맞춤"; onClicked: { zoomDialog.factor = 1; zoomDialog.panX = 0; zoomDialog.panY = 0 } }
            }
            Label { text: zoomDialog.cropMode ? "사진의 머리 전체와 글자가 들어가도록 드래그하세요." : "휠: 확대·축소  /  확대 시 드래그: 사진 이동  /  ← →: 이전·다음 옷"; color: Theme.secondary }
            Rectangle {
                id: zoomArea
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: Theme.photoBackground
                clip: true
                focus: true
                Keys.onLeftPressed: appBackend.nextGarment(-1)
                Keys.onRightPressed: appBackend.nextGarment(1)
                Image {
                    id: detailImage
                    source: zoomDialog.opened && zoomDialog.anglePhotos[angleBox.currentIndex] ? zoomDialog.anglePhotos[angleBox.currentIndex].url : ""
                    width: parent.width * zoomDialog.factor
                    height: parent.height * zoomDialog.factor
                    x: (parent.width-width)/2 + zoomDialog.panX
                    y: (parent.height-height)/2 + zoomDialog.panY
                    asynchronous: true
                    autoTransform: true
                    sourceSize.width: 2400
                    sourceSize.height: 2400
                    fillMode: Image.PreserveAspectFit
                }
                Rectangle {
                    visible: zoomDialog.cropMode && !!appState.current.roi
                    x: detailImage.x + (detailImage.width-detailImage.paintedWidth)/2 + (appState.current.roi || [0,0,1,1])[0]*detailImage.paintedWidth
                    y: detailImage.y + (detailImage.height-detailImage.paintedHeight)/2 + (appState.current.roi || [0,0,1,1])[1]*detailImage.paintedHeight
                    width: ((appState.current.roi || [0,0,1,1])[2]-(appState.current.roi || [0,0,1,1])[0])*detailImage.paintedWidth
                    height: ((appState.current.roi || [0,0,1,1])[3]-(appState.current.roi || [0,0,1,1])[1])*detailImage.paintedHeight
                    color: "#220f766e"
                    border.color: Theme.primary
                    border.width: 2
                }
                Rectangle {
                    visible: zoomMouse.pressed && zoomDialog.cropMode
                    x: Math.min(zoomMouse.startX, zoomMouse.mouseX)
                    y: Math.min(zoomMouse.startY, zoomMouse.mouseY)
                    width: Math.abs(zoomMouse.mouseX-zoomMouse.startX)
                    height: Math.abs(zoomMouse.mouseY-zoomMouse.startY)
                    color: "#332563eb"
                    border.color: Theme.primary
                    border.width: 2
                }
                MouseArea {
                    id: zoomMouse
                    anchors.fill: parent
                    property real startX: 0
                    property real startY: 0
                    property real oldX: 0
                    property real oldY: 0
                    onPressed: function(mouse) { startX = mouse.x; startY = mouse.y; oldX = zoomDialog.panX; oldY = zoomDialog.panY; zoomArea.forceActiveFocus() }
                    onPositionChanged: function(mouse) {
                        if (pressed && !zoomDialog.cropMode && zoomDialog.factor > 1) {
                            zoomDialog.panX = oldX + mouse.x-startX
                            zoomDialog.panY = oldY + mouse.y-startY
                        }
                    }
                    onWheel: function(wheel) { if (!zoomDialog.cropMode) zoomDialog.factor = Math.max(1, Math.min(6, zoomDialog.factor*(wheel.angleDelta.y > 0 ? 1.15 : 1/1.15))) }
                    onReleased: function(mouse) {
                        if (zoomDialog.cropMode && Math.abs(mouse.x-startX) > 8 && Math.abs(mouse.y-startY) > 8) {
                            let left = detailImage.x + (detailImage.width-detailImage.paintedWidth)/2
                            let top = detailImage.y + (detailImage.height-detailImage.paintedHeight)/2
                            let x1 = Math.max(0, Math.min(1, (Math.min(startX,mouse.x)-left)/detailImage.paintedWidth))
                            let y1 = Math.max(0, Math.min(1, (Math.min(startY,mouse.y)-top)/detailImage.paintedHeight))
                            let x2 = Math.max(0, Math.min(1, (Math.max(startX,mouse.x)-left)/detailImage.paintedWidth))
                            let y2 = Math.max(0, Math.min(1, (Math.max(startY,mouse.y)-top)/detailImage.paintedHeight))
                            if (angleBox.currentText !== appState.current.front) { appBackend.setFront(appState.current.code, angleBox.currentText) }
                            appBackend.setRoi(appState.current.code, x1,y1,x2,y2)
                        } else if (!zoomDialog.cropMode && zoomDialog.factor === 1 && Math.abs(mouse.x-startX) > 65)
                            appBackend.nextGarment(mouse.x < startX ? 1 : -1)
                    }
                }
            }
        }
    }

    SurfaceDialog {
        id: transferDialog
        objectName: "transferDialog"
        title: "엑셀 파일에 새 시트로 저장"
        palette.window: Theme.surface
        palette.windowText: Theme.text
        palette.text: Theme.text
        modal: true
        anchors.centerIn: parent
        width: Math.min(900, window.width-40)
        height: Math.min(720, window.height-70)
        standardButtons: Dialog.Ok | Dialog.Close
        acceptLabel: "새 시트에 저장"
        acceptObjectName: "excelSaveButton"
        acceptTone: "export"
        acceptEnabled: appState.planReady && !appState.busy
        onAccepted: { window.contentItem.forceActiveFocus(); appBackend.saveExcel() }
        onOpened: { excelPath.text = appState.excelPath; newTab.text = appState.date.replace(/-/g, ""); appBackend.invalidatePlan() }
        ColumnLayout {
            anchors.fill: parent
            spacing: 10
            Label { Layout.fillWidth: true; text: "엑셀 파일 하나에 작업 날짜별 시트를 추가합니다. 기존 시트는 유지합니다."; color: Theme.secondary; wrapMode: Text.Wrap }
            RowLayout {
                Layout.fillWidth: true
                ActionButton { text: "새 엑셀 파일"; enabled: !appState.navigationBlocked; onClicked: newExcelFile.open() }
                ActionButton { text: "기존 엑셀 파일 선택"; enabled: !appState.navigationBlocked; onClicked: existingExcelFile.open() }
            }
            AppField {
                id: excelPath
                objectName: "excelPathField"
                enabled: !appState.navigationBlocked
                Layout.fillWidth: true
                placeholderText: "저장할 .xlsx 파일을 선택하거나 전체 경로를 입력하세요."
                selectByMouse: true
                onTextChanged: appBackend.excelTargetChanged(text)
            }
            RowLayout {
                Label { text: "내장 표준 양식"; color: Theme.secondary }
                Item { Layout.fillWidth: true }
                Label { text: "새 시트 이름" }
                AppField { id: newTab; objectName: "newExcelSheetName"; enabled: !appState.navigationBlocked; Layout.preferredWidth: 160; onTextChanged: appBackend.invalidatePlan() }
                ActionButton { text: "미리보기"; objectName: "excelPreviewButton"; enabled: !appState.busy && excelPath.text.trim().length > 0 && newTab.text.trim().length > 0; onClicked: appBackend.previewExcel(excelPath.text, newTab.text) }
            }
            Label { objectName: "exportCounts"; Layout.fillWidth: true; text: "대상 " + appState.counts.total + "벌  ·  저장 가능 " + appState.counts.confirmed + "벌  ·  에러번호로 제외 " + appState.counts.excluded + "벌"; font.bold: true; wrapMode: Text.Wrap }
            Label { objectName: "emptyPhotosSummary"; Layout.fillWidth: true; text: window.emptyPhotoSummary(); wrapMode: Text.Wrap; color: appState.emptyPhotoCodes.length ? Theme.warningText : Theme.secondary }
            Label { Layout.fillWidth: true; text: appState.planReady ? (appState.planAddsToFile ? "기존 파일에 시트 추가: " : "새 파일에 시트 생성: ") + appState.planTitle + "  /  아래 내용을 확인한 뒤 저장하세요." : "모든 대상의 네 항목 입력과 필요한 확인을 마치면 미리보기를 만들 수 있습니다."; color: Theme.accentText; wrapMode: Text.Wrap }
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 90
                color: Theme.surface
                border.color: Theme.border
                radius: 6
                ListView {
                    objectName: "exportPreviewList"
                    anchors.fill: parent
                    anchors.margins: 8
                    clip: true
                    model: appState.planRows
                    header: ExportRow { width: ListView.view.width; heading: true; values: ["번호", "외투코드", "카테고리", "성별", "연령", "사이즈"] }
                    delegate: ExportRow { objectName: "exportRow_" + modelData[1]; width: ListView.view.width; values: modelData }
                    ScrollBar.vertical: ScrollBar {}
                }
            }
            Label { Layout.fillWidth: true; text: "같은 이름이 있으면 (2), (3)을 붙입니다. 사진 식별자가 저장되며 실제 사진은 파일에 포함하지 않습니다."; color: Theme.secondary; wrapMode: Text.Wrap }
            Label { visible: appState.error.length > 0; Layout.fillWidth: true; text: appState.error; color: Theme.pendingText; wrapMode: Text.Wrap }
            RowLayout {
                ActionButton { visible: appState.lastFileUrl.length > 0; text: "저장한 파일 열기"; onClicked: Qt.openUrlExternally(appState.lastFileUrl) }
                Item { Layout.fillWidth: true }
                BusyIndicator { implicitWidth: 30; implicitHeight: 30; running: appState.busy; visible: running }
            }
        }
    }
    FileDialog {
        id: newExcelFile
        objectName: "newExcelFileDialog"
        title: "새 엑셀 파일 저장 위치"
        fileMode: FileDialog.SaveFile
        options: FileDialog.DontConfirmOverwrite
        defaultSuffix: "xlsx"
        nameFilters: ["엑셀 파일 (*.xlsx)"]
        onAccepted: excelPath.text = selectedFile.toString()
    }
    FileDialog {
        id: existingExcelFile
        objectName: "existingExcelFileDialog"
        title: "시트를 추가할 엑셀 파일"
        fileMode: FileDialog.OpenFile
        nameFilters: ["엑셀 파일 (*.xlsx)"]
        onAccepted: excelPath.text = selectedFile.toString()
    }
}
