import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

RowLayout {
    id: root
    property string label: ""
    property string value: ""
    property var choices: []
    property bool editable: false
    property bool requiresReview: false
    property string hint: ""
    property var choiceLabels: []
    property var host: Window.window
    function choose(index) {
        if (index < 0 || index >= combo.model.length) return
        root.committed(combo.model[index])
        optionsPopup.close()
    }
    signal committed(string value)
    spacing: 8
    Layout.alignment: Qt.AlignTop
    Label { text: root.label; color: Theme.text; font.pixelSize: 12; Layout.preferredWidth: 62 }
    ComboBox {
        id: combo
        objectName: root.objectName + "_combo"
        Layout.fillWidth: true
        implicitHeight: 32
        hoverEnabled: true
        model: [""].concat(root.choices)
        currentIndex: Math.max(0, model.indexOf(root.value))
        displayText: root.value && model.indexOf(root.value) < 0 ? root.value + " · 다시 선택" :
                     (currentIndex > 0 && root.choiceLabels.length ? root.choiceLabels[currentIndex-1] : (currentText || "선택"))
        font.pixelSize: 14
        font.weight: Font.DemiBold
        Accessible.name: root.label + " " + combo.displayText + (root.requiresReview ? " · 확인 필요" : "")
        indicator: AppIcon {
            name: "down"
            ink: Theme.secondary
            x: combo.width-width-10
            y: (combo.height-height)/2
            width: 12
            height: 12
        }
        contentItem: RowLayout {
            spacing: 6
            Text { text: combo.displayText; color: root.requiresReview ? Theme.pendingText : Theme.text; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight; font: combo.font; Layout.fillWidth: true }
            AppIcon { visible: root.requiresReview; name: "warning"; ink: Theme.pendingText; Layout.preferredWidth: 14; Layout.preferredHeight: 14 }
        }
        leftPadding: 8
        rightPadding: 26
        background: Item {
            Rectangle {
                anchors.fill: parent
                radius: 6
                color: Theme.control
                opacity: combo.hovered && !root.requiresReview ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: Theme.duration } }
            }
            Rectangle {
                anchors.fill: parent
                radius: 6
                color: root.requiresReview ? Theme.reviewField : "transparent"
                border.color: combo.visualFocus ? Theme.focusRing : Theme.reviewBorder
                border.width: combo.visualFocus ? 2 : (root.requiresReview ? 1 : 0)
            }
        }
        popup: Popup {
            id: optionsPopup
            parent: Overlay.overlay
            x: Math.min(parent.width-width-8, Math.max(8, combo.mapToItem(parent, 0, 0).x))
            y: Math.min(parent.height-height-8, Math.max(8, combo.mapToItem(parent, 0, combo.height).y+4))
            width: Math.max(190, Math.min(300, combo.width))
            height: Math.min(360, list.contentHeight+12)
            padding: 6
            modal: true
            dim: false
            focus: true
            closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
            onOpened: { if (root.host) root.host.popupCount++; list.currentIndex = combo.currentIndex; list.forceActiveFocus() }
            onClosed: if (root.host) root.host.popupCount--
            background: Rectangle { color: Theme.surface; radius: 9; border.color: Theme.border }
            contentItem: ListView {
                id: list
                clip: true
                implicitHeight: contentHeight
                model: combo.delegateModel
                currentIndex: combo.highlightedIndex
                focus: true
                Keys.onPressed: function(event) {
                    if (event.key === Qt.Key_Home) { currentIndex = 0; event.accepted = true }
                    if (event.key === Qt.Key_End) { currentIndex = count-1; event.accepted = true }
                }
                Keys.onDownPressed: currentIndex = Math.min(count-1, currentIndex+1)
                Keys.onUpPressed: currentIndex = Math.max(0, currentIndex-1)
                Keys.onReturnPressed: root.choose(currentIndex)
                Keys.onEnterPressed: root.choose(currentIndex)
                ScrollIndicator.vertical: ScrollIndicator {}
            }
        }
        delegate: SelectOption {
            id: optionRow
            width: optionsPopup.width-12
            height: 36
            text: index > 0 && root.choiceLabels.length ? root.choiceLabels[index-1] : (modelData || "선택")
            highlighted: list.currentIndex === index
            currentValue: combo.currentIndex === index
            onClicked: root.choose(index)
        }
        onActivated: root.committed(currentText)
        ToolTip.visible: hovered && root.hint.length > 0
        ToolTip.text: root.hint
    }
}
