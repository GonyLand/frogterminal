#!/usr/bin/env python3
"""데모 페이지를 헤드리스 크롬으로 열어 README 용 스크린샷을 찍는다.

    python3 docs/demo/build_demo.py && python3 docs/demo/render.py
    → docs/demo/img/{grid,cpu,inbox,phone,login}.png

실제 브릿지·tmux·설정은 전혀 건드리지 않는다(가짜 브릿지는 demo-shim.js 가 브라우저 안에서 돈다).
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "docs" / "demo"
IMG = DEMO / "img"
INDEX = (DEMO / "index.html").as_uri()
LOGIN = (ROOT / "web" / "login.html").as_uri()

SCENES = [                                      # (파일, 해시, 뷰포트, 설명)
    ("grid",  "",       (1440, 900), "격자 4칸 + 하단 공통 HUD"),
    ("cpu",   "#cpu",   (1440, 900), "CPU(htop) 팝업"),
    ("inbox", "#inbox", (1440, 900), "결재 인박스"),
    ("phone", "#phone", (390, 844),  "폰 1칸"),
]


def shoot(page, name, url, viewport, phone=False):
    page.set_viewport_size({"width": viewport[0], "height": viewport[1]})
    page.goto("about:blank")                     # 해시만 다른 주소는 같은 문서로 쳐서 새로 읽지 않는다 — 먼저 비운다
    page.goto(url)
    page.wait_for_function("document.body.dataset.demoReady === '1'", timeout=15000)
    page.wait_for_timeout(900)                  # xterm 렌더·글꼴 로드 여유
    out = IMG / f"{name}.png"
    page.screenshot(path=str(out))
    print(f"  {out.relative_to(ROOT)}  {viewport[0]}x{viewport[1]}")


def main() -> int:
    IMG.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(device_scale_factor=2, locale="ko-KR",
                                  color_scheme="dark")
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        for name, frag, vp, _ in SCENES:
            if name == "phone":
                ctx2 = browser.new_context(device_scale_factor=3, locale="ko-KR", has_touch=True,
                                           is_mobile=True, color_scheme="dark",
                                           viewport={"width": vp[0], "height": vp[1]})
                p2 = ctx2.new_page()
                p2.on("pageerror", lambda e: errors.append(str(e)))
                shoot(p2, name, INDEX + frag, vp, phone=True)
                ctx2.close()
            else:
                shoot(page, name, INDEX + frag, vp)
        # 로그인 화면 — 정적 파일 그대로({{error}} 자리는 비운다)
        html = (ROOT / "web" / "login.html").read_text(encoding="utf-8").replace("{{error}}", "")
        page.set_viewport_size({"width": 1440, "height": 900})
        page.set_content(html)
        page.wait_for_timeout(300)
        page.screenshot(path=str(IMG / "login.png"))
        print(f"  {(IMG / 'login.png').relative_to(ROOT)}  1440x900")
        browser.close()
        if errors:
            print("page errors:", *errors, sep="\n  ")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
