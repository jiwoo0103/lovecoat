import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: root
    property string label: ""
    property string value: ""
    property var choices: []
    property bool editable: false
    signal committed(string value)
    spacing: 2
    Label { text: root.label; color: "#64748b"; font.pixelSize: 11 }
    ComboBox {
        visible: !root.editable
        Layout.fillWidth: true
        implicitHeight: 31
        model: [""].concat(root.choices)
        currentIndex: Math.max(0, model.indexOf(root.value))
        displayText: currentText || "선택"
        font.pixelSize: 13
        onActivated: root.committed(currentText)
    }
    RowLayout {
        visible: root.editable
        spacing: 0
        TextField {
            Layout.fillWidth: true
            implicitHeight: 31
            text: root.value
            placeholderText: "직접 입력"
            font.pixelSize: 13
            onEditingFinished: if (text !== root.value) root.committed(text)
        }
        ToolButton {
            text: "▾"
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
}
