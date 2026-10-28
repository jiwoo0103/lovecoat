import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

ColumnLayout {
    id: form
    property var controller
    property bool busy: false
    signal submitted()
    spacing: 6

    Label { text: "작업 이름"; color: "#334155" }
    TextField {
        id: workName
        implicitHeight: 34
        objectName: "workspaceName"
        Layout.fillWidth: true
        placeholderText: "예: 20261026 또는 10월 외투 작업"
        onEditingFinished: {
            if (/^\d{8}$/.test(text) && !workDate.text)
                workDate.text = text.slice(0,4)+"-"+text.slice(4,6)+"-"+text.slice(6,8)
        }
    }
    Label { text: "촬영일"; color: "#334155" }
    TextField { id: workDate; implicitHeight: 34; Layout.fillWidth: true; placeholderText: "YYYY-MM-DD · 비워두면 오늘 또는 작업 이름의 날짜" }
    Label { text: "사진 폴더"; color: "#334155" }
    RowLayout {
        Layout.fillWidth: true
        TextField { id: sourcePath; implicitHeight: 34; objectName: "workspaceSource"; Layout.fillWidth: true; placeholderText: "0_0001 같은 폴더가 들어 있는 위치" }
        ActionButton { text: "찾기"; implicitHeight: 34; enabled: !form.busy; onClicked: sourceFolder.open() }
    }
    RowLayout {
        Layout.fillWidth: true
        ColumnLayout {
            Layout.fillWidth: true
            Label { text: "시작 번호"; color: "#334155" }
            TextField { id: rangeStart; implicitHeight: 34; text: "1"; Layout.fillWidth: true; validator: IntValidator { bottom: 0; top: 99999999 } }
        }
        Label { text: "—"; Layout.topMargin: 22; color: "#94a3b8" }
        ColumnLayout {
            Layout.fillWidth: true
            Label { text: "끝 번호"; color: "#334155" }
            TextField { id: rangeEnd; implicitHeight: 34; objectName: "workspaceEnd"; text: "400"; Layout.fillWidth: true; validator: IntValidator { bottom: 0; top: 99999999 } }
        }
    }
    Label { text: "에러번호 · 선택"; color: "#334155" }
    TextField { id: workErrors; implicitHeight: 34; Layout.fillWidth: true; placeholderText: "예: 212, 300" }
    Label {
        Layout.fillWidth: true
        text: "원본 사진은 그대로 두고 검수 내용을 자동 저장합니다."
        wrapMode: Text.Wrap
        color: "#64748b"
        font.pixelSize: 12
    }
    ActionButton {
        objectName: "createWorkspaceButton"
        Layout.fillWidth: true
        Layout.topMargin: 6
        implicitHeight: 42
        text: form.busy ? "진행 중인 작업을 기다리는 중…" : "사진 검사하고 작업 시작"
        highlighted: true
        enabled: !form.busy && workName.text.trim().length > 0 && sourcePath.text.trim().length > 0
        onClicked: {
            form.forceActiveFocus()
            form.controller.createWorkspace(workName.text, workDate.text, sourcePath.text, rangeStart.text, rangeEnd.text, workErrors.text)
            form.submitted()
        }
    }
    FolderDialog { id: sourceFolder; title: "원본 사진 상위 폴더"; onAccepted: sourcePath.text = selectedFolder.toString() }
}
