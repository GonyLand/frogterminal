"use strict";
// 결재 인박스 — "나를 기다리는 것"만 모아 보여주고, 답을 그 tmux 세션으로 쏜다.
// 렌더는 전부 textContent/createElement — 카드 내용에는 터미널 화면에서 긁어온 질문·선택지가
// 그대로 들어오므로, innerHTML 을 쓰면 그 글자가 HTML(또는 스크립트)로 해석된다.

// 방금 답을 보낸 카드. 브릿지는 답을 넣은 뒤 카드 파일을 지우지만, 화면스캔(capture-pane)이
// 다시 돌아 목록이 바뀌기까지 몇 초가 뜬다. 그 사이 7초 폴링이 같은 카드를 되살리는데,
// 버튼이 살아 있으면 사용자가 "안 보내졌나?" 하고 한 번 더 누르고 → 같은 숫자가 터미널에 두 번 찍힌다.
// 그래서 보낸 카드는 목록에서 사라질 때까지 화면 쪽에서 잠가 둔다.
//   * 잠금 단위는 sessionId 가 아니라 project 다 — 답은 send_answer(project) 로 그 tmux 세션에
//     들어가고, 지워진 카드가 화면스캔으로 되살아날 땐 sessionId 가 null 이라 짝이 안 맞는다.
//   * 대신 "방금 답한 그 카드일 때만" 잠근다(cardMark) — 답한 직후 클로드가 다음 질문을 띄우거나
//     다음 턴을 끝내면 그건 방금 보낸 답이 아니라 새로 답해야 할 것이다.
const sentAnswers = new Map();               // project → { mark, label, at }
const SENT_HOLD_MS = 60000;                  // 이만큼 지나도 카드가 안 사라지면 잠금을 푼다(재시도 허용)

// 이 카드가 무엇을 기다리고 있는지 — 잠금 대조용 표식.
//   * picker/permission/idle 은 질문 글이 곧 표식이다. 같은 질문이 화면스캔으로 되살아나면
//     잠긴 채로, 다음 질문이면 곧바로 답할 수 있게 갈린다.
//   * done(✅ 완료 — 다음 지시 대기)에는 묻는 말이 없다 — 훅이 Stop 에서 message:"" 로 쓴다
//     (hooks/frog_hook.py). 질문 글로 대조하면 완료 카드끼리 빈 문자열로 겹쳐, 지시를 한 번
//     보낸 프로젝트는 다음 턴이 끝나 새 완료 카드가 떠도 잠긴 채 "보냄: 옛 지시" 를 단다
//     (보내지도 않은 것을 보냈다고 표시). 그래서 완료 카드는 '그 완료 시점' 자체를 표식으로
//     쓴다 — 훅은 클로드 세션당 카드 1장을 덮어쓰므로 sessionId 는 턴이 바뀌어도 같다.
//     updatedAt 까지 함께 봐야 "같은 완료 카드"와 "다음 턴의 완료 카드"가 갈린다.
const cardMark = c => c.kind === "done"
  ? `done\n${c.sessionId ?? ""}\n${c.updatedAt ?? ""}`
  : c.question || c.message || "";
const cardKey = c => c.project ?? "";

// 빨간불(결재)로 셀 카드 — 브릿지의 DECISION_KINDS 와 같은 값이어야 한다(bridge/decisions.py).
// idle("입력 대기")은 뺀다: 할 말을 마치고 빈 프롬프트에서 쉬는 것뿐이라 결정할 거리가 없는데도
// 빨간불이 켜져, 정작 답해야 할 결재가 그 안에 묻혔다. 그런 카드는 '완료 — 다음 지시 대기'로 간다.
const DECISION_KINDS = new Set(["permission", "picker"]);
const isDecision = c => DECISION_KINDS.has(c.kind);

