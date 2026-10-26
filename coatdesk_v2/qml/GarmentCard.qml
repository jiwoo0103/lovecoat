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
    signal zoomRequested(string code)
    signal headRequested(string code)
    radius: 10
    color: record.excluded ? "#e5e7eb" : record.boundary ? "#fff2df" : "#ffffff"
    border.color: record.selected ? "#2563eb" : record.boundary ? "#f3a840" : "#dce3eb"
    border.width: record.selected ? 3 : 1
    Behavior on color { ColorAnimation { duration: 100 } }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 10
        spacing: 6
        RowLayout {
            Layout.fillWidth: true
            Label { text: "# " + (card.record.code || ""); font.bold: true; font.pixelSize: 15; color: "#172b42" }
            Label { visible: !!card.record.boundary; text: "경계"; color: "#a85d0a"; font.pixelSize: 11 }
            Item { Layout.fillWidth: true }
            Label {
                text: card.record.status || ""
                color: card.record.status === "확인 완료" ? "#0f766e" : "#64748b"
                font.pixelSize: 12
            }
            ToolButton {
                text: card.record.selected ? "✓" : "+"
                implicitHeight: 25
                implicitWidth: 28
                onClicked: card.controller.select(card.record.code, Qt.ControlModifier)
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
                onSelected: function(modifiers) { card.forceActiveFocus(); card.controller.select(card.record.code, modifiers) }
                onEnlarged: card.zoomRequested(card.record.code)
                onPaged: function(delta) { card.forceActiveFocus(); card.controller.pageBy(delta) }
            }
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 5
                GridLayout {
                    Layout.fillWidth: true
                    columns: 2
                    columnSpacing: 8
                    rowSpacing: 4
                    Choice {
                        Layout.fillWidth: true
                        label: "카테고리"
                        choices: card.options.category || []
                        value: card.record.fields.category || ""
                        enabled: !card.record.excluded
                        onCommitted: function(value) { card.controller.setField(card.record.code, "category", value) }
                    }
                    Choice {
                        Layout.fillWidth: true
                        label: "사이즈"
                        editable: true
                        choices: card.options.size || []
                        value: card.record.fields.size || ""
                        enabled: !card.record.excluded
                        onCommitted: function(value) { card.controller.setField(card.record.code, "size", value) }
                    }
                    Choice {
                        Layout.fillWidth: true
                        label: "남성 / 여성"
                        choices: card.options.gender || []
                        value: card.record.fields.gender || ""
                        enabled: !card.record.excluded
                        onCommitted: function(value) { card.controller.setField(card.record.code, "gender", value) }
                    }
                    Choice {
                        Layout.fillWidth: true
                        label: "성인 / 유아동"
                        choices: card.options.age || []
                        value: card.record.fields.age || ""
                        enabled: !card.record.excluded
                        onCommitted: function(value) { card.controller.setField(card.record.code, "age", value) }
                    }
                }
                Label {
                    Layout.fillWidth: true
                    text: "제안  " + [card.record.suggestions.gender, card.record.suggestions.age, card.record.suggestions.size].filter(function(v) { return !!v }).join(" · ")
                    visible: card.height >= 250 && Object.keys(card.record.suggestions).length > 0
                    color: "#0f766e"
                    font.pixelSize: 12
                    elide: Text.ElideRight
                }
                Label {
                    visible: card.height >= 250 && text.length > 0
                    Layout.fillWidth: true
                    text: card.record.issues || card.record.note || ""
                    color: card.record.issues ? "#9a5c20" : "#64748b"
                    font.pixelSize: 11
                    elide: Text.ElideRight
                    ToolTip.visible: issueMouse.containsMouse && text.length > 0
                    ToolTip.text: (card.record.issues || "") + "\n" + (card.record.note || "")
                    MouseArea { id: issueMouse; anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.NoButton }
                }
                Label {
                    Layout.fillWidth: true
                    font.pixelSize: 11
                    color: card.record.held && card.record.held.length > 0 ? "#a03227" : "#475569"
                    text: {
                        var s = card.record.aiScores || {};
                        function pct(k) { return s[k] === undefined ? "미실행" : (s[k] * 100).toFixed(1) + "%"; }
                        return "판넬 " + (card.record.manualRoi ? "직접 지정" : pct("panel")) + " · 성별 " + pct("shape") + " · 사이즈 " + pct("size");
                    }
                    wrapMode: Text.Wrap
                }
                Label {
                    Layout.fillWidth: true
                    visible: card.record.held && card.record.held.length > 0
                    font.pixelSize: 11
                    color: "#a03227"
                    text: "보류: " + (card.record.held || []).join(" · ") + " / 머리 확대에서 확인"
                    wrapMode: Text.Wrap
                }
                Item { Layout.fillHeight: true }
                RowLayout {
                    Button { text: "머리 확대"; implicitHeight: 29; font.pixelSize: 12; onClicked: card.headRequested(card.record.code) }
                    Button { text: "상세 / 다른 각도"; implicitHeight: 29; font.pixelSize: 12; onClicked: card.zoomRequested(card.record.code) }
                }
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
                    onSelected: function(modifiers) { card.forceActiveFocus(); card.controller.select(card.record.code, modifiers) }
                    onEnlarged: card.zoomRequested(card.record.code)
                    onPaged: function(delta) { card.forceActiveFocus(); card.controller.pageBy(delta) }
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
