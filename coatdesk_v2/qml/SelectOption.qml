import QtQuick
import QtQuick.Controls

ItemDelegate {
    id: row
    required property int index
    required property var modelData
    property bool currentValue: false
    text: modelData || "선택"
    height: 36
    font.pixelSize: 14
    contentItem: Text {
        text: row.text
        font: row.font
        color: row.currentValue ? Theme.primaryText : Theme.text
        verticalAlignment: Text.AlignVCenter
        leftPadding: 10
        elide: Text.ElideRight
    }
    background: Rectangle {
        radius: 5
        color: row.currentValue ? Theme.primary : (row.hovered || row.highlighted ? Theme.control : Theme.surface)
        border.color: row.highlighted && !row.currentValue ? Theme.focusRing : "transparent"
    }
}
