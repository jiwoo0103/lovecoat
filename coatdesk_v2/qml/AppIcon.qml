import QtQuick

Canvas {
    id: icon
    property string name: ""
    property color ink: Theme.text
    implicitWidth: 16
    implicitHeight: 16
    onNameChanged: requestPaint()
    onInkChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onPaint: {
        var c = getContext("2d")
        c.reset(); c.clearRect(0, 0, width, height)
        c.save(); c.scale(width/24, height/24)
        c.strokeStyle = ink; c.fillStyle = ink; c.lineWidth = 1.7
        c.lineCap = "round"; c.lineJoin = "round"
        function line(points) { c.beginPath(); c.moveTo(points[0], points[1]); for (var i=2;i<points.length;i+=2) c.lineTo(points[i],points[i+1]); c.stroke() }
        if (name === "analysis") {
            c.fillRect(4,14,3.5,6); c.fillRect(10.25,9,3.5,11); c.fillRect(16.5,4,3.5,16)
        } else if (name === "warning") {
            line([12,3,22,21,2,21,12,3]); line([12,9,12,14]); c.beginPath(); c.arc(12,17.5,.8,0,Math.PI*2); c.fill()
        } else if (name === "excel") {
            line([5,2,15,2,20,7,20,22,5,22,5,2]); line([15,2,15,7,20,7]); c.strokeRect(8,11,9,7); line([8,14.5,17,14.5]); line([12.5,11,12.5,18])
        } else if (name === "grid") {
            c.strokeRect(3,3,7,7); c.strokeRect(14,3,7,7); c.strokeRect(3,14,7,7); c.strokeRect(14,14,7,7)
        } else if (name === "filter") {
            line([3,4,21,4,14,12,14,20,10,18,10,12,3,4])
        } else if (name === "search") {
            c.beginPath(); c.arc(10,10,6,0,Math.PI*2); c.stroke(); line([14.5,14.5,21,21])
        } else if (name === "check") line([4,12,9,17,20,6])
        else if (name === "home") { line([2,11,12,3,22,11]); line([5,9,5,21,10,21,10,14,14,14,14,21,19,21,19,9]) }
        else if (name === "more") { for (var x=5;x<=19;x+=7) { c.beginPath(); c.arc(x,12,1.6,0,Math.PI*2); c.fill() } }
        else if (name === "close") { line([6,6,18,18]); line([18,6,6,18]) }
        else if (name === "plus") { line([12,4,12,20]); line([4,12,20,12]) }
        else if (name === "down") line([5,9,12,16,19,9])
        else if (name === "right") line([9,5,16,12,9,19])
        c.restore()
    }
}
