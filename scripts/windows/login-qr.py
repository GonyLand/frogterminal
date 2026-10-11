"""폰 첫 로그인용 QR — 토큰을 채팅·로그에 찍지 않고, QR 이미지 한 장으로만 넘긴다.

  .venv\\Scripts\\python.exe scripts\\windows\\login-qr.py            지금 토큰으로 QR 을 만들어 연다
  .venv\\Scripts\\python.exe scripts\\windows\\login-qr.py --rotate   토큰을 새로 갈고(브릿지 재시작) QR 을 만든다

폰 카메라로 찍으면 https://<이 PC 의 MagicDNS 이름>/?token=… 이 열리고, 로그인 쿠키가 기기에 1년 남는다.
QR 이미지는 토큰이 든 비밀이다 — 폰에서 찍은 뒤 지운다(이 스크립트가 만든 파일은 ~/.frogterminal/login-qr.png).
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import segno

APP = Path.home() / ".frogterminal"
TOKEN_FILE = APP / "token"
PNG = APP / "login-qr.png"
REPO = Path(__file__).resolve().parents[2]


def tailscale_exe() -> str:
    cfg = APP / "bridge.json"
    try:
        found = json.loads(cfg.read_text(encoding="utf-8")).get("tailscaleBin")
        if found:
            return found
    except (OSError, ValueError):
        pass
    return r"C:\Program Files\Tailscale\tailscale.exe"


def magic_dns() -> str:
    out = subprocess.run([tailscale_exe(), "status", "--json"], capture_output=True, text=True, check=True,
                         timeout=20).stdout
    return json.loads(out)["Self"]["DNSName"].rstrip(".")


def rotate() -> None:
    TOKEN_FILE.unlink(missing_ok=True)
    subprocess.run(["pwsh", "-NoProfile", "-File", str(REPO / "scripts" / "windows" / "restart.ps1")], check=False)
    for _ in range(40):
        if TOKEN_FILE.exists():
            return
        time.sleep(0.5)
    sys.exit("새 토큰이 안 만들어졌다 — 브릿지가 떴는지(~/.frogterminal/bridge.log) 확인")


def main() -> None:
    if "--rotate" in sys.argv:
        rotate()
    token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    url = f"https://{magic_dns()}/?token={token}"
    segno.make(url, error="m").save(str(PNG), scale=10, border=3)
    try:
        os.chmod(PNG, 0o600)
    except OSError:
        pass
    print(f"QR 저장: {PNG}")
    os.startfile(str(PNG))                                   # noqa: S606 — 우리가 방금 만든 PNG


if __name__ == "__main__":
    main()
