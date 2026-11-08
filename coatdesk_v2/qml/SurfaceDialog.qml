import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dialog
    parent: Overlay.overlay
    property string acceptLabel: "확인"
    property string acceptObjectName: objectName + "Accept"
    property bool acceptEnabled: true
    property bool destructive: false
    property bool autoAccept: true
    property string acceptTone: "primary"
    signal primaryTriggered()
    padding: 24
    topPadding: 12
    bottomPadding: 16
    spacing: 12
    background: Rectangle { radius: 12; color: Theme.surface; border.color: Theme.border }
    Overlay.modal: Rectangle { color: Theme.modalOverlay }
    header: RowLayout {
        Label { text: dialog.title; Layout.fillWidth: true; Layout.leftMargin: 24; Layout.topMargin: 20; Layout.bottomMargin: 8; font.pixelSize: 20; font.weight: Font.DemiBold; color: Theme.text; wrapMode: Text.Wrap }
        ThemeToggle { objectName: dialog.objectName + "ThemeToggle"; Layout.topMargin: 12 }
        ActionButton { text: ""; iconName: "close"; flat: true; Layout.rightMargin: 16; Layout.topMargin: 12; implicitWidth: 32; implicitHeight: 32; Accessible.name: "닫기"; onClicked: dialog.reject() }
    }
    footer: RowLayout {
        visible: dialog.standardButtons !== Dialog.NoButton
        height: visible ? 58 : 0
        spacing: 12
        Item { Layout.fillWidth: true }
        ActionButton {
            objectName: dialog.objectName + "Cancel"
            text: dialog.standardButtons & Dialog.Cancel ? "취소" : "닫기"
            visible: !!(dialog.standardButtons & (Dialog.Cancel | Dialog.Close))
            Layout.bottomMargin: 16
            implicitWidth: 88
            onClicked: dialog.reject()
        }
        ActionButton {
            objectName: dialog.acceptObjectName
            text: dialog.acceptLabel
            visible: !!(dialog.standardButtons & (Dialog.Ok | Dialog.Yes | Dialog.Save))
            enabled: dialog.acceptEnabled
            highlighted: true
            tone: dialog.acceptTone
            iconName: dialog.acceptTone === "export" ? "excel" : ""
            danger: dialog.destructive
            Layout.bottomMargin: 16
            implicitWidth: Math.max(100, contentItem.implicitWidth + 24)
            onClicked: { dialog.contentItem.forceActiveFocus(); if (dialog.autoAccept) dialog.accept(); else dialog.primaryTriggered() }
        }
        Item { Layout.preferredWidth: 8 }
    }
}
