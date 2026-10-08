import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import Quickshell.Hyprland
import QtQuick.Effects

// Runs the Laser daemon, mirrors its state file, and draws the red edge while you drift.
// All judgement lives in the daemon (python, stdlib only); this displays and forwards clicks.
Item {
  id: root

  // Injected by omarchy-shell (the plugin service loader).
  property var shell: null
  property var manifest: null

  readonly property string pluginDir: manifest && manifest.__sourceDir
    ? String(manifest.__sourceDir)
    : Quickshell.env("HOME") + "/.config/omarchy/plugins/yasserdo.laser"
  readonly property string cli: pluginDir + "/bin/laser"
  // Never fall back to a shared directory: another user could plant a fake state there.
  readonly property string runtimeDir: Quickshell.env("XDG_RUNTIME_DIR") || ""
  readonly property string stateFile: runtimeDir ? runtimeDir + "/laser/state.json" : ""

  property var state: ({})
  property real nowS: Date.now() / 1000
  property string keyMessage: ""
  property bool keyBusy: false

  // The daemon rewrites the file every poll; a stale file means it isn't running.
  readonly property bool online: state.updated !== undefined && nowS - state.updated < 15
  readonly property bool active: online && state.active === true
  readonly property string status: !online ? "offline" : (state.status || "idle")
  readonly property int level: active ? (state.level_index || 0) : 0
  readonly property bool tint: active && state.tint === true
  readonly property var settings: state.settings || ({privacy: "balanced", strictness: "gentle"})
  readonly property var usage: state.usage || ({today: {calls: 0, tokens: 0, cost_usd: 0}, month: {calls: 0, tokens: 0, cost_usd: 0}})

  readonly property color focusedColor: "#5fb85f"
  readonly property color distractedColor: "#ff3b3b"
  readonly property color unsureColor: "#e0a83a"

  function colorFor(fallback) {
    if (!active) return fallback
    if (status === "focused") return focusedColor
    if (status === "distracted") return level >= 1 ? distractedColor : unsureColor
    if (status === "unsure") return unsureColor
    return fallback
  }

  function duration(seconds) {
    if (seconds === undefined || seconds === null) return ""
    var m = Math.floor(seconds / 60)
    return m >= 60 ? Math.floor(m / 60) + "h " + (m % 60 < 10 ? "0" : "") + (m % 60) + "m" : m + "m"
  }

  function money(usd) {
    usd = usd || 0
    return usd < 1 ? "$" + usd.toFixed(4) : "$" + usd.toFixed(2)
  }

  function run(args) { Quickshell.execDetached([cli].concat(args)) }

  // The task goes over stdin, never argv: other local users can read any process's arguments.
  function start(task, minutes) {
    if (!task || startProcess.running) return
    startProcess.task = task
    startProcess.command = [cli, "start", "-"].concat(minutes > 0 ? ["-m", String(minutes)] : [])
    startProcess.running = true
  }

  Process {
    id: startProcess
    property string task: ""
    stdinEnabled: true
    onStarted: {
      write(task + "\n")
      task = ""
    }
  }

  // The key goes over stdin, never argv, so it can't show up in a process list.
  function saveKey(key) {
    if (!key || keyBusy) return
    keyBusy = true
    keyMessage = "Checking the key…"
    keyProcess.secret = key
    keyProcess.running = true
  }

  function parse(text) {
    try { root.state = JSON.parse(text) } catch (e) {}
  }

  Process {
    id: keyProcess
    property string secret: ""
    command: [root.cli, "key"]
    stdinEnabled: true
    stdout: StdioCollector { id: keyOut }
    stderr: StdioCollector { id: keyErr }
    onStarted: {
      write(secret + "\n")
      secret = ""
    }
    onExited: function(exitCode) {
      root.keyBusy = false
      root.keyMessage = exitCode === 0 ? "✓ Key saved and verified" : ("✗ " + (String(keyErr.text).trim() || "Couldn't save the key"))
    }
  }

  // --- the daemon ------------------------------------------------------------------------

  Process {
    id: daemon
    property int crashes: 0
    property real startedAt: 0
    command: [root.cli, "daemon"]
    running: root.runtimeDir !== ""
    onStarted: startedAt = Date.now()
    onExited: function(exitCode) {
      // 3 = another instance is already watching (e.g. started from a terminal): check back later.
      // A daemon that keeps dying quickly backs off up to a minute instead of spinning.
      crashes = Date.now() - startedAt < 30000 ? crashes + 1 : 0
      restart.interval = exitCode === 3 ? 30000 : Math.min(60000, 3000 * Math.pow(2, Math.min(crashes, 5)))
      restart.restart()
    }
  }

  Timer {
    id: restart
    onTriggered: daemon.running = true
  }

  FileView {
    id: file
    path: root.stateFile
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: root.parse(text())
  }

  // Atomic renames can slip past the watcher; a slow poll keeps us honest and ticks `nowS`.
  Timer {
    interval: 2000
    running: true
    repeat: true
    onTriggered: {
      root.nowS = Date.now() / 1000
      file.reload()
    }
  }

  // --- locked in: a thin laser beam along the bottom edge, with a glint sweeping across it ---
  // (the window border and dimming are set by the daemon; see laser/look.py)

  Variants {
    model: Quickshell.screens

    PanelWindow {
      required property var modelData
      screen: modelData
      visible: root.state.locked_in === true || beam.opacity > 0.01
      color: "transparent"
      anchors { bottom: true; left: true; right: true }
      implicitHeight: 3
      exclusionMode: ExclusionMode.Ignore
      WlrLayershell.namespace: "laser-beam"
      WlrLayershell.layer: WlrLayer.Overlay
      WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
      mask: Region {}

      Item {
        id: beam
        anchors.fill: parent
        opacity: root.state.locked_in === true ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 900; easing.type: Easing.InOutQuad } }

        Rectangle {
          anchors.fill: parent
          opacity: 0.55
          gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0.0; color: "#0039ff88" }
            GradientStop { position: 0.15; color: "#39ff88" }
            GradientStop { position: 0.85; color: "#00d4ff" }
            GradientStop { position: 1.0; color: "#0000d4ff" }
          }
        }
        Rectangle {
          id: glint
          width: parent.width * 0.18
          height: parent.height
          gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0.0; color: "#00ffffff" }
            GradientStop { position: 0.5; color: "#ddffffff" }
            GradientStop { position: 1.0; color: "#00ffffff" }
          }
          NumberAnimation on x {
            running: beam.opacity > 0
            loops: Animation.Infinite
            from: -glint.width
            to: beam.width
            duration: 4200
            easing.type: Easing.InOutSine
          }
        }
      }
    }
  }

  // --- fog: after a few minutes on a distraction, a soft haze over that one window ---------
  // A live capture of the window, lightly blurred and desaturated, drawn exactly over it and
  // click-through. Deliberately minimal: it takes the shine off, it doesn't hide anything.
  // Only that window, only while you're on it; switching to anything else clears it at once.

  readonly property var fogState: state.fog || null
  readonly property real fogAmount: active && fogState ? fogState.amount : 0
  readonly property var fogTarget: {
    if (!fogState) return null
    var list = Hyprland.toplevels.values
    for (var i = 0; i < list.length; i++)
      if ("0x" + list[i].address === fogState.address) return list[i]
    return null
  }

  Timer {  // keep the window's position fresh so the haze follows it if it moves
    interval: 1000
    running: root.fogState !== null
    repeat: true
    triggeredOnStart: true
    onTriggered: Hyprland.refreshToplevels()
  }

  // --- the red edge: a click-through overlay on every screen that breathes while you drift --

  Variants {
    model: Quickshell.screens

    PanelWindow {
      id: driftWindow
      required property var modelData
      screen: modelData
      // One overlay for the whole drift stage, so the red always sits above the haze.
      readonly property var ipc: root.fogTarget ? root.fogTarget.lastIpcObject : null
      readonly property bool fogHere: !!(ipc && ipc.at && ipc.size)
        && ipc.at[0] < modelData.x + modelData.width && ipc.at[0] + ipc.size[0] > modelData.x
        && ipc.at[1] < modelData.y + modelData.height && ipc.at[1] + ipc.size[1] > modelData.y
      visible: root.tint || glow.opacity > 0.01 || (fogHere && haze.opacity > 0.01)
      color: "transparent"
      anchors { top: true; bottom: true; left: true; right: true }
      exclusionMode: ExclusionMode.Ignore
      WlrLayershell.namespace: "laser-edge"
      WlrLayershell.layer: WlrLayer.Overlay
      WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
      mask: Region {}

      // The haze: a live capture of just the distracting window, lightly blurred, drawn over it.
      Item {
        id: haze
        x: driftWindow.fogHere ? driftWindow.ipc.at[0] - driftWindow.modelData.x : 0
        y: driftWindow.fogHere ? driftWindow.ipc.at[1] - driftWindow.modelData.y : 0
        width: driftWindow.fogHere ? driftWindow.ipc.size[0] : 0
        height: driftWindow.fogHere ? driftWindow.ipc.size[1] : 0
        opacity: driftWindow.fogHere && root.fogAmount > 0 ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 250 } }

        ScreencopyView {
          id: capture
          anchors.fill: parent
          captureSource: root.fogAmount > 0 && root.fogTarget ? root.fogTarget.wayland : null
          live: true
          visible: false
        }
        MultiEffect {
          anchors.fill: parent
          source: capture
          visible: capture.hasContent
          blurEnabled: true
          blurMax: 12                       // a soft haze at most
          blur: root.fogAmount
          saturation: -0.3 * root.fogAmount
          Behavior on blur { NumberAnimation { duration: 1500 } }
        }
      }

      Item {
        id: glow
        anchors.fill: parent
        opacity: root.tint ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 600; easing.type: Easing.InOutQuad } }

        property real pulse: 0.75
        SequentialAnimation on pulse {
          running: root.tint
          loops: Animation.Infinite
          NumberAnimation { to: 1.0; duration: 1100; easing.type: Easing.InOutSine }
          NumberAnimation { to: 0.6; duration: 1100; easing.type: Easing.InOutSine }
        }

        readonly property real band: Math.min(width, height) * 0.09
        readonly property color red: Qt.rgba(1, 0.16, 0.16, 0.55)

        Rectangle {
          anchors { top: parent.top; left: parent.left; right: parent.right }
          height: glow.band; opacity: glow.pulse
          gradient: Gradient {
            GradientStop { position: 0; color: glow.red }
            GradientStop { position: 1; color: "transparent" }
          }
        }
        Rectangle {
          anchors { bottom: parent.bottom; left: parent.left; right: parent.right }
          height: glow.band; opacity: glow.pulse
          gradient: Gradient {
            GradientStop { position: 0; color: "transparent" }
            GradientStop { position: 1; color: glow.red }
          }
        }
        Rectangle {
          anchors { top: parent.top; bottom: parent.bottom; left: parent.left }
          width: glow.band; opacity: glow.pulse
          gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0; color: glow.red }
            GradientStop { position: 1; color: "transparent" }
          }
        }
        Rectangle {
          anchors { top: parent.top; bottom: parent.bottom; right: parent.right }
          width: glow.band; opacity: glow.pulse
          gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0; color: "transparent" }
            GradientStop { position: 1; color: glow.red }
          }
        }
        // A faint wash over the whole screen; it deepens once the haze kicks in.
        Rectangle {
          anchors.fill: parent
          color: Qt.rgba(1, 0.1, 0.1, (0.07 + 0.16 * root.fogAmount) * glow.pulse)
          Behavior on color { ColorAnimation { duration: 1500 } }
        }

        Rectangle {
          anchors.horizontalCenter: parent.horizontalCenter
          anchors.bottom: parent.bottom
          anchors.bottomMargin: 48
          radius: height / 2
          color: Qt.rgba(0.08, 0.02, 0.02, 0.88)
          border.color: Qt.rgba(1, 0.25, 0.25, 0.9)
          border.width: 1
          width: banner.implicitWidth + 40
          height: banner.implicitHeight + 18

          Row {
            id: banner
            anchors.centerIn: parent
            spacing: 10
            LaserIcon { ink: "#ff6b6b"; hot: true; width: 16; height: 16; anchors.verticalCenter: parent.verticalCenter }
            Text {
              anchors.verticalCenter: parent.verticalCenter
              color: "#ffdada"
              font.pixelSize: 15
              textFormat: Text.PlainText
              text: "Drifting on " + (root.state.label || "this") + "  ·  back to: " + (root.state.task || "")
            }
          }
        }
      }
    }
  }

  IpcHandler {
    target: "laser"

    function status(): string { return JSON.stringify(root.state) }
    function start(task: string, minutes: string): void { root.start(task, Number(minutes) || 0) }
    function stop(): void { root.run(["stop"]) }
    function allow(): void { root.run(["allow"]) }
    function pause(minutes: string): void { root.run(["pause", minutes || "5"]) }
    function resume(): void { root.run(["resume"]) }
  }
}
