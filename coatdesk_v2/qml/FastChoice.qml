import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: root
    property string label: ""
    property string value: ""
    property var choices: []
    property var choiceLabels: []
    property bool requiresReview: false
    property string hint: ""
    property int columns: 2
    signal committed(string value)
    spacing: 4
    Label { text: root.label; color: Theme.secondary; font.pixelSize: 12 }
    GridLayout {
        Layout.fillWidth: true
        columns: root.columns
        uniformCellWidths: true
        columnSpacing: 4
        rowSpacing: 4
        Repeater {
            model: root.choices
            ActionButton {
                required property string modelData
                required property int index
                objectName: root.objectName + "_" + index
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                implicitHeight: 30
                padding: 2
                text: root.choiceLabels.length ? root.choiceLabels[index] : modelData
                font.pixelSize: 12
                focusPolicy: Qt.NoFocus
                contentItem: Text { text: parent.text; font: parent.font; color: root.value === modelData ? (root.requiresReview ? Theme.pendingText : Theme.primaryText) : Theme.text; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                background: Rectangle { radius: 6; color: root.value === modelData ? (root.requiresReview ? Theme.pending : Theme.primary) : (parent.hovered ? Theme.hover : Theme.control); border.color: root.requiresReview && root.value === modelData ? Theme.pendingBorder : Theme.border }
                onClicked: root.committed(modelData)
                ToolTip.visible: hovered && root.hint.length > 0
                ToolTip.text: root.hint
            }
        }
    }
    Label { visible: root.requiresReview; Layout.fillWidth: true; text: root.hint || "확인 필요"; color: Theme.pendingText; font.pixelSize: 10; elide: Text.ElideRight }
}
