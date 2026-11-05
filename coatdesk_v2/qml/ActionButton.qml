import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Button {
    id: control
    property bool danger: false
    property string iconName: ""
    property string tone: "neutral"
    property bool chevron: false
    readonly property bool strong: highlighted || checked || tone === "primary" || tone === "export"
    readonly property color foreground: !enabled ? Theme.disabledText : (tone === "warning" ? Theme.warningText : (strong ? Theme.primaryText : Theme.text))
    font.pixelSize: 14
    leftPadding: 12
    rightPadding: 12
    implicitHeight: 36
    hoverEnabled: true
    palette.buttonText: foreground
    contentItem: RowLayout {
        spacing: 8
        AppIcon { visible: control.iconName.length > 0; name: control.iconName; ink: control.foreground; Layout.preferredWidth: 16; Layout.preferredHeight: 16 }
        Text { text: control.text; font: control.font; color: control.foreground; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight; Layout.fillWidth: true; visible: control.text.length > 0 }
        AppIcon { name: "down"; ink: control.foreground; visible: control.chevron; Layout.preferredWidth: 12; Layout.preferredHeight: 12 }
    }
    background: Rectangle {
        radius: 6
        color: !control.enabled ? Theme.control : (control.danger && control.strong ? Theme.danger : (control.tone === "warning" ? Theme.warning : (control.tone === "export" ? (control.hovered ? Theme.exportHover : Theme.exportColor) : (control.strong ? (control.hovered ? Theme.primaryHover : Theme.primary) : (control.down ? Theme.pressed : (control.hovered ? Theme.hover : (control.flat ? "transparent" : Theme.control)))))))
        border.color: control.visualFocus ? Theme.focusRing : "transparent"
        border.width: control.visualFocus ? 2 : 1
        Behavior on color { enabled: !Theme.transitioning; ColorAnimation { duration: Theme.duration } }
    }
}
