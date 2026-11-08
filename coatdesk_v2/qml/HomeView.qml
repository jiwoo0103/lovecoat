import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: home
    property var controller
    property var appState
    signal createRequested()
    signal manageRequested(var record, string action)
    spacing: 18
    RowLayout {
        Layout.fillWidth: true
        Label { text: "내 작업공간"; font.pixelSize: 24; font.weight: Font.DemiBold }
        Item { Layout.fillWidth: true }
        Label { text: "최근 작업 순"; color: Theme.secondary; font.pixelSize: 12 }
    }
    GridView {
        id: workspaces
        objectName: "homeWorkspaceGrid"
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        readonly property int columns: Math.max(1, Math.floor(width / 300))
        cellWidth: width / columns
        cellHeight: 190
        model: [{id: "", name: "새 작업 만들기"}].concat(home.appState.recent || [])
        ScrollBar.vertical: ScrollBar {}
        delegate: ActionButton {
            id: tile
            required property var modelData
            width: workspaces.cellWidth - 16
            height: workspaces.cellHeight - 16
            objectName: modelData.id ? "homeWorkspace_" + modelData.id : "newWorkspaceTile"
            enabled: !home.appState.navigationBlocked
            padding: 0
            leftPadding: 0
            rightPadding: 0
            Accessible.name: modelData.name + (modelData.id ? " 작업 열기" : "")
            background: Rectangle {
                radius: 10
                color: !tile.modelData.id ? Theme.newWorkspace : (tile.down ? Theme.pressed : (tile.hovered ? Theme.control : Theme.surface))
                border.color: !tile.modelData.id || tile.visualFocus ? Theme.primary : Theme.border
                border.width: tile.visualFocus ? 2 : 1
                Behavior on color { enabled: !Theme.transitioning; ColorAnimation { duration: Theme.duration } }
            }
            contentItem: Item {
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 1
                    visible: !!tile.modelData.id
                    spacing: 0
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 50
                        color: Theme.workspaceHeading
                        topLeftRadius: 9
                        topRightRadius: 9
                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 17
                            anchors.rightMargin: 9
                            Label {
                                text: tile.modelData.name
                                color: Theme.text
                                font.pixelSize: 20
                                font.weight: Font.DemiBold
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                            }
                            ActionButton {
                                objectName: "workspaceManage_" + tile.modelData.id
                                iconName: "more"; text: ""; flat: true
                                implicitWidth: 32; implicitHeight: 32
                                enabled: !home.appState.busy
                                Accessible.name: "작업 관리"
                                ToolTip.visible: hovered; ToolTip.text: "작업 관리"
                                onClicked: manageMenu.open()
                                AppMenu {
                                    id: manageMenu
                                    AppMenuItem { objectName: "renameWorkspace_" + tile.modelData.id; text: "이름 변경"; onTriggered: home.manageRequested(tile.modelData, "rename") }
                                    AppMenuSeparator {}
                                    AppMenuItem { objectName: "deleteWorkspace_" + tile.modelData.id; text: "작업 삭제…"; onTriggered: home.manageRequested(tile.modelData, "delete") }
                                }
                            }
                        }
                    }
                    Item {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        GridLayout {
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.leftMargin: 17
                            anchors.rightMargin: 17
                            anchors.verticalCenter: parent.verticalCenter
                            columns: 2
                            columnSpacing: 18
                            rowSpacing: 8
                            Label { text: "촬영일"; color: Theme.secondary }
                            Label { text: tile.modelData.shooting_date || ""; color: Theme.text; font.pixelSize: 15; Layout.fillWidth: true; elide: Text.ElideRight }
                            Label { text: "사진 범위"; color: Theme.secondary }
                            Label { text: tile.modelData.id ? tile.modelData.start + "–" + tile.modelData.end + "번" : ""; color: Theme.text; font.pixelSize: 15; Layout.fillWidth: true; elide: Text.ElideRight }
                            Label { text: "옷 수"; color: Theme.secondary }
                            Label { text: (tile.modelData.count || 0) + "벌"; color: Theme.text; font.pixelSize: 15 }
                        }
                    }
                }
                ColumnLayout {
                    anchors.centerIn: parent
                    visible: !tile.modelData.id
                    spacing: 12
                    AppIcon { name: "plus"; ink: Theme.newWorkspaceText; Layout.alignment: Qt.AlignHCenter; Layout.preferredWidth: 40; Layout.preferredHeight: 40 }
                    Label { text: "새 작업 만들기"; color: Theme.newWorkspaceText; font.pixelSize: 18; font.weight: Font.DemiBold; Layout.alignment: Qt.AlignHCenter }
                    Label { text: "사진 폴더로 작업 시작"; color: Theme.newWorkspaceCaption; Layout.alignment: Qt.AlignHCenter }
                }
            }
            onClicked: {
                var key = modelData.id
                home.forceActiveFocus()
                if (key) home.controller.requestWorkspace(key)
                else home.createRequested()
            }
        }
    }
}
