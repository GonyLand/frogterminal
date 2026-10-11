"use strict";
// 3열 분할선 — 사이드바↔가운데, 가운데↔파일패널의 폭을 잡아끌어 바꾼다(좌우 여백을
// 조정할 수 있게). 마우스·손가락 둘 다 같은 길로 처리한다(포인터 이벤트, CSS 에 touch-action:none).
//
// 폰(<900px)에서는 CSS 가 분할선을 숨기고 한 화면씩 갈아끼운다. 그런데 인라인 style.width 는
// 미디어쿼리의 width:100% 를 이기므로, 폰 폭에서는 반드시 지워야 배치가 안 깨진다(applySizes).

const SPLIT_KEY = { list: "frog.split.list", file: "frog.split.file", browser: "frog.split.browser" };
const SPLIT_PANE = { list: "list-view", file: "file-panel", browser: "browser-panel" };
// 하한 — 0 이 되면 잡을 분할선조차 사라져 되돌릴 방법이 없다. 값의 근거:
//   list 160px: 프로젝트 이름(15px)·경로·상태줄이 세 줄로 들어가는 최소 폭. 더 좁으면 전부 … 뿐이다.
//   file 220px: 트리가 깊이마다 14px 씩 들여쓴다 — 3층이면 이름 자리가 이보다 좁아진다.
// 상한 — 가운데(터미널)에 항상 360px 을 남긴다. 터미널이 지나치게 좁아지면 클로드 피커 상자가 깨진다.
//   browser 320px: 주소칸+버튼 한 줄과 폰 폭 미리보기가 들어가는 최소. 더 좁으면 개발 앱이 폰 배치로 떨어진다.
const SPLIT_MIN = { list: 160, file: 220, browser: 320 };
const SPLIT_CENTER_MIN = 360;

const splitPane = key => document.getElementById(SPLIT_PANE[key]);

// 지금 창에서 이 칸이 가질 수 있는 폭으로 자른다 — 보이는 옆칸 '전부'와 분할선 폭까지 빼고 계산한다.
// (옛 코드는 반대편 칸 하나만 뺐다 — 4번째 칸(브라우저)이 생기며 두 칸이 동시에 열리면 터미널 360px
//  하한이 깨졌다. 코덱스 재검증 2026-09-20: 분할선 5px 도 안 빼서 1280px 에서 350px 이 나왔다.)
function splitClamp(key, want) {
  let used = 0;
  for (const k of Object.keys(SPLIT_PANE)) {
    const pane = splitPane(k);
    if (k !== key && !pane.hidden) used += pane.getBoundingClientRect().width;
  }
  for (const s of document.querySelectorAll(".splitter")) used += s.getBoundingClientRect().width;
  const max = Math.max(SPLIT_MIN[key], window.innerWidth - used - SPLIT_CENTER_MIN);
  return Math.round(Math.max(SPLIT_MIN[key], Math.min(max, want)));
}

function splitSaved(key) {
  const value = parseInt(localStorage.getItem(SPLIT_KEY[key]) ?? "", 10);
  return Number.isFinite(value) ? value : null;
}

// 저장해 둔 폭을 다시 건다. 창 크기가 바뀌면 상·하한이 달라지므로 그때마다 다시 계산한다.
// 사용자가 한 번도 끌지 않았으면(저장값 없음) 아무것도 걸지 않는다 — CSS 기본값(250px·320px)과
// 파일을 열 때 넓어지는 .wide(55%)가 그대로 산다.
function applySizes() {
  for (const key of Object.keys(SPLIT_PANE)) {
    const pane = splitPane(key);
    const saved = splitSaved(key);
    if (!isDesktop() || saved === null) pane.style.width = "";
    else pane.style.width = `${splitClamp(key, saved)}px`;
  }
}

