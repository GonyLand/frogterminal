"use strict";
// 상단 결재 대시보드 — "지금 내가 결정해야 할 것"을 인박스까지 들어가지 않고 그 자리에서 고른다.
// 대표 지적: "결정해야 될 게 있으면 피커 형태로 만들어 달라고 했는데 지금은 '결재 대기' 글자뿐이다."
//
// 인박스(inbox.js)와 나눠 쓰는 것: 카드 맥락(contextNodes)·잠금(sentAnswers·sendAnswer)·
// 오류 문구(ANSWER_ERRORS)·10번 이상 규칙(oneKey·MANUAL_NOTE). 여기서 다시 만들지 않는다 —
// 두 화면이 다른 말을 하거나 각자 잠그면 같은 답이 두 번 나간다.
// 렌더는 전부 textContent/createElement (카드 글자는 터미널 화면·대화 원문이라 innerHTML 금지).

const DASH_OPEN_KEY = "frog.dash.open";        // 접기/펴기 — 기기에 기억시킨다(폰에서 자리를 많이 먹는다)
// 여러 칸(grid.js)일 때는 따로 기억하고 기본은 접힘 — 칸이 세로로 납작해지는 주범이었다(대표 지적 2026-10-11).
// 1칸 기억(DASH_OPEN_KEY)은 건드리지 않는다: 1칸으로 돌아오면 원래 펴 둔 대로 돌아온다.
const DASH_GRID_KEY = "frog.dash.open.grid";
const dashKey = () => (typeof isGrid === "function" && isGrid() ? DASH_GRID_KEY : DASH_OPEN_KEY);
const dashOpen = () => (dashKey() === DASH_GRID_KEY
  ? localStorage.getItem(DASH_GRID_KEY) === "1"
  : localStorage.getItem(DASH_OPEN_KEY) !== "0");

async function loadDash() {
  let cards = [];
  try {
    const res = await fetch(`${API}/api/decisions`);
    if (!res.ok) throw new Error(res.status);
    cards = (await res.json()).decisions ?? [];
  } catch {
    return;                                    // 연결 안내는 사이드바가 한다 — 같은 말을 두 번 하지 않는다
  }
  renderDash(cards);
}

// 인박스도 자기가 받은 같은 목록으로 이 함수를 부른다(loadInbox) — 한 응답으로 두 화면을 그리므로
// 상단 수와 인박스 수가 어긋날 수 없다.
function renderDash(cards) {
  forgetStaleSent(cards);
  const dash = document.getElementById("dash");
  const box = document.getElementById("dash-cards");
  // 답이 없으면 클로드가 못 나가는 것만 올린다(isDecision — inbox.js 와 같은 잣대).
  // 완료(done)·입력 대기(idle)는 '다음 지시를 넣는 자리'라 인박스 몫이다 — 여기에 같이 올리면
  // 정작 결정할 것이 그 안에 파묻힌다(상단 '결재 대기 N건'이 놀고 있는 세션까지 세던 문제).
  const waiting = cards.filter(isDecision);
  const byTime = (a, b) => (a.updatedAt || "").localeCompare(b.updatedAt || "");
  const live = waiting.filter(c => c.live !== false).sort(byTime);   // 놓친 것부터 — 오래된 순
  const dead = waiting.filter(c => c.live === false).sort(byTime);
  dash.hidden = !waiting.length;
  if (!waiting.length) {                       // 결정할 것이 없으면 자리를 통째로 비운다
    box.replaceChildren();
    dashNote("");
    return;
  }
  // 상단 헤드라인(#sum-pending)은 살아 있는 카드만 센다(브릿지 api_summary) — 여기서도 같은 수를
  // 말해야 한다. 답을 못 보내는 죽은 세션 카드는 그 수에 섞지 않고 뒤에 따로 적는다.
  const open = dashOpen();
  const head = document.getElementById("dash-toggle");
  head.textContent = `${open ? "▾" : "▸"} 결재 대기 ${live.length}건`
                     + (dead.length ? ` · 세션 꺼짐 ${dead.length}건` : "");
  head.setAttribute("aria-expanded", String(open));
  // #dash-cards 는 display:flex 라 [hidden] 속성만으로는 안 접힌다(작성자 display 가 UA 의
  // display:none 을 이긴다 — #term-view 가 같은 함정을 겪고 CSS 에 규칙을 따로 뒀다).
  // style.css 는 손대지 않기로 했으므로 인라인으로 접되, hidden 속성도 같이 둔다(보조기기·검색).
  box.hidden = !open;
  box.style.display = open ? "" : "none";
  box.replaceChildren(...[...live, ...dead].map(dashCard));
}

document.getElementById("dash-toggle").onclick = () => {
  localStorage.setItem(dashKey(), dashOpen() ? "0" : "1");
  loadDash();
};