// 살아있는 것과 '여기서 답을 보낼 수 있는 것'은 다르다. 답은 tmux send-keys 로만 들어가는데
// 클로드는 대부분 tmux 밖(Zed·iTerm)에서 돈다 — 그런 세션은 살아 있어도 웹에서 답할 길이 없다
// (브릿지가 404 를 낸다). 눌러 보고서야 안 된다는 말을 듣지 않도록 버튼을 미리 잠근다.
// 옛 응답(canAnswer 없음)에서는 예전대로 live 만 본다.
const canAnswer = c => c.canAnswer ?? c.live !== false;
const OFF_TMUX_NOTE = "● tmux 밖에서 도는 세션 — 답은 그 터미널 창에서 직접 하세요";

// tmux send-keys "12" 는 '1' 다음 '2', 두 키를 보낸다. 클로드 피커는 숫자 하나로 즉시 고르므로
// 옵션이 10개 넘는 화면에서 '12번' 버튼을 누르면 1번이 선택되고 남은 '2' 가 다음 프롬프트로
// 흘러간다 — 그런데 화면엔 "보냄: 12. …" 가 남아 사용자는 12번을 골랐다고 믿는다.
// 그래서 한 키로 보낼 수 있는 1~9 만 버튼으로 만들고, 나머지는 무엇이 있는지만 글로 보여준 뒤
// 고르는 일은 [터미널 열기]에 맡긴다(브릿지도 10 이상은 400 으로 되돌려 보낸다).
// 대시보드도 같은 규칙·같은 문구를 쓴다 — 두 화면이 다른 말을 하면 사용자가 어느 쪽을 믿을지 모른다.
const oneKey = o => Number.isInteger(o.n) && o.n >= 1 && o.n <= 9;
const MANUAL_NOTE = "10번 이상은 버튼으로 못 고릅니다 (숫자가 한 자리씩 들어가 1번이 눌립니다)"
                    + " — [터미널 열기]에서 직접 고르세요";

// "2026-08-05T12:00:00+09:00" → "08-05 12:00". 훅이 KST 로 써 둔 문자열을 그대로 자른다
// (Date 로 파싱해 되돌리면 보는 기기의 시간대로 바뀐다 — 표시는 항상 KST).
function whenText(iso) {
  const m = /^\d{4}-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso || "");
  return m ? `${m[1]}-${m[2]} ${m[3]}:${m[4]}` : "";
}

// ---------- 카드 맥락 — "무슨 일을 하다 무엇을 묻고 멈췄나" ----------
// 훅이 쓴 message("Claude is waiting for your input")는 기계 문구라 사용자가 판단할 거리가 없다.
// 그래서 브릿지가 대화 기록에서 뽑아 주는 두 필드를 대신 보여준다(bridge/decisions.py, 각 400자):
//   asked = 사용자가 마지막에 시킨 것 → '무슨 일을 하다 멈췄나'의 단서
//   said  = 클로드가 마지막에 한 말 → 보통 이것이 진짜 질문이다
// 시간순으로 ① 시킨 것 ② 클로드가 한 말 순으로 놓아, 답할 버튼 바로 위에 질문이 오게 한다.
// 마크다운은 렌더하지 않는다 — 전부 textContent(이 repo 의 절대 계약). 원문 그대로 보여준다.
// 라벨은 글자만 — 컬러 이모지는 OS 마다 다르게 그려지고 점(dot) 상태 언어와 충돌한다(디자이너 지적).
const ASK_LABEL = "지시 — ";
const SAY_LABEL = "클로드 — ";
const SAID_LABEL = "클로드가 한 말 — ";
// 인박스는 한 화면을 통째로 쓰므로 400자를 그대로 편다. 대시보드는 카드가 여럿 늘어서는 자리라
// 앞머리만 보여주고 나머지는 접어 둔다(모달 금지 — <details> 로 그 자리에서 편다).
const INBOX_CONTEXT = { askMax: 400, sayMax: 400 };
const DASH_CONTEXT = { askMax: 90, sayMax: 160, askCls: "dash-meta", sayCls: "dash-q" };

