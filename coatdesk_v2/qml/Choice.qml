import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: root
    property string label: ""
    property string value: ""
    property var choices: []
    property bool editable: false
    property bool requiresReview: false
    property string hint: ""
    property var choiceLabels: []
    signal committed(string value)
    spacing: 2
    Layout.alignment: Qt.AlignTop
    Label { text: root.label; color: "#64748b"; font.pixelSize: 11 }
    ComboBox {
        id: choiceCombo
        objectName: root.objectName + "_combo"
        visible: !root.editable
        Layout.fillWidth: true
        implicitHeight: 31
        model: [""].concat(root.choices)
        currentIndex: Math.max(0, model.indexOf(root.value))
        displayText: currentIndex > 0 && root.choiceLabels.length ? root.choiceLabels[currentIndex - 1] : (currentText || "선택")
        delegate: ItemDelegate {
            required property int index
            required property var modelData
            width: choiceCombo.width
            text: index > 0 && root.choiceLabels.length ? root.choiceLabels[index - 1] : (modelData || "선택")
            highlighted: choiceCombo.highlightedIndex === index
        }
        background: Rectangle { radius: 4; color: root.requiresReview ? "#fff3dc" : "#f8fafb"; border.color: root.requiresReview ? "#d9b373" : "#dce3eb" }
        font.pixelSize: 13
        onActivated: root.committed(currentText)
    }
    RowLayout {
        visible: root.editable
        spacing: 0
        TextField {
            objectName: root.objectName + "_text"
            Layout.fillWidth: true
            implicitHeight: 31
            text: root.value
            placeholderText: "직접 입력"
            font.pixelSize: 13
            background: Rectangle { radius: 4; color: root.requiresReview ? "#fff3dc" : "#f8fafb"; border.color: root.requiresReview ? "#d9b373" : "#dce3eb" }
            onEditingFinished: if (text !== root.value) root.committed(text)
        }
        ToolButton {
            text: "▼"
            font.pixelSize: 9
            implicitWidth: 28
            implicitHeight: 31
            onClicked: sizes.open()
            Menu {
                id: sizes
                Repeater {
                    model: root.choices
                    MenuItem { text: modelData; onTriggered: root.committed(text) }
                }
            }
        }
    }
    Label {
        visible: root.requiresReview && root.hint.length > 0
        Layout.fillWidth: true
        text: root.hint
        color: "#945818"
        font.pixelSize: 11
        elide: Text.ElideRight
        ToolTip.visible: hintMouse.containsMouse
        ToolTip.text: root.hint
        MouseArea { id: hintMouse; anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.NoButton }
    }
}
