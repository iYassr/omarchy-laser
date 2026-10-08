import QtQuick
import Quickshell.Io
import qs.Commons
import qs.Ui as Ui

// The reticle in the bar. Grey when idle, green when focused, amber when unsure, red and
// throbbing when drifting. During a session it also shows the time left (or elapsed).
Ui.BarWidget {
  id: root
  moduleName: "yasserdo.laser"
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  readonly property var service: bar?.shell?.serviceFor("yasserdo.laser")
  readonly property var st: service ? service.state : ({})
  readonly property string status: service ? service.status : "offline"
  readonly property bool active: service ? service.active : false
  readonly property bool drifting: active && status === "distracted" && service.level >= 1
  readonly property bool opened: panelLoader.item ? panelLoader.item.opened : false

  readonly property string label: {
    if (!active) return ""
    if (status === "break") return "break " + service.duration(st.break_remaining_s)
    if (status === "away") return "away"
    return st.remaining_s !== null && st.remaining_s !== undefined
      ? service.duration(st.remaining_s) : service.duration(st.elapsed_s)
  }

  // Page titles reach the tooltip, which may render rich text: escape them.
  function plain(text) { return String(text || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;") }

  readonly property string tip: {
    if (!service || status === "offline") return "Laser is starting…"
    if (st.needs_key) return "Laser needs your TypeSafe API key. Click to add it"
    if (!active) return "Laser: click to start a focus session"
    var lines = [plain(st.task), status + (st.label ? " · " + plain(st.label) : "")]
    var ratio = st.focus_ratio !== null && st.focus_ratio !== undefined ? " · " + Math.round(st.focus_ratio * 100) + "% focus" : ""
    lines.push("focused " + service.duration(st.focused_s) + " · drifted " + service.duration(st.distracted_s) + ratio)
    if (st.error) lines.push("⚠ " + plain(st.error))
    lines.push("click: panel · right-click: it's on-task · middle-click: stop")
    return lines.join("\n")
  }

  function open() {
    unloadTimer.stop()
    if (panelLoader.item) panelLoader.item.open()
    else { pendingOpen = true; panelLoader.active = true }
  }
  function close() { if (panelLoader.item) panelLoader.item.close() }
  function toggle() { opened ? close() : open() }
  function closeForPopoutSwitch() { if (panelLoader.item) panelLoader.item.closeForPopoutSwitch() }

  property bool pendingOpen: false
  onOpenedChanged: if (!opened) unloadTimer.restart()

  Timer {
    id: unloadTimer
    interval: 300
    onTriggered: if (!root.opened) panelLoader.active = false
  }

  Loader {
    id: panelLoader
    active: false
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      item.bar = root.bar
      item.anchorItem = button
      item.hostWidget = root
      if (root.pendingOpen) {
        root.pendingOpen = false
        item.open()
      }
    }
  }

  Ui.WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: ""
    labelVisible: false
    hasVisualContent: true
    fixedWidth: root.vertical ? -1 : row.implicitWidth + scaledHorizontalMargin * 2
    tooltipText: root.opened ? "" : root.tip
    Accessible.role: Accessible.Button
    Accessible.name: "Laser: " + root.status
    onPressed: function(b) {
      if (!root.service) return
      if (b === Qt.RightButton && root.active) root.service.run(["allow"])
      else if (b === Qt.MiddleButton && root.active) root.service.run(["stop"])
      else root.toggle()
    }

    Row {
      id: row
      anchors.centerIn: parent
      spacing: Style.space(5)

      LaserIcon {
        id: icon
        anchors.verticalCenter: parent.verticalCenter
        width: Style.space(15)
        height: width
        hot: root.active
        ink: root.service ? root.service.colorFor(button.foreground) : button.foreground
        opacity: root.active || root.opened ? 1 : 0.75

        // Drifting: the reticle throbs, faster as it escalates.
        SequentialAnimation on scale {
          running: root.drifting
          loops: Animation.Infinite
          onRunningChanged: if (!running) icon.scale = 1
          NumberAnimation { to: 1.3; duration: root.service ? 700 - root.service.level * 120 : 700; easing.type: Easing.OutQuad }
          NumberAnimation { to: 1.0; duration: root.service ? 700 - root.service.level * 120 : 700; easing.type: Easing.InQuad }
        }
      }

      Text {
        visible: !root.vertical && root.label !== ""
        anchors.verticalCenter: parent.verticalCenter
        text: root.label
        textFormat: Text.PlainText
        color: root.drifting ? root.service.distractedColor : button.foreground
        font.family: button.fontFamily
        font.pixelSize: button.fontSize
      }
    }
  }

  IpcHandler {
    target: "laser-panel"
    function open(): void { root.open() }
    function close(): void { root.close() }
    function toggle(): void { root.toggle() }
  }
}
