import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: card
    objectName: "card_" + (record.code || "")
    property var record: ({ fields: {}, suggestions: {}, photos: [] })
    property var controller
    property bool review: true
    property bool frontOnly: true
    property int photoSize: 150
    property var options: ({})
    function selectCard(modifiers) {
        var code = record.code
        var backend = controller
        card.forceActiveFocus()
        backend.select(code, modifiers)
    }
    function turnPage(delta) {
        var backend = controller
        card.forceActiveFocus()
        backend.pageBy(delta)
    }
    function pending(key) { return (record.pendingReview || []).indexOf(key) >= 0 }
    function reason(key) {
        if (key === "gender" && record.fields.age === "유아동") return "아동 성별 확인 필수"
        if ((record.held || []).indexOf("판넬 위치") >= 0) return "판넬 위치 확인 필요"
        var score = (record.aiScores || {})[key === "gender" || key === "age" ? "shape" : key]
        return score === undefined ? "직접 확인 필요" : (score * 100).toFixed(0) + "% · 확인 필요"
    }
    signal zoomRequested(string code)
    signal headRequested(string code)
    radius: 10
    color: record.excluded ? "#e5e7eb" : "#ffffff"
    border.color: record.selected ? "#2563eb" : "#dce3eb"
    border.width: record.selected ? 3 : 1
    Behavior on color { ColorAnimation { duration: 100 } }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 10
        spacing: 6
        RowLayout {
            Layout.fillWidth: true
            Label { text: "# " + (card.record.code || ""); font.bold: true; font.pixelSize: 15; color: "#172b42" }
            Item { Layout.fillWidth: true }
            Label {
                visible: !!card.record.needsReview || !!card.record.excluded
                text: card.record.excluded ? "제외" : "확인 필요"
                color: card.record.status === "확인 완료" ? "#0f766e" : "#64748b"
                font.pixelSize: 12
            }
            ToolButton {
                text: card.record.selected ? "✓" : "+"
                implicitHeight: 25
                implicitWidth: 28
                focusPolicy: Qt.NoFocus
                onClicked: card.selectCard(Qt.ControlModifier)
                ToolTip.visible: hovered
                ToolTip.text: "선택 추가 / 해제"
            }
        }
        RowLayout {
            visible: card.review
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12
            PhotoPane {
                objectName: "photo_" + card.record.code
                Layout.fillHeight: true
                Layout.preferredWidth: card.width * 0.43
                photo: card.record.photo || ""
                onSelected: function(modifiers) { card.selectCard(modifiers) }
                onEnlarged: card.zoomRequested(card.record.code)
                onPaged: function(delta) { card.turnPage(delta) }
            }
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 5
                GridLayout {
                    Layout.fillWidth: true
                    columns: 2
                    uniformCellWidths: true
                    columnSpacing: 8
                    rowSpacing: 4
                    Choice {
                        Layout.fillWidth: true
                        label: "옷 분류"
                        choices: card.options.category || []
                        value: card.record.fields.category || ""
                        objectName: "category_" + card.record.code
                        requiresReview: card.pending("category")
                        hint: card.reason("category")
                        enabled: !card.record.excluded
                        onCommitted: function(value) { card.controller.setField(card.record.code, "category", value) }
                    }
                    Choice {
                        Layout.fillWidth: true
                        label: "사이즈"
                        editable: true
                        choices: card.options.size || []
                        value: card.record.fields.size || ""
                        objectName: "size_" + card.record.code
                        requiresReview: card.pending("size")
                        hint: card.reason("size")
                        enabled: !card.record.excluded
                        onCommitted: function(value) { card.controller.setField(card.record.code, "size", value) }
                    }
                    Choice {
                        Layout.fillWidth: true
                        label: "성별"
                        choices: card.options.gender || []
                        value: card.record.fields.gender || ""
                        objectName: "gender_" + card.record.code
                        requiresReview: card.pending("gender")
                        hint: card.reason("gender")
                        choiceLabels: card.record.fields.age === "유아동" ? ["남아", "여아"] : []
                        enabled: !card.record.excluded
                        onCommitted: function(value) { card.controller.setField(card.record.code, "gender", value) }
                    }
                    Choice {
                        Layout.fillWidth: true
                        label: "성인 / 아동"
                        choices: card.options.age || []
                        value: card.record.fields.age || ""
                        objectName: "age_" + card.record.code
                        requiresReview: card.pending("age")
                        hint: card.reason("age")
                        enabled: !card.record.excluded
                        onCommitted: function(value) { card.controller.setField(card.record.code, "age", value) }
                    }
                }
                ReviewActions { Layout.fillWidth: true; record: card.record; controller: card.controller }
                Item { Layout.fillHeight: true }

            }
        }
        RowLayout {
            visible: !card.review
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 6
            Repeater {
                model: card.frontOnly ? [{angle: card.record.front, url: card.record.photo}] : card.record.photos.slice(0, 3)
                PhotoPane {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    photo: modelData.url || ""
                    zoom: card.photoSize
                    onSelected: function(modifiers) { card.selectCard(modifiers) }
                    onEnlarged: card.zoomRequested(card.record.code)
                    onPaged: function(delta) { card.turnPage(delta) }
                    Label {
                        anchors.left: parent.left
                        anchors.bottom: parent.bottom
                        anchors.margins: 5
                        text: "각도 " + modelData.angle
                        color: "#475569"
                        font.pixelSize: 11
                    }
                }
            }
        }
    }
}