function startDrag(splitter, e) {
  if (!isDesktop() || e.button !== 0) return;  // 폰엔 분할선이 없고(CSS), 오른쪽 버튼은 끌기가 아니다
  const key = splitter.dataset.pane;
  const pane = splitPane(key);
  const startX = e.clientX;
  const startW = pane.getBoundingClientRect().width;
  const dir = key === "list" ? 1 : -1;         // 오른쪽 칸(브라우저·파일)은 왼쪽으로 끌수록 넓어진다
  let width = startW, frame = 0;
  splitter.classList.add("dragging");
  splitter.setPointerCapture(e.pointerId);     // 커서가 분할선을 벗어나도 계속 따라온다

  const move = ev => {
    width = splitClamp(key, startW + dir * (ev.clientX - startX));
    if (frame) return;                         // 한 프레임에 한 번만 — 끄는 동안 리사이즈 폭주 방지
    frame = requestAnimationFrame(() => { frame = 0; pane.style.width = `${width}px`; });
  };
  const stop = () => {
    splitter.removeEventListener("pointermove", move);
    splitter.classList.remove("dragging");
    cancelAnimationFrame(frame);
    pane.style.width = `${width}px`;           // 마지막 프레임까지 반영하고 저장한다
    localStorage.setItem(SPLIT_KEY[key], String(width));
    // xterm 은 칸마다의 ResizeObserver(pane.js)가 폭 변화를 보고 따라오지만, 마지막 프레임을
    // 못 봤거나 관찰자가 없을 수 있다 — 끝에 한 번 더 맞춘다(PTY 크기가 어긋나면 피커 상자가 깨진다).
    sendResize();
  };
  splitter.addEventListener("pointermove", move);
  splitter.addEventListener("pointerup", stop, { once: true });
  splitter.addEventListener("pointercancel", stop, { once: true });
  e.preventDefault();                          // 드래그가 글자 선택으로 새지 않게
}

for (const splitter of document.querySelectorAll(".splitter"))
  splitter.addEventListener("pointerdown", e => startDrag(splitter, e));

// 파일 패널이 접혀 있으면 그 분할선도 접는다 — 아무것도 없는 오른쪽 끝에 잡을 선만 남으면
// 끌어도 아무 일이 일어나지 않는다. 패널을 여닫는 것은 files.js 라 hidden 속성 변화를 지켜본다.
// 브라우저 패널(browser.js)도 같은 결 — 분할선은 패널의 hidden 을 그대로 따른다.
const syncSplitters = () => {
  for (const key of ["file", "browser"]) {
    const splitter = document.querySelector(`.splitter[data-pane="${key}"]`);
    splitter.hidden = splitPane(key).hidden;
  }
};
const syncFileSplitter = syncSplitters;
for (const key of ["file", "browser"])
  new MutationObserver(syncSplitters).observe(splitPane(key), { attributes: true, attributeFilter: ["hidden"] });

// 파일을 열면 files.js 가 .wide(55%)를 건다. 사용자가 직접 끈 폭은 인라인 style 이라 그 규칙을
// 이기는데, 그게 맞다 — "폭 조절이 안 된다"가 이 작업의 출발점이라, 한 번 정한 폭을 화면이
// 제멋대로 되돌리면 고친 뜻이 없다. 손대기 전에는 인라인 폭 자체가 없어 .wide 가 그대로 산다.
syncFileSplitter();
applySizes();
addEventListener("resize", applySizes);        // 창이 좁아지면 상한이 줄어든다 — 다시 자른다
matchMedia("(min-width: 900px)").addEventListener("change", applySizes);   // 폰↔데스크톱 경계

// ---------- 사이드바 접기 — 터미널 헤더의 « » (슬라이드로 숨김) ----------
// 폭 자체(SPLIT_KEY.list)는 건드리지 않는다 — 접었다 펴면 끌어 둔 폭 그대로 돌아온다.
// 기억은 기기별(localStorage) — 맥에서 접었다고 폰·다른 맥이 따라 접히면 당황스럽다.
const SIDE_KEY = "frog.side.collapsed";
const mainEl = document.getElementById("main");
const sideToggle = document.getElementById("side-toggle");

function applySide(collapsed) {
  mainEl.classList.toggle("side-collapsed", collapsed);
  sideToggle.textContent = collapsed ? "»" : "«";
  sideToggle.title = collapsed ? "사이드바 펴기" : "사이드바 접기";
  sideToggle.setAttribute("aria-expanded", String(!collapsed));
}

sideToggle.onclick = () => {
  const collapsed = !mainEl.classList.contains("side-collapsed");
  localStorage.setItem(SIDE_KEY, collapsed ? "1" : "0");
  applySide(collapsed);
  // 폭이 .2s 동안 움직인다 — 끝난 뒤 PTY 크기를 한 번 더 맞춘다(ResizeObserver 가 중간값을
  // 보고 멈출 수 있다. 어긋나면 클로드 피커 상자가 깨진다).
  setTimeout(sendResize, 250);
};
applySide(localStorage.getItem(SIDE_KEY) === "1");
