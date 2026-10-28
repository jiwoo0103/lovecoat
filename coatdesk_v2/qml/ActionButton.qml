import QtQuick
import QtQuick.Controls

Button {
    id: control
    font.pixelSize: 13
    leftPadding: 12
    rightPadding: 12
    implicitHeight: 38
    palette.buttonText: highlighted || checked ? "#ffffff" : "#334155"
    contentItem: Text {
        text: control.text
        font: control.font
        color: !control.enabled ? "#64748b" : (control.highlighted || control.checked ? "#ffffff" : "#334155")
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    background: Rectangle {
        radius: 7
        color: !control.enabled ? "#e9edf4" : (control.highlighted || control.checked ? (control.down ? "#1d4ed8" : "#2563eb") : (control.down ? "#e3ecfc" : (control.hovered ? "#f0f5ff" : "#ffffff")))
        border.color: control.highlighted || control.checked ? "transparent" : "#d8e1ee"
    }
}
