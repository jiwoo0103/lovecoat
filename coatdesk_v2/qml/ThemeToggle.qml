import QtQuick
import QtQuick.Controls
import QtQuick.Shapes

ToolButton {
    id: toggle
    objectName: "themeToggle"
    implicitWidth: 36
    implicitHeight: 36
    hoverEnabled: true
    readonly property string actionLabel: Theme.dark ? "라이트 모드로 전환" : "다크 모드로 전환"
    Accessible.name: actionLabel
    ToolTip.visible: hovered
    ToolTip.text: actionLabel
    background: Item {}
    onClicked: { ToolTip.hide(); appBackend.setTheme(Theme.dark ? "light" : "dark") }
    contentItem: Item {
        Item {
            id: symbol
            objectName: "themeIcon"
            anchors.centerIn: parent
            width: 26; height: 26
            readonly property real phase: Theme.phase
            property color ink: toggle.hovered ? Theme.accentText : Theme.text
            scale: 1 - 0.18 * Math.sin(Math.PI * phase)
            Shape {
                anchors.fill: parent
                opacity: 1 - symbol.phase
                rotation: 45 * symbol.phase
                ShapePath {
                    fillColor: "transparent"
                    strokeColor: symbol.ink
                    strokeWidth: 1.7
                    capStyle: ShapePath.RoundCap
                    PathSvg { path: "M17.2 13 A4.2 4.2 0 1 0 8.8 13 A4.2 4.2 0 1 0 17.2 13 M13 3.5 V6 M13 20 V22.5 M3.5 13 H6 M20 13 H22.5 M6.3 6.3 L8.1 8.1 M17.9 17.9 L19.7 19.7 M6.3 19.7 L8.1 17.9 M17.9 8.1 L19.7 6.3" }
                }
            }
            Shape {
                anchors.fill: parent
                opacity: symbol.phase
                rotation: -45 * (1 - symbol.phase)
                ShapePath {
                    fillColor: "transparent"
                    strokeColor: symbol.ink
                    strokeWidth: 1.7
                    capStyle: ShapePath.RoundCap
                    joinStyle: ShapePath.RoundJoin
                    PathSvg { path: "M14.5 4 C4 5 1 17 10 21 C15 24 22 20 22 15 C15 18 11 12 14.5 4 Z" }
                }
            }
        }
        Rectangle { anchors.fill: parent; anchors.margins: -1; radius: width/2; color: "transparent"; border.width: toggle.visualFocus ? 2 : 0; border.color: Theme.focusRing }
    }
}
