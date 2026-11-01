import QtQuick
import QtQuick.Controls

TextField {
    id: control
    implicitHeight: 40
    leftPadding: 12
    rightPadding: 12
    selectByMouse: true
    color: "#203541"
    placeholderTextColor: "#81909c"
    background: Rectangle {
        radius: 7
        color: control.enabled ? "#ffffff" : "#f1f5f9"
        border.color: control.activeFocus ? "#2563eb" : "#d8e1ee"
        border.width: control.activeFocus ? 2 : 1
    }
}
