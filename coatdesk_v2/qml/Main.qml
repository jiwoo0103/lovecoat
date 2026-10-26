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
    title: "외투 작업실 2 · " + appState.name
    color: "#f2f5f8"
    font.family: "Malgun Gothic"
    font.pixelSize: 13
    property var appState: appBackend.state
    palette.highlight: "#0f766e"
    palette.button: "#f8fafc"
    palette.buttonText: "#1e293b"
    palette.text: "#1e293b"
    onClosing: contentItem.forceActiveFocus()

    function showZoom(code) { appBackend.focusGarment(code); zoomDialog.open() }
    function showHead(code) { appBackend.focusGarment(code); headDialog.open() }

    header: Rectangle {
        height: 66
        color: "#ffffff"
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 24
            anchors.rightMargin: 24
            spacing: 12
            Rectangle { width: 9; height: 30; radius: 4; color: "#0f766e" }
            ColumnLayout {
                spacing: 0
                Label { text: "외투 작업실"; font.pixelSize: 21; font.bold: true; color: "#153344" }
                Label { text: "사진에서 검수까지, 한 작업공간에서"; color: "#81909d"; font.pixelSize: 10 }
            }
            Item { Layout.fillWidth: true }
            Button { text: "+ 새 작업"; enabled: !appState.busy; onClicked: createDialog.open() }
            Button { text: "작업 열기"; enabled: !appState.busy; onClicked: recentDialog.open() }
            Button { text: "Google 시트로 보내기"; highlighted: true; enabled: appState.hasWorkspace && !appState.busy; onClicked: transferDialog.open() }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 18
        spacing: 10
        Rectangle {
            visible: appState.error.length > 0
            Layout.fillWidth: true
            implicitHeight: errorLabel.implicitHeight + 20
            radius: 7
            color: "#fff0ee"
            RowLayout {
                anchors.fill: parent
                anchors.margins: 8
                Label { id: errorLabel; Layout.fillWidth: true; text: appState.error; wrapMode: Text.Wrap; color: "#a03227" }
                ToolButton { text: "닫기"; onClicked: appBackend.dismissError() }
            }
        }
        ColumnLayout {
            visible: appState.hasWorkspace
            Layout.fillWidth: true
            spacing: 8
            RowLayout {
                Layout.fillWidth: true
                Label { text: appState.name; font.pixelSize: 20; font.bold: true; color: "#183449" }
                Label { text: appState.date + "  ·  " + appState.start + "–" + appState.end + "번"; color: "#64748b" }
                Item { Layout.fillWidth: true }
                Label { text: "확인 " + appState.counts.confirmed + " / " + appState.counts.total + "벌"; font.bold: true; color: "#0f766e" }
                Label { text: "제외 " + appState.counts.excluded + " · 누락 " + appState.counts.missing; color: "#64748b" }
                ToolButton { text: "에러번호 / 사진 폴더"; enabled: !appState.exporting; onClicked: sourceDialog.open() }
            }
            RowLayout {
                Layout.fillWidth: true
                Button { text: "검수"; checked: appState.mode === "review"; checkable: true; onClicked: appBackend.setMode("review") }
                Button { text: "사진만 보기"; checked: appState.mode === "photos"; checkable: true; onClicked: appBackend.setMode("photos") }
                ComboBox {
                    id: filterBox
                    model: ["전체", "미확인", "확인 필요", "확인 완료", "제외"]
                    currentIndex: model.indexOf(appState.filter)
                    onActivated: appBackend.setFilter(currentText)
                    implicitWidth: 115
                }
                TextField { id: searchField; placeholderText: "외투코드 검색"; text: appState.search; Layout.preferredWidth: 135; onTextEdited: appBackend.setSearch(text) }
                Button { text: "보기 설정"; onClicked: viewDialog.open() }
                Item { Layout.fillWidth: true }
                Button { text: "분석 / 이어서"; enabled: !appState.busy; onClicked: appBackend.startAnalysis(false) }
                Button { text: appState.recognizing ? "분석 중단" : "다시 분석"; enabled: appState.recognizing || !appState.busy; onClicked: appState.recognizing ? appBackend.cancelAnalysis() : appBackend.startAnalysis(true) }
                Button { text: "AI 보류 기준"; enabled: !appState.busy; onClicked: aiDialog.open() }
            }
            RowLayout {
                Layout.fillWidth: true
                Label { text: appState.selectedCount + "벌 선택"; Layout.preferredWidth: 85; color: "#475569" }
                Button { text: "현재 페이지 선택"; onClicked: appBackend.selectPage() }
                Button { text: "일괄 입력"; enabled: appState.selectedCount > 0 && !appState.exporting; onClicked: batchDialog.open() }
                Button { text: "제안 적용"; enabled: appState.selectedCount > 0 && !appState.exporting; onClicked: appBackend.applySuggestions() }
                Button { text: "선택 확인 완료"; highlighted: true; enabled: appState.selectedCount > 0 && !appState.exporting; onClicked: { board.forceActiveFocus(); appBackend.confirmSelected() } }
                Button { text: "입력 지우기"; enabled: appState.selectedCount > 0 && !appState.exporting; onClicked: appBackend.clearSelected() }
                Item { Layout.fillWidth: true }
                ToolButton { text: "↶ 실행 취소"; enabled: appState.canUndo && !appState.exporting; onClicked: appBackend.undo() }
            }
        }

        Item {
            id: board
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
                anchors.fill: parent
                visible: appState.hasWorkspace
                columns: appState.mode === "review" ? 2 : (appState.frontOnly ? 3 : 1)
                columnSpacing: 10
                rowSpacing: 10
                property int displayRows: appState.mode === "review" ? appState.cards/2 : appState.rows
                Repeater {
                    model: appBackend.pageModel
                    delegate: Item {
                        required property var record
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.preferredWidth: (board.width-20)/3
                        Layout.preferredHeight: (board.height - (parent.displayRows-1)*10)/parent.displayRows
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
            ColumnLayout {
                anchors.centerIn: parent
                visible: !appState.hasWorkspace
                spacing: 18
                Label { text: "오늘 작업할 옷을 불러오세요"; font.pixelSize: 28; font.bold: true; color: "#183449" }
                Label { text: "사진 폴더 · 사용할 번호 범위 · 제외번호로 작업공간을 만듭니다."; color: "#64748b" }
                RowLayout {
                    Button { text: "+ 작업공간 만들기"; highlighted: true; onClicked: createDialog.open() }
                    Button { text: "최근 작업 열기"; onClicked: recentDialog.open() }
                }
            }
            Label { anchors.centerIn: parent; visible: appState.hasWorkspace && appState.visibleCount === 0; text: "이 조건에 해당하는 옷이 없습니다."; color: "#64748b" }
        }
        RowLayout {
            Layout.fillWidth: true
            Button { text: "← 이전"; enabled: appState.page > 1; onClicked: appBackend.pageBy(-1) }
            Label { text: appState.page + " / " + appState.pages + " 페이지"; color: "#475569" }
            Button { text: "다음 →"; enabled: appState.page < appState.pages; onClicked: appBackend.pageBy(1) }
            Item { Layout.fillWidth: true }
            BusyIndicator { running: appState.busy; visible: running; implicitWidth: 25; implicitHeight: 25 }
            Label { Layout.maximumWidth: window.width*0.5; text: appState.message; elide: Text.ElideRight; color: "#64748b" }
            Label { text: appState.saved ? "자동 저장" : "저장 확인 필요"; color: appState.saved ? "#0f766e" : "#a03227" }
        }
    }

    Shortcut { sequence: "Ctrl+Z"; enabled: !zoomDialog.opened; onActivated: appBackend.undo() }
    Shortcut { sequence: "Ctrl+F"; onActivated: searchField.forceActiveFocus() }

    Dialog {
        id: createDialog
        objectName: "createDialog"
        title: "새 작업공간"
        modal: true
        anchors.centerIn: parent
        width: 580
        standardButtons: Dialog.Cancel
        ColumnLayout {
            width: parent.width
            spacing: 12
            Label { text: "작업 이름" }
            TextField {
                id: workName
                Layout.fillWidth: true
                placeholderText: "예: 20261024 또는 10월 외투 작업"
                onEditingFinished: {
                    if (/^\d{8}$/.test(text) && !workDate.text)
                        workDate.text = text.slice(0,4)+"-"+text.slice(4,6)+"-"+text.slice(6,8)
                }
            }
            Label { text: "촬영일 (비워두면 오늘 날짜 또는 작업 이름의 날짜)" }
            TextField { id: workDate; placeholderText: "YYYY-MM-DD"; Layout.fillWidth: true }
            Label { text: "원본 사진 상위 폴더" }
            RowLayout {
                TextField { id: sourcePath; Layout.fillWidth: true; placeholderText: "0_0001 같은 폴더가 들어 있는 위치" }
                Button { text: "찾기"; onClicked: sourceFolder.open() }
            }
            RowLayout {
                Label { text: "시작 번호" }
                TextField { id: rangeStart; text: "1"; Layout.preferredWidth: 110; validator: IntValidator { bottom: 0; top: 99999999 } }
                Label { text: "끝 번호" }
                TextField { id: rangeEnd; text: "400"; Layout.preferredWidth: 110; validator: IntValidator { bottom: 0; top: 99999999 } }
            }
            Label { text: "에러번호 (선택, 쉼표로 구분)" }
            TextField { id: workErrors; Layout.fillWidth: true; placeholderText: "예: 212, 300" }
            Label { Layout.fillWidth: true; text: "원본 사진은 읽기만 합니다. 입력한 범위 안에 실제로 있는 옷을 사용합니다."; wrapMode: Text.Wrap; color: "#64748b" }
            Button {
                text: "사진 검사하고 만들기"
                highlighted: true
                Layout.alignment: Qt.AlignRight
                enabled: !appState.busy && workName.text.trim().length > 0 && sourcePath.text.length > 0
                onClicked: { appBackend.createWorkspace(workName.text, workDate.text, sourcePath.text, rangeStart.text, rangeEnd.text, workErrors.text); createDialog.close() }
            }
        }
    }
    FolderDialog { id: sourceFolder; title: "원본 사진 상위 폴더"; onAccepted: sourcePath.text = selectedFolder.toString() }

    Dialog {
        id: recentDialog
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
                onClicked: { appBackend.openWorkspace(modelData.id); recentDialog.close() }
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
        title: "모델별 확인 필요 기준"
        modal: true
        anchors.centerIn: parent
        width: 430
        standardButtons: Dialog.Close
        ColumnLayout {
            width: parent.width
            Label { Layout.fillWidth: true; text: "기준 미달 항목은 제안에 넣지 않습니다. 점수는 실제 정답 확률이 아닙니다."; wrapMode: Text.Wrap }
            RowLayout { Label { text: "판넬 위치 (%)" }; SpinBox { id: panelThreshold; from: 1; to: 100; value: Math.round(appState.aiThresholds.panel * 100) } }
            RowLayout { Label { text: "성별 / 성인·아동 (%)" }; SpinBox { id: shapeThreshold; from: 1; to: 100; value: Math.round(appState.aiThresholds.shape * 100) } }
            RowLayout { Label { text: "사이즈 (%)" }; SpinBox { id: sizeThreshold; from: 1; to: 100; value: Math.round(appState.aiThresholds.size * 100) } }
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
        width: 620
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
                Button { text: "이 각도를 정면으로"; enabled: !appState.recognizing; onClicked: appBackend.setFront(appState.current.code, angleBox.currentText) }
                CheckBox { text: "머리 영역 지정"; checked: zoomDialog.cropMode; enabled: !appState.recognizing; onClicked: { zoomDialog.cropMode = checked; zoomDialog.factor = 1; zoomDialog.panX = 0; zoomDialog.panY = 0 } }
                Button { text: "영역을 선택한 옷에도 적용"; enabled: appState.selectedCount > 0 && !appState.recognizing; onClicked: appBackend.applyRoiToSelected() }
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
        title: "새 Google 시트 탭으로 보내기"
        modal: true
        anchors.centerIn: parent
        width: 900
        height: Math.min(720, window.height-70)
        standardButtons: Dialog.Close
        onOpened: { sheetUrl.text = appState.sheetUrl; newTab.text = appState.date.replace(/-/g, "") }
        ColumnLayout {
            anchors.fill: parent
            spacing: 10
            RowLayout {
                Button { text: "Google 로그인"; enabled: !appState.busy; onClicked: appBackend.login() }
                Label { text: "본인 계정으로 로그인하고 편집 가능한 파일을 지정하세요."; color: "#64748b" }
                Item { Layout.fillWidth: true }
                ToolButton { text: "관리자 연결 설정"; enabled: !appState.busy; onClicked: oauthFile.open() }
            }
            RowLayout {
                TextField { id: sheetUrl; enabled: !appState.busy; Layout.fillWidth: true; placeholderText: "Google 스프레드시트 URL"; onTextChanged: appBackend.sheetTargetChanged(text) }
                Button { text: "탭 불러오기"; enabled: !appState.busy; onClicked: appBackend.loadTabs(sheetUrl.text) }
            }
            RowLayout {
                Label { text: "복제할 양식" }
                ComboBox {
                    id: templateBox
                    enabled: !appState.busy
                    Layout.fillWidth: true
                    model: appState.tabs
                    textRole: "title"
                    valueRole: "sheetId"
                    onCurrentIndexChanged: appBackend.invalidatePlan()
                    onCountChanged: {
                        for (let i=0;i<count;i++) {
                            if (textAt(i) === "양식예시(복사해서 사용)") { currentIndex = i; break }
                        }
                    }
                }
                Label { text: "새 탭 이름" }
                TextField { id: newTab; enabled: !appState.busy; Layout.preferredWidth: 160; onTextChanged: appBackend.invalidatePlan() }
                Button { text: "미리보기"; enabled: !appState.busy && templateBox.count > 0; onClicked: appBackend.preview(sheetUrl.text, templateBox.currentValue, newTab.text) }
            }
            Label { text: "대상 " + appState.counts.total + "벌  ·  확인 완료 " + appState.counts.confirmed + "벌  ·  제외 " + appState.counts.excluded + "벌  ·  누락 " + appState.counts.missing + "벌"; font.bold: true }
            Label { text: appState.planReady ? "생성할 새 탭: " + appState.planTitle + "  /  아래 내용을 확인한 뒤 전송하세요." : "모든 대상의 네 항목 입력과 확인 완료 후 미리보기를 만들 수 있습니다."; color: "#0f766e" }
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: "#f8fafc"
                border.color: "#dce3eb"
                radius: 6
                ListView {
                    anchors.fill: parent
                    anchors.margins: 8
                    clip: true
                    model: appState.planRows
                    header: Label { text: "번호     외투코드          카테고리        성별        연령        사이즈"; height: 32; font.bold: true }
                    delegate: Label { width: ListView.view.width; text: modelData.join("          "); height: 30; font.family: "Malgun Gothic" }
                    ScrollBar.vertical: ScrollBar {}
                }
            }
            Label { Layout.fillWidth: true; text: "전송할 때마다 양식을 복제한 새 탭을 만듭니다. 이미지 칸에는 사진 식별자가 들어갑니다."; color: "#64748b"; wrapMode: Text.Wrap }
            Label { visible: appState.error.length > 0; Layout.fillWidth: true; text: appState.error; color: "#a03227"; wrapMode: Text.Wrap }
            RowLayout {
                Button { visible: appState.unresolved > 0; text: "이전 전송 결과 확인"; enabled: !appState.busy; onClicked: appBackend.checkPreviousExport() }
                Button { visible: appState.lastUrl.length > 0; text: "전송한 탭 열기"; onClicked: Qt.openUrlExternally(appState.lastUrl) }
                Item { Layout.fillWidth: true }
                BusyIndicator { implicitWidth: 30; implicitHeight: 30; running: appState.busy; visible: running }
                Button { text: "확인한 내용으로 새 탭에 전송"; highlighted: true; enabled: appState.planReady && !appState.busy && appState.unresolved === 0; onClicked: appBackend.send() }
            }
        }
    }
    FileDialog { id: oauthFile; title: "관리자용 데스크톱 OAuth 연결 JSON"; nameFilters: ["JSON 파일 (*.json)"]; onAccepted: appBackend.setOAuthClient(selectedFile.toString()) }
}
