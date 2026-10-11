# 🐸 frogterminal

[한국어](README.md) · **English**

A **web terminal that puts your Mac's tmux sessions in a phone or laptop browser**. One tmux session per project folder, with an AI coding agent (Claude Code, Codex) running inside, so you can pick up where you left off from anywhere.

> **macOS only.** The bridge (the server side) runs on a Mac only; it depends on tmux, `open`, `mdfind` and launchd. Linux and Windows hosts are not supported. The **connecting** side, however, can be any device with a browser — see section 2.

- Several panes (1 / 4 / 6 / 8) to watch multiple projects on one screen
- An **approval inbox** that collects everything the agent stopped to ask (Claude Code hooks, optional)
- Browse and edit project files, drag-and-drop upload, paste photos
- Special-key bar for phone soft keyboards, Korean/CJK input (IME) handling
- The bridge binds to **127.0.0.1 only**; you choose how to expose it (Tailscale Serve recommended)

## 1. Architecture

```
browser ──https──▶ Tailscale Serve (recommended) or another reverse proxy
                        └─▶ 127.0.0.1:7788  bridge (Python aiohttp)
                                └─▶ tmux session (one per project folder) → claude · codex · shell
```

The bridge serves the UI (HTML/JS) itself. The only external CDN assets are xterm.js and a font, both pinned with integrity hashes (SRI).

## 2. Which devices — everything connects to one Mac

```
                 ┌──────────────── Tailscale (your tailnet) ────────────────┐
 main Mac ◀───────┤  Windows PC · iPhone · Android · iPad · another Mac      │
 (bridge + tmux)  └──────────────── a browser is all you need ─────────────┘
```

- **The host is a single Mac.** Terminals, agents and files all live on that machine.
- **Each connecting device just installs the Tailscale app and signs in to the same account.** Windows PCs, iPhones, Android phones, iPads and other Macs all work the same way. No client to install: open `https://<your Mac's MagicDNS name>` in a browser.
- Enter the token once and that device/browser remembers it for a year. On a phone you can "Add to Home Screen" (PWA) to use it like an app (the PWA has its own storage, so enter the token once more there).
- Away from home (LTE, other Wi-Fi) the same address works as long as Tailscale is on. Exposing the bridge to the public internet via Funnel is refused at startup.

## 3. Requirements

- **macOS** (uses tmux, `open`, `mdfind`, launchd — Linux/Windows hosts are not supported)
- Python 3.12+, `tmux` (`brew install tmux`)
- Recommended: [Tailscale](https://tailscale.com) — keep it inside your tailnet and receive the visitor's identity as a header
- Optional: Claude Code / Codex CLI (plain shell sessions work without them)

## 4. Install

```bash
git clone <this-repo> frogterminal && cd frogterminal
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Machine config (optional) — allowed logins, origins, open-anywhere, HUD. Without it, token-only auth.
mkdir -p ~/.frogterminal && cp bridge.example.json ~/.frogterminal/bridge.json && $EDITOR ~/.frogterminal/bridge.json

# Run at login (launchd) — fills the template with this repo/home path, installs and starts it
scripts/install_launchd.sh
cat ~/.frogterminal/token          # access token generated on first start
```

To run by hand: `.venv/bin/python -m bridge.server`.

### Expose with Tailscale Serve (recommended)

```bash
tailscale serve --bg --https=443 http://127.0.0.1:7788
```

Then put your Tailscale login (email) in `allowedLogins` and `https://<this Mac's MagicDNS name>` in `allowedOrigins` in `~/.frogterminal/bridge.json`, and restart. That gives you **two layers: Tailscale identity + token**. If port 7788 is exposed via Funnel (public internet), the bridge refuses to start.

### First connection

Opening the bridge URL in a browser **asks for the token once** (remembered per device for a year). Paste the value of `cat ~/.frogterminal/token`, or open `https://<address>/?token=<token>`. To rotate the token, delete the file and restart: a new one is generated and every device must log in again.

## 5. Configuration — `~/.frogterminal/bridge.json`

| Key | Default | Meaning |
|---|---|---|
| `allowedLogins` | `[]` | Tailscale logins. Empty = identity header is not checked (token only) |
| `allowedOrigins` | `[]` | Fixed allowed Origins. Same-origin as the connected Host is always allowed |
| `tailscaleBin` | `/Applications/Tailscale.app/Contents/MacOS/Tailscale` | CLI used for the Funnel self-check |
| `openAnywhere` | `false` | `true` allows "Open on Mac" for **any absolute path** printed in a terminal |
| `hudScript` | `null` | Script that prints an account-usage line under the grid (compatible with Claude Code statusline) |
| `defaultProjectsRoot` | `~/projects` | Default project folder until you set one in the settings panel |

A broken file does not silently fall back to defaults; the bridge **refuses to start**. The ⚙ settings in the UI (agent, bypass, resume, aliases, projects root) are stored separately in `~/.frogterminal/settings.json`.

## 6. Claude Code approval inbox (optional)

When Claude Code stops to ask a question, a card appears; answering on the card types into that terminal. This requires six hooks in your global `~/.claude/settings.json`.

```bash
python3 hooks/install_hooks.py --dry-run    # show what would change
python3 hooks/install_hooks.py              # register (backs up settings.json.bak once before the first change)
python3 hooks/install_hooks.py --uninstall  # remove only what we added
```

## 7. Security model — read this

This program is **a remote shell running with your user's privileges**. Anyone who gets in can do everything you can do in a terminal.

- The bridge binds to 127.0.0.1 only (no option to change it). The proxy that exposes it is your responsibility.
- Two auth layers: ① if `allowedLogins` is set, the `Tailscale-User-Login` header (added by Tailscale Serve); ② a cookie equal to `~/.frogterminal/token` (always). **If you expose it with nginx etc. without Tailscale, ① is a header anyone can forge** — that is why ② exists.
- Token login locks after 10 failures in 10 minutes. The cookie is HttpOnly, SameSite=Lax and Secure over https.
- The file API answers 404 for anything outside the projects root and for `.env`, `.git`, keys, `auth.json`, `*token.json` and the like. Absolute paths for "Open on Mac" require `openAnywhere`.
- Every state change is appended to `<projectsRoot>/_uploads/frogterminal-audit.log` (not readable through the web file API).
- Single-user by design. Two logins in `allowedLogins` see each other's sessions and files.
- The default `bypass: true` starts Claude with `--dangerously-skip-permissions` and Codex with `--yolo`. You can turn it off in settings.

Vulnerability reports: `SECURITY.md`.

## 8. Development and tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest bridge/tests -q     # bridge (uses real tmux — only creates and removes frogtest-* sessions)
.venv/bin/playwright install chromium
.venv/bin/python -m pytest tests -q            # web (fake bridge + headless Chromium, several minutes)
```

- `web/*.js` is served straight from disk, so edits are live on save. `bridge/*.py` needs a restart (`scripts/restart.sh` runs a preflight first: "if I stop it now, will it come back?").
- A new static file must be added in two places: `bridge/server.py STATIC_FILES` and `tests/conftest.py STATIC`.

## License

MIT — `LICENSE`.
