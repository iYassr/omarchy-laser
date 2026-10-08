# Changelog

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
