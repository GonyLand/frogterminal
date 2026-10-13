// PC 에서 이미 떠 있는 클로드 창 — 목록(작업중·결재·완료)과 화면 보기. frog 로 연 세션이 아니라 Windows Terminal 등에서 직접 연 창이다.
// 서버: GET /api/ext (훅 카드 + pid 생존), WS /ws/ext/<pid> (콘솔 화면 → ANSI). 남의 창이라 크기는 서버가 알려 준 cols×rows 로 고정이다.
// app.js 의 panes/grid 와 따로 논다 — 팝업 하나가 터미널 하나를 직접 쥔다(CPU 팝업과 같은 방식).
(function () {
  'use strict';
  const STATE_LABEL = { working: '작업중…', waiting: '결재 필요', done: '완료 — 다음 지시 대기' };
  const POLL_MS = 7000;
  const MIN_FONT = 8, MAX_FONT = 15;
  const $ = (id) => document.getElementById(id);
  const box = $('ext-box'), list = $('ext-list'), toggle = $('ext-toggle');
  const pop = $('ext-pop');
  let windows = [];
  let cur = null;                 // 열려 있는 화면 { pid, term, ws, input, retry, timers… }

  // ── 목록 ────────────────────────────────────────────────────────────────
  function oneLine(s, n) {
    s = (s || '').replace(/\s+/g, ' ').trim();
    return s.length > n ? s.slice(0, n - 1) + '…' : s;
  }

  function renderList() {
    const sum = { working: 0, waiting: 0, done: 0 };
    windows.forEach((w) => { sum[w.state] = (sum[w.state] || 0) + 1; });
    box.hidden = windows.length === 0;
    toggle.textContent = `PC 클로드 창 ${windows.length} · 작업 ${sum.working} · 결재 ${sum.waiting} · 완료 ${sum.done}`;
    toggle.classList.toggle('hot', sum.waiting > 0);
    list.replaceChildren(...windows
      .slice().sort((a, b) => ({ waiting: 0, done: 1, working: 2 })[a.state] - ({ waiting: 0, done: 1, working: 2 })[b.state])
      .map((w) => {
        const li = document.createElement('li');
        li.dataset.pid = w.pid;
        const dot = document.createElement('em');
        dot.className = 'xdot ' + w.state;
        const body = document.createElement('div');
        body.className = 'xbody';
        const head = document.createElement('div');
        head.className = 'xhead';
        const name = document.createElement('b');
        name.textContent = w.project;
        const st = document.createElement('span');
        st.className = 'st ' + w.state;
        st.textContent = STATE_LABEL[w.state] || w.state;
        head.append(name, st);
        const sub = document.createElement('small');
        sub.textContent = oneLine(w.state === 'waiting' && w.message ? w.message : (w.asked || w.said), 70);
        body.append(head, sub);
        li.append(dot, body);
        li.addEventListener('click', () => openExt(w));
        return li;
      }));
    // 탭 제목에 숫자 — 폰 홈 화면 앱/탭 목록에서도 '내가 할 일이 생겼다'가 보이게
    const todo = sum.waiting + sum.done;
    document.title = (todo ? `(${todo}) ` : '') + 'frogterminal';
  }

  async function refresh() {
    if (document.hidden) return;
    try {
      const r = await fetch('/api/ext', { credentials: 'same-origin' });
      if (!r.ok) return;
      windows = (await r.json()).windows || [];
      renderList();
      if (cur) paintHead();
    } catch (_) { /* 다음 폴링에 */ }
  }

  toggle.addEventListener('click', () => {
    const open = toggle.getAttribute('aria-expanded') !== 'true';
    toggle.setAttribute('aria-expanded', String(open));
    list.hidden = !open;
  });

  // ── 화면 ────────────────────────────────────────────────────────────────
  const unesc = (k) => k.replace(/\\x([0-9a-f]{2})/gi, (_, h) => String.fromCharCode(parseInt(h, 16)))
    .replace(/\\t/g, '\t').replace(/\\r/g, '\r');

  function paintHead() {
    const w = windows.find((x) => x.pid === cur.pid);
    $('ext-name').textContent = w ? w.project : `pid ${cur.pid}`;
    const st = $('ext-state');
    st.textContent = w ? (STATE_LABEL[w.state] || '') : '';
    st.className = 'st ' + (w ? w.state : '');
    $('ext-input').textContent = cur.input ? '입력 켜짐' : '입력 잠김';
    $('ext-input').setAttribute('aria-pressed', String(cur.input));
    $('ext-input').classList.toggle('on', cur.input);
  }

  function fitFont() {
    if (!cur || !cur.term || !cur.cols) return;
    const avail = $('ext-scroll').clientWidth - 8;
    const size = Math.max(MIN_FONT, Math.min(MAX_FONT, Math.floor(avail / (cur.cols * 0.6))));
    if (cur.term.options.fontSize !== size) cur.term.options.fontSize = size;
  }

  function ensureTerm(cols, rows) {
    if (!cur.term) {
      const opts = typeof termOptions === 'function' ? termOptions() : {};
      cur.term = new Terminal({ ...opts, cols, rows, scrollback: 0, cursorBlink: false, allowProposedApi: true });
      cur.term.open($('ext-term'));
      cur.term.onData((d) => sendKeys(d));
    } else if (cur.cols !== cols || cur.rows !== rows) {
      cur.term.resize(cols, rows);
    }
    cur.cols = cols; cur.rows = rows;
    fitFont();
  }

  function sendKeys(d) {
    if (!cur || !cur.input || !cur.ws || cur.ws.readyState !== WebSocket.OPEN) return false;
    cur.ws.send(new TextEncoder().encode(d));
    return true;
  }

  function connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${location.host}/ws/ext/${cur.pid}`);
    ws.binaryType = 'arraybuffer';
    cur.ws = ws;
    ws.onopen = () => {
      cur.retry = 0;
      if (cur.input) ws.send(JSON.stringify({ type: 'input', on: true }));   // 재접속해도 켜 둔 입력은 그대로
      cur.ping = setInterval(() => { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'ping' })); }, 15000);
    };
    ws.onmessage = (e) => {
      if (typeof e.data === 'string') {
        let m; try { m = JSON.parse(e.data); } catch (_) { return; }
        if (m.type === 'ext' && m.cols && m.rows) ensureTerm(m.cols, m.rows);
      } else if (cur && cur.term) {
        cur.term.write(new Uint8Array(e.data));
      }
    };
    ws.onclose = (ev) => {
      if (!cur || cur.ws !== ws) return;
      clearInterval(cur.ping);
      if (cur.closing || cur.gone) return;
      if (cur.retry >= 5) { if (cur.term) cur.term.write('\r\n\x1b[0m[연결이 끊겼다 — 닫고 다시 연다]\r\n'); return; }
      cur.retry += 1;
      setTimeout(() => { if (cur && cur.ws === ws && !cur.closing) connect(); }, Math.min(15000, 500 * 2 ** cur.retry));
    };
  }

  function openExt(w) {
    closeExt();
    cur = { pid: w.pid, term: null, ws: null, input: false, retry: 0, cols: 0, rows: 0, closing: false, gone: false };
    pop.hidden = false;
    $('ext-term').replaceChildren();
    paintHead();
    connect();
  }

  function closeExt() {
    if (!cur) return;
    cur.closing = true;
    clearInterval(cur.ping);
    try { cur.ws && cur.ws.close(); } catch (_) { /* 이미 닫힘 */ }
    try { cur.term && cur.term.dispose(); } catch (_) { /* 이미 닫힘 */ }
    cur = null;
    pop.hidden = true;
  }

  $('ext-close').addEventListener('click', closeExt);
  $('ext-input').addEventListener('click', () => {
    if (!cur) return;
    cur.input = !cur.input;
    if (cur.ws && cur.ws.readyState === WebSocket.OPEN) cur.ws.send(JSON.stringify({ type: 'input', on: cur.input }));
    paintHead();
  });
  $('ext-keys').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-k]');
    if (!b || !cur) return;
    if (!sendKeys(unesc(b.dataset.k))) {
      $('ext-input').classList.add('nudge');
      setTimeout(() => $('ext-input').classList.remove('nudge'), 700);     // 잠겨 있어서 안 들어갔다는 신호
    }
  });
  window.addEventListener('resize', fitFont);

  refresh();
  setInterval(refresh, POLL_MS);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  window.extWindows = () => windows;   // 시험·디버깅용
})();
