import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dialog
    property string acceptLabel: "확인"
    property string acceptObjectName: objectName + "Accept"
    property bool acceptEnabled: true
    property bool destructive: false
    property bool autoAccept: true
    signal primaryTriggered()
    padding: 20
    topPadding: 12
    bottomPadding: 16
    spacing: 12
    background: Rectangle { radius: 12; color: "#ffffff"; border.color: "#d8e1ee" }
    header: Label {
        text: dialog.title
        padding: 20
        bottomPadding: 8
        font.pixelSize: 18
        font.bold: true
        color: "#183449"
        wrapMode: Text.Wrap
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
            danger: dialog.destructive
            Layout.bottomMargin: 16
            implicitWidth: Math.max(100, contentItem.implicitWidth + 24)
            onClicked: { dialog.contentItem.forceActiveFocus(); if (dialog.autoAccept) dialog.accept(); else dialog.primaryTriggered() }
        }
        Item { Layout.preferredWidth: 8 }
    }
}
