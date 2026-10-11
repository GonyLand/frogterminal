"use strict";
// 브라우저 패널 2단계 — "브라우저" 모드. 맥에서 도는 진짜 크롬(헤드리스, 프로젝트 프로필)의 화면을 캔버스에 그리고
// 클릭·휠·키·한글을 되돌려 보낸다(브릿지 /ws/browser/<project>, 원시 CDP 아님 — 제한 명령만).
// 코덱스 앱·클로드 앱의 "앱 안 브라우저"와 같은 그림: 외부 사이트도 열리고(최상위 페이지라 프레임 거부 없음),
// 뒤로/앞으로/새로고침은 그 탭 기준, 우클릭 메뉴는 우리 것, 에이전트(클로드·플레이라이트)는 같은 크롬에 붙는다.
// 1단계 "미리보기"(iframe, 지연 0)는 개발 서버용으로 남긴다 — browser.js 가 그쪽, 여기는 모드 전환과 원격 뷰어.

const rb = { ws: null, gen: 0, frameMeta: null, drawing: false, pendingBlob: null, state: null,
             resizeTimer: null, retry: 0, moveAt: 0, lastViewport: "", dpr: 1, closed: false };
const rbCanvas = () => document.getElementById("rb-canvas");
const rbWrap = () => document.getElementById("rb-wrap");
const rbInput = () => document.getElementById("rb-input");        // 숨은 입력칸 — 한글 조합은 여기서
const MOD = { alt: 1, ctrl: 2, meta: 4, shift: 8 };                  // CDP modifiers 비트
const VK = { Enter: 13, Backspace: 8, Tab: 9, Escape: 27, ArrowLeft: 37, ArrowUp: 38, ArrowRight: 39, ArrowDown: 40,
             Delete: 46, Home: 36, End: 35, PageUp: 33, PageDown: 34, F5: 116 };

function rbMode() { return bpPanel().dataset.mode || "preview"; }
// start=true 는 "없으면 새로 띄워라" — 사용자가 [브라우저] 를 직접 누를 때만이다. 프로젝트를 갈아타거나 패널을 다시 열어
// 저절로 붙는 경우는 start=false 라, 안 연 프로젝트에 크롬이 서지 않는다(메모리 절약).
function rbSetMode(mode, start = false) {
  bpPanel().dataset.mode = mode;
  document.getElementById("bp-mode-preview").classList.toggle("on", mode === "preview");
  document.getElementById("bp-mode-browser").classList.toggle("on", mode === "browser");
  if (mode === "browser") {
    bp.gen++;                                  // 진행 중인 미리보기 자동 열기(bpOpenService)가 늦게 iframe 을 채우지 않게
    bpFrame().removeAttribute("src"); clearTimeout(bp.hintTimer); bpNote(""); rbConnect(start);
  }
  else rbDisconnect();
  if (bp.project) {
    const saved = bpSaved(bp.project) || {};
    localStorage.setItem(bpKey(bp.project), JSON.stringify({ ...saved, mode }));
  }
}
document.getElementById("bp-mode-preview").onclick = () => rbSetMode("preview");
document.getElementById("bp-mode-browser").onclick = () => rbSetMode("browser", true);   // 손으로 누른 것 = 열어도 된다
// 브라우저 끄기 — 그 프로젝트 크롬을 종료해 메모리를 돌려준다(1.2GB 가량). 다시 누르면 새로 뜬다.
document.getElementById("bp-power").onclick = async () => {
  const project = bp.project;
  if (!project) return;
  rbDisconnect();
  bpNote("브라우저를 끄는 중…");
  try {
    await fetch(`${API}/api/browser/${encodeURIComponent(project)}/stop`, { method: "POST" });
    rbClosedNote(project);
  } catch { bpNote("맥에 연결되지 않아 끄지 못했습니다"); }
};

function rbClosedNote(project) {
  const c = rbCanvas(); c.getContext("2d").clearRect(0, 0, c.width, c.height);
  document.getElementById("bp-tabs").replaceChildren();
  bpUrlBox().value = "";
  bpNote(`'${project}' 브라우저가 꺼져 있습니다 — `, { label: "브라우저 열기", onClick: () => rbConnect(true) });
}

