import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

ColumnLayout {
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
    spacing: 4
    Layout.alignment: Qt.AlignTop
    Label { text: root.label; color: "#64748b"; font.pixelSize: 12 }
    ComboBox {
        id: combo
        objectName: root.objectName + "_combo"
        Layout.fillWidth: true
        implicitHeight: 38
        model: [""].concat(root.choices)
        currentIndex: Math.max(0, model.indexOf(root.value))
        displayText: root.value && model.indexOf(root.value) < 0 ? root.value + " · 다시 선택" :
                     (currentIndex > 0 && root.choiceLabels.length ? root.choiceLabels[currentIndex-1] : (currentText || "선택"))
        font.pixelSize: 14
        indicator: Canvas {
            x: combo.width-width-10
            y: (combo.height-height)/2
            width: 12
            height: 8
            onPaint: { var ctx = getContext("2d"); ctx.strokeStyle = "#64748b"; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.moveTo(1,1); ctx.lineTo(6,6); ctx.lineTo(11,1); ctx.stroke() }
        }
        contentItem: Text { text: combo.displayText; color: "#172b42"; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight; leftPadding: 10; rightPadding: 22; font: combo.font }
        background: Rectangle { radius: 7; color: root.requiresReview ? "#fff5df" : (combo.hovered ? "#f0f5ff" : "#ffffff"); border.color: root.requiresReview ? "#f1b94c" : (combo.activeFocus ? "#2563eb" : "#d8e1ee") }
        popup: Popup {
            id: optionsPopup
            parent: Overlay.overlay
            x: Math.min(parent.width-width-8, Math.max(8, combo.mapToItem(parent, 0, 0).x))
            y: Math.min(parent.height-height-8, Math.max(8, combo.mapToItem(parent, 0, combo.height).y+4))
            width: Math.max(190, Math.min(300, combo.width))
            height: Math.min(360, list.contentHeight+12)
            padding: 6
            modal: true
            focus: true
            closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
            onOpened: { if (root.host) root.host.popupCount++; list.currentIndex = combo.currentIndex; list.forceActiveFocus() }
            onClosed: if (root.host) root.host.popupCount--
            background: Rectangle { color: "white"; radius: 9; border.color: "#d8e1ee" }
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
    Label {
        visible: root.requiresReview && root.hint.length > 0
        Layout.fillWidth: true
        text: root.hint
        color: "#956014"
        font.pixelSize: 11
        elide: Text.ElideRight
        ToolTip.visible: hintMouse.containsMouse
        ToolTip.text: root.hint
        MouseArea { id: hintMouse; anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.NoButton }
    }
}
