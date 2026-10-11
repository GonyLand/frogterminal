# 🐸 frogterminal

맥에서 돌아가는 **tmux 세션을 폰·노트북 브라우저에서 그대로 쓰는 웹 터미널**입니다. 프로젝트 폴더마다 tmux 세션 하나, 그 안에서 Claude Code·Codex 같은 AI 코딩 에이전트를 띄워 놓고 어디서든 이어서 봅니다.

- 여러 칸(1·4·6·8분할)으로 프로젝트 여러 개를 한 화면에
- 에이전트가 물어보고 멈춘 것들을 모아 보는 **결재 인박스**(Claude Code 훅 연동, 선택)
- 프로젝트 파일 보기·편집, 드래그 업로드, 사진 붙이기
- 폰 소프트 키보드용 특수키 바, 한글 입력(IME) 대응
- 브릿지는 **127.0.0.1 에만** 뜨고, 밖으로 내는 길은 사용자가 고른다(권장: Tailscale Serve)

English summary at the bottom.

## 1. 구조

```
브라우저 ──https──▶ Tailscale Serve(권장) 또는 다른 리버스 프록시
                        └─▶ 127.0.0.1:7788  브릿지(Python aiohttp)
                                └─▶ tmux 세션(프로젝트 폴더마다 1개) → claude · codex · 셸
```

화면(HTML·JS)은 브릿지가 직접 내줍니다. 외부 CDN 은 xterm.js 와 글꼴 둘뿐이고 전부 무결성 해시(SRI)가 걸려 있습니다.

## 2. 필요한 것

