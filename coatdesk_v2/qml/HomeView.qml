import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: home
    property var controller
    property var appState
    signal createRequested()
    spacing: 22
    ColumnLayout {
        spacing: 6
        Label { text: "오늘의 작업을 시작하세요"; font.pixelSize: 26; font.bold: true; color: "#203541" }
        Label { text: "새 작업을 만들거나, 저장된 작업을 이어서 진행하세요."; color: "#73818c" }
    }
    RowLayout {
        Layout.fillWidth: true
        Label { text: "내 작업공간"; font.pixelSize: 16; font.bold: true }
        Item { Layout.fillWidth: true }
        Label { text: "최근 작업 순"; color: "#73818c"; font.pixelSize: 11 }
    }
    GridView {
        id: workspaces
        objectName: "homeWorkspaceGrid"
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        property int columns: width >= 1160 ? 4 : 3
        cellWidth: width / columns
        cellHeight: Math.min(240, cellWidth * 0.8)
        model: [{id: "", name: "새 작업 만들기"}].concat(home.appState.recent || [])
        ScrollBar.vertical: ScrollBar {}
        delegate: Button {
            required property var modelData
            width: workspaces.cellWidth - 14
            height: workspaces.cellHeight - 14
            objectName: modelData.id ? "homeWorkspace_" + modelData.id : "newWorkspaceTile"
            enabled: !home.appState.busy
            padding: 23
            background: Rectangle {
                radius: 11
                color: modelData.id ? (parent.hovered ? "#f2f8f6" : "#ffffff") : "#e8f3f0"
                border.color: parent.hovered || !modelData.id ? "#74b8ac" : "#e1e7eb"
            }
            contentItem: ColumnLayout {
                spacing: 12
                Label {
                    text: modelData.id ? "작업" : "+"
                    font.pixelSize: modelData.id ? 12 : 42
                    color: "#19786f"
                    Layout.alignment: modelData.id ? Qt.AlignLeft : Qt.AlignHCenter
                }
                Label {
                    text: modelData.name
                    Layout.fillWidth: true
                    horizontalAlignment: modelData.id ? Text.AlignLeft : Text.AlignHCenter
                    font.pixelSize: 16
                    font.bold: true
                    color: modelData.id ? "#203541" : "#19786f"
                    elide: Text.ElideRight
                }
                Label {
                    Layout.fillWidth: true
                    text: modelData.id ? modelData.shooting_date + " · " + modelData.start + "–" + modelData.end + "번" : "사진 폴더로 작업 시작"
                    horizontalAlignment: modelData.id ? Text.AlignLeft : Text.AlignHCenter
                    color: "#73818c"
                    font.pixelSize: 12
                }
                Item { Layout.fillHeight: true }
                Label { visible: !!modelData.id; text: "작업 열기 →"; color: "#73818c"; font.pixelSize: 12 }
            }
            onClicked: {
                var key = modelData.id
                home.forceActiveFocus()
                if (key) home.controller.openWorkspace(key)
                else home.createRequested()
            }
        }
    }
}
