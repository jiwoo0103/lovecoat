pragma Singleton
import QtQuick

QtObject {
    readonly property bool dark: appBackend.state.theme === "dark"
    property real phase: dark ? 1 : 0
    readonly property bool transitioning: themeAnimation.running

    Behavior on phase {
        NumberAnimation {
            id: themeAnimation
            duration: 320
            easing.type: Easing.InOutSine
        }
    }

    function blend(light: color, dark: color): color {
        return Qt.rgba(light.r + (dark.r - light.r) * phase,
                       light.g + (dark.g - light.g) * phase,
                       light.b + (dark.b - light.b) * phase,
                       light.a + (dark.a - light.a) * phase)
    }

    readonly property color newWorkspace: blend("#EAF0F9", "#3D5F96")
    readonly property color newWorkspaceText: blend("#4267A5", "#FFFFFF")
    readonly property color newWorkspaceCaption: blend("#62625D", "#FFFFFF")
    readonly property color workspaceHeading: blend("#EEF2F7", "#2C3542")
    readonly property color tabIndicator: blend("#2868C5", "#77ACF5")
    readonly property color reviewBorder: blend("#BF5754", "#DF827D")
    readonly property color reviewField: blend("#FCEBEA", "#472B2D")
    readonly property color modalOverlay: blend("#33000000", "#66000000")
    property color canvas: blend("#F7F7F5", "#191919")
    property color surface: blend("#FFFFFF", "#222222")
    property color text: blend("#30302E", "#F0EFED")
    property color secondary: blend("#62625D", "#B8B8B2")
    property color border: blend("#DEDEDA", "#41413E")
    property color control: blend("#ECECE8", "#30302E")
    property color hover: blend("#E4E4DF", "#393936")
    property color pressed: blend("#D9D9D3", "#454540")
    property color primary: blend("#4267A5", "#3D5F96")
    property color accentText: blend("#4267A5", "#A9C5F1")
    property color focusRing: blend("#4267A5", "#98BAE8")
    property color primaryHover: blend("#385A92", "#466EAC")
    property color primaryText: "#FFFFFF"
    property color warning: blend("#F5E7B4", "#66572F")
    property color warningText: blend("#584716", "#F4E7BB")
    property color exportColor: blend("#24674F", "#285D4A")
    property color exportHover: blend("#1D5742", "#32745C")
    property color pending: blend("#FFF9EA", "#302C22")
    property color pendingText: blend("#9D4540", "#E2A09D")
    property color danger: blend("#A5413C", "#984D48")
    property color disabledText: blend("#71716A", "#92928B")
    property color photoBackground: blend("#ECECE8", "#30302E")
    property color selection: blend("#EAF0F9", "#293C5A")
    readonly property int duration: 140
}