function dashCard(c) {
  const div = document.createElement("div");
  div.className = "dash-card";
  // live=false = 그 tmux 세션이 죽었다(크래시로 SessionEnd 훅을 못 남긴 잔재). 답을 보내면 404 라
  // 아예 못 누르게 하고, [터미널 열기]만 살린다(브릿지가 new-session -A 로 다시 켜 준다).
  const dead = c.live === false;                           // 세션 자체가 죽었다
  const reachable = canAnswer(c);                          // 여기서 답을 넣을 수 있나(인박스와 같은 잣대)
  const prev = sentAnswers.get(cardKey(c));
  const sent = prev?.mark === cardMark(c) ? prev : null;   // 인박스와 같은 잠금 — 방금 답한 그 카드만
  const locked = !reachable || !!sent;
  div.classList.toggle("dead", !reachable);

  const proj = el("b", c.project || "(프로젝트 밖)");
  proj.className = "dash-proj";
  const meta = el("small", [c.machine, whenText(c.updatedAt),
                            dead ? "● 세션 꺼짐 — 답을 보낼 수 없습니다"
                                 : reachable ? "" : OFF_TMUX_NOTE]
                           .filter(Boolean).join(" · "));
  meta.className = "dash-meta";
  const head = document.createElement("div");
  head.append(proj, meta);
  div.append(head);
  // 기계 문구(message) 대신 대화 맥락 — 이 카드만 보고 "아 이거 하다 멈췄구나"를 알 수 있게.
  div.append(...contextNodes(c, DASH_CONTEXT));
  if (sent) div.append(badge(`보냄: ${sent.label}`, "dash-meta"));

  // 답을 넣을 수 없는 카드에는 버튼·입력창을 그리지 않는다(인박스와 같은 규칙 — 디자이너 지적 [상]).
  const options = reachable ? (c.options ?? []) : [];
  const picks = options.filter(oneKey);
  const manual = options.filter(o => !oneKey(o));
  if (picks.length) {                          // ★ 이 버튼들이 대표가 말한 '피커'다
    const opts = document.createElement("div");
    opts.className = "dash-opts";
    for (const o of picks) {
      const label = `${o.n}. ${o.label}`;
      const btn = el("button", label);
      btn.className = "dash-opt";
      btn.disabled = locked;
      btn.onclick = () => sendAnswer(c, div, "option", String(o.n), label, DASH_VIEW);
      opts.append(btn);
    }
    div.append(opts);
  }
  if (manual.length) {                         // 10번 이상 — 버튼으로 만들면 1번이 눌린다(inbox.js 주석)
    div.append(dashLine("dash-manual", MANUAL_NOTE));
    for (const o of manual) div.append(dashLine("dash-manual", `${o.n}. ${o.label}`));
  }

  const row = document.createElement("div");
  row.className = "dash-row";
  if (reachable && !picks.length) row.append(...askRow(c, div, locked));   // 고를 것이 없으면 직접 지시
  if (c.project) {                             // 프로젝트를 모르는 카드는 열 터미널도 없다
    const open = el("button", "터미널 열기");
    open.className = "dash-term";
    open.onclick = () => openTerminal({ name: c.project, path: c.cwd || null, alive: !dead });
    row.append(open);
  }
  if (row.children.length) div.append(row);
  return div;
}

// 선택지를 못 읽은 결재 카드(permission) = 대표가 답을 글로 직접 넣는 자리.
// (답을 못 넣는 카드는 이 줄 자체가 안 그려진다 — dashCard 의 reachable 분기.)
function askRow(c, div, locked) {
  const ask = document.createElement("input");
  ask.type = "text";
  ask.className = "dash-ask";
  ask.placeholder = "직접 지시 입력… (⌘/Ctrl+Enter)";
  ask.disabled = locked;
  const send = el("button", "보내기");
  send.className = "dash-send";
  send.disabled = locked;
  send.onclick = () => {
    const text = ask.value.trim();
    if (text) sendAnswer(c, div, "text", text, text, DASH_VIEW);
  };
  // 엔터 단독은 전송으로 쓰지 않는다(인박스와 같은 규칙) — 폰 한글 조합 중에 눌리면 쓰다 만
  // 지시가 그대로 클로드에게 나간다. 보내는 길은 옆의 44px [보내기] 버튼이다.
  ask.onkeydown = e => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && !e.isComposing) send.onclick();
  };
  return [ask, send];
}

function dashLine(cls, text) {
  const p = el("p", text);
  p.className = cls;
  return p;
}

// alert() 금지 — 안내는 대시보드 안 한 줄로. #dash-cards 바깥이라 다시 그려도 지워지지 않는다.
function dashNote(text) {
  const note = document.getElementById("dash-note");
  note.textContent = text;
  note.hidden = !text;
}

const DASH_VIEW = { note: dashNote, reload: reloadDecisions };

if (!REDIRECTING) loadDash();     // 첫 그림 — 이후 갱신은 app.js 의 7초 폴링이 부른다(새 타이머 없음)
