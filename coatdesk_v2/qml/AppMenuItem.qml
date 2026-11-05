import QtQuick
import QtQuick.Controls

MenuItem {
    id: entry
    implicitHeight: 36
    implicitWidth: Math.max(210, label.implicitWidth + 48)
    leftPadding: 12; rightPadding: 30
    font.pixelSize: 14
    contentItem: Text {
        id: label
        text: entry.text; font: entry.font
        color: !entry.enabled ? Theme.disabledText : (entry.text.indexOf("삭제") >= 0 ? Theme.pendingText : Theme.text)
        verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
    }
    arrow: AppIcon { name: "right"; ink: Theme.secondary; visible: !!entry.subMenu; x: entry.width-width-10; y: (entry.height-height)/2 }
    background: Rectangle { radius: 6; color: entry.highlighted || entry.hovered ? Theme.control : "transparent"; Behavior on color { enabled: !Theme.transitioning; ColorAnimation { duration: Theme.duration } } }
}
