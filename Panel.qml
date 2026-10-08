import QtQuick
import QtQuick.Controls as Controls
import Quickshell
import qs.Commons
import qs.Ui as Ui

Ui.Panel {
  id: root
  moduleName: "yasserdo.laser"
  manageIpc: false
  property var anchorItem: null
  property var hostWidget: null
  property bool editing: false
  property int minutes: 50

  readonly property var service: hostWidget ? hostWidget.service : null
  readonly property var st: service ? service.state : ({})
  readonly property bool active: service ? service.active : false
  readonly property string status: service ? service.status : "offline"
  readonly property var settings: service ? service.settings : ({})
  readonly property var usage: service ? service.usage : ({})
  readonly property color ink: Color.popups.text
  readonly property color accent: Color.accent
  readonly property color muted: Qt.alpha(ink, 0.55)
  readonly property color faint: Qt.alpha(ink, 0.08)

  readonly property var privacyHelp: ({
    strict: "Only the site or app name is sent (e.g. youtube.com). The screen is never read.",
    balanced: "Site, plus common words from the title and screen. No sentences, names, numbers, emails or links. Chats, email and banking are always site-only.",
    full: "Site, title, page path and screen text, with emails, numbers and secrets masked. Most accurate. Chats, email and banking are still site-only."
  })

  onOpenedChanged: {
    if (!opened) { editing = false; return }
    if (!active) Qt.callLater(function() { taskField.forceActiveFocus() })
  }

  function open() { root.controller.show() }

  // Dropdowns emit `changed` while they load; only a real choice on the settings page counts.
  function setSetting(key, value) {
    if (!root.editing || !root.service || !value || root.settings[key] === value) return
    root.service.run([key, value])
  }

  function startSession() {
    var task = taskField.text.trim()
    if (!root.taskValid(task) || !service) return
    service.start(task, root.minutes)
    taskField.text = ""
  }

  // A stray keystroke or two must never start a session.
  function taskValid(task) { return task.length >= 4 && (task.match(/[A-Za-z\u0600-\u06FF]/g) || []).length >= 3 }

  function statusLine() {
    if (status === "break") return "On a break · " + service.duration(st.break_remaining_s) + " left"
    if (status === "away") return "Away · not judging"
    if (status === "focused") return "Locked on" + (st.label ? " · " + st.label : "")
    if (status === "distracted") return "Drifting · " + (st.label || "")
    if (status === "unsure") return "Not sure about " + (st.label || "this")
    return "Watching…"
  }

  component Caption: Text {
    textFormat: Text.PlainText
    color: root.muted
    font.pixelSize: Style.space(11)
    wrapMode: Text.WordWrap
  }

  component SectionTitle: Text {
    textFormat: Text.PlainText
    color: root.ink
    font.pixelSize: Style.space(12)
    font.weight: Font.Medium
  }

  Ui.KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.hostWidget || root
    bar: root.bar
    open: root.opened
    centerOnBar: false
    focusTarget: root.editing ? keyField : (root.active ? focusScope : taskField)
    contentWidth: fittedContentWidth(Style.space(320))
    contentHeight: fittedContentHeight(content.implicitHeight)
    padding: Style.space(14)
    borderSpec: Border.flat(Qt.alpha(root.ink, 0.16), 1)

    FocusScope {
      id: focusScope
      anchors.fill: parent
      focus: true
      Keys.onEscapePressed: { if (root.editing) root.editing = false; else root.close() }

      Flickable {
        id: scroll
        anchors.fill: parent
        clip: true
        contentWidth: width
        contentHeight: content.implicitHeight
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height
        Controls.ScrollBar.vertical: Controls.ScrollBar { policy: Controls.ScrollBar.AsNeeded }

        Column {
          id: content
          width: parent.width
          spacing: Style.space(12)

          // --- header --------------------------------------------------------------------
          Item {
            width: parent.width
            height: Style.space(28)
            Row {
              spacing: Style.space(8)
              anchors.verticalCenter: parent.verticalCenter
              LaserIcon {
                width: Style.space(20); height: width
                ink: root.service ? root.service.colorFor(root.ink) : root.ink
                hot: root.active
                anchors.verticalCenter: parent.verticalCenter
              }
              Text {
                textFormat: Text.PlainText; text: "Laser"; color: root.ink
                font.pixelSize: Style.space(16); font.weight: Font.Medium
                anchors.verticalCenter: parent.verticalCenter
              }
            }
            Ui.PanelActionButton {
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              iconText: root.editing ? "×" : ""
              tooltipText: root.editing ? "Back" : "Settings: API key, privacy, strictness"
              size: Style.space(24); foreground: root.muted; hoverColor: root.accent; focusable: true
              onClicked: root.editing = !root.editing
            }
          }

          // --- needs a key ----------------------------------------------------------------
          Rectangle {
            visible: !root.editing && root.st.needs_key === true
            width: parent.width
            height: keyPrompt.implicitHeight + Style.space(20)
            radius: Style.cornerRadius
            color: Qt.alpha(root.accent, 0.12)
            Column {
              id: keyPrompt
              anchors.centerIn: parent
              width: parent.width - Style.space(20)
              spacing: Style.space(8)
              SectionTitle { width: parent.width; text: "Bring your own TypeSafe key"; wrapMode: Text.WordWrap }
              Caption { width: parent.width; text: "Laser asks TypeSafe's Jev model whether your screen matches your task. It costs about a cent per workday." }
              Ui.Button { text: "Add API key"; onClicked: root.editing = true }
            }
          }

          // --- idle: start a session --------------------------------------------------------
          Column {
            visible: !root.editing && !root.active
            width: parent.width
            spacing: Style.space(10)

            Ui.TextField {
              id: taskField
              width: parent.width
              placeholderText: "What are you focusing on?"
              onAccepted: root.startSession()
            }
            Row {
              spacing: Style.space(6)
              Repeater {
                model: [{m: 25, l: "25m"}, {m: 50, l: "50m"}, {m: 90, l: "90m"}, {m: 0, l: "∞"}]
                delegate: Ui.Button {
                  required property var modelData
                  text: modelData.l
                  selected: root.minutes === modelData.m
                  bordered: true
                  onClicked: root.minutes = modelData.m
                }
              }
            }
            Ui.Button {
              width: parent.width
              text: "Start focusing"
              bordered: true
              enabled: root.taskValid(taskField.text.trim()) && root.status !== "offline"
              onClicked: root.startSession()
            }
            Caption {
              width: parent.width
              text: root.status === "offline" ? "Starting Laser…" : "Describe the task in plain words. Laser judges every screen against it."
            }
          }

          // --- active session ---------------------------------------------------------------
          Column {
            visible: !root.editing && root.active
            width: parent.width
            spacing: Style.space(8)

            Text {
              width: parent.width
              textFormat: Text.PlainText
              text: root.st.task || ""
              color: root.ink
              font.pixelSize: Style.space(14)
              font.weight: Font.Medium
              wrapMode: Text.WordWrap
            }
            Row {
              spacing: Style.space(6)
              Rectangle {
                width: Style.space(8); height: width; radius: width / 2
                color: root.service ? root.service.colorFor(root.muted) : root.muted
                anchors.verticalCenter: parent.verticalCenter
              }
              Text {
                textFormat: Text.PlainText
                text: root.service ? root.statusLine() : ""
                color: root.ink
                font.pixelSize: Style.space(12)
                anchors.verticalCenter: parent.verticalCenter
              }
            }
            Caption {
              width: parent.width
              text: {
                if (!root.service) return ""
                var parts = [root.service.duration(root.st.elapsed_s) + " in"]
                if (root.st.remaining_s !== null && root.st.remaining_s !== undefined) parts.push(root.service.duration(root.st.remaining_s) + " left")
                if (root.st.focus_ratio !== null && root.st.focus_ratio !== undefined) parts.push(Math.round(root.st.focus_ratio * 100) + "% focused")
                parts.push("drifted " + root.service.duration(root.st.distracted_s))
                return parts.join(" · ")
              }
            }
            Row {
              spacing: Style.space(6)
              Ui.Button {
                text: root.status === "break" ? "Resume" : "Break 5m"
                bordered: true
                onClicked: root.service.run(root.status === "break" ? ["resume"] : ["pause", "5"])
              }
              Ui.Button {
                text: "It's on-task"
                tooltipText: "The site you were flagged on counts as work for this session"
                bordered: true
                enabled: root.status === "distracted" || root.status === "unsure"
                onClicked: root.service.run(["allow"])
              }
              Ui.Button {
                text: "Stop"
                bordered: true
                onClicked: root.service.run(["stop"])
              }
            }
          }

          // --- error ----------------------------------------------------------------------
          Caption {
            visible: !root.editing && !!root.st.error && root.st.needs_key !== true
            width: parent.width
            text: "⚠ " + (root.st.error || "")
            color: root.service ? root.service.unsureColor : root.muted
          }

          // --- settings -------------------------------------------------------------------
          Column {
            visible: root.editing
            width: parent.width
            spacing: Style.space(10)

            SectionTitle { text: "TypeSafe API key" }
            Row {
              width: parent.width
              spacing: Style.space(6)
              Ui.TextField {
                id: keyField
                width: parent.width - saveKey.width - Style.space(6)
                password: true
                echoMode: TextInput.Password
                placeholderText: root.st.needs_key ? "apikey_…" : "•••••••• (saved, enter a new one to replace)"
                onAccepted: saveKey.clicked()
              }
              Ui.Button {
                id: saveKey
                text: "Save"
                bordered: true
                enabled: keyField.text.trim().length > 0 && !(root.service && root.service.keyBusy)
                onClicked: { root.service.saveKey(keyField.text.trim()); keyField.text = "" }
              }
            }
            Caption {
              width: parent.width
              text: (root.service && root.service.keyMessage) ? root.service.keyMessage
                : "Stored only in ~/.config/laser/env (readable by you alone). Get one at typesafe.ai."
            }

            SectionTitle { text: "Privacy" }
            Ui.Dropdown {
              width: parent.width
              showLabel: false
              value: root.settings.privacy || "balanced"
              options: [
                {value: "strict", label: "Strict · site name only"},
                {value: "balanced", label: "Balanced · keywords, no personal data"},
                {value: "full", label: "Full · screen text, masked"}
              ]
              onChanged: function(v) { root.setSetting("privacy", v) }
            }
            Caption { width: parent.width; text: root.privacyHelp[root.settings.privacy || "balanced"] + " Screenshots never leave your computer." }

            SectionTitle { text: "Strictness" }
            Ui.Dropdown {
              width: parent.width
              showLabel: false
              value: root.settings.strictness || "gentle"
              options: [
                {value: "gentle", label: "Gentle · nudge 1m, red 2m, haze 3m, limit 4m"},
                {value: "strict", label: "Strict · nudge 30s, red 1m, haze 90s, limit 2m"},
                {value: "warn_only", label: "Warn only · never closes anything"}
              ]
              onChanged: function(v) { root.setSetting("strictness", v) }
            }

            SectionTitle { text: "At the limit" }
            Ui.Dropdown {
              width: parent.width
              showLabel: false
              value: root.settings.final_step || "close"
              options: [
                {value: "close", label: "Close the tab (browsers and web apps only)"},
                {value: "fog", label: "Keep it hazy · never close anything"}
              ]
              onChanged: function(v) { root.setSetting("final_step", v) }
            }

            SectionTitle { text: "Locked-in look" }
            Ui.Dropdown {
              width: parent.width
              showLabel: false
              value: root.settings.look || "full"
              options: [
                {value: "full", label: "Full · glowing border, dimmed background, beam"},
                {value: "subtle", label: "Subtle · glowing border only"},
                {value: "off", label: "Off · leave the screen alone"}
              ]
              onChanged: function(v) { root.setSetting("look", v) }
            }
            Caption { width: parent.width; text: "While you're focused, the active window's border glows green (red when you drift). Your theme's border comes back when the session ends." }

            SectionTitle { text: "What Jev saw last" }
            Rectangle {
              width: parent.width
              height: lastSent.implicitHeight + Style.space(14)
              radius: Style.cornerRadius
              color: root.faint
              Text {
                id: lastSent
                anchors.centerIn: parent
                width: parent.width - Style.space(14)
                textFormat: Text.PlainText
                wrapMode: Text.WrapAnywhere
                font.family: "monospace"
                font.pixelSize: Style.space(10)
                color: root.muted
                text: root.st.last_sent ? JSON.stringify(root.st.last_sent.state, null, 1) : "Nothing sent yet."
              }
            }
            Caption { width: parent.width; text: "Full log of everything sent: laser audit" }
          }

          // --- usage footer ---------------------------------------------------------------
          Rectangle { width: parent.width; height: 1; color: root.faint }
          Caption {
            width: parent.width
            text: {
              if (!root.service) return ""
              var t = root.usage.today || {}, m = root.usage.month || {}
              return "Today " + (t.calls || 0) + " checks · " + root.service.money(t.cost_usd)
                + "   ·   This month " + root.service.money(m.cost_usd)
                + "   ·   " + (root.settings.privacy || "balanced")
            }
          }
        }
      }
    }
  }
}
