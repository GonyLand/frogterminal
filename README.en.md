<p align="center">
  <img src="docs/demo/img/frog.png" width="360" alt="Autofrog's frog mascot coding on a laptop">
</p>

<h1 align="center">🐸 frogterminal</h1>

<p align="center"><b>Run Claude Code and Codex on one Mac, and pick them up from your phone or Windows PC in a browser</b></p>

<p align="center">
  <a href="README.md">한국어</a> · <b>English</b><br>
  <sub>frogterminal by Autofrog (<a href="https://autofrog.kr">autofrog.kr</a>) · owned by <a href="https://github.com/d-onworks">d-onworks</a> · MIT</sub>
</p>

<p align="center">
  <img src="docs/demo/img/grid.png" width="100%" alt="Four projects on one screen, each pane a Claude Code or Codex session, shared HUD at the bottom">
</p>

<p align="center"><sub>Screenshots are taken from the demo page in <code>docs/demo</code> with headless Chromium. The UI code is the real thing; only the text inside the terminals is sample data.</sub></p>

---

## What it is

On your Mac, every project folder gets one tmux session, and Claude Code or Codex runs inside it. frogterminal puts all of those sessions on **one browser screen**. At the desk, on the subway with your phone, or at a Windows PC at home, the same screen comes up.

**The host is macOS only** (it leans on tmux, `open`, `mdfind` and launchd). The connecting side needs nothing but a browser.

```
                     ┌──────────────── Tailscale (your tailnet) ────────────────┐
 main Mac  ◀──────────┤  Windows PC · iPhone · Android · iPad · another Mac      │
 (bridge + tmux)      └──────────────── a browser is all you need ─────────────┘
```

## What makes it different

There are several tools in this category in 2026. This is the spot frogterminal picked.

| | frogterminal |
|---|---|
| **Sessions survive** | Terminals live inside tmux. Your phone sleeps, Wi-Fi changes, the agent keeps working, and you reopen exactly where you were. |
| **Light** | The bridge is a single Python process. With 15 sessions attached it used about 40 MB of resident memory (Mac Studio, measured Oct 2026). The UI is one browser tab. |
| **Many projects, one screen** | 1 / 4 / 6 / 8 pane grid. Double-click a pane to enlarge it, double-click again to go back. |
| **Shared HUD at the bottom** | With four panes open, the account-usage line appears once at the bottom instead of once per pane, so the terminals get the space. |
| **Stop all · Restore** | One button stops every running session; Restore brings back exactly that list. For reboots and end of day. |
| **Pick Claude or Codex per project** | Each project opens with "Start with Claude" or "Start with Codex". You can open the same project with both side by side. |
| **Approval inbox** | Every moment an agent stops to ask becomes a card. Tap an option on your phone and the answer goes into that terminal (Claude Code hooks). |
| **CPU popup** | The [CPU] button opens htop in a floating window, so you can see whether the Mac is coping when several agents run at once. |
| **Korean input (IME)** | Hangul composition from phone soft keyboards reaches the terminal intact. A special-key bar (Esc, Tab, arrows, Ctrl) is included. |
| **Conservative security defaults** | The bridge binds to 127.0.0.1 only. Two layers: Tailscale identity + token cookie. If the port is exposed to the public internet (Funnel), it refuses to start. |

<table>
<tr>
<td width="50%"><img src="docs/demo/img/inbox.png" alt="Approval inbox — answer the agent's question with card buttons"></td>
<td width="50%"><img src="docs/demo/img/cpu.png" alt="CPU popup — htop in a floating window"></td>
</tr>
<tr>
<td align="center"><sub>Approval inbox — everything that stopped to ask, in one place</sub></td>
<td align="center"><sub>CPU popup — is the Mac coping?</sub></td>
</tr>
</table>

<p align="center">
  <img src="docs/demo/img/phone.png" width="300" alt="Phone view — approval card, terminal and special-key bar">
  <br><sub>On a phone: one pane. Approval card → terminal → key bar on one screen</sub>
</p>

### Compared with similar tools

