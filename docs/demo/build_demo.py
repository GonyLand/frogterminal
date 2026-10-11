#!/usr/bin/env python3
"""web/index.html 에서 데모 페이지(docs/demo/index.html)를 만든다.

진짜 화면 코드(web/*.js, style.css)를 그대로 쓰고, 브릿지 호출(fetch·WebSocket)만 demo-shim.js 가
브라우저 안에서 가짜 응답으로 바꿔친다. 그래서 서버 없이 파일을 열기만 해도 실제 UI 가 뜬다.

    python3 docs/demo/build_demo.py            # docs/demo/index.html 생성
    open docs/demo/index.html                  # 격자 4칸 · #cpu · #inbox · #phone 장면은 render.py 참고
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "web" / "index.html"
DST = ROOT / "docs" / "demo" / "index.html"

html = SRC.read_text(encoding="utf-8")

# 상대 경로 자원 → ../../web/… (CDN 은 그대로)
html = re.sub(r'(href|src)="(?!https?:|//|#|data:)([^"]+)"', r'\1="../../web/\2"', html)

# 가짜 브릿지는 config.js 다음, app.js 앞에 끼운다 — fetch·WebSocket 을 app.js 가 잡기 전에 바꿔친다.
anchor = '<script src="../../web/config.js"></script>'
assert anchor in html, "config.js 스크립트 태그를 못 찾았다 — web/index.html 구조가 바뀌었나?"
html = html.replace(anchor, anchor + '\n<script src="demo-shim.js"></script>', 1)

html = html.replace("<title>", "<title>[demo] ", 1)
banner = ("<!-- 생성 파일 — docs/demo/build_demo.py 가 web/index.html 에서 만든다. 직접 고치지 말 것. -->\n")
html = re.sub(r"(<!doctype html>\s*)", lambda m: m.group(1) + banner, html, count=1, flags=re.I)

DST.write_text(html, encoding="utf-8")
print(f"wrote {DST.relative_to(ROOT)} ({len(html)} bytes)")
