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
    title: "외투 작업실 2" + (appState.home ? "" : " · " + appState.name)
    color: "#f2f5f8"
    font.family: "Malgun Gothic"
    font.pixelSize: 13
    property var appState: appBackend.state
    property bool working: appState.hasWorkspace && !appState.home
    palette.highlight: "#0f766e"
    palette.button: "#f8fafc"
    palette.buttonText: "#1e293b"
    palette.text: "#1e293b"
    palette.base: "#ffffff"
    palette.placeholderText: "#81909c"
    palette.dark: "#0f766e"
    palette.brightText: "#ffffff"
    onClosing: contentItem.forceActiveFocus()

    function showZoom(code) { appBackend.focusGarment(code); zoomDialog.open() }
    function showHead(code) { appBackend.focusGarment(code); headDialog.open() }
    function applyClassFilters() {
        appBackend.setClassificationFilters(genderFilter.currentIndex > 0 ? genderFilter.currentText : "",
            sizeFilter.currentIndex > 0 ? sizeFilter.currentText : "",
            ageFilter.currentIndex > 0 ? ageFilter.currentText : "")
    }
    header: Rectangle {
        height: 62
        color: "#ffffff"
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 22
            anchors.rightMargin: 22
            Rectangle { width: 7; height: 27; radius: 3; color: "#0f766e" }
            ToolButton {
                objectName: "homeButton"
                text: "외투 작업실"
                font.pixelSize: 21
                font.bold: true
                enabled: !appState.busy
                onClicked: { window.contentItem.forceActiveFocus(); appBackend.goHome() }
                ToolTip.visible: hovered
                ToolTip.text: "홈으로"
            }
            Item { Layout.fillWidth: true }
            Label { text: "사진에서 정리까지, 한 작업공간에서"; color: "#81909d"; font.pixelSize: 11 }
        }
    }
    footer: Rectangle {
        height: 48
        color: "#e9eef1"
        RowLayout {
            anchors.fill: parent
            anchors.margins: 6
            spacing: 5
            ListView {
                id: workspaceTabs
                objectName: "workspaceTabs"
                Layout.fillWidth: true
                Layout.fillHeight: true
                orientation: ListView.Horizontal
                spacing: 5
                clip: true
                model: appState.openWorkspaces
                ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AsNeeded }
                delegate: Button {
                    required property var modelData
                    objectName: "workspaceTab_" + modelData.id
                    height: workspaceTabs.height
                    width: Math.min(230, Math.max(140, implicitWidth))
                    text: modelData.name
                    checkable: true
                    focusPolicy: Qt.NoFocus
                    checked: !appState.home && appState.workspaceId === modelData.id
                    enabled: !appState.busy
                    contentItem: Label { text: parent.text; color: parent.checked ? "#0f766e" : "#475569"; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
                    background: Rectangle { radius: 6; color: parent.checked ? "#ffffff" : (parent.hovered ? "#dfe9e7" : "#e2e8ec"); border.color: parent.checked ? "#a8ccc4" : "transparent" }
                    onClicked: { var key = modelData.id; window.contentItem.forceActiveFocus(); appBackend.openWorkspace(key) }
                }
            }
            ToolButton {
                objectName: "addWorkspaceTab"
                text: "+"
                font.pixelSize: 24
                implicitWidth: 38
                enabled: !appState.busy
                onClicked: addTabMenu.open()
                Menu {
                    id: addTabMenu
                    y: -implicitHeight
                    MenuItem { objectName: "newWorkspaceTabAction"; text: "새 작업 만들기"; onTriggered: createDialog.open() }
                    MenuItem { objectName: "openWorkspaceTabAction"; text: "기존 작업 열기"; onTriggered: recentDialog.open() }
                }
            }
        }
    }
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 10
        Rectangle {
            visible: appState.error.length > 0
            Layout.fillWidth: true
            implicitHeight: errorLabel.implicitHeight + 16
            radius: 7
            color: "#fff0ee"
            RowLayout {
                anchors.fill: parent
                anchors.margins: 8
                Label { id: errorLabel; Layout.fillWidth: true; text: appState.error; wrapMode: Text.Wrap; color: "#a03227" }
                ToolButton { text: "닫기"; onClicked: appBackend.dismissError() }
            }
        }
        HomeView {
            objectName: "homeView"
            visible: appState.home
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.topMargin: 22
            controller: appBackend
            appState: window.appState
            onCreateRequested: createDialog.open()
        }
        RowLayout {
            visible: window.working
            Layout.fillWidth: true
            spacing: 12
            Label { text: appState.name; font.pixelSize: 21; font.bold: true; color: "#183449"; Layout.maximumWidth: window.width * 0.4; elide: Text.ElideRight }
            Label { text: appState.date + " · " + appState.counts.total + "벌"; color: "#73818c" }
            Item { Layout.fillWidth: true }
            Label { text: appState.saved ? "자동 저장" : "저장 확인 필요"; color: appState.saved ? "#74958d" : "#a03227"; font.pixelSize: 11 }
            ToolButton {
                objectName: "workspaceMenuButton"
                implicitWidth: 40
                implicitHeight: 38
                contentItem: Item {
                    implicitWidth: 24
                    implicitHeight: 24
                    Repeater { model: 3; Rectangle { required property int index; x: (parent.width-19)/2; y: (parent.height-14)/2 + index*6; width: 19; height: 2; radius: 1; color: "#334155" } }
                }
                enabled: !appState.busy
                onClicked: workspaceMenu.open()
                ToolTip.visible: hovered
                ToolTip.text: "작업 메뉴"
                Menu {
                    id: workspaceMenu
                    objectName: "workspaceMenu"
                    x: parent.width - width
                    y: parent.height
                    MenuItem { objectName: "excelExportAction"; text: "엑셀로 저장"; onTriggered: { window.contentItem.forceActiveFocus(); transferDialog.open() } }
                    MenuSeparator {}
                    MenuItem { text: "보기 설정"; onTriggered: viewDialog.open() }
                    MenuItem { text: "사진 폴더 / 에러번호"; onTriggered: sourceDialog.open() }
                    MenuItem { text: "분석 기준"; onTriggered: aiDialog.open() }
                    MenuItem { text: "다시 분석"; onTriggered: appBackend.startAnalysis(true) }
                    MenuSeparator {}
                    MenuItem { text: "현재 페이지 선택"; onTriggered: appBackend.selectPage() }
                    MenuItem { text: "선택한 " + appState.selectedCount + "벌 일괄 입력"; enabled: appState.selectedCount > 0; onTriggered: batchDialog.open() }
                    MenuItem { text: "선택한 옷 입력 지우기"; enabled: appState.selectedCount > 0; onTriggered: appBackend.clearSelected() }
                    MenuItem { text: "실행 취소  Ctrl+Z"; enabled: appState.canUndo; onTriggered: appBackend.undo() }
                }
            }
        }
        RowLayout {
            visible: window.working
            Layout.fillWidth: true
            spacing: 8
            Button {
                objectName: "analyzeButton"
                text: appState.recognizing ? "분석 중단" : "분석"
                implicitHeight: 35
                highlighted: true
                enabled: appState.recognizing || !appState.busy
                onClicked: { board.forceActiveFocus(); appState.recognizing ? appBackend.cancelAnalysis() : appBackend.startAnalysis(false) }
            }
            Button {
                objectName: "reviewFilterButton"
                text: "확인 필요  " + appState.reviewCount
                implicitHeight: 35
                checkable: true
                checked: appState.filter === "확인 필요"
                enabled: !appState.exporting
                onClicked: { board.forceActiveFocus(); appBackend.setFilter(checked ? "확인 필요" : "전체") }
            }
            Label { visible: appState.selectedCount > 0; text: appState.selectedCount + "벌 선택"; color: "#73818c"; font.pixelSize: 11 }
            Item { Layout.fillWidth: true }
            TextField {
                id: searchField
                objectName: "garmentSearch"
                placeholderText: "옷 번호 검색"
                text: appState.search
                implicitHeight: 35
                Layout.preferredWidth: 155
                enabled: !appState.exporting
                onTextEdited: appBackend.setSearch(text)
            }
            Button {
                id: classificationButton
                objectName: "classificationButton"
                property int activeCount: Object.keys(appState.classFilters).filter(function(k) { return !!appState.classFilters[k] }).length
                text: "분류" + (activeCount ? " · " + activeCount : "") + "  ▼"
                implicitHeight: 35
                enabled: !appState.exporting
                onClicked: classificationPopup.open()
                Popup {
                    id: classificationPopup
                    objectName: "classificationPopup"
                    x: parent.width - width
                    y: parent.height + 7
                    width: 310
                    padding: 16
                    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
                    background: Rectangle { color: "#ffffff"; radius: 9; border.color: "#dce3eb" }
                    contentItem: ColumnLayout {
                        spacing: 10
                        RowLayout {
                            Label { text: "성별"; Layout.preferredWidth: 72 }
                            ComboBox { id: genderFilter; objectName: "genderFilter"; Layout.fillWidth: true; model: ["전체"].concat(appState.options.gender); currentIndex: Math.max(0, model.indexOf(appState.classFilters.gender)); onActivated: window.applyClassFilters() }
                        }
                        RowLayout {
                            Label { text: "사이즈"; Layout.preferredWidth: 72 }
                            ComboBox { id: sizeFilter; objectName: "sizeFilter"; Layout.fillWidth: true; model: ["전체"].concat(appState.options.size); currentIndex: Math.max(0, model.indexOf(appState.classFilters.size)); onActivated: window.applyClassFilters() }
                        }
                        RowLayout {
                            Label { text: "성인 / 아동"; Layout.preferredWidth: 72 }
                            ComboBox { id: ageFilter; objectName: "ageFilter"; Layout.fillWidth: true; model: ["전체"].concat(appState.options.age); currentIndex: Math.max(0, model.indexOf(appState.classFilters.age)); onActivated: window.applyClassFilters() }
                        }
                        RowLayout {
                            Label { text: "선택한 조건을 모두 만족하는 옷"; font.pixelSize: 11; color: "#73818c"; Layout.fillWidth: true }
                            ToolButton { objectName: "resetClassFilters"; text: "초기화"; onClicked: appBackend.setClassificationFilters("", "", "") }
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
            Keys.onLeftPressed: appBackend.pageBy(-1)
            Keys.onRightPressed: appBackend.pageBy(1)
            Keys.onUpPressed: appBackend.pageBy(-1)
            Keys.onDownPressed: appBackend.pageBy(1)
            Keys.onPressed: function(event) {
                if (event.key === Qt.Key_PageUp) { appBackend.pageBy(-1); event.accepted = true }
                if (event.key === Qt.Key_PageDown) { appBackend.pageBy(1); event.accepted = true }
            }
            GridLayout {
                id: garmentGrid
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                height: shownRows > 0 ? shownRows * ((board.height - (displayRows-1)*10)/displayRows + 10) - 10 : 0
                columns: appState.mode === "review" ? 2 : (appState.frontOnly ? 3 : 1)
                columnSpacing: 10
                rowSpacing: 10
                property int displayRows: appState.mode === "review" ? appState.cards/2 : appState.rows
                property int shownRows: Math.ceil(Math.min(appState.visibleCount, appState.mode === "review" ? appState.cards : appState.rows * columns) / columns)
                Repeater {
                    model: appBackend.pageModel
                    delegate: Item {
                        required property var record
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.preferredWidth: 1
                        Layout.preferredHeight: (board.height - (garmentGrid.displayRows-1)*10)/garmentGrid.displayRows
                        GarmentCard {
                            anchors.fill: parent
                            record: parent.record
                            controller: appBackend
                            review: appState.mode === "review"
                            frontOnly: appState.frontOnly
                            photoSize: appState.photoSize
                            options: appState.options
                            enabled: !appState.exporting
                            onZoomRequested: function(code) { window.showZoom(code) }
                            onHeadRequested: function(code) { window.showHead(code) }
                        }
                    }
                }
            }
            Label { anchors.centerIn: parent; visible: appState.visibleCount === 0; text: "이 조건에 해당하는 옷이 없습니다."; color: "#64748b" }
        }
        RowLayout {
            visible: window.working
            Layout.fillWidth: true
            spacing: 8
            ToolButton { text: "←"; implicitHeight: 29; enabled: appState.page > 1; onClicked: appBackend.pageBy(-1) }
            Label { text: appState.page + " / " + appState.pages + " 페이지"; color: "#73818c"; font.pixelSize: 11 }
            ToolButton { text: "→"; implicitHeight: 29; enabled: appState.page < appState.pages; onClicked: appBackend.pageBy(1) }
            BusyIndicator { running: appState.busy; visible: running; implicitWidth: 22; implicitHeight: 22 }
            Label { Layout.fillWidth: true; text: appState.message; elide: Text.ElideRight; color: "#73818c"; font.pixelSize: 11 }
            Switch { objectName: "photoOnlySwitch"; text: "사진만 보기"; checked: appState.mode === "photos"; font.pixelSize: 11; implicitHeight: 29; enabled: !appState.exporting; onClicked: { board.forceActiveFocus(); appBackend.setMode(checked ? "photos" : "review") } }
        }
    }
    Shortcut { sequence: "Ctrl+Z"; enabled: window.working && !zoomDialog.opened && !appState.busy; onActivated: appBackend.undo() }
    Shortcut { sequence: "Ctrl+F"; enabled: window.working; onActivated: searchField.forceActiveFocus() }
    Dialog {
        id: createDialog
        objectName: "createDialog"
        title: "새 작업 만들기"
        modal: true
        anchors.centerIn: parent
        width: 580
        standardButtons: Dialog.Cancel
        WorkspaceForm { width: parent.width; controller: appBackend; busy: appState.busy; onSubmitted: createDialog.close() }
    }

    Dialog {
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
                font.pixelSize: 13
                onClicked: { var key = modelData.id; recentDialog.close(); appBackend.openWorkspace(key) }
            }
            Label { anchors.centerIn: parent; visible: appState.recent.length === 0; text: "저장한 작업공간이 없습니다."; color: "#64748b" }
        }
    }

    Dialog {
        id: sourceDialog
        objectName: "sourceDialog"
        title: "에러번호와 사진 폴더"
        modal: true
        anchors.centerIn: parent
        width: 600
        standardButtons: Dialog.Close
        onOpened: editErrors.text = appState.errors
        ColumnLayout {
            width: parent.width
            Label { text: "범위 " + appState.start + "–" + appState.end + "번에서 제외할 번호" }
            TextField { id: editErrors; Layout.fillWidth: true; placeholderText: "212, 300" }
            Label { text: "다른 작업공간과 이미 보낸 시트에는 영향을 주지 않습니다."; color: "#64748b" }
            Button { text: "에러번호 저장"; onClicked: { appBackend.setErrors(editErrors.text); sourceDialog.close() } }
            Label { Layout.fillWidth: true; text: "사진 위치: " + appState.source; wrapMode: Text.Wrap; topPadding: 18 }
            Button { text: "옮긴 사진 폴더 다시 연결"; enabled: !appState.busy; onClicked: reconnectFolder.open() }
        }
    }
    FolderDialog { id: reconnectFolder; title: "같은 번호 구조의 사진 상위 폴더"; onAccepted: { appBackend.reconnect(selectedFolder.toString()); sourceDialog.close() } }

    Dialog {
        id: aiDialog
        objectName: "aiDialog"
        title: "모델별 확인 필요 기준"
        modal: true
        anchors.centerIn: parent
        width: 430
        standardButtons: Dialog.Close
        ColumnLayout {
            width: parent.width
            Label { Layout.fillWidth: true; text: "후보값을 먼저 입력하고, 기준 미달 항목만 확인이 필요하도록 표시합니다. 점수는 실제 정답 확률이 아닙니다."; wrapMode: Text.Wrap }
            RowLayout { Label { text: "판넬 위치 (%)" } SpinBox { id: panelThreshold; from: 1; to: 100; value: Math.round(appState.aiThresholds.panel * 100) } }
            RowLayout { Label { text: "성별 / 성인·아동 (%)" } SpinBox { id: shapeThreshold; from: 1; to: 100; value: Math.round(appState.aiThresholds.shape * 100) } }
            RowLayout { Label { text: "사이즈 (%)" } SpinBox { id: sizeThreshold; from: 1; to: 100; value: Math.round(appState.aiThresholds.size * 100) } }
            Button { text: "기준 저장"; onClicked: { appBackend.setAiThresholds(panelThreshold.value, shapeThreshold.value, sizeThreshold.value); aiDialog.close() } }
        }
    }

    Dialog {
        id: batchDialog
        objectName: "batchDialog"
        title: "선택한 " + appState.selectedCount + "벌에 일괄 입력"
        modal: true
        anchors.centerIn: parent
        width: 440
        standardButtons: Dialog.Close
        ColumnLayout {
            width: parent.width
            Label { text: "고른 항목만 적용합니다. 빈 항목은 기존 값을 유지합니다."; color: "#64748b" }
            Choice { id: batchCategory; Layout.fillWidth: true; label: "카테고리"; choices: appState.options.category; onCommitted: function(v) { value = v } }
            Choice { id: batchGender; Layout.fillWidth: true; label: "성별"; choices: appState.options.gender; onCommitted: function(v) { value = v } }
            Choice { id: batchAge; Layout.fillWidth: true; label: "성인 / 유아동"; choices: appState.options.age; onCommitted: function(v) { value = v } }
            Choice { id: batchSize; Layout.fillWidth: true; label: "사이즈"; choices: appState.options.size; editable: true; onCommitted: function(v) { value = v } }
            Button { text: "선택한 옷에 적용"; highlighted: true; Layout.alignment: Qt.AlignRight; onClicked: { forceActiveFocus(); appBackend.batchFields(batchCategory.value, batchGender.value, batchAge.value, batchSize.value); batchDialog.close() } }
        }
    }

    Dialog {
        id: viewDialog
        objectName: "viewDialog"
        title: "보기 설정"
        modal: true
        anchors.centerIn: parent
        width: 440
        standardButtons: Dialog.Close
        ColumnLayout {
            width: parent.width
            RowLayout {
                Label { text: "검수 화면" }
                RadioButton { text: "4벌"; checked: appState.cards === 4; onClicked: appBackend.viewOptions(4, appState.rows, appState.frontOnly, appState.photoSize) }
                RadioButton { text: "2벌"; checked: appState.cards === 2; onClicked: appBackend.viewOptions(2, appState.rows, appState.frontOnly, appState.photoSize) }
            }
            CheckBox { text: "사진 전용 보기: 정면만 (해제하면 한 벌당 3장)"; checked: appState.frontOnly; onClicked: appBackend.viewOptions(appState.cards, appState.rows, checked, appState.photoSize) }
            RowLayout {
                Label { text: "사진 줄 수" }
                SpinBox { from: 1; to: 8; value: appState.rows; onValueModified: appBackend.viewOptions(appState.cards, value, appState.frontOnly, appState.photoSize) }
            }
            Label { text: "사진 크기 " + appState.photoSize + "%" }
            Slider { Layout.fillWidth: true; from: 100; to: 250; stepSize: 10; value: appState.photoSize; onMoved: appBackend.viewOptions(appState.cards, appState.rows, appState.frontOnly, value) }
            Label { Layout.fillWidth: true; text: "사진을 클릭해 선택하고, Ctrl/Shift로 여러 벌을 고릅니다. 더블클릭은 확대, 좌우 드래그는 페이지 이동입니다."; wrapMode: Text.Wrap; color: "#64748b" }
        }
    }

    Dialog {
        id: headDialog
        objectName: "headDialog"
        title: "머리 확대 · " + (appState.current.code || "")
        modal: true
        anchors.centerIn: parent
        width: Math.min(800, window.width-80)
        height: 540
        standardButtons: Dialog.Close
        ColumnLayout {
            anchors.fill: parent
            Label { Layout.fillWidth: true; text: appState.current.note || ""; wrapMode: Text.Wrap }
            Image { source: appState.current.head || ""; asynchronous: false; fillMode: Image.PreserveAspectFit; sourceSize.width: 1200; Layout.fillWidth: true; Layout.fillHeight: true }
            RowLayout {
                Button { text: "← 이전 옷"; onClicked: appBackend.nextGarment(-1) }
                Button { text: "다음 옷 →"; onClicked: appBackend.nextGarment(1) }
                Item { Layout.fillWidth: true }
                Button { text: "머리 영역 지정"; onClicked: { headDialog.close(); zoomDialog.cropMode = true; zoomDialog.open() } }
                Button { text: "자동 위치 사용"; onClicked: appBackend.automaticPanel() }
                Button { text: "다시 분석"; enabled: !appState.busy; onClicked: appBackend.startAnalysis(false) }
            }
        }
    }

    Dialog {
        id: zoomDialog
        objectName: "zoomDialog"
        title: "사진 상세 · " + (appState.current.code || "")
        modal: true
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
        onCurrentCodeChanged: { factor = 1; panX = 0; panY = 0; chooseFront() }
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
                Button { text: "← 이전 옷"; onClicked: appBackend.nextGarment(-1) }
                Button { text: "다음 옷 →"; onClicked: appBackend.nextGarment(1) }
                ComboBox { id: angleBox; model: zoomDialog.anglePhotos; textRole: "angle"; implicitWidth: 80 }
                ToolButton { text: "머리 확대"; onClicked: { zoomDialog.close(); headDialog.open() } }
                Button { text: "정면으로"; enabled: !appState.recognizing; onClicked: appBackend.setFront(appState.current.code, angleBox.currentText) }
                CheckBox { text: "머리 영역 지정"; checked: zoomDialog.cropMode; enabled: !appState.recognizing; onClicked: { zoomDialog.cropMode = checked; zoomDialog.factor = 1; zoomDialog.panX = 0; zoomDialog.panY = 0 } }
                Button { text: "선택한 옷에 적용"; enabled: appState.selectedCount > 0 && !appState.recognizing; onClicked: appBackend.applyRoiToSelected() }
                Item { Layout.fillWidth: true }
                Button { text: "전체 맞춤"; onClicked: { zoomDialog.factor = 1; zoomDialog.panX = 0; zoomDialog.panY = 0 } }
            }
            Label { text: zoomDialog.cropMode ? "사진의 머리 전체와 글자가 들어가도록 드래그하세요." : "휠: 확대·축소  /  확대 시 드래그: 사진 이동  /  ← →: 이전·다음 옷"; color: "#64748b" }
            Rectangle {
                id: zoomArea
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: "#e6eaee"
                clip: true
                focus: true
                Keys.onLeftPressed: appBackend.nextGarment(-1)
                Keys.onRightPressed: appBackend.nextGarment(1)
                Image {
                    id: detailImage
                    source: zoomDialog.anglePhotos[angleBox.currentIndex] ? zoomDialog.anglePhotos[angleBox.currentIndex].url : ""
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
                    border.color: "#0f766e"
                    border.width: 2
                }
                Rectangle {
                    visible: zoomMouse.pressed && zoomDialog.cropMode
                    x: Math.min(zoomMouse.startX, zoomMouse.mouseX)
                    y: Math.min(zoomMouse.startY, zoomMouse.mouseY)
                    width: Math.abs(zoomMouse.mouseX-zoomMouse.startX)
                    height: Math.abs(zoomMouse.mouseY-zoomMouse.startY)
                    color: "#332563eb"
                    border.color: "#2563eb"
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

    Dialog {
        id: transferDialog
        objectName: "transferDialog"
        title: "엑셀 파일에 새 시트로 저장"
        palette.window: "#ffffff"
        palette.windowText: "#203a42"
        palette.text: "#203a42"
        modal: true
        anchors.centerIn: parent
        width: Math.min(900, window.width-40)
        height: Math.min(720, window.height-70)
        standardButtons: Dialog.Close
        onOpened: { excelPath.text = appState.excelPath; newTab.text = appState.date.replace(/-/g, ""); appBackend.invalidatePlan() }
        ColumnLayout {
            anchors.fill: parent
            spacing: 10
            Label { Layout.fillWidth: true; text: "엑셀 파일 하나에 작업 날짜별 시트를 추가합니다. 기존 시트는 유지합니다."; color: "#64748b"; wrapMode: Text.Wrap }
            RowLayout {
                Layout.fillWidth: true
                Button { text: "새 엑셀 파일"; enabled: !appState.busy; onClicked: newExcelFile.open() }
                Button { text: "기존 엑셀 파일 선택"; enabled: !appState.busy; onClicked: existingExcelFile.open() }
            }
            TextField {
                id: excelPath
                objectName: "excelPathField"
                enabled: !appState.busy
                Layout.fillWidth: true
                placeholderText: "저장할 .xlsx 파일을 선택하거나 전체 경로를 입력하세요."
                selectByMouse: true
                onTextChanged: appBackend.excelTargetChanged(text)
            }
            RowLayout {
                Label { text: "내장 표준 양식"; color: "#73818c" }
                Item { Layout.fillWidth: true }
                Label { text: "새 시트 이름" }
                TextField { id: newTab; objectName: "newExcelSheetName"; enabled: !appState.busy; Layout.preferredWidth: 160; onTextChanged: appBackend.invalidatePlan() }
                Button { text: "미리보기"; objectName: "excelPreviewButton"; enabled: !appState.busy && excelPath.text.trim().length > 0 && newTab.text.trim().length > 0; onClicked: appBackend.previewExcel(excelPath.text, newTab.text) }
            }
            Label { Layout.fillWidth: true; text: "대상 " + appState.counts.total + "벌  ·  저장 가능 " + appState.counts.confirmed + "벌  ·  제외 " + appState.counts.excluded + "벌  ·  누락 " + appState.counts.missing + "벌"; font.bold: true; wrapMode: Text.Wrap }
            Label { Layout.fillWidth: true; text: appState.planReady ? (appState.planAddsToFile ? "기존 파일에 시트 추가: " : "새 파일에 시트 생성: ") + appState.planTitle + "  /  아래 내용을 확인한 뒤 저장하세요." : "모든 대상의 네 항목 입력과 필요한 확인을 마치면 미리보기를 만들 수 있습니다."; color: "#0f766e"; wrapMode: Text.Wrap }
            Label { visible: appState.planReady; Layout.fillWidth: true; text: appState.planPath; color: "#64748b"; elide: Text.ElideMiddle }
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 90
                color: "#f8fafc"
                border.color: "#dce3eb"
                radius: 6
                ListView {
                    anchors.fill: parent
                    anchors.margins: 8
                    clip: true
                    model: appState.planRows
                    header: Label { text: "번호     외투코드          카테고리        성별        연령        사이즈"; height: 32; font.bold: true; color: "#203a42" }
                    delegate: Label { width: ListView.view.width; text: modelData.join("          "); height: 30; font.family: "Malgun Gothic"; color: "#203a42" }
                    ScrollBar.vertical: ScrollBar {}
                }
            }
            Label { Layout.fillWidth: true; text: "같은 이름이 있으면 (2), (3)을 붙입니다. 사진 식별자가 저장되며 실제 사진은 파일에 포함하지 않습니다."; color: "#64748b"; wrapMode: Text.Wrap }
            Label { visible: appState.error.length > 0; Layout.fillWidth: true; text: appState.error; color: "#a03227"; wrapMode: Text.Wrap }
            RowLayout {
                Button { visible: appState.lastFileUrl.length > 0; text: "저장한 파일 열기"; onClicked: Qt.openUrlExternally(appState.lastFileUrl) }
                Item { Layout.fillWidth: true }
                BusyIndicator { implicitWidth: 30; implicitHeight: 30; running: appState.busy; visible: running }
                Button { objectName: "excelSaveButton"; text: "확인한 내용으로 새 시트에 저장"; highlighted: true; enabled: appState.planReady && !appState.busy; onClicked: appBackend.saveExcel() }
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
