import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

ColumnLayout {
    id: form
    property var controller
    property bool busy: false
    signal submitted()
    readonly property bool canSubmit: !busy && workName.text.trim().length > 0 && sourcePath.text.trim().length > 0
    function submit() {
        form.forceActiveFocus()
        form.controller.createWorkspace(workName.text, workDate.text, sourcePath.text, rangeStart.text, rangeEnd.text, workErrors.text)
        if (!form.controller.state.error) form.submitted()
    }
    spacing: 12

    RowLayout {
        Layout.fillWidth: true
        spacing: 12
    ColumnLayout {
        Layout.fillWidth: true
        spacing: 6
    Label { text: "작업 이름"; color: Theme.text }
    AppField {
        id: workName
        implicitHeight: 40
        objectName: "workspaceName"
        Layout.fillWidth: true
        placeholderText: "예: 20261026 또는 10월 외투 작업"
        onEditingFinished: {
            if (/^\d{8}$/.test(text) && !workDate.text)
                workDate.text = text.slice(0,4)+"-"+text.slice(4,6)+"-"+text.slice(6,8)
        }
    }
    }
    ColumnLayout {
        Layout.preferredWidth: 210
        spacing: 6
    Label { text: "촬영일"; color: Theme.text }
    AppField { id: workDate; implicitHeight: 40; Layout.fillWidth: true; placeholderText: "YYYY-MM-DD · 비우면 자동" }
    }
    }
    Label { text: "사진 폴더"; color: Theme.text }
    RowLayout {
        Layout.fillWidth: true
        spacing: 12
        AppField { id: sourcePath; implicitHeight: 40; objectName: "workspaceSource"; Layout.fillWidth: true; placeholderText: "0_0001 같은 폴더가 들어 있는 위치" }
        ActionButton { text: "찾기"; implicitHeight: 40; enabled: !form.busy; onClicked: sourceFolder.open() }
    }
    RowLayout {
        Layout.fillWidth: true
        spacing: 12
        ColumnLayout {
            Layout.fillWidth: true
            Label { text: "시작 번호"; color: Theme.text }
            AppField { id: rangeStart; implicitHeight: 40; text: "1"; Layout.fillWidth: true; validator: IntValidator { bottom: 0; top: 99999999 } }
        }
        Label { text: "—"; Layout.topMargin: 22; color: Theme.secondary }
        ColumnLayout {
            Layout.fillWidth: true
            Label { text: "끝 번호"; color: Theme.text }
            AppField { id: rangeEnd; implicitHeight: 40; objectName: "workspaceEnd"; text: "400"; Layout.fillWidth: true; validator: IntValidator { bottom: 0; top: 99999999 } }
        }
    }
    Label { text: "에러번호 · 선택"; color: Theme.text }
    AppField { id: workErrors; implicitHeight: 40; Layout.fillWidth: true; placeholderText: "예: 212, 300" }
    Label {
        Layout.fillWidth: true
        text: "원본 사진은 그대로 두고 검수 내용을 자동 저장합니다."
        wrapMode: Text.Wrap
        color: Theme.secondary
        font.pixelSize: 12
    }
    Label { Layout.fillWidth: true; visible: text.length > 0; text: form.controller ? form.controller.state.error : ""; wrapMode: Text.Wrap; color: Theme.pendingText }
    FolderDialog { id: sourceFolder; title: "원본 사진 상위 폴더"; onAccepted: sourcePath.text = selectedFolder.toString() }
}