// ---------- 연결 ----------
function rbConnect(start = false) {
  rbDisconnect();
  if (!bp.project) return;
  const project = bp.project;
  const gen = ++rb.gen;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const sock = new WebSocket(`${proto}://${location.host}/ws/browser/${encodeURIComponent(project)}${start ? "?start=1" : ""}`);
  sock.binaryType = "blob";
  rb.ws = sock;
  rb.closed = false;
  { const c = rbCanvas(); c.getContext("2d").clearRect(0, 0, c.width, c.height); }   // 옛 프로젝트 화면을 남기지 않는다
  document.getElementById("bp-tabs").replaceChildren();
  bpNote(start ? `'${project}' 브라우저를 여는 중…` : `'${project}' 브라우저에 붙는 중…`);
  sock.onopen = () => { if (gen !== rb.gen) return; rb.retry = 0; rbSendViewport(true); };
  sock.onmessage = e => {
    if (gen !== rb.gen) return;
    if (typeof e.data === "string") {
      const d = JSON.parse(e.data);
      if (d.type === "frame") rb.frameMeta = d;
      else if (d.type === "state") rbApplyState(d);
      else if (d.type === "closed") { rb.closed = true; rbClosedNote(bp.project); }   // 안 연 프로젝트 — 띄우지 않는다
      else if (d.type === "error") bpNote(d.message);
      return;
    }
    rbDraw(e.data);
  };
  sock.onclose = () => {
    if (gen !== rb.gen) return;
    rb.ws = null;
    if (rb.closed || rbMode() !== "browser" || bpPanel().hidden) return;   // 꺼져 있다고 답한 것은 다시 붙지 않는다
    bpNote("브라우저 연결이 끊겼습니다 — 다시 붙는 중…");
    setTimeout(() => { if (gen === rb.gen) rbConnect(false); }, Math.min(10000, 500 * 2 ** rb.retry++));
  };
}
function rbDisconnect() {
  rb.gen++;
  if (rb.ws) { try { rb.ws.close(); } catch {} }
  rb.ws = null; rb.state = null; rb.frameMeta = null;
}
function rbSend(cmd) {
  if (rb.ws?.readyState === 1) rb.ws.send(JSON.stringify(cmd));
}

// ---------- 화면 ----------
async function rbDraw(blob) {
  if (rb.drawing) { rb.pendingBlob = blob; return; }           // 그리는 중이면 최신 것만 남긴다
  rb.drawing = true;
  try {
    const bmp = await createImageBitmap(blob);
    const c = rbCanvas();
    if (c.width !== bmp.width || c.height !== bmp.height) { c.width = bmp.width; c.height = bmp.height; }
    c.getContext("2d").drawImage(bmp, 0, 0);
    bmp.close?.();
    if (bp.project && /여는 중|붙는 중/.test(document.getElementById("bp-note").textContent)) bpNote("");
  } catch { /* 깨진 프레임 하나는 버린다 */ }
  rb.drawing = false;
  if (rb.pendingBlob) { const b = rb.pendingBlob; rb.pendingBlob = null; rbDraw(b); }
}

function rbApplyState(d) {
  rb.state = d;
  const box = bpUrlBox();
  if (document.activeElement !== box) box.value = d.url === "about:blank" ? "" : d.url;
  document.getElementById("bp-back").disabled = !d.canBack;
  document.getElementById("bp-forward").disabled = !d.canForward;
  const bar = document.getElementById("bp-tabs");
  bar.replaceChildren();
  for (const t of d.tabs) {
    const b = el("button", (t.title || t.url || "새 탭").slice(0, 24) || "새 탭");
    b.title = t.url; b.className = t.id === d.active ? "on" : "";
    b.onclick = () => rbSend({ type: "tab", id: t.id });
    const x = el("i", "×"); x.title = "탭 닫기";
    x.onclick = ev => { ev.stopPropagation(); rbSend({ type: "closetab", id: t.id }); };
    b.append(x); bar.append(b);
  }
  const plus = el("button", "＋"); plus.title = "새 탭"; plus.onclick = () => rbSend({ type: "newtab", url: "about:blank" });
  bar.append(plus);
}

// 뷰포트 = 캔버스 자리의 CSS 크기 — 원격 크롬이 그 크기로 배치하고 캐스트도 그 크기로 온다(글자가 안 뭉갠다).
function rbSendViewport(force) {
  const r = rbWrap().getBoundingClientRect();
  const w = Math.max(320, Math.floor(r.width)), h = Math.max(240, Math.floor(r.height));
  const dpr = Math.min(2, Math.max(1, window.devicePixelRatio || 1));   // 레티나 2 — 원격도 같은 배율로 그려야 안 흐리다
  const key = `${w}x${h}@${dpr}`;
  if (!force && key === rb.lastViewport) return;
  rb.lastViewport = key; rb.dpr = dpr;
  rbSend({ type: "viewport", w, h, dpr });
}
new ResizeObserver(() => {
  if (rbMode() !== "browser") return;
  clearTimeout(rb.resizeTimer);
  rb.resizeTimer = setTimeout(() => rbSendViewport(false), 200);
}).observe(document.getElementById("rb-wrap"));

