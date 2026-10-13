# frogterminal — 윈도우판

윈도우 PC 한 대에서 돌리는 Claude Code·Codex 를 **갤럭시 폰(안드로이드)·다른 PC 브라우저**에서 그대로 이어 쓴다.
맥판(`README.md`)과 같은 화면·같은 웹 코드를 쓰고, 서버만 윈도우로 옮겼다.

```
 이 PC(브릿지 + ConPTY 세션) ◀── Tailscale(테일넷) ──▶ 갤럭시 · 다른 PC · 아이폰 … 브라우저만 있으면 됨
```

## 맥판과 다른 점

| | 맥판 | 윈도우판 |
|---|---|---|
| 세션 호스트 | tmux (서버가 따로 살아 있다) | **브릿지 프로세스 안의 ConPTY**(`bridge/winmux.py`) |
| 세션 수명 | 브릿지를 재시작해도 산다 | 폰 절전·탭 닫기·와이파이 전환에는 **산다**. 브릿지 재시작·PC 재부팅에는 **끝난다**(이어서 시작/`/resume`) |
| 다시 붙을 때 | tmux 가 화면을 다시 그린다 | pyte 화면 모델이 보이는 화면(+스크롤백)을 ANSI 로 다시 그린다. 대체 화면(codex·vim)도 따로 보관 |
| 스크롤 | tmux 복사 모드 | 브라우저 xterm 스크롤백(폰 스와이프 그대로) |
| 셸 | zsh -lic | **pwsh**(PowerShell 7) — 프로파일을 그대로 탄다 |
| "맥에서 열기" | `open` | 이 PC 의 기본 앱(`.exe`·`.bat`·`.ps1` 같은 실행 파일은 거부) · 탐색기에서 위치 보기 |
| CPU 팝업 | htop | `bridge/monitor_win.py`(psutil) — q 로 닫기 |
| 상시 실행 | launchd | 작업 스케줄러(로그온 때 시작, 죽으면 5초 뒤 재시작) |
| 드래그 원본 찾기 | Spotlight | 없음 → 사본 업로드로 물러선다 |
| 별칭 설정 | `~/.zshrc` 블록 | 셸 파일을 건드리지 않는다(값만 저장, 실제 명령 `claude`/`codex` 로 띄움) |

## 설치

