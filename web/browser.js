"use strict";
// 브라우저 패널(1단계 — 로컬 미리보기) — 터미널 옆 4번째 칸에 그 프로젝트의 개발 서버를 iframe 으로 띄운다.
// 설계·검증 = docs/2026-09-20-브라우저-패널-검토와-계획.md (9절이 코덱스 재검증 반영본).
//
// 지키는 것:
//  * 주소는 어느 기기에서든 Serve https(`https://<이 화면의 호스트>:<외부포트>/`)로 통일한다 — localhost 분기
//    없음(사파리는 https 안의 http://localhost iframe 을 혼합콘텐츠로 막고, 폰의 localhost 는 폰 자신이다).
//  * 포트를 직접 내주지 않는다 — 브릿지의 서비스 목록(lsof)에서 고른 ID 로만 연다(/api/preview/open).
//  * "안 보이면 새 탭" 안내는 3초 뒤 띄우되 판정하지 않는다 — iframe 은 거부돼도 load 가 오고, 별도 출처는
//    성공해도 DOM 을 못 읽는다. 뒤로/앞으로/새로고침도 별도 출처라 못 건드린다 → 다시 열기·새 탭만.
//  * 열려 있는 동안 5분마다 갱신(renew) — 브릿지는 15분 갱신이 없으면 매핑을 거둔다(폰 절전·탭 강제종료).
//    닫을 때 브릿지에 '닫기'를 보내지 않는다: 다른 기기가 같은 포트를 보고 있을 수 있어 만료에 맡긴다.
//  * 폰(<900px)은 파일 패널처럼 전면으로 덮는다. 헤더의 🌐 는 가려지므로 패널 자체의 ✕ 로 돌아온다.
//  * 4칸이 안 들어가면(터미널 360px 하한) 파일 패널을 먼저 접는다 — 편집 중이면 fpGo 가 되묻는다.

const BP_RENEW_MS = 5 * 60 * 1000;
const BP_HINT_MS = 3000;
const bp = { project: null, port: null, url: null, services: [], renewTimer: null, hintTimer: null, gen: 0 };
const bpPanel = () => document.getElementById("browser-panel");
const bpFrame = () => document.getElementById("bp-frame");
const bpUrlBox = () => document.getElementById("bp-url");
const bpKey = project => `frog.browser.${project}`;

function bpSaved(project) {
  try { return JSON.parse(localStorage.getItem(bpKey(project)) || "null"); } catch { return null; }
}
// 이 프로젝트의 패널 기억 — {open, mode, url, port}. ★열림 여부까지 프로젝트별이다(대표 지적 2026-09-21:
// "오토프로그에서 열었는데 다른 프로젝트에 와도 열려 있고, 여기서 끄면 오토프로그도 꺼져 있다"). 패널의 hidden 은
// 화면 하나뿐이라 전역이었다 — 갈아탈 때마다 그 프로젝트의 open 값을 보고 열거나 접는다.
function bpSave(project, patch) {
  if (project) localStorage.setItem(bpKey(project), JSON.stringify({ ...(bpSaved(project) || {}), ...patch }));
}
function bpRemember() {
  if (bp.project && bp.url) bpSave(bp.project, { url: bp.url, port: bp.port });
}

function bpNote(text, action) {
  const note = document.getElementById("bp-note");
  note.replaceChildren();
  if (text) {
    note.append(text);
    if (action) {
      const btn = el("button", action.label);
      btn.onclick = action.onClick;
      note.append(btn);
    }
  }
  note.hidden = !text;
}

// ---------- 열기·닫기 ----------
const bpUserClose = () => { bpSave(bp.project, { open: false }); bpClose(); };
document.getElementById("browser-toggle").onclick = () => {
  if (!bpPanel().hidden) { bpUserClose(); return; }
  if (!currentSession) return;
  bpSave(baseOf(currentSession.name), { open: true });
  bpShow();
};
document.getElementById("bp-close").onclick = () => bpUserClose();
document.getElementById("bp-reload").onclick = () => {
  if (rbMode() === "browser") rbSend({ type: "reload" });
  else if (bp.url) bpLoad(bp.url);
};
document.getElementById("bp-newtab").onclick = () => {
  const url = rbMode() === "browser" ? rb.state?.url : bp.url;
  if (url) window.open(url, "_blank", "noopener");
};
bpUrlBox().onkeydown = e => {
  if (e.key === "Enter") (rbMode() === "browser" ? rbGo : bpGo)(e.target.value.trim());
  if (e.key === "Escape") e.target.blur();
};
document.getElementById("bp-services").onchange = e => {
  const svc = bp.services.find(s => s.id === e.target.value);
  if (svc) bpOpenService(svc);
  e.target.value = "";
};