// ---------- 좌표·마우스·휠 ----------
function rbPoint(e) {                                            // CDP 좌표는 CSS 픽셀 — 원격 뷰포트(CSS) ÷ 캔버스 표시 폭
  const c = rbCanvas(), r = c.getBoundingClientRect();
  const [vw, vh] = rb.state?.viewport ?? [c.width / (rb.dpr || 1), c.height / (rb.dpr || 1)];
  const sx = vw / r.width, sy = vh / r.height;
  return { x: Math.round((e.clientX - r.left) * sx * 100) / 100, y: Math.round((e.clientY - r.top) * sy * 100) / 100, sx, sy };
}
function rbMods(e) { return (e.altKey ? MOD.alt : 0) | (e.ctrlKey ? MOD.ctrl : 0) | (e.metaKey ? MOD.meta : 0) | (e.shiftKey ? MOD.shift : 0); }
const BUTTON = ["left", "middle", "right"];

{
  const c = rbCanvas();
  c.addEventListener("pointerdown", e => {
    if (e.button === 2) return;                                  // 우클릭은 우리 메뉴(contextmenu)
    const p = rbPoint(e);
    rbSend({ type: "mouse", kind: "mousePressed", x: p.x, y: p.y, button: BUTTON[e.button] ?? "left",
             clicks: e.detail || 1, modifiers: rbMods(e) });
    rbInput().focus({ preventScroll: true });                    // 키·한글은 숨은 칸이 받는다
    e.preventDefault();
  });
  c.addEventListener("pointerup", e => {
    if (e.button === 2) return;
    const p = rbPoint(e);
    rbSend({ type: "mouse", kind: "mouseReleased", x: p.x, y: p.y, button: BUTTON[e.button] ?? "left",
             clicks: e.detail || 1, modifiers: rbMods(e) });
  });
  c.addEventListener("pointermove", e => {
    const now = performance.now();
    if (now - rb.moveAt < 33) return;                            // 초당 30번이면 호버·드래그에 충분하다
    rb.moveAt = now;
    const p = rbPoint(e);
    rbSend({ type: "mouse", kind: "mouseMoved", x: p.x, y: p.y, button: e.buttons & 1 ? "left" : "none", modifiers: rbMods(e) });
  });
  c.addEventListener("wheel", e => {
    const p = rbPoint(e);
    const k = e.deltaMode === 1 ? 16 : 1;                        // 줄 단위 휠은 픽셀로
    rbSend({ type: "wheel", x: p.x, y: p.y, dx: e.deltaX * k * p.sx, dy: e.deltaY * k * p.sy });
    e.preventDefault();
  }, { passive: false });
  c.addEventListener("contextmenu", e => { e.preventDefault(); rbMenu(e.clientX, e.clientY); });
}

// ---------- 키·한글 ----------
// 글자는 숨은 textarea 의 input/compositionend 로 받아 insertText 로 보낸다(맥·폰 IME 가 조합을 끝낸 결과만).
// 특수키(Enter·Backspace·화살표…)와 ⌘/Ctrl 조합은 keydown 에서 키 이벤트로 보낸다. 조합 중(isComposing)엔 안 보낸다.
{
  const ta = rbInput();
  ta.addEventListener("compositionend", e => { if (e.data) rbSend({ type: "text", text: e.data }); ta.value = ""; });
  ta.addEventListener("input", e => {
    if (e.isComposing) return;
    if (e.inputType === "insertText" || e.inputType === "insertFromPaste") {
      if (e.data) rbSend({ type: "text", text: e.data });
      else if (ta.value) rbSend({ type: "text", text: ta.value });
    } else if (e.inputType === "insertLineBreak") rbKey("Enter");
    else if (e.inputType === "deleteContentBackward") rbKey("Backspace");
    ta.value = "";
  });
  ta.addEventListener("keydown", e => {
    if (e.isComposing || e.keyCode === 229) return;
    if (rbShortcut(e)) return;
    if (VK[e.key] !== undefined) { rbKey(e.key, rbMods(e)); e.preventDefault(); return; }
    if ((e.metaKey || e.ctrlKey) && e.key.length === 1) {       // ⌘A·⌘C·⌘Z 같은 조합 — 원격 페이지 몫
      if (e.key.toLowerCase() === "v") { rbPaste(); e.preventDefault(); return; }
      rbSend({ type: "key", kind: "keyDown", key: e.key, code: e.code, vk: e.key.toUpperCase().charCodeAt(0), modifiers: rbMods(e) });
      rbSend({ type: "key", kind: "keyUp", key: e.key, code: e.code, vk: e.key.toUpperCase().charCodeAt(0), modifiers: rbMods(e) });
      e.preventDefault();
    }
  });
}
function rbKey(key, modifiers = 0) {
  const vk = VK[key] ?? 0, code = key === " " ? "Space" : key;
  const text = key === "Enter" ? "\r" : "";
  rbSend({ type: "key", kind: "keyDown", key, code, vk, text, modifiers });
  rbSend({ type: "key", kind: "keyUp", key, code, vk, modifiers });
}
async function rbPaste() {                                       // 이 기기의 클립보드 글자를 원격 페이지에
  try { const t = await navigator.clipboard.readText(); if (t) rbSend({ type: "text", text: t }); }
  catch { bpNote("클립보드를 읽지 못했습니다 — 브라우저가 권한을 막았습니다"); }
}