- macOS (tmux·`open`·`mdfind`·launchd 를 쓴다 — 리눅스는 아직 아님)
- Python 3.12 이상, `tmux` (`brew install tmux`)
- 권장: [Tailscale](https://tailscale.com) — 테일넷 안에서만 열고, 접속자 신원을 헤더로 받는다
- 선택: Claude Code / Codex CLI (없어도 셸 세션은 된다)

## 3. 설치

```bash
git clone <this-repo> frogterminal && cd frogterminal
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 기기 설정(선택) — 허용 계정·Origin·열기 범위·HUD. 없으면 토큰만으로 인증한다.
mkdir -p ~/.frogterminal && cp bridge.example.json ~/.frogterminal/bridge.json && $EDITOR ~/.frogterminal/bridge.json

# 상시 실행(launchd) — 템플릿의 경로를 이 저장소·이 홈으로 채워 설치하고 바로 띄운다
scripts/install_launchd.sh
cat ~/.frogterminal/token          # 첫 기동 때 만들어진 접속 토큰
```

손으로 띄우려면 `.venv/bin/python -m bridge.server`.

### Tailscale Serve 로 열기(권장)

```bash
tailscale serve --bg --https=443 http://127.0.0.1:7788
```

그리고 `~/.frogterminal/bridge.json` 에 `allowedLogins` 에 내 Tailscale 로그인(이메일)을, `allowedOrigins` 에 `https://<이 맥의 MagicDNS 이름>` 을 적고 재시작합니다. 이러면 **Tailscale 신원 + 토큰** 두 겹입니다. Funnel(공개 인터넷) 로 7788 을 내보내면 브릿지가 기동을 거부합니다.

### 처음 접속

브라우저에서 브릿지 주소를 열면 **토큰을 한 번 묻습니다**(기기마다 1년 기억). `cat ~/.frogterminal/token` 값을 붙이거나 `https://<주소>/?token=<토큰>` 으로 엽니다. 토큰을 바꾸려면 파일을 지우고 재시작하면 새로 만들어지고 모든 기기가 다시 로그인합니다.

## 4. 설정 — `~/.frogterminal/bridge.json`

| 키 | 기본 | 뜻 |
|---|---|---|
| `allowedLogins` | `[]` | Tailscale 로그인 목록. 비어 있으면 신원 헤더를 보지 않는다(토큰만) |
| `allowedOrigins` | `[]` | 고정 허용 Origin. 접속한 Host 와 같은 출처는 늘 허용 |
| `tailscaleBin` | `/Applications/Tailscale.app/Contents/MacOS/Tailscale` | Funnel 자가점검에 쓰는 CLI |
| `openAnywhere` | `false` | `true` 면 터미널에 찍힌 **절대경로 어디든** "맥에서 열기" 허용 |
| `hudScript` | `null` | 여러 칸일 때 하단에 계정 사용량 줄을 찍는 스크립트(Claude Code statusline 과 호환) |
| `defaultProjectsRoot` | `~/projects` | 설정 화면에서 루트를 정하기 전의 기본 프로젝트 폴더 |

파일이 깨져 있으면 조용히 기본값으로 가지 않고 **기동을 거부**합니다. 화면의 ⚙ 설정(에이전트·bypass·이어서 시작·별칭·프로젝트 루트)은 `~/.frogterminal/settings.json` 에 따로 저장됩니다.

## 5. Claude Code 결재 인박스(선택)

Claude Code 가 질문하고 멈추면 카드가 뜨고, 카드에서 답하면 그 터미널에 입력됩니다. 전역 `~/.claude/settings.json` 에 훅 6개를 넣어야 합니다.

```bash
python3 hooks/install_hooks.py --dry-run    # 무엇을 바꿀지 먼저 본다
python3 hooks/install_hooks.py              # 등록(첫 변경 전 settings.json.bak 1회 백업)
python3 hooks/install_hooks.py --uninstall  # 우리가 넣은 것만 뺀다
```

## 6. 보안 모델 — 꼭 읽을 것

이 프로그램은 **당신 계정 권한으로 돌아가는 원격 셸**입니다. 들어온 사람은 당신이 터미널에서 할 수 있는 모든 것을 할 수 있습니다.

- 브릿지는 127.0.0.1 에만 바인드한다(바꾸는 옵션 없음). 밖으로 내는 프록시는 당신 책임이다.
- 인증은 두 겹: ①`allowedLogins` 가 있으면 `Tailscale-User-Login` 헤더(Tailscale Serve 가 붙임) ②`~/.frogterminal/token` 과 같은 쿠키(항상). **Tailscale 없이 nginx 등으로 내면 ①은 아무나 쓸 수 있는 헤더다** — 그래서 ②가 있다.
- 토큰 로그인은 10분에 10회 실패하면 잠긴다. 쿠키는 HttpOnly·SameSite=Lax·(https 면) Secure.
- 파일 API 는 프로젝트 루트 밖·`.env`·`.git`·키·`auth.json`·`*token.json` 류를 404 로 거절한다. "맥에서 열기"의 절대경로는 `openAnywhere` 를 켠 사람만.
- 모든 상태 변경은 `<projectsRoot>/_uploads/frogterminal-audit.log` 에 남는다(웹 파일 API 로는 못 읽는다).
- 단일 사용자 설계다. `allowedLogins` 에 두 명을 넣으면 서로의 세션·파일을 전부 본다.
- 기본값 `bypass: true` 는 Claude 를 `--dangerously-skip-permissions`, Codex 를 `--yolo` 로 띄운다. 설정에서 끌 수 있다.

취약점 신고는 `SECURITY.md`.

## 7. 개발·테스트

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest bridge/tests -q     # 브릿지(실제 tmux 를 쓴다 — frogtest- 세션만 만들고 지운다)
.venv/bin/playwright install chromium
.venv/bin/python -m pytest tests -q            # 웹(가짜 브릿지 + 헤드리스 크롬, 수 분)
```

- `web/*.js` 는 브릿지가 파일을 직접 내주므로 저장하는 즉시 반영된다. `bridge/*.py` 는 재시작(`scripts/restart.sh` — 재시작 전 프리플라이트가 "지금 내리면 다시 뜨는가"를 본다).
- 새 정적 파일은 `bridge/server.py STATIC_FILES` 와 `tests/conftest.py STATIC` 두 곳에 넣는다.

## License

MIT — `LICENSE`.

---

## English

**frogterminal** is a web terminal for macOS that exposes your tmux sessions (one per project folder) to a phone or laptop browser, built for keeping AI coding agents (Claude Code, Codex) running and answerable from anywhere.

- The bridge (Python aiohttp) binds to **127.0.0.1 only**; you choose how to expose it (Tailscale Serve recommended; Funnel to the public internet is refused at startup).
- Two authentication layers: an optional Tailscale identity header (when `allowedLogins` is set in `~/.frogterminal/bridge.json`) and a mandatory token cookie (`~/.frogterminal/token`, generated on first start). Ten failed logins in ten minutes lock the login for the window.
- Setup: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`, copy `bridge.example.json` to `~/.frogterminal/bridge.json`, run `scripts/install_launchd.sh`, then open the bridge URL and paste the token once per device.
- This is a remote shell running with your user's privileges. Read section 6 before exposing it. Single-user by design.
- Tests: `pytest bridge/tests` (uses real tmux) and `pytest tests` (Playwright). MIT licensed.