// 4칸(목록·터미널·브라우저·파일)이 지금 창에 들어가나 — 터미널 360px 하한 기준. split.js 의 상수를 쓴다.
function bpFits() {
  const width = k => { const p = splitPane(k); return p.hidden ? 0 : p.getBoundingClientRect().width; };
  const splitters = [...document.querySelectorAll(".splitter")].reduce((n, s) => n + s.getBoundingClientRect().width, 0);
  return window.innerWidth - width("list") - width("file") - splitters - 5 - SPLIT_MIN.browser >= SPLIT_CENTER_MIN;
}

function bpShow() {
  if (isDesktop() && !fpPanel().hidden && !bpFits()) {
    // 파일 패널을 먼저 접는다(우선순위: 파일 → 브라우저). 저장 안 한 수정이 있으면 fpGo 가 되묻고, 그때는 안 연다.
    fpGo(() => { fpClose(); bpShow(); });
    return;
  }
  bpPanel().hidden = false;
  document.getElementById("browser-toggle").classList.add("on");
  bpReset(baseOf(currentSession.name));
}

function bpClose() {
  bp.gen++;
  rbDisconnect();
  clearInterval(bp.renewTimer); clearTimeout(bp.hintTimer);
  bp.renewTimer = bp.hintTimer = null;
  bpFrame().removeAttribute("src");
  bpPanel().hidden = true;
  document.getElementById("browser-toggle").classList.remove("on");
  bp.project = bp.port = bp.url = null;
  bpNote("");
}

// 프로젝트 하나를 새로 잡는다 — 서비스 목록을 읽고, 기억한 주소가 있으면 그것, 아니면 그 프로젝트 것이 하나뿐일 때 바로 연다.
async function bpReset(project) {
  const gen = ++bp.gen;
  bp.project = project; bp.port = bp.url = null;
  bpFrame().removeAttribute("src");
  bpUrlBox().value = "";
  // 모드는 프로젝트별로 기억한다 — 브라우저 모드면 원격 크롬에 붙고 끝(개발 서버 목록은 미리보기 모드 몫)
  const mode = bpSaved(project)?.mode || "preview";
  rbSetMode(mode);
  if (mode === "browser") return;
  bpNote("서비스 목록을 읽는 중…");
  const services = await bpFetchServices(project);
  if (gen !== bp.gen) return;                  // 그 사이 닫혔거나 다른 프로젝트로 갔다
  bp.services = services;
  bpRenderServices(services, project);
  const saved = bpSaved(project);
  // 자동으로 여는 후보 = 이 프로젝트 것 중 평문 HTTP 에 답하는 것만. 듣기만 하는 포트(pytest 가짜 서버·MCP·
  // adb 같은 것)는 목록에 "HTTP 아님"으로 남기되 손으로 골라야 열린다(2026-09-20 실사고: 흰 화면).
  const mine = services.filter(s => s.project === project && s.http != null);
  if (saved?.port && services.some(s => s.port === saved.port)) {
    bpOpenService(services.find(s => s.port === saved.port));
  } else if (mine.length === 1) {
    bpOpenService(mine[0]);
  } else if (mine.length > 1) {
    bpNote("이 프로젝트가 띄운 서버가 여럿입니다 — ▾ 에서 고르세요");
  } else if (services.length) {
    bpNote(`'${project}' 가 띄운 개발 서버가 없습니다 — ▾ 에서 다른 것을 고르거나 포트를 입력하세요`);
  } else {
    bpNote("지금 맥에서 듣는 개발 서버가 없습니다 — 서버를 띄운 뒤 ↻ 를 누르세요");
  }
}

async function bpFetchServices(project) {
  try {
    const res = await fetch(`${API}/api/preview/services?project=${encodeURIComponent(project)}`);
    if (!res.ok) throw new Error(res.status);
    return (await res.json()).services ?? [];
  } catch {
    bpNote("맥에 연결되지 않아 서비스 목록을 못 읽었습니다");
    return [];
  }
}

function bpRenderServices(services, project) {
  const sel = document.getElementById("bp-services");
  sel.replaceChildren(el("option", "▾ 포트"));
  sel.firstChild.value = "";
  for (const s of services) {
    const opt = el("option", `${s.port} · ${s.cmd}${s.project ? ` (${s.project})` : ""}${s.http == null ? " · HTTP 아님" : ""}`);
    opt.value = s.id;
    if (s.project === project) opt.className = "mine";
    sel.append(opt);
  }
}

// ---------- 열기 ----------
async function bpOpenService(svc) {
  const gen = bp.gen;
  bpNote(`${svc.port} 포트를 여는 중…`);
  try {
    const res = await fetch(`${API}/api/preview/open`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: svc.id, project: bp.project }),
    });
    const body = await res.json().catch(() => ({}));
    if (gen !== bp.gen) return;
    if (!res.ok) { bpNote(body.error ?? `열지 못했습니다 (서버 응답 ${res.status})`); return; }
    bp.port = body.port;
    bpLoad(`https://${location.hostname}:${body.port}/`);
    clearInterval(bp.renewTimer);
    bp.renewTimer = setInterval(bpRenew, BP_RENEW_MS);
  } catch {
    if (gen === bp.gen) bpNote("맥에 연결되지 않아 열지 못했습니다");
  }
}

