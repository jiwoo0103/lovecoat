import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

RowLayout {
    id: actions
    property var record
    property var controller
    visible: !!record.needsReview && !record.excluded
    Item { Layout.fillWidth: true }
    ActionButton {
        objectName: "confirm_" + actions.record.code
        text: "확인"
        focusPolicy: Qt.NoFocus
        implicitHeight: 29
        implicitWidth: 70
        palette.button: "#fff3dc"
        palette.buttonText: "#945818"
        onClicked: {
            var code = actions.record.code
            var backend = actions.controller
            actions.forceActiveFocus()
            backend.confirmGarment(code)
        }
    }
}