필요한 것: Windows 10/11 · Python 3.12+ · PowerShell 7(`pwsh`) · [Tailscale](https://tailscale.com)(PC·폰 둘 다, 같은 계정) · Claude Code / Codex CLI.

```powershell
git clone <이 저장소> Y:\frogterminal\src ; cd Y:\frogterminal\src
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`C:\Users\<나>\.frogterminal\bridge.json` (경로는 `/` 로 적는다):

```json
{
  "allowedLogins": ["내-테일스케일-로그인@gmail.com"],
  "allowedOrigins": ["https://<이 PC 의 MagicDNS 이름>.ts.net"],
  "tailscaleBin": "C:/Program Files/Tailscale/tailscale.exe",
  "defaultProjectsRoot": "Y:/"
}
```

- `defaultProjectsRoot` 가 프로젝트 목록의 뿌리다(그 아래 `.git`·`CLAUDE.md`·`AGENTS.md` 가 있는 폴더가 프로젝트). 홈 밖(`Y:\`)도 여기서 정한 값은 허용한다.
- MagicDNS 이름은 `tailscale status --json` 의 `Self.DNSName`(끝 `.` 뺀 것).

`C:\Users\<나>\.frogterminal\settings.json` — 처음 쓰기 전에 정할 것(없으면 맥판 기본값이 적용된다):

```json
{"agent": "claude", "bypass": false, "cont": false, "rc": false, "commands": {"claude": "", "codex": ""}}
```

- `bypass`: 기본 `true` 는 `--dangerously-skip-permissions`/`--yolo` 로 띄운다. 평소 권한 모드(auto 등)를 쓰면 `false` 로 둔다.
- `cont`: 기본 `true` 는 `--continue`(그 폴더의 가장 최근 대화)다. **같은 폴더를 여러 창에서 쓰면 서로 대화를 가로채니** `false` 권장.

상시 실행 + 테일넷 HTTPS:

```powershell
pwsh -File scripts\windows\install-task.ps1            # 작업 스케줄러 등록·시작(로그온 때 자동)
tailscale serve --bg --https=443 http://127.0.0.1:7788 # 테일넷 안에서만 열린다(Funnel 아님). 끄기: tailscale serve --https=443 off
```

## 폰에서 쓰기

1. 폰에 Tailscale 앱을 깔고 PC 와 **같은 계정**으로 로그인해 켜 둔다.
2. PC 에서 `.\.venv\Scripts\python.exe scripts\windows\login-qr.py` — QR 이미지가 열린다. 폰 카메라로 찍으면 로그인된다(쿠키 1년).
   토큰을 새로 갈려면 `--rotate`(브릿지가 재시작돼 열린 세션이 끝난다). QR 은 토큰이 든 비밀이니 찍은 뒤 `~/.frogterminal/login-qr.png` 를 지운다.
3. 크롬 메뉴 → "설치 및 바로가기 만들기"로 앱처럼 쓴다. (`web/index.html` 의 manifest 링크에 `crossorigin="use-credentials"` 를 붙여 뒀다 — 없으면 크롬이 쿠키 없이 `/manifest.json` 을 받아 403 이 나고, 메뉴가 일반 바로가기로만 나온다.)

## PC 의 PowerShell 창에서 붙기 — `frog`

맥판은 tmux 라서 PC 터미널과 폰이 같은 세션에 같이 붙는다. 윈도우판은 브릿지가 세션을 들고 있으므로, PC 쪽도 `frog` 로 붙어야 폰과 같은 세션을 본다.
**PowerShell 에서 그냥 `claude` 를 띄운 창은 폰에서 볼 수 없다**(ConPTY 는 만든 프로세스만 읽는다). 앞으로의 작업을 `frog` 로 시작하면 PC·폰이 한 세션을 같이 쓴다.

**창 하나 = 세션 하나.** 같은 프로젝트에서 `frog` 를 또 띄우면 새 독립 세션 `<프로젝트>@2` … `@9`(웹의 ＋ 탭과 같은 것)가 열린다.

```powershell
frog                  # 지금 폴더(프로젝트 루트 아래)에 새 창 — 떠 있는 세션이 없으면 기본 세션(claude 는 마지막 대화를 잇는다),
                      #   있으면 빈 번호 @2~@9 의 새 대화. 아무 화면도 안 붙은 세션이 남아 있으면 먼저 붙을지 묻는다(Enter = 새 창)
frog weather          # 프로젝트 이름으로(같은 규칙)
frog weather codex    # claude · codex · shell
frog -a               # 떠 있는 세션을 골라 붙는다 — 폰에서 연 세션 이어 쓰기 · 창을 닫아 남은 세션 되찾기
frog weather@2        # 그 세션에 바로 붙는다
frog -l               # 살아 있는 세션 목록(붙은 화면 수 · 시작 시각)
```

- 떼기: **Ctrl+]** — 세션은 산다(폰·`frog -a` 로 이어 본다). 창을 X 로 닫아도 같다. 안에서 `exit` 하면 세션이 끝난다.
- 창 제목은 `<세션> · <앱이 정한 제목>` — 탭만 보고 어느 창이 어느 세션인지 안다.
- 다른 세션이 떠 있을 때 기본 세션 자리를 쓰지 않는 이유: 기본 세션의 claude 는 `--continue` 로 시작해 지금 다른 창이 쓰는 대화를 또 연다.
- 같은 세션에 PC 창과 폰(또는 `frog -a` 창 둘)이 같이 붙으면 화면 크기는 **가장 작은 쪽**에 맞는다(tmux 와 같다). 한쪽이 떠나면 남은 크기로 돌아온다.
- PC 창엔 스크롤백이 터미널 창 것을 쓰므로 붙을 때 보이는 화면만 받는다(웹은 기록까지).
- 설치: `scripts\windows\frog.cmd` 를 PATH 의 폴더에 두거나(절대경로로 고쳐 복사), `function frog { & 'Y:\...\scripts\windows\frog.cmd' @args }` 를 프로필에.
- 이미 떠 있는 창의 대화를 옮기려면 그 창을 닫고 `frog` 로 열어 `/resume` 한다.

## 폰에서 PC 의 클로드 창 보기 · 결재/완료 알림

`frog` 로 연 게 아니라 Windows Terminal·VS Code 에 이미 떠 있는 클로드 창도 폰 목록 위쪽(**PC 클로드 창**)에 작업중·결재·완료로 나온다(위 「결재 인박스」 훅이 필요).
창을 누르면 그 창의 화면이 뜬다 — 크기는 PC 창 것으로 고정이고, 글자 입력은 **입력 켜기** 를 눌러야 들어간다(기본은 잠금). 목록에 있는 창에만 붙는다.
색은 16색표로 내려오고(24비트 색 손실) 보이는 영역만 온다(스크롤백 없음).
팝업 머리의 **폭맞춤 / 크게** 는 글자 크기 토글이다 — 폭맞춤(기본)은 PC 창 폭 전체를 폰 한 화면에 맞추고(120열이면 6px 안팎), 크게는 11px 로 키우고 옆으로 민다(기기마다 기억).
한글 칸 폭은 나눔고딕코딩(웹폰트, jsDelivr)으로 맞춘다 — 폰엔 D2Coding 이 없어 기본 폴백 글꼴로는 한글 자간이 벌어졌다.

**알림**: 같은 칸의 **🔕 알림 켜기** 를 폰에서 한 번 누르고 허용하면, 창이 결재를 기다리거나 끝날 때(전부 끝나면 한 통) 화면이 꺼져 있어도 웹푸시가 온다. **시험** 으로 바로 확인.
HTTPS(테일넷 serve)에서만 켤 수 있다. 구독은 `~/.frogterminal/push-subs.json`, 서명 키는 `vapid.pem`(지우면 구독이 모두 무효 — 폰에서 끄고 다시 켠다).
푸시 서비스 호출은 윈도우 인증서 저장소를 믿는다 — Avast 등 HTTPS 검사 프로그램이 있으면 파이썬 기본 번들(certifi)은 `CERTIFICATE_VERIFY_FAILED` 로 막힌다.

## 운영

- 로그: `~/.frogterminal/bridge.log` · 재시작: `pwsh -File scripts\windows\restart.ps1`(안의 세션도 끝난다) · 내리기: `... restart.ps1 -Stop`
- 등록 해제: `pwsh -File scripts\windows\install-task.ps1 -Uninstall`
- 감사 로그: `<프로젝트 루트>\_uploads\frogterminal-audit.log`
- 이 PC 가 잠들면 폰이 못 붙는다 — 전원 옵션의 "절전"을 끄거나 길게 잡는다.

## 보안

원격 셸이다 — 이 PC 계정 권한 그대로. 인증은 두 겹(Tailscale 신원 헤더 + 토큰 쿠키)이고 브릿지는 `127.0.0.1` 에만 뜬다. `~/.frogterminal` 은 사용자 프로필 ACL(본인·관리자·SYSTEM)을 상속한다.
Funnel(공개 인터넷)로 열려 있으면 기동을 거부한다. 나머지는 맥판 `README.md` 의 「보안 모델」 그대로.

## 결재 인박스(선택)

Claude Code 훅 6개(Notification · Stop · UserPromptSubmit · SessionEnd · PreCompact · SessionStart)를 `~/.claude/settings.json` 에 넣는다.
훅은 상태 카드(`<프로젝트 루트>\_frogterminal\decisions\*.json`)를 쓰기만 하고 Claude 를 막지 않는다. 브릿지가 연 세션(`FROGTERMINAL_SESSION`)의 카드만 화면에 올리고,
다른 창의 카드는 `inTmux:false` 로 걸러진다. 단 **전역 설정을 바꾸므로 자동으로 하지 않는다.** 먼저 미리보기:

```powershell
.\.venv\Scripts\python.exe hooks\install_hooks.py --dry-run
.\.venv\Scripts\python.exe hooks\install_hooks.py            # 등록(첫 변경 전 settings.json.bak 1회)
.\.venv\Scripts\python.exe hooks\install_hooks.py --uninstall
```

## 알려진 한계

- 브릿지 재시작·PC 재부팅 때 세션이 끝난다(위 표). 필요하면 [복원] 버튼 + 이어서 시작.
- 실기기(갤럭시 S20 · 크롬 · 삼성 천지인 키보드)에서 확인한 것: 쿠키 로그인 · 프로젝트 목록 · Claude 띄우기 · 한글 입력(조합·재조합·백스페이스) · 탭을 닫았다 다시 열어도 로그인 유지 · 메뉴의 "설치 및 바로가기 만들기".
  PC 크롬에서는 새로고침 뒤 화면 복원도 확인했다. 서버 쪽 실측: 세션 4개(Claude 1 + 셸 3, 탭 `name@2` 포함) 동시 · 출력 섞임 0 · 훅 카드(`done`, `inTmux:true`) · RC(`rc:true` 로 띄우면 `/remote-control is active`).
  codex 세션 · 재부팅 뒤 자동 기동은 **실측하지 않았다**.
- 동시에 여러 개: 프로젝트마다 세션 하나 + 같은 프로젝트의 병렬 탭(`name@2~9`). 측정값은 Claude 1 + 셸 3 에서 브릿지 프로세스 트리가 약 +745 MB — Claude 하나당 약 0.6~0.7 GB 로 잡는다.
  폰 폭(900px 미만)은 한 번에 한 칸이고, 넓은 화면(PC·태블릿)은 격자로 여러 칸이 보인다.
- 이미 떠 있는 다른 창(Windows Terminal·VS Code 의 Claude)은 브릿지가 붙잡을 수 없다 — ConPTY 는 만든 프로세스만 읽는다. 앞으로는 `frog` 로 열고(위), 기존 대화는 `frog` 세션에서 `/resume` 한 뒤 원래 창을 닫는다(같은 대화를 두 곳에서 동시에 쓰지 않는다).
- 폰에서 스와이프하면 Claude Code 같은 TUI 가 마우스 모드를 켜 둔 동안은 휠 이벤트가 그 앱으로 간다(브라우저 스크롤백이 안 움직인다). tmux 복사 모드 스크롤은 없다.
- 확인 박스(「클로드로 켜기」)는 7초 폴링 때 사라진다(원본 동작) — 손이 느리면 다시 누른다.
- 브라우저 패널(헤드리스 크롬)·미리보기 서비스 목록의 윈도우 분기는 코드만 옮겼고 실서버로는 안 돌려 봤다.
- 마우스 휠을 쓰는 TUI(코덱스 등)가 마우스 모드를 켜면 스크롤이 그 앱으로 간다 — 일반 xterm 과 같다.

## 시험

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest bridge/tests -q   # 윈도우에서 건너뛰는 시험(맥·tmux 전제)은 bridge/tests/conftest.py WINDOWS_SKIP 에 사유와 함께 있다
```

윈도우 전용 시험은 `bridge/tests/test_winmux.py`(화면 복원 · 대체 화면 · 리사이즈 · 진짜 ConPTY 왕복).
