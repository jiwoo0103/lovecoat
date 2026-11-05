import QtQuick
import QtQuick.Controls

Rectangle {
    id: pane
    property real renderScale: 1
    property string photo: ""
    property int zoom: 100
    signal activated()
    signal enlarged()
    signal paged(int delta)
    color: Theme.photoBackground
    clip: true
    radius: 6
    Image {
        id: picture
        source: pane.photo
        anchors.centerIn: parent
        width: parent.width * pane.zoom / 100
        height: parent.height * pane.zoom / 100
        sourceSize.width: Math.max(240, Math.min(1800, width * pane.renderScale * Screen.devicePixelRatio))
        sourceSize.height: Math.max(240, Math.min(1800, height * pane.renderScale * Screen.devicePixelRatio))
        fillMode: Image.PreserveAspectFit
        asynchronous: true
        autoTransform: true
        cache: true
        smooth: true
    }
    Label {
        anchors.centerIn: parent
        text: pane.photo ? (picture.status === Image.Loading ? "사진 불러오는 중…" : "") : "사진 없음"
        color: Theme.secondary
        font.pixelSize: 12
    }
    MouseArea {
        anchors.fill: parent
        property real pressX: 0
        property real pressY: 0
        property bool openingDetail: false
        onPressed: function(mouse) {
            openingDetail = false
            pressX = mouse.x
            pressY = mouse.y
        }
        onReleased: function(mouse) {
            // The release following a double-click must not take focus from the detail dialog.
            if (openingDetail) return
            if (Math.abs(mouse.x-pressX) > 65 && Math.abs(mouse.x-pressX) > Math.abs(mouse.y-pressY)*1.5)
                pane.paged(mouse.x < pressX ? 1 : -1)
            else pane.activated()
        }
        onDoubleClicked: {
            openingDetail = true
            pane.enlarged()
        }
        cursorShape: Qt.PointingHandCursor
    }
}
