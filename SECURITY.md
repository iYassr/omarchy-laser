# Security

Laser watches your screen. That makes it a privileged piece of software, and it should be
held to that standard. This document describes what Laser can touch, how it is constrained,
and what an end-to-end audit found.

## Report a vulnerability

Please use a **[private security advisory](https://github.com/iYassr/omarchy-laser/security/advisories/new)**
rather than a public issue. You'll get a reply within a few days, and fixes are released as soon as possible.

## What Laser can access

| Capability | Why | Constraint |
|---|---|---|
| Focused window class, title, geometry (`hyprctl activewindow`) | to know what you're looking at | read-only, every 2 s, only during a session |
| Pixels of the focused window (`grim` → `tesseract`) | on-device OCR for Balanced/Full | piped in memory, never written to disk; never for Strict, sensitive apps, or tabs whose site is unknown |
| Browser history databases | to resolve a tab's site from its title | opened read-only (`immutable=1`), one parameterized query by exact title; query strings and fragments discarded |
| Network | the Jev API | one constant host, `api.typesafe.ai:443`, TLS with certificate and hostname verification, no redirects, 64 KiB response cap |
| Window border and dimming (`hyprctl eval`) | the locked-in look | runtime-only; originals saved and restored; only regex-validated hex colours and numbers reach the Lua string |
| Closing a tab or window (`hyprctl dispatch`) | the last escalation step | browser tabs and web-app windows only, never native apps; only if the same window **and** title are still focused; off in *Warn only* |
| Notifications (`notify-send`) | nudges | text is HTML-escaped and passed after `--` |

Laser needs no root privileges, no privilege escalation, no background services outside the shell, no
package installs at runtime and no third-party Python packages. It runs entirely as your user.

## Data at rest

All files are owner-only: folders `0700`, files `0600`, created under `umask 077`.

| Path | Contents |
|---|---|
| `~/.config/laser/env` | your TypeSafe API key |
| `~/.config/laser/settings.json` | privacy, strictness, look, sensitive list |
| `~/.local/share/laser/sessions.jsonl` | session summaries (task, focus %, top distractions) |
| `~/.local/share/laser/usage.json` | checks and tokens per day |
| `~/.local/share/laser/sent.jsonl` | the last 200 payloads sent to Jev (`laser audit`) |
| `~/.local/state/laser/laser.log` | daemon log, rotated at 512 KB |
| `$XDG_RUNTIME_DIR/laser/` | live state, control socket (`0600`), lock, session, saved theme border |

### The API key

- It is entered in the panel or with `laser key` and **passed over stdin**, so it never appears in a command line or process list.
- It is **verified before saving**, so a typo can't replace a working key, and stored in `~/.config/laser/env` (`0600`).
- It is never logged, never written to the state file or audit log, and never echoed in errors.
- If supplied through `TYPESAFE_API_KEY`, it is removed from the environment at start-up, so child
  processes (`hyprctl`, `grim`, `tesseract`, `notify-send`) don't inherit it.

## Threat model

1. **Hostile web content.** Page titles, page text and URLs are attacker-controlled. They are treated as
   data only. Subprocesses take argument lists with no shell. Lua strings never include them. Notification
   text is escaped. The bar tooltip is escaped, and the panel renders plain text only. A web page can, at
   most, get *its own* tab judged.
2. **Other local users.** All state is owner-only, and the control socket lives in your private runtime directory.
   The bar never falls back to a shared directory such as `/tmp`.
3. **Code injection through Python's import path.** `bin/laser` runs `python3 -P -s` with a fixed
   `PYTHONPATH`, so a `laser/` or `json.py` in your current directory is never imported.
4. **A malicious or broken API response.** Responses are size-capped and schema-checked. Probabilities
   must be finite and in [0, 1], and categories must come from the fixed list. Server error text is stripped
   of control characters before display.
5. **Privacy leakage.** See the README's [Privacy](README.md#privacy) section. Everything sent is built in
   `laser/privacy.py` and can be checked with `laser audit`.

## Audit, October 2026

An end-to-end review, independent of the original implementation, covered every module, the QML and the
packaging. All of the following were fixed before the first public release:

| Severity | Finding | Fix |
|---|---|---|
| High | After a tab switch inside one browser window, a stale verdict for the previous site could carry over to the new tab (e.g. while the API was unreachable) and lead to closing it | A window's last verdict is reused only for the same site |
| High | Sensitive-site protection depended on resolving the tab's URL. Private windows or unsaved visits fell back to sending screen text | Chat, mail, banking and password pages are also recognised by title; tabs with an unknown site never send screen text |
| Medium | Data files were created world-readable under the default umask | `umask 077`, `0700` folders, existing files tightened on start-up |
| Medium | `python3 -m` imported modules from the current directory | `python3 -P -s` with a fixed path |
| Low–Medium | Page titles could inject markup into notifications or the tooltip, or a leading `-` into `notify-send` options | HTML escaping and `--` |
| Low–Medium | Closing a native app window could lose unsaved work | Only browser tabs and web apps are ever closed |
| Low | `inf`/`NaN` durations or probabilities could wedge the state file | Range and finiteness validation |
| Low | Unbounded API responses and raw server text in the terminal | 64 KiB cap, control characters stripped |
| Low | A daemon that crashed on start was restarted every 3 s indefinitely | Exponential back-off to 60 s |
| Low | The API key from the environment was inherited by subprocesses | Removed from the environment at start-up |

### Known limitations

- **OCR sees what is drawn over the window.** The capture is the focused window's screen region, so a
  notification popup or floating window on top of it is read too. Balanced mode reduces that text to
  dictionary keywords with names removed. Use Strict if this matters to you.
- **Name filtering is a list, not a guarantee.** Rare names that are also dictionary words may pass in
  Balanced mode. Chat and mail apps never send text at all.
- **Ctrl+W is synthetic input.** If you physically hold Shift at the exact moment a tab is closed, the
  browser could see Ctrl+Shift+W (close window). This needs four minutes of drift plus a held key at the
  same instant.
- **Plugins are not sandboxed.** Omarchy shell plugins run unsandboxed as your user, like any desktop app.
  Read the code: it's small, and stdlib only.
