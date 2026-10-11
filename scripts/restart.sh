#!/bin/bash
# 브릿지 재시작 — 프리플라이트를 통과할 때만 내리고, 다시 뜬 것을 눈으로 확인하고 끝낸다.
# 맨손 `launchctl kickstart` 금지(2026-08-28 사고: 남의 Funnel 설정이 잠복해 있어 3분 끊김).
set -u
cd "$(dirname "$0")/.." || exit 1
LOG="$HOME/Library/Logs/frogterminal.err.log"
PORT=7788

.venv/bin/python -m bridge.preflight || exit 1

echo "· 재시작한다…"
launchctl kickstart -k "gui/$(id -u)/com.frogterminal.bridge" || exit 1

# ★ http 로 잰다. https 로 재면 TLS 가 없어 000 만 나와 '아직 안 떴다'로 오독한다(실측).
for _ in $(seq 1 40); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 2 "http://127.0.0.1:$PORT/api/summary")
  if [ "$code" != "000" ]; then
    echo "✓ 복구 확인 — HTTP $code (403 = 정상 인증 관문, 테일넷 신원 헤더가 없어서다)"
    exit 0
  fi
  sleep 1
done

echo "✗ 40초 안에 안 떴다. 로그 꼬리:"
tail -5 "$LOG"
exit 1
