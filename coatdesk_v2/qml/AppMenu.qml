import QtQuick
import QtQuick.Controls

Menu {
    id: menu
    padding: 6
    implicitWidth: Math.max(210, contentItem.implicitWidth + leftPadding + rightPadding)
    background: Rectangle { radius: 12; color: Theme.surface; border.color: Theme.border }
    delegate: AppMenuItem {}
}
