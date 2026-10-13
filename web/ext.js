// PC 에서 이미 떠 있는 클로드 창 — 목록(작업중·결재·완료)과 화면 보기. frog 로 연 세션이 아니라 Windows Terminal 등에서 직접 연 창이다.
// 서버: GET /api/ext (훅 카드 + pid 생존), WS /ws/ext/<pid> (콘솔 화면 → ANSI). 남의 창이라 크기는 서버가 알려 준 cols×rows 로 고정이다.
// app.js 의 panes/grid 와 따로 논다 — 팝업 하나가 터미널 하나를 직접 쥔다(CPU 팝업과 같은 방식).
(function () {
  'use strict';
  const STATE_LABEL = { working: '작업중…', waiting: '결재 필요', done: '완료 — 다음 지시 대기' };
  const POLL_MS = 7000;
  const STATE_SHORT = { working: '작업중', waiting: '결재 필요', done: '완료' };     // 팝업 머리는 한 줄 — 긴 이름과 같은 줄에 앉는다
  // 폭맞춤 = 창 폭 전체를 폰 한 화면에(글자는 작아진다) · 크게 = READ_FONT 로 키우고 옆으로 민다. 폰 폭 100열 안팎이면 맞춤은 6~7px.
  const MIN_FONT = 5, MAX_FONT = 15, READ_FONT = 11;
  // 폰엔 D2Coding 이 없어 한글이 영문 칸의 2배 폭으로 안 떨어지고 자간이 벌어졌다 → 한글이 정확히 2칸인 나눔고딕코딩(index.html, CDN)을 뒤에 둔다.
  const EXT_FONT = '"D2Coding", "Nanum Gothic Coding", "JetBrains Mono", ui-monospace, Menlo, Consolas, monospace';
  const $ = (id) => document.getElementById(id);
  let zoom = 'fit';                 // 'fit' | 'read' — 기기마다 기억한다
  try { if (localStorage.getItem('frog.ext.zoom') === 'read') zoom = 'read'; } catch (_) { /* 저장소 막힘 — 맞춤으로 */ }
  let cellRatio = 0.6;              // 글자 칸 폭 / 글자 크기 — 폰트마다 달라 화면에서 재서 고친다(fitFont)
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

  async function refresh(force) {
    if (document.hidden && !force) return;
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
    st.textContent = w ? (STATE_SHORT[w.state] || '') : '';
    st.className = 'st ' + (w ? w.state : '');
    $('ext-zoom').textContent = zoom === 'read' ? '크게' : '폭맞춤';
    $('ext-zoom').setAttribute('aria-pressed', String(zoom === 'read'));
    $('ext-zoom').classList.toggle('on', zoom === 'read');
    $('ext-input').textContent = cur.input ? '입력 켜짐' : '입력 잠김';
    $('ext-input').setAttribute('aria-pressed', String(cur.input));
    $('ext-input').classList.toggle('on', cur.input);
  }

  function fitFont() {
    if (!cur || !cur.term || !cur.cols) return;
    const avail = $('ext-scroll').clientWidth - 8;
    const size = zoom === 'read' ? READ_FONT
      : Math.max(MIN_FONT, Math.min(MAX_FONT, Math.floor(avail / (cur.cols * cellRatio) * 2) / 2));
    if (cur.term.options.fontSize !== size) cur.term.options.fontSize = size;
    // 칸 폭은 폰트·기기 픽셀에 따라 0.6 이 아니다 — 그린 화면 폭으로 비율을 다시 재서 한 번 더 맞춘다(두 번까지만).
    const me = cur, tries = (fitFont.tries = (fitFont.tries || 0) + 1);
    requestAnimationFrame(() => {
      const sc = cur === me && me.term ? $('ext-term').querySelector('.xterm-screen') : null;
      if (!sc || !sc.offsetWidth || !me.cols) { fitFont.tries = 0; return; }
      const r = sc.offsetWidth / (me.cols * me.term.options.fontSize);
      if (r > 0.3 && r < 1 && Math.abs(r - cellRatio) > 0.03 && tries < 3) { cellRatio = r; fitFont(); } else fitFont.tries = 0;
    });
  }

  function ensureTerm(cols, rows) {
    if (!cur.term) {
      const opts = typeof termOptions === 'function' ? termOptions() : {};
      cur.term = new Terminal({ ...opts, fontFamily: EXT_FONT, cols, rows, scrollback: 0, cursorBlink: false, allowProposedApi: true });
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
    const me = cur = { pid: w.pid, term: null, ws: null, input: false, retry: 0, cols: 0, rows: 0, closing: false, gone: false };
    pop.hidden = false;
    $('ext-term').replaceChildren();
    paintHead();
    // 글자 칸 폭은 터미널을 여는 순간 잰다 — 웹폰트가 아직 안 왔으면 엉뚱한 폭으로 굳는다. 영문·한글 조각만 먼저 받고(최대 2초) 연다.
    const fontsReady = document.fonts && document.fonts.load
      ? Promise.race([document.fonts.load('14px "Nanum Gothic Coding"', 'A가'), new Promise((r) => setTimeout(r, 2000))]).catch(() => {})
      : Promise.resolve();
    fontsReady.then(() => { if (cur === me && !me.closing) connect(); });
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
  $('ext-zoom').addEventListener('click', () => {
    zoom = zoom === 'read' ? 'fit' : 'read';
    try { localStorage.setItem('frog.ext.zoom', zoom); } catch (_) { /* 저장소 막힘 — 이번만 */ }
    paintHead();
    fitFont();
  });
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

  // 알림을 눌러 들어온 경우 — 그 창(pid)의 팝업을 바로 연다. 목록에 아직 없으면(새 창·갓 켠 앱) 한 번 받아서 다시 찾는다.
  async function openByPid(pid) {
    pid = Number(pid);
    if (!pid || (cur && cur.pid === pid)) return;
    let w = windows.find((x) => x.pid === pid);
    if (!w) { await refresh(true); w = windows.find((x) => x.pid === pid); }
    if (w) openExt(w);                                  // 그새 끝난 창이면 목록만 보인다
  }

  // 알림 클릭 — 열려 있는 탭엔 서비스워커가 메시지로, 앱이 꺼져 있었으면 /?ext=<pid> 로 새로 열린다
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.addEventListener('message', (e) => {
      const m = e.data;
      if (m && m.type === 'frog-open-ext') openByPid(m.pid);
    });
  }
  const want = new URLSearchParams(location.search).get('ext');
  if (want) { try { history.replaceState(null, '', location.pathname); } catch (_) { /* 주소줄 못 바꿔도 팝업은 열린다 */ } }   // 새로고침이 팝업을 또 열지 않게

  refresh(!!want).then(() => { if (want) openByPid(want); });
  setInterval(refresh, POLL_MS);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  window.extWindows = () => windows;   // 시험·디버깅용
})();