| Tool | Shape | How frogterminal differs |
|---|---|---|
| [Orca](https://github.com/stablyai/orca) | Desktop app (macOS, Windows, Linux) with a phone companion. Fans one prompt across several agents in separate worktrees | A workbench for running a fleet of agents. frogterminal is the "no app, just a browser onto tmux" side |
| [herdr](https://github.com/herdrdev/herdr) | Terminal multiplexer (runtime) for coding agents, with a native Mac console, [herdrm](https://github.com/lcandy2/herdrm). Agents on several machines in one sidebar | Strong at multi-machine oversight. frogterminal focuses on one Mac plus phone and Windows access |
| [VibeTunnel](https://github.com/amantus-ai/vibetunnel) | Mac menu-bar app, browser terminal, Tailscale integration | The closest neighbour. frogterminal adds the grid, approval inbox, Korean IME and shared HUD, and is one Python process instead of a menu-bar app |
| ttyd · Wetty | General web terminals | No agent-oriented features (approvals, grid, HUD) |

Descriptions are based on each project's public docs as of Oct 2026. Open an issue if something is wrong.

## Install (Mac)

Requirements: macOS, Python 3.12+, `tmux` (`brew install tmux`). Recommended: [Tailscale](https://tailscale.com). Optional: Claude Code / Codex CLI.

```bash
git clone https://github.com/d-onworks/frogterminal.git && cd frogterminal
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Machine config (optional) — allowed logins, origins, open-anywhere, HUD. Without it: token-only auth.
mkdir -p ~/.frogterminal && cp bridge.example.json ~/.frogterminal/bridge.json && $EDITOR ~/.frogterminal/bridge.json

# Run at login (launchd) — fills the template with this repo/home path, installs and starts it
scripts/install_launchd.sh
cat ~/.frogterminal/token          # access token generated on first start
```

To run by hand: `.venv/bin/python -m bridge.server`.

### Reach it from outside — Tailscale Serve (recommended)

```bash
tailscale serve --bg --https=443 http://127.0.0.1:7788
```

Then put your Tailscale login (email) in `allowedLogins` and `https://<this Mac's MagicDNS name>` in `allowedOrigins` in `~/.frogterminal/bridge.json`, and restart. That gives you Tailscale identity + token, two layers.

### From a phone or a Windows PC

1. Install the Tailscale app on that device and sign in with the same account as the Mac.
2. Open `https://<your Mac's MagicDNS name>` in a browser.
3. Enter the token once (`cat ~/.frogterminal/token`). Each device remembers it for a year. Opening `https://<address>/?token=<token>` works too.
4. On a phone, "Add to Home Screen" makes it feel like an app (the PWA has separate storage, so enter the token once more there).

Away from home (LTE, other Wi-Fi) the same address works as long as Tailscale is on. The bridge refuses to be exposed to the public internet via Funnel.

## Configuration — `~/.frogterminal/bridge.json`

| Key | Default | Meaning |
|---|---|---|
| `allowedLogins` | `[]` | Tailscale logins. Empty = the identity header is not checked (token only) |
| `allowedOrigins` | `[]` | Fixed allowed Origins. Same-origin as the connected Host is always allowed |
| `tailscaleBin` | `/Applications/Tailscale.app/Contents/MacOS/Tailscale` | CLI used for the Funnel self-check |
| `openAnywhere` | `false` | `true` allows "Open on Mac" for any absolute path printed in a terminal |
| `hudScript` | `null` | Script that prints an account-usage line under the grid (Claude Code statusline compatible) |
| `defaultProjectsRoot` | `~/projects` | Default project folder until you set one in the settings panel |

A broken file does not silently fall back to defaults; the bridge refuses to start. The ⚙ settings in the UI (agent, bypass, resume, aliases, projects root) live separately in `~/.frogterminal/settings.json`.

## Claude Code approval inbox (optional)

When Claude Code stops to ask, a card appears; answering on the card types into that terminal. It needs six hooks in your global `~/.claude/settings.json`.

```bash
python3 hooks/install_hooks.py --dry-run    # show what would change
python3 hooks/install_hooks.py              # register (backs up settings.json.bak once)
python3 hooks/install_hooks.py --uninstall  # remove only what we added
```

## Security model — please read

This program is **a remote shell running with your user's privileges**. Anyone who gets in can do everything you can do in a terminal.

- The bridge binds to 127.0.0.1 only (no option to change it). The proxy that exposes it is your responsibility.
- Two auth layers: ① if `allowedLogins` is set, the `Tailscale-User-Login` header (added by Tailscale Serve); ② a cookie equal to `~/.frogterminal/token` (always). Without Tailscale, behind nginx etc., ① is a header anyone can forge, which is why ② exists.
- Token login locks after 10 failures in 10 minutes. The cookie is HttpOnly, SameSite=Lax and Secure over https.
- The file API answers 404 for anything outside the projects root and for `.env`, `.git`, keys, `auth.json`, `*token.json` and the like. Absolute paths for "Open on Mac" require `openAnywhere`.
- Every state change is appended to `<projectsRoot>/_uploads/frogterminal-audit.log` (not readable through the web file API).
- Single-user by design. Two logins in `allowedLogins` see each other's sessions and files.
- The default `bypass: true` starts Claude with `--dangerously-skip-permissions` and Codex with `--yolo`. You can turn it off in settings.

Vulnerability reports: `SECURITY.md`.

## Demo page

Open `docs/demo/index.html` in a browser and the real UI comes up with no server. The UI code (`web/*.js`) is the real one; only the bridge calls are answered by `docs/demo/demo-shim.js` with canned data. The screenshots above were taken from it.

```bash
python3 docs/demo/build_demo.py                 # web/index.html → docs/demo/index.html
.venv/bin/python docs/demo/render.py            # 5 screenshots → docs/demo/img/ (needs playwright)
```

## Development and tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest bridge/tests -q     # bridge (uses real tmux — only creates and removes frogtest-* sessions)
.venv/bin/playwright install chromium
.venv/bin/python -m pytest tests -q            # web (fake bridge + headless Chromium, several minutes)
```

- `web/*.js` is served straight from disk, so edits are live on save. `bridge/*.py` needs a restart (`scripts/restart.sh`).
- A new static file must be added in two places: `bridge/server.py STATIC_FILES` and `tests/conftest.py STATIC`.

## Who made it

frogterminal is made by [Autofrog](https://autofrog.kr) and owned by [d-onworks](https://github.com/d-onworks). It was built by a one-person company running several AI agents at once, so that nobody has to sit in front of the Mac. MIT licensed. The frog character belongs to Autofrog and is not covered by the code license.
