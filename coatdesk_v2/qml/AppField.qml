import QtQuick
import QtQuick.Controls

TextField {
    id: control
    implicitHeight: 36
    leftPadding: 12
    rightPadding: 12
    selectByMouse: true
    color: enabled ? Theme.text : Theme.disabledText
    placeholderTextColor: Theme.secondary
    selectedTextColor: Theme.primaryText
    selectionColor: Theme.primary
    background: Rectangle {
        radius: 6
        color: control.enabled ? Theme.surface : Theme.control
        border.color: control.activeFocus ? Theme.focusRing : Theme.border
        border.width: control.activeFocus ? 2 : 1
        Behavior on color { enabled: !Theme.transitioning; ColorAnimation { duration: Theme.duration } }
    }
}