function bpLoad(url) {
  if (rbMode() === "browser") return;         // 브라우저 모드로 넘어간 뒤 늦게 도착한 미리보기 열기는 버린다
  bp.url = url;
  bpUrlBox().value = url;
  bpRemember();
  bpNote("");
  bpFrame().src = url;
  clearTimeout(bp.hintTimer);
  // 판정이 아니라 안내다 — 느린 서버면 그냥 뜨고, 프레임을 거부하는 사이트면 이 줄이 유일한 출구다.
  bp.hintTimer = setTimeout(() => bpNote("안 보이면 → ", { label: "새 탭으로 열기",
    onClick: () => window.open(bp.url, "_blank", "noopener") }), BP_HINT_MS);
}

async function bpRenew() {
  if (!bp.port) return;
  try {
    const res = await fetch(`${API}/api/preview/renew`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ port: bp.port }),
    });
    if (res.status === 404 && bp.url && bp.services.length) {          // 브릿지가 이미 거둔 뒤 — 다시 연다
      const svc = bp.services.find(s => s.port === bpSaved(bp.project)?.port);
      if (svc) bpOpenService(svc);
    }
  } catch { /* 다음 갱신 때 */ }
}

// 주소칸 — 숫자만 치면 그 포트에서 듣는 서비스를 찾아 연다. 전체 주소면 그대로 iframe 에 넣는다(외부 사이트는
// 대개 프레임을 거부한다 — 3초 안내가 새 탭으로 안내한다). localhost 주소는 이 기기의 localhost 라 뜻이 없으니
// 포트만 뽑아 서비스로 바꾼다.
// 주소가 아닌 글("네이버")은 호스트명으로 해석하지 않는다 — 브라우저처럼 검색으로 보내되, 검색 사이트는 프레임을
// 거부하므로 새 탭에서 연다(2026-09-20 대표 실사용: "네이버" → xn--… DNS 실패 화면).
const bpLooksLikeHost = h => /^(\[[0-9a-f:.]+\]|localhost|(\d{1,3}\.){3}\d{1,3}|[a-z0-9-]+(\.[a-z0-9-]+)+)$/i.test(h);
function bpGo(raw) {
  if (!raw) return;
  let port = null;
  if (/^\d{2,5}$/.test(raw)) port = Number(raw);
  else {
    const explicit = /^[a-z]+:\/\//i.test(raw);
    const head = raw.replace(/^[a-z]+:\/\//i, "").split(/[/?#]/)[0].replace(/:\d+$/, "");
    if (!explicit && !bpLooksLikeHost(head)) {
      window.open(`https://www.google.com/search?q=${encodeURIComponent(raw)}`, "_blank", "noopener");
      bpNote(`'${raw}' 는 주소가 아니라서 새 탭에서 검색했습니다 — 이 칸에는 포트 번호나 주소를 넣으세요`);
      return;
    }
    try {
      const u = new URL(explicit ? raw : `https://${raw}`);
      if (LOOPBACK_HOSTS.has(u.hostname) || u.hostname === location.hostname) port = Number(u.port || (u.protocol === "https:" ? 443 : 80));
      else { bpLoad(u.toString()); return; }
    } catch { bpNote("주소를 읽지 못했습니다"); return; }
  }
  const svc = bp.services.find(s => s.port === port);
  if (svc) bpOpenService(svc);
  else bpNote(`${port} 포트에서 듣는 서비스가 없습니다 — 서버를 띄운 뒤 ↻`, { label: "목록 새로 읽기",
    onClick: () => bpReset(bp.project) });
}

// ---------- 터미널을 갈아타면 그 프로젝트의 기억대로 (열어 뒀으면 열고, 안 열어 뒀으면 접는다) ----------
// app.js 의 openTerminal 도 이 함수를 부른다 — 사이드바 클릭 한 번에 패널이 그 프로젝트 상태로 맞춰지게.
document.addEventListener("click", () => setTimeout(bpSync), true);
function bpSync() {
  const now = currentSession ? baseOf(currentSession.name) : null;
  if (!now) { if (!bpPanel().hidden) bpClose(); return; }      // 목록으로 나갔다 — 접는다(기억은 그대로)
  if (isGrid() && bpPanel().hidden) return;                    // 여러 칸 — 기억만으로 저절로 열지 않는다(🌐 로 직접)
  if (bpSaved(now)?.open !== true) {                           // 이 프로젝트에서는 연 적이 없다
    if (!bpPanel().hidden) bpClose();
    return;
  }
  if (bpPanel().hidden) { bpShow(); return; }                  // 열어 뒀던 프로젝트로 돌아왔다
  if (now !== bp.project) bpReset(now);
}
