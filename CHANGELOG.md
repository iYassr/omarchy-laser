# Changelog

## 1.1.2 (2026-10-08)

- The haze and the red warning now share one overlay, so the red wash and the "back to your task" banner
  always sit on top of the hazed window (the haze used to cover them)
- The red wash deepens across the whole screen once the haze kicks in
- New marketplace preview showing the full drift state

## 1.1.1 (2026-10-08)

- Security: the focus task and drifted-to sites never appear in process arguments any more. The bar passes
  the task over stdin, and notifications use a built-in D-Bus client instead of `notify-send`.
  Reported in marketplace review.
- `laser start -` reads the task from stdin

## 1.1.0 (2026-10-08)

- New escalation step: a soft haze over the distracting window (3 min gentle, 90 s strict), drawn from a
  live local capture, deliberately minimal and cleared the instant you return to work
- New setting "At the limit": close the tab (default) or keep it hazy and never close
- `laser final close|fog`

## 1.0.0 (2026-10-08)

First public release.

- Task-aware focus sessions judged by TypeSafe Jev, with your own API key
- Escalation: red reticle, notification, red screen edge, tab closed (browser tabs and web apps only)
- Locked-in look: glowing active border, dimmed background, laser beam; theme restored after the session
- Privacy levels Strict / Balanced / Full; chats, mail, banking and password managers always site-only
- On-device OCR; screenshots never leave the machine; `laser audit` shows every payload
- Live cost tracking (`laser usage`), session history, break and allow controls
- Stdlib-only Python daemon supervised by the bar plugin; owner-only files; IPv4 fallback for broken IPv6