// 패널 단축키 — 코덱스 앱과 같은 배치(⌘L 주소칸 · ⌘R 새로고침 · ⌘←/⌘[ 뒤로 · ⌘→/⌘] 앞으로 · ⌘T 새 탭). 패널 안에서만.
function rbShortcut(e) {
  const m = e.metaKey || e.ctrlKey;
  if (!m) return false;
  const k = e.key.toLowerCase();
  const act = { l: () => { bpUrlBox().focus(); bpUrlBox().select(); },
                r: () => rbSend({ type: "reload", hard: e.shiftKey }),
                arrowleft: () => rbSend({ type: "back" }), "[": () => rbSend({ type: "back" }),
                arrowright: () => rbSend({ type: "forward" }), "]": () => rbSend({ type: "forward" }),
                t: () => rbSend({ type: "newtab", url: "about:blank" }) }[k];
  if (!act) return false;
  act(); e.preventDefault(); return true;
}
bpPanel().addEventListener("keydown", e => { if (rbMode() === "browser" && e.target !== rbInput()) rbShortcut(e); });

// ---------- 주소칸(브라우저 모드) — 주소면 이동, 포트 숫자면 맥의 localhost:<포트>, 나머지는 검색 ----------
// 검색 엔진은 구글(코덱스 앱과 같게). 헤드리스 봇 판정은 브릿지가 자동화 표식·클라이언트힌트를 보정해 통과한다(browser.py).
const RB_SEARCH = "https://www.google.com/search?q=";
function rbGo(raw) {
  if (!raw) return;
  let url;
  if (/^\d{2,5}$/.test(raw)) url = `http://localhost:${raw}/`;   // 원격 크롬은 맥에서 돈다 — 맥의 개발 서버가 곧 localhost
  else if (/^[a-z]+:\/\//i.test(raw)) url = raw;
  else if (bpLooksLikeHost(raw.split(/[/?#]/)[0].replace(/:\d+$/, ""))) url = `https://${raw}`;
  else url = `${RB_SEARCH}${encodeURIComponent(raw)}`;
  rbSend({ type: "navigate", url });
  bpUrlBox().blur();
}
document.getElementById("bp-back").onclick = () => rbSend({ type: "back" });
document.getElementById("bp-forward").onclick = () => rbSend({ type: "forward" });

// ---------- 우클릭 메뉴(우리 것) ----------
function rbMenu(x, y) {
  document.getElementById("rb-menu")?.remove();
  const menu = document.createElement("div");
  menu.id = "rb-menu";
  const items = [["← 뒤로", () => rbSend({ type: "back" })], ["→ 앞으로", () => rbSend({ type: "forward" })],
                 ["↻ 새로고침", () => rbSend({ type: "reload" })], ["＋ 새 탭", () => rbSend({ type: "newtab", url: "about:blank" })],
                 ["주소 복사", () => navigator.clipboard?.writeText(rb.state?.url ?? "")],
                 ["이 브라우저 끄기", () => document.getElementById("bp-power").click()],
                 ["이 기기 브라우저로 열기", () => rb.state?.url && window.open(rb.state.url, "_blank", "noopener")]];
  for (const [label, run] of items) {
    const b = el("button", label);
    b.onclick = () => { menu.remove(); run(); };
    menu.append(b);
  }
  menu.style.left = `${Math.min(x, innerWidth - 200)}px`; menu.style.top = `${Math.min(y, innerHeight - 200)}px`;
  document.body.append(menu);
  setTimeout(() => document.addEventListener("pointerdown", ev => { if (!menu.contains(ev.target)) menu.remove(); }, { once: true, capture: true }));
}
