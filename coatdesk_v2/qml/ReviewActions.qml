import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

RowLayout {
    id: actions
    property var record
    property var controller
    visible: !!record.needsReview && !record.excluded
    Label {
        text: (actions.record.pendingReview || []).map(function(key) { return ({category: "분류", size: "사이즈", gender: "성별", age: "연령"})[key] || key }).join(" · ") + " 확인 필요"
        color: Theme.pendingText; font.pixelSize: 12; Layout.fillWidth: true; elide: Text.ElideRight
    }
    ActionButton {
        objectName: "confirm_" + actions.record.code
        text: "확인"
        focusPolicy: Qt.NoFocus
        implicitHeight: 32
        implicitWidth: 92
        flat: true
        iconName: "check"
        onClicked: {
            var code = actions.record.code
            var backend = actions.controller
            actions.forceActiveFocus()
            backend.confirmGarment(code)
        }
    }
}
