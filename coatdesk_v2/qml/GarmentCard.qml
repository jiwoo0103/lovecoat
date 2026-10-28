import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: viewport
    property var record: ({ fields: {}, suggestions: {}, photos: [] })
    property var controller
    property bool review: true
    property bool frontOnly: true
    property int photoSize: 150
    property var options: ({})
    property bool fastInput: false
    signal zoomRequested(string code)
    signal headRequested(string code)
    Rectangle {
    id: card
    property bool fastInput: viewport.fastInput
    property real fitScale: Math.max(.01, Math.min(1, viewport.width/450, viewport.height/(fastInput && review ? 300 : 265)))
    width: viewport.width/fitScale
    height: viewport.height/fitScale
    scale: fitScale
    transformOrigin: Item.TopLeft
    objectName: "card_" + (record.code || "")
    property var record: viewport.record
    property var controller: viewport.controller
    property bool review: viewport.review
    property bool frontOnly: viewport.frontOnly
    property int photoSize: viewport.photoSize
    property var options: viewport.options
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
        if (key === "category") {
            var detail = record.categoryAnalysis || {}
            if (record.fieldSources && record.fieldSources.category === "default") return "이전 기본값 · 분류 분석 또는 직접 확인 필요"
            var scores = detail.sleeve_score === undefined ? "" : "소매 " + Math.round(detail.sleeve_score*100) + "% · 기장 " + Math.round(detail.length_score*100) + "%"
            return [detail.candidate ? "AI 후보 " + detail.candidate : "", (detail.review_reasons || []).join(" · "), scores].filter(function(v) { return !!v }).join(" · ")
        }
        if (key === "size" && record.invalidSize) return "기존 값: " + record.fields.size + " · 허용 목록에서 다시 선택"
        if (key === "gender" && record.fields.age === "유아동") return "아동 성별 확인 필수"
        if ((record.held || []).indexOf("판넬 위치") >= 0) return "판넬 위치 확인 필요"
        var score = (record.aiScores || {})[key === "gender" || key === "age" ? "shape" : key]
        return score === undefined ? "직접 확인 필요" : (score * 100).toFixed(0) + "% · 확인 필요"
    }
    signal zoomRequested(string code)
    signal headRequested(string code)
    onZoomRequested: function(code) { viewport.zoomRequested(code) }
    onHeadRequested: function(code) { viewport.headRequested(code) }
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
                color: card.record.excluded ? "#64748b" : "#a66812"
                padding: 5
                background: Rectangle { radius: 5; color: card.record.excluded ? "#edf0f5" : "#fff1d4" }
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
                renderScale: card.fitScale
                photo: card.record.photo || ""
                onSelected: function(modifiers) { card.selectCard(modifiers) }
                onEnlarged: card.zoomRequested(card.record.code)
                onPaged: function(delta) { card.turnPage(delta) }
            }
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 5
                Loader {
                    Layout.fillWidth: true
                    Layout.preferredHeight: item ? item.implicitHeight : 0
                    sourceComponent: card.fastInput ? fastFields : normalFields
                }
                Component {
                    id: normalFields
                    GridLayout {
                        columns: 2
                        uniformCellWidths: true
                        columnSpacing: 10
                        rowSpacing: 8
                    Choice {
                        Layout.fillWidth: true
                        Layout.alignment: Qt.AlignTop
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
                        Layout.alignment: Qt.AlignTop
                        label: "사이즈"
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
                        Layout.alignment: Qt.AlignTop
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
                        Layout.alignment: Qt.AlignTop
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
                }
                Component {
                    id: fastFields
                    GridLayout {
                        columns: 2
                        uniformCellWidths: true
                        columnSpacing: 10
                        rowSpacing: 8
                    FastChoice {
                        Layout.fillWidth: true
                        Layout.alignment: Qt.AlignTop
                        label: "옷 분류"
                        choices: card.options.category || []
                        value: card.record.fields.category || ""
                        objectName: "fast_category_" + card.record.code
                        requiresReview: card.pending("category")
                        hint: card.reason("category")

                        enabled: !card.record.excluded
                        onCommitted: function(value) { card.controller.setField(card.record.code, "category", value) }
                    }
                    FastChoice {
                        Layout.fillWidth: true
                        Layout.alignment: Qt.AlignTop
                        label: "사이즈"
                        choices: card.options.size || []
                        value: card.record.fields.size || ""
                        objectName: "fast_size_" + card.record.code
                        requiresReview: card.pending("size")
                        hint: card.reason("size")
                        columns: 4

                        enabled: !card.record.excluded
                        onCommitted: function(value) { card.controller.setField(card.record.code, "size", value) }
                    }
                    FastChoice {
                        Layout.fillWidth: true
                        Layout.alignment: Qt.AlignTop
                        label: "성별"
                        choices: card.options.gender || []
                        value: card.record.fields.gender || ""
                        objectName: "fast_gender_" + card.record.code
                        requiresReview: card.pending("gender")
                        hint: card.reason("gender")
                        choiceLabels: card.record.fields.age === "유아동" ? ["남아", "여아"] : []
                        enabled: !card.record.excluded
                        onCommitted: function(value) { card.controller.setField(card.record.code, "gender", value) }
                    }
                    FastChoice {
                        Layout.fillWidth: true
                        Layout.alignment: Qt.AlignTop
                        label: "성인 / 아동"
                        choices: card.options.age || []
                        value: card.record.fields.age || ""
                        objectName: "fast_age_" + card.record.code
                        requiresReview: card.pending("age")
                        hint: card.reason("age")

                        enabled: !card.record.excluded
                        onCommitted: function(value) { card.controller.setField(card.record.code, "age", value) }
                    }
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
                    renderScale: card.fitScale
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

}
