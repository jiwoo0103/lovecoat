import QtQuick
import QtQuick.Controls

ComboBox {
    id: combo
    implicitHeight: 36
    leftPadding: 12; rightPadding: 30
    font.pixelSize: 14
    contentItem: Text { text: combo.displayText; font: combo.font; color: combo.enabled ? Theme.text : Theme.disabledText; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
    indicator: AppIcon { name: "down"; ink: Theme.secondary; width: 12; height: 12; x: combo.width-width-10; y: (combo.height-height)/2 }
    background: Rectangle { radius: 6; color: combo.hovered ? Theme.control : Theme.surface; border.color: combo.visualFocus ? Theme.focusRing : Theme.border; border.width: combo.visualFocus ? 2 : 1 }
    popup: Popup {
        y: combo.height+4
        width: Math.max(180, combo.width)
        height: Math.min(340, list.contentHeight+12)
        padding: 6
        background: Rectangle { color: Theme.surface; radius: 12; border.color: Theme.border }
        contentItem: ListView { id: list; clip: true; implicitHeight: contentHeight; model: combo.popup.visible ? combo.delegateModel : null; currentIndex: combo.highlightedIndex; ScrollIndicator.vertical: ScrollIndicator {} }
    }
    delegate: SelectOption { width: combo.popup.width-12; currentValue: index === combo.currentIndex; highlighted: index === combo.highlightedIndex }
}
