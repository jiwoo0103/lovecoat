import QtQuick
import QtQuick.Controls

Rectangle {
    id: pane
    property string photo: ""
    property int zoom: 100
    signal selected(int modifiers)
    signal enlarged()
    signal paged(int delta)
    color: "#eef1f4"
    clip: true
    radius: 6
    Image {
        id: picture
        source: pane.photo
        anchors.centerIn: parent
        width: parent.width * pane.zoom / 100
        height: parent.height * pane.zoom / 100
        sourceSize.width: Math.max(240, Math.min(1800, width * Screen.devicePixelRatio))
        sourceSize.height: Math.max(240, Math.min(1800, height * Screen.devicePixelRatio))
        fillMode: Image.PreserveAspectFit
        asynchronous: true
        autoTransform: true
        cache: true
        smooth: true
    }
    Label {
        anchors.centerIn: parent
        text: pane.photo ? (picture.status === Image.Loading ? "사진 불러오는 중…" : "") : "사진 없음"
        color: "#64748b"
        font.pixelSize: 12
    }
    MouseArea {
        anchors.fill: parent
        property real pressX: 0
        property real pressY: 0
        onPressed: function(mouse) { pressX = mouse.x; pressY = mouse.y }
        onReleased: function(mouse) {
            if (Math.abs(mouse.x-pressX) > 65 && Math.abs(mouse.x-pressX) > Math.abs(mouse.y-pressY)*1.5)
                pane.paged(mouse.x < pressX ? 1 : -1)
            else pane.selected(mouse.modifiers)
        }
        onDoubleClicked: pane.enlarged()
        cursorShape: Qt.PointingHandCursor
    }
}