// 마크다운 마커(**·백틱)는 렌더하지 않으므로 표시 전에 걷어낸다 — 카드가 원문 기호를 그대로
// 뿜으면 최상위 콘텐츠가 제일 거칠어 보인다(디자이너 지적). innerHTML 렌더는 계약상 금지라
// 마커 제거가 정답이다(내용은 그대로, 기호만 사라진다).
const normText = s => (s ?? "").replace(/\*\*/g, "").replace(/`/g, "")
                               .replace(/\s+/g, " ").trim();
const clipText = (s, max) => s.length > max ? s.slice(0, max).trimEnd() + "…" : s;

// 피커 카드에는 화면에서 긁은 question 이 이미 있다. 대화의 said 가 그 질문과 사실상 같은 문장이면
// 한 카드에 같은 말이 두 번 뜬다 — 둘 다 400자에서 잘렸을 수 있으니 앞머리(40자)로 견준다.
function sameSay(a, b) {
  a = normText(a); b = normText(b);
  if (!a || !b) return false;
  const head = s => s.replace(/…$/, "").slice(0, 40);
  return a.includes(head(b)) || b.includes(head(a));
}

function ctxLine(cls, text) {
  const p = el("p", text);
  if (cls) p.className = cls;
  return p;
}

function contextNodes(card, { askMax, sayMax, askCls = "", sayCls = "" }) {
  const asked = normText(card.asked);
  const question = normText(card.question);
  // 피커면 화면에 떠 있는 그 질문이 곧 '클로드가 지금 묻는 것'이다. 대화의 said 는 겹치면 버린다.
  const said = sameSay(question, card.said) ? "" : normText(card.said);
  const say = question || said;
  const extra = question && said ? said : "";      // 피커인데 대화 마지막 말이 따로 있는 경우
  const out = [];
  if (asked) out.push(ctxLine(askCls, ASK_LABEL + clipText(asked, askMax)));
  if (say) out.push(ctxLine(sayCls, SAY_LABEL + clipText(say, sayMax)));
  // 맥락이 통째로 빈 카드(/clear 직후 등)도 있다 — 그때만 훅 원문을 쓴다(빈 카드보다는 낫다).
  if (!out.length && normText(card.message)) out.push(ctxLine(sayCls, normText(card.message)));
  // '전체 대화 보기'는 화면에서 잘렸을 때(clip)만이 아니라 서버가 400자에서 자른 흔적("…")이
  // 있을 때도 편다 — 마커 제거로 글자 수가 줄면 length 비교만으로는 접힌 채 잘린 글이 남는다(실측).
  const cut = (s, max) => s.length > max || s.endsWith("…");
  const full = [cut(asked, askMax) ? ASK_LABEL + asked : "",
                cut(say, sayMax) ? SAY_LABEL + say : "",
                extra ? SAID_LABEL + extra : ""].filter(Boolean);
  if (full.length) out.push(ctxDetails(full, sayCls));
  return out;
}

// 접고 펴는 장치 — <details> 는 브라우저가 그려 주므로 CSS·모달 없이 그 자리에서 펴진다.
function ctxDetails(lines, cls) {
  const box = document.createElement("details");
  box.append(el("summary", "전체 대화 보기"));
  for (const text of lines) box.append(ctxLine(cls, text));
  return box;
}

async function loadInbox() {
  const box = document.getElementById("inbox-cards");
  let cards = [];
  try {
    const res = await fetch(`${API}/api/decisions`);
    if (!res.ok) throw new Error(res.status);
    cards = (await res.json()).decisions ?? [];
  } catch {
    box.replaceChildren(el("p", "결재 목록을 불러오지 못했습니다 — 맥에 연결됐는지 확인하세요"));
    return;
  }
  forgetStaleSent(cards);
  renderDash(cards);          // 같은 목록으로 상단 대시보드까지 — 두 화면이 다른 수를 말할 수 없게
  cards.sort((a, b) => (a.updatedAt || "").localeCompare(b.updatedAt || ""));   // 오래된 것 먼저
  // live=false(tmux 세션이 죽은 잔재)는 여기서 통째로 갈라 낸다. 답을 보낼 수 없는 카드를
  // '결재 필요' 에 같이 세면, 살아 있는 것만 세는 상단 헤드라인('결재 대기 N건')과 숫자가
  // 어긋난다 — 정확한 수를 알려주려고 있는 화면이 사용자에게 어느 쪽을 믿을지를 떠넘기는 셈이다.
  // 숨기지는 않는다: 답만 못 보낼 뿐 사용자가 손봐야 할 일이라, 맨 아래 따로 모아 [터미널 열기]로
  // 잇게 한다(브릿지가 new-session -A 로 다시 켜 준다).
  const live = cards.filter(c => c.live !== false);
  const dead = cards.filter(c => c.live === false);
  const need = live.filter(isDecision);
  const done = live.filter(c => !isDecision(c));
  box.replaceChildren();
  // 섹션 표식은 이모지 대신 CSS 점(::before) — 사이드바·헤드라인과 같은 상태 언어(amber·green·alert).
  // 섹션1 = 답이 없으면 클로드가 못 나가는 것(permission/picker). 놓친 것부터 처리하도록 오래된 순.
  renderSection(box, `결재 필요 (${need.length})`, need,
                "지금 나를 기다리는 결정이 없습니다 — 세션이 권한을 물으면 여기에 카드가 뜹니다", "need");
  // 섹션2 = 할 말을 마치고 쉬는 세션(done/idle) — 다음 지시를 넣는 자리다.
  renderSection(box, `완료 — 다음 지시 대기 (${done.length})`, done, "", "done");
  if (dead.length)
    renderSection(box, `세션 꺼짐 — 터미널을 열어 확인 (${dead.length})`, dead, "", "dead");
}

function forgetStaleSent(cards) {
  const keys = new Set(cards.map(cardKey));
  for (const [key, sent] of sentAnswers)
    if (!keys.has(key) || Date.now() - sent.at > SENT_HOLD_MS) sentAnswers.delete(key);
}

function renderSection(box, title, cards, emptyText, cls = "") {
  const head = el("h3", title);
  if (cls) head.className = cls;
  box.append(head);
  if (!cards.length) {
    if (emptyText) box.append(el("p", emptyText));
    return;
  }
  for (const c of cards) box.append(inboxCard(c));
}

function inboxCard(c) {
  const div = document.createElement("div");
  div.className = "inbox-card";
  // live=false = 그 tmux 세션이 죽었다(크래시로 SessionEnd 훅을 못 남긴 잔재). 답을 보내면
  // 브릿지가 404 를 낸다 → 답할 수 있는 것처럼 보이면 안 된다. 대신 카드는 남겨 두고
  // [터미널 열기]만 살린다. 브릿지가 attach 를 new-session -A 로 하므로 거기서 다시 켜서 이어간다.
  const dead = c.live === false;                           // 세션 자체가 죽었다
  const reachable = canAnswer(c);                          // 여기서 답을 넣을 수 있나
  const prev = sentAnswers.get(cardKey(c));
  const sent = prev?.mark === cardMark(c) ? prev : null;   // 방금 답한 그 카드일 때만 잠근다
  const locked = !reachable || !!sent;
  div.classList.toggle("dead", !reachable);                // 잠긴 카드의 표시 — 왜 잠겼는지는 배지로
  div.classList.toggle("sent", !!sent);

  div.append(el("b", c.project || "(프로젝트 밖)"),
             el("small", [c.machine, whenText(c.updatedAt)].filter(Boolean).join(" · ")));
  // 죽은 카드에 '세션 꺼짐' 배지를 또 달지 않는다 — 그 카드가 사는 섹션 제목이 이미 말한다
  // (카드마다 반복되면 소음 — 디자이너 지적). tmux 밖 카드의 안내만 카드에 남긴다.
  if (!dead && !reachable) div.append(badge(OFF_TMUX_NOTE));
  if (sent) div.append(badge(`보냄: ${sent.label}`));
  div.append(...contextNodes(c, INBOX_CONTEXT));

  // 답을 넣을 수 없는 카드에는 입력창·보내기를 아예 그리지 않는다 — 흐려진 초록 버튼도
  // '눌러 볼 수 있는 것'으로 읽힌다(디자이너 지적 [상]). 이 카드의 길은 [터미널 열기] 하나다.
  if (reachable) {
    const options = c.options ?? [];
    for (const o of options.filter(oneKey)) {
      const label = `${o.n}. ${o.label}`;
      const btn = el("button", label);
      btn.className = "opt-btn";
      btn.disabled = locked;
      btn.onclick = () => sendAnswer(c, div, "option", String(o.n), label);
      div.append(btn);
    }
    const manual = options.filter(o => !oneKey(o));
    if (manual.length) {
      const note = el("p", MANUAL_NOTE);
      note.className = "manual-note";
      div.append(note);
      for (const o of manual) {
        const line = el("p", `${o.n}. ${o.label}`);
        line.className = "manual-opt";
        div.append(line);
      }
    }

    const ta = document.createElement("textarea");
    ta.placeholder = "직접 지시 입력… (⌘/Ctrl+Enter 로 전송)";
    ta.disabled = locked;
    const sendBtn = el("button", "보내기");   // app.js 의 전역 send() 와 헷갈리지 않게 이름을 나눈다
    sendBtn.className = "send-btn";
    sendBtn.disabled = locked;
    sendBtn.onclick = () => {
      const text = ta.value.trim();
      if (text) sendAnswer(c, div, "text", text, text);
    };
    // 엔터 단독은 전송으로 쓰지 않는다 — 폰 한글 조합 중에 눌리면 쓰다 만 지시가 그대로 나간다.
    ta.onkeydown = e => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && !e.isComposing) sendBtn.onclick();
    };
    div.append(ta, sendBtn);
  }

  if (c.project) {                            // 프로젝트를 모르는 카드는 열 터미널도 없다
    const open = el("button", "터미널 열기");
    open.className = "open-btn";
    open.onclick = () => openTerminal({ name: c.project, path: c.cwd || null, alive: !dead });
    div.append(open);
  }
  return div;
}

// extra = 대시보드 카드용 보조 클래스. .badge 스타일은 #inbox-cards 안에서만 걸리므로,
// 같은 함수를 대시보드에서 쓸 때는 그 카드의 보조 글자 클래스(.dash-meta)를 함께 준다.
function badge(text, extra = "") {
  const node = el("span", text);
  node.className = extra ? `badge ${extra}` : "badge";
  return node;
}

// 답이 안 들어간 이유 → 사용자가 읽을 말 + 다음에 할 일. 숫자(서버 응답 409)는 아무것도 알려주지 않는다.
// 404 와 409 는 아예 다른 상황이라 문구도 갈라야 한다 — 사용자가 할 일이 서로 다르다.
//   404 = 그 tmux 세션이 죽었다            → 터미널을 열어 '다시 켜야' 한다
//   409 = 세션은 살아 있는데 그 화면이 이미 지나갔다(다른 곳에서 답했거나 클로드가 다음으로 갔다).
//         답은 같은 이름의 tmux 세션으로 가므로, 그대로 보내면 넘어간 화면·빈 셸에 숫자가 명령으로
//         실행된다 — 그래서 브릿지가 막는다(server.py 의 NoPicker). 아무것도 안 나갔고 카드도
//         지워지지 않았으니 → 터미널을 열어 '지금 뭐가 떠 있는지 눈으로 봐야' 한다.
const ANSWER_ERRORS = {
  404: p => `'${p}' 세션이 꺼져 있어 보내지 못했습니다 — [터미널 열기]로 다시 켜세요`,
  409: p => `'${p}' 화면이 이미 넘어갔습니다 (다른 곳에서 답했거나 클로드가 다음으로 갔습니다)`
            + ` — 아무것도 보내지 않았으니 [터미널 열기]로 지금 화면을 확인하세요`,
};

// 답을 보내는 길은 인박스와 대시보드 둘인데, 잠금(sentAnswers)·오류 문구·전송은 이 한 곳뿐이다.
// 화면마다 다른 것은 둘 — 안내 줄을 어디에 쓰는가(note), 보낸 뒤 무엇을 다시 그리는가(reload).
// 두 화면이 각자 잠그면 대시보드에서 보낸 답을 인박스에서 한 번 더 보낼 수 있다 → 터미널에 숫자가
// 두 번 찍히고 엉뚱한 항목이 눌린다. 그래서 잠금은 화면이 아니라 카드(project+표식)에 건다.
const INBOX_VIEW = { note: text => inboxNote(text), reload: reloadDecisions };

// 답을 보낸 뒤에는(성공·실패 모두) 두 화면을 함께 다시 그린다 — 한쪽만 갱신하면 같은 카드가
// 인박스에선 잠기고 대시보드에선 열려 있는 상태가 된다. 인박스가 열려 있으면 그 한 번이
// 대시보드까지 그린다(loadInbox → renderDash, 같은 응답이라 수가 어긋날 수 없다).
async function reloadDecisions() {
  if (!document.getElementById("inbox-view").hidden) await loadInbox();
  else await loadDash();
}

async function sendAnswer(card, div, type, value, label, view = INBOX_VIEW) {
  const key = cardKey(card), mark = cardMark(card);
  if (sentAnswers.get(key)?.mark === mark) return;   // 응답을 기다리는 동안의 연타도 여기서 끊긴다
  sentAnswers.set(key, { mark, label, at: Date.now() });
  lockCard(div, `보내는 중… ${label}`);       // 화면도 곧바로 잠근다(누른 것이 먹혔다는 신호)
  view.note("");
  let error = "";
  try {
    const res = await fetch(`${API}/api/decisions/answer`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: card.project, sessionId: card.sessionId, type, value }),
    });
    if (!res.ok)
      error = ANSWER_ERRORS[res.status]?.(card.project)
              ?? `보내지 못했습니다 (서버 응답 ${res.status})`;
  } catch {
    error = "맥에 연결되지 않아 보내지 못했습니다 — Tailscale 연결을 확인하세요";
  }
  if (error) {
    // 실패는 전부 '아무것도 안 들어간' 상태다(404·409·연결 끊김 모두) → 잠금을 풀어 다시 누를 수 있게.
    // 그 뒤 목록을 새로 불러오는 것도 계약이다: 잠금이 풀린 카드로 다시 그려지고, 409 라면 화면스캔이
    // 그 사이 읽어 둔 '지금 떠 있는 질문'으로 카드가 갱신돼 무엇을 답해야 하는지가 맞아진다.
    if (sentAnswers.get(key)?.mark === mark) sentAnswers.delete(key);
    await view.reload();
    view.note(error);
    return;
  }
  view.reload();
  refreshSummary();                           // 답했으니 상단 "결재 대기 n건"도 같이 갱신
}

// 답이 나가는 것은 전부 잠근다. [터미널 열기](.open-btn·.dash-term)만은 남긴다 — 카드에서
// 할 수 있는 일이 하나도 없어지면 사용자가 갈 곳이 없다.
const LOCKABLE = ".opt-btn, .send-btn, textarea, .dash-opt, .dash-send, .dash-ask";

function lockCard(div, text) {
  div.classList.add("sent");
  for (const node of div.querySelectorAll(LOCKABLE)) node.disabled = true;
  div.append(badge(text, div.classList.contains("dash-card") ? "dash-meta" : ""));
}

// alert() 금지(포커스·자동화를 망가뜨린다) — 안내는 인박스 상단 한 줄로.
// #inbox-cards 바깥에 두어 7초 폴링의 다시 그리기에 지워지지 않는다(다음 전송 때 지운다).
function inboxNote(text) {
  const note = document.getElementById("inbox-note");
  note.textContent = text;
  note.hidden = !text;
}
