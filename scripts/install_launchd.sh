#!/bin/bash
# launchd 등록 — 템플릿의 __REPO__·__HOME__ 을 이 저장소·이 홈으로 채워 설치하고 바로 띄운다.
#   scripts/install_launchd.sh            # 설치(이미 있으면 교체) 후 기동
#   scripts/install_launchd.sh --remove   # 내리고 plist 삭제
set -eu
cd "$(dirname "$0")/.."
REPO=$(pwd)
LABEL=com.frogterminal.bridge
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ "${1:-}" = "--remove" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$DEST"
  echo "✓ 내렸다 — $DEST 삭제"
  exit 0
fi

[ -x "$REPO/.venv/bin/python" ] || { echo "✗ .venv 가 없다 — python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"; exit 1; }
mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s#__REPO__#$REPO#g" -e "s#__HOME__#$HOME#g" launchd/$LABEL.plist.template > "$DEST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DEST"
echo "✓ 설치·기동 — $DEST"
echo "  토큰: cat ~/.frogterminal/token   (첫 기동 때 생성) · 로그: ~/Library/Logs/frogterminal.err.log"
