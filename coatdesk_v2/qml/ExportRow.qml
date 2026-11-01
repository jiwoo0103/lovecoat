import QtQuick
import QtQuick.Controls

Row {
    id: row
    property var values: []
    property bool heading: false
    height: 36
    Repeater {
        model: row.values
        Label {
            required property int index
            required property var modelData
            width: row.width * [.09, .16, .25, .16, .17, .17][index]
            height: row.height
            leftPadding: 10
            rightPadding: 6
            text: modelData
            font.bold: row.heading
            color: row.heading ? "#183449" : "#334155"
            elide: Text.ElideRight
            verticalAlignment: Text.AlignVCenter
            background: Rectangle { color: row.heading ? "#eaf1ff" : "transparent" }
        }
    }
}
