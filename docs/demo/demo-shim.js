"use strict";
// 데모용 가짜 브릿지 — 브라우저 안에서 fetch 와 WebSocket 을 바꿔쳐 실제 화면 코드(web/*.js)가
// 서버 없이 돌게 한다. 터미널에 흐르는 글자는 전부 여기 적힌 샘플이며 실제 세션이 아니다.
//
// 장면은 주소의 해시로 고른다(render.py 가 스크린샷에 쓴다):
//   (없음)  격자 4칸 + 하단 공통 HUD        #cpu    CPU(htop) 팝업 열림
//   #inbox  결재 인박스 열림                #phone  폰 폭에서 1칸(뷰포트만 좁히면 된다)

(() => {
  const ESC = "\x1b[";
  const C = {                                   // 자주 쓰는 ANSI 조각
    r: ESC + "0m", b: ESC + "1m", d: ESC + "2m",
    red: ESC + "31m", green: ESC + "32m", yellow: ESC + "33m", blue: ESC + "34m",
    magenta: ESC + "35m", cyan: ESC + "36m", gray: ESC + "90m", bblue: ESC + "94m",
    bgCyan: ESC + "46m", bgBlue: ESC + "44m", bgGreen: ESC + "42m", black: ESC + "30m",
    bgSel: ESC + "48;5;31m",
  };
  const NL = "\r\n";

  // ---------- 세션·결재·설정 ----------
  const SESSIONS = [
    { name: "my-app",     alive: true, path: "/Users/you/projects/my-app",     status: "working",  pending: 0, agent: "claude" },
    { name: "blog",       alive: true, path: "/Users/you/projects/blog",       status: "done",     pending: 0, agent: "codex" },
    { name: "docs-site",  alive: true, path: "/Users/you/projects/docs-site",  status: "decision", pending: 1, agent: "claude" },
    { name: "api-server", alive: true, path: "/Users/you/projects/api-server", status: "done",     pending: 0, agent: "claude" },
    { name: "landing",    alive: false, path: "/Users/you/projects/landing",   status: "off",      pending: 0 },
  ];
  const DECISIONS = [
    { sessionId: "docs-1", project: "docs-site", cwd: "/Users/you/projects/docs-site", machine: "mac",
      kind: "picker", message: "문서 사이트 테마를 어느 것으로 할까요?", updatedAt: new Date().toISOString(),
      live: true, question: "문서 사이트 테마를 어느 것으로 할까요?",
      asked: "문서 사이트 뼈대를 잡아줘. 테마는 나한테 먼저 물어보고.",
      said: "문서 사이트 테마를 어느 것으로 할까요?",
      options: [{ n: 1, label: "라이트" }, { n: 2, label: "다크" }, { n: 3, label: "시스템 설정 따라가기" }] },
    { sessionId: "api-1", project: "api-server", cwd: "/Users/you/projects/api-server", machine: "mac",
      kind: "done", message: "", updatedAt: new Date(Date.now() - 4 * 60000).toISOString(), live: true,
      asked: "FastAPI 로 /health 엔드포인트 하나 있는 main.py 를 만들어줘",
      said: "main.py 와 requirements.txt 를 만들고, 서버를 띄워 curl 로 200 OK 를 확인한 뒤 내렸습니다." },
  ];
  const SETTINGS = { projectsRoot: "/Users/you/projects", agent: "claude", bypass: true, cont: true, rc: true,
                     commands: { claude: "", codex: "" } };
  const HUD = [
    `${C.cyan}▶${C.r}${C.b}work${C.r} ${C.d}5h${C.r} ${C.bblue}█${C.d}░░░░░${C.r} ${C.bblue} 22%${C.r} ${C.d}2h13m${C.r} ${C.d}7d${C.r} ${C.bblue}██${C.d}░░░░${C.r} ${C.bblue} 31%${C.r} ${C.d}2d 4h${C.r}`,
    ` ${C.d}home${C.r} ${C.d}5h${C.r} ${C.bblue}███${C.d}░░░${C.r} ${C.bblue} 58%${C.r} ${C.d}1h02m${C.r} ${C.d}7d${C.r} ${C.bblue}████${C.d}░░${C.r} ${C.bblue} 65%${C.r} ${C.d}3d 9h${C.r}`,
    `${C.green}●${C.r}${C.d}cdx ${C.r} ${C.d}7d${C.r} ${C.bblue}${C.d}░░░░░░${C.r} ${C.bblue}  4%${C.r} ${C.d}6d16h${C.r} ${C.d}cr${C.r} 1,534`,
  ];

  // ---------- 터미널 샘플 화면 ----------
  const claudeHead = (proj) => [
    `${C.yellow}▐█▌${C.r} ${C.b}Claude Code${C.r} ${C.d}v2.1${C.r}`,
    `${C.yellow}▐█▌${C.r} ${C.d}Fable 5.1 · ~/projects/${proj}${C.r}`,
    "",
  ];
  const claudeFoot = (proj, ctx) => [
    "",
    `${C.d}──────────────────────────────────────────────────────────────────${C.r}`,
    `${C.d}Fable 5.1  ⚙high  ctx ${C.r}${C.green}█${C.d}░░░░░░░${C.r} ${C.d}${ctx}%  ${proj}(main)${C.r}`,
    `${C.d}⏵⏵ bypass permissions on (shift+tab to cycle) · ← 1 agent${C.r}`,
  ];
  const SCREENS = {
    "my-app": [
      ...claudeHead("my-app"),
      `${C.d}>${C.r} node 내장 http 모듈만 써서 8080 포트에 '안녕하세요'를 내는 server.js 를 만들어줘`,
      "",
      `${C.green}●${C.r} 의존성 없이 http 모듈만 쓰겠습니다.`,
      "",
      `${C.green}●${C.r} ${C.b}Write${C.r}(server.js)`,
      `  ⎿  Wrote 9 lines to server.js`,
      `     ${C.green}+${C.r} const http = require("http");`,
      `     ${C.green}+${C.r} http.createServer((req, res) => {`,
      `     ${C.green}+${C.r}   res.writeHead(200, { "Content-Type": "text/plain; charset=utf-8" });`,
      `     ${C.green}+${C.r}   res.end("안녕하세요");`,
      `     ${C.green}+${C.r} }).listen(8080);`,
      "",
      `${C.green}●${C.r} ${C.b}Bash${C.r}(node server.js & sleep 1; curl -s localhost:8080; kill %1)`,
      `  ⎿  안녕하세요`,
      "",
      `${C.green}●${C.r} server.js 를 만들고 8080 에서 "안녕하세요"가 나오는 것까지 확인했습니다.`,
      "",
      `${C.magenta}✻${C.r} ${C.d}Baked for 14s · ↓ 1.8k tokens${C.r}`,
      ...claudeFoot("my-app", 7),
    ],
    "blog": [
      `${C.d}╭─${C.r} ${C.b}>_ OpenAI Codex${C.r} ${C.d}(v0.160)${C.r}`,
      `${C.d}│${C.r}   ~/projects/blog`,
      `${C.d}│${C.r}   permissions: ${C.red}YOLO mode${C.r}`,
      `${C.d}╰─${C.r}`,
      "",
      `${C.cyan}› ${C.r}README.md 를 읽고 블로그 글 제목 3개를 제안해줘`,
      "",
      `${C.magenta}•${C.r} README 를 읽었습니다. 글 후보 셋입니다.`,
      "",
      `  1. ${C.b}맥 한 대로 폰에서 코딩 에이전트 돌리기${C.r} — 설치 20분 기록`,
      `  2. ${C.b}tmux 세션이 안 죽는다는 것의 의미${C.r} — 지하철에서 이어서 본 작업 로그`,
      `  3. ${C.b}결재 인박스를 만든 이유${C.r} — 에이전트가 멈춰 묻는 순간을 모으기`,
      "",
      `${C.d}  어느 것으로 초안을 쓸까요? 번호를 주시면 바로 시작합니다.${C.r}`,
      "",
      `${C.cyan}› ${C.r}${C.d}Ask Codex to do anything${C.r}`,
      `${C.d}  GPT-6-Astra medium · main · Context 97% left · weekly 95% left${C.r}`,
    ],
    "docs-site": [
      ...claudeHead("docs-site"),
      `${C.d}>${C.r} 문서 사이트 뼈대를 잡아줘. 테마는 나한테 먼저 물어보고.`,
      "",
      `${C.green}●${C.r} 뼈대를 잡기 전에 한 가지만 정하겠습니다.`,
      "",
      `${C.b}╭─ 문서 사이트 테마를 어느 것으로 할까요?${C.r}`,
      `${C.b}│${C.r}  ${C.cyan}❯ 1.${C.r} 라이트`,
      `${C.b}│${C.r}    2. 다크`,
      `${C.b}│${C.r}    3. 시스템 설정 따라가기`,
      `${C.b}│${C.r}  ${C.d}Enter to confirm · Esc to cancel${C.r}`,
      `${C.b}╰─${C.r}`,
      "",
      `${C.yellow}⏸${C.r} ${C.d}답을 기다리는 중 — 폰에서는 결재 인박스 카드로 바로 답할 수 있다${C.r}`,
      ...claudeFoot("docs-site", 3),
    ],
    "api-server": [
      ...claudeHead("api-server"),
      `${C.d}>${C.r} FastAPI 로 /health 엔드포인트 하나 있는 main.py 를 만들어줘`,
      "",
      `${C.green}●${C.r} ${C.b}Write${C.r}(main.py)`,
      `  ⎿  Wrote 8 lines to main.py`,
      `     ${C.green}+${C.r} from fastapi import FastAPI`,
      `     ${C.green}+${C.r} app = FastAPI(title="api-server")`,
      `     ${C.green}+${C.r} @app.get("/health")`,
      `     ${C.green}+${C.r} def health() -> dict[str, str]:`,
      `     ${C.green}+${C.r}     return {"status": "ok"}`,
      "",
      `${C.green}●${C.r} ${C.b}Bash${C.r}(.venv/bin/uvicorn main:app --port 8765 & sleep 2; curl -i localhost:8765/health)`,
      `  ⎿  HTTP/1.1 200 OK`,
      `     {"status":"ok"}`,
      "",
      `${C.green}●${C.r} main.py 와 requirements.txt 를 만들고 200 OK 를 확인한 뒤 서버를 내렸습니다.`,
      "",
      `${C.magenta}✻${C.r} ${C.d}Baked for 39s · ↓ 1.3k tokens${C.r}`,
      ...claudeFoot("api-server", 9),
    ],
  };

  // htop 화면 — 실제 htop 의 배치를 따라 그린다(숫자는 샘플).
  function bar(pct, width, color) {
    const n = Math.round(pct / 100 * width);
    return `${C.d}[${C.r}${color}${"|".repeat(n)}${C.r}${" ".repeat(width - n)}${C.d}${String(pct.toFixed(1)).padStart(5)}%]${C.r}`;
  }
  const cores = [63.3, 58.3, 54.6, 48.4, 1.3, 2.6, 0.7, 21.2, 1.3, 0.0, 25.0, 17.8, 13.1, 9.9];
  const htopRows = [
    ["28466", "you", "24", "0", "418G", "2506M", "R", "23.5", "6.8", "3h58:18", "/opt/homebrew/share/android-commandlinetools/emulator/qemu"],
    ["57888", "you", "17", "0", "421G", "1698M", "S", "22.5", "4.6", "4h46:14", "/System/Library/Frameworks/Virtualization.framework"],
    ["28975", "you", "17", "0", "421G", "1430M", "S", "16.1", "3.9", "2h03:43", "/opt/homebrew/share/android-commandlinetools/emulator"],
    ["32879", "you", "17", "0", "1861G", "913M", "S", "4.7", "2.5", "11:22.21", "/Applications/Google Chrome.app/Contents/Frameworks"],
    ["77108", "you", "17", "0", "1860G", "681M", "S", "0.1", "1.8", "6:40.14", "/Applications/ChatGPT.app/Contents/Frameworks"],
    ["37144", "you", "17", "0", "512G", "587M", "S", "1.3", "1.6", "59:03.77", "/Applications/Google Chrome.app/Contents/Frameworks"],
    ["2358", "you", "17", "0", "421G", "581M", "S", "3.3", "1.6", "3h53:48", "claude --dangerously-skip-permissions --remote-control"],
    ["15138", "you", "17", "0", "420G", "509M", "S", "4.2", "1.4", "1h30:21", "claude --dangerously-skip-permissions --remote-control"],
    ["97778", "you", "17", "0", "421G", "446M", "S", "0.4", "1.2", "2h05:35", "zsh -lic claude --dangerously-skip-permissions"],
    ["52980", "you", "17", "0", "1872G", "438M", "S", "1.3", "1.2", "30:57.20", "/Applications/Claude.app/Contents/Frameworks"],
    ["2353", "you", "24", "0", "421G", "436M", "S", "0.5", "1.2", "3h23:03", "claude --dangerously-skip-permissions --remote-control"],
    ["51797", "you", "17", "0", "420G", "436M", "S", "4.9", "1.2", "1h45:41", "claude --dangerously-skip-permissions --remote-control"],
    ["34258", "you", "17", "0", "420G", "430M", "S", "8.6", "1.2", "1h39:44", "claude --dangerously-skip-permissions --remote-control"],
    ["2330", "you", "24", "0", "421G", "428M", "S", "0.1", "1.2", "3h07:59", "claude --dangerously-skip-permissions --remote-control"],
  ];
  function htopScreen(cols, rows) {
    const w = Math.max(60, cols);
    const ncol = w >= 120 ? 4 : 2;               // 팝업 폭에 맞춰 코어 묶음 수를 정한다(실제 htop 도 폭 따라 접는다)
    const cw = Math.min(22, Math.floor((w - ncol * 12) / ncol));   // 코어 막대 폭
    const coreRows = Math.ceil(cores.length / ncol);
    const out = [];
    const col = (i) => `${C.cyan}${String(i).padStart(2)}${C.r}`;
    for (let i = 0; i < coreRows; i++) {
      const parts = [];
      for (let c = 0; c < ncol; c++) {
        const k = i + c * coreRows;
        if (cores[k] !== undefined) parts.push(`${col(k)}${bar(cores[k], cw, cores[k] > 40 ? C.green : C.blue)}`);
      }
      out.push(parts.join("  "));
    }
    out.push(`${C.cyan}Mem${C.r}${C.d}[${C.r}${C.green}${"|".repeat(29)}${C.yellow}${"|".repeat(9)}${C.r}${" ".repeat(4)}${C.d}29.6G/36.0G]${C.r}  ${C.cyan}Tasks: ${C.r}${C.b}743${C.r}, ${C.b}4413${C.r} thr; ${C.green}9${C.r} running`);
    out.push(`${C.cyan}Swp${C.r}${C.d}[${C.r}${C.red}${"|".repeat(31)}${C.r}${" ".repeat(11)}${C.d}5.31G/7.00G]${C.r}  ${C.cyan}Load average: ${C.r}${C.b}4.43 4.58 4.28${C.r}`);
    out.push(`${" ".repeat(48)}${C.cyan}Uptime: ${C.r}${C.b}10 days, 11:01:35${C.r}`);
    out.push("");
    const head = "  PID USER       PRI  NI  VIRT   RES S CPU% MEM%   TIME+  Command";
    out.push(`${C.black}${C.bgGreen}${head.padEnd(w)}${C.r}`);
    const fit = Math.max(3, rows - out.length - 3);   // 팝업 높이에 맞춰 프로세스 줄 수를 자른다(실제 htop 도 그렇다)
    htopRows.slice(0, fit).forEach((r, i) => {
      const [pid, user, pri, ni, virt, res, s, cpu, mem, t, cmd] = r;
      const line = `${pid.padStart(5)} ${user.padEnd(10)} ${pri.padStart(3)} ${ni.padStart(3)} ${virt.padStart(5)} ${res.padStart(5)} ${s} ${cpu.padStart(4)} ${mem.padStart(4)} ${t.padStart(8)}  ${cmd}`;
      out.push(i === 0 ? `${C.black}${C.bgCyan}${line.slice(0, w).padEnd(w)}${C.r}` : line.slice(0, w));
    });
    const fkeys = [["1", "Help"], ["2", "Setup"], ["3", "Search"], ["4", "Filter"], ["5", "Tree"], ["6", "SortBy"], ["7", "Nice -"], ["8", "Nice +"], ["9", "Kill"], ["10", "Quit"]];
    const footer = fkeys.map(([n, l]) => `${C.b}F${n}${C.r}${C.black}${C.bgCyan}${l.padEnd(6)}${C.r}`).join("");
    return { lines: out, footer };
  }

  // ---------- fetch 바꿔치기 ----------
  const json = (obj, status = 200) =>
    new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
  const routes = [
    [/\/api\/sessions\/(kill|kill-all|restore)$/, () => json({ ok: true, killed: [], restored: [] })],
    [/\/api\/sessions$/, () => json({ sessions: SESSIONS.map(s => ({ ...s, hidden: false, fav: null })), restorable: 0 })],
    [/\/api\/summary$/, () => json({ alive: 4, pending: 1, waiting: 1, dead: 0, web: "demo" })],
    [/\/api\/decisions\/answer$/, () => json({ ok: true })],
    [/\/api\/decisions$/, () => json({ decisions: DECISIONS })],
    [/\/api\/settings$/, () => json({ settings: SETTINGS, agents: ["claude", "codex", "shell"], home: "/Users/you" })],
    [/\/api\/agents$/, () => json({ agents: {
      claude: { installed: true, version: "2.1.0 (Claude Code)", email: "you@example.com", org: "", latestVersion: "2.1.0", updateAvailable: false },
      codex: { installed: true, version: "codex-cli 0.160", loggedIn: true, latestVersion: "0.160", updateAvailable: false } } })],
    [/\/api\/usage$/, () => json({ usage: { account: "you@example.com", windows: [
      { name: "5h", pct: 22, reset: "23:40", left: "2h 13m" }, { name: "7d", pct: 31, reset: "Oct 14 09:00", left: "2d 4h" }] } })],
    [/\/api\/hud$/, () => json({ lines: HUD })],
    [/\/api\/hidden$/, () => json({ hidden: [] })],
    [/\/api\/favorites$/, () => json({ favorites: [] })],
    [/\/api\/uploads$/, () => json({ bytes: 0, files: 0 })],
    [/\/api\/preview\/services/, () => json({ services: [], active: {} })],
    [/\/api\/files/, () => json({ entries: [
      { name: "README.md", type: "file", size: 1200 }, { name: "src", type: "dir" }, { name: "docs", type: "dir" }] })],
  ];
  const realFetch = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const url = typeof input === "string" ? input : input.url;
    const path = url.replace(/^[a-z]+:\/\/[^/]+/, "");
    const wait = new Promise(r => setTimeout(r, 30));   // 진짜 브릿지처럼 한 박자 늦게 — 모든 스크립트가 실린 뒤 첫 목록이 온다
    for (const [re, handler] of routes) if (re.test(path.split("?")[0])) return wait.then(() => handler(path, init));
    if (path.startsWith("/api/")) return wait.then(() => json({}, 404));
    return realFetch(input, init);
  };

  // ---------- WebSocket 바꿔치기 ----------
  const enc = new TextEncoder();
  class DemoSocket {
    constructor(url) {
      this.url = url; this.readyState = 0; this.binaryType = "blob";
      this.onopen = null; this.onmessage = null; this.onclose = null; this.onerror = null;
      const m = url.match(/\/ws\/([^?]+)/);
      const name = decodeURIComponent(m ? m[1] : "");
      const cols = Number((url.match(/cols=(\d+)/) || [])[1] || 100);
      const rows = Number((url.match(/rows=(\d+)/) || [])[1] || 30);
      setTimeout(() => {
        this.readyState = 1; this.onopen?.({});
        this.emit("\x1b[?1000h\x1b[?1006h");    // tmux 처럼 마우스 리포트 on
        if (name === "_frogtop") {
          const { lines, footer } = htopScreen(cols, rows);
          this.emit(ESC + "2J" + ESC + "H" + lines.join(NL) + NL + NL + footer);
        } else {
          this.emit((SCREENS[name.replace(/@\d+$/, "")] ?? [`$ `]).join(NL));
        }
      }, 60);
    }
    emit(text) { const data = enc.encode(text).buffer; this.onmessage?.({ data }); }
    send() { /* 데모 — 입력은 아무 데도 안 간다 */ }
    close(code = 1000) { if (this.readyState === 3) return; this.readyState = 3; this.onclose?.({ code }); }
    addEventListener(type, fn) { this["on" + type] = fn; }
  }
  window.WebSocket = DemoSocket;

  // ---------- 장면 ----------
  // 첫 목록이 오면 app.js 가 gridBoot() 로 기억한 칸 수를 연다 — 기억값을 미리 심어 4칸이 뜨게 한다.
  try { localStorage.setItem("frog.grid", JSON.stringify({ n: 4, names: ["my-app", "blog", "docs-site", "api-server"] })); }
  catch { /* 저장소가 막혀 있으면 아래에서 직접 연다 */ }
  localStorage.removeItem?.("frog.theme");

  const scene = location.hash.replace("#", "");
  window.addEventListener("load", () => {
    setTimeout(() => {
      if (typeof panes !== "undefined" && !panes.some(p => p.session) && typeof setGrid === "function" && innerWidth >= 900)
        setGrid(4, ["my-app", "blog", "docs-site", "api-server"], false);
      setTimeout(() => {
        if (scene === "cpu" && typeof openCpu === "function") openCpu();
        if (scene === "inbox" && typeof openInbox === "function") openInbox();
        if (scene === "phone" && typeof openTerminal === "function") {
          const s = SESSIONS.find(x => x.name === "docs-site");
          if (s) openTerminal(s);
        }
        setTimeout(() => {                      // 첫 목록 뒤 늦게 도는 정리(showView 등)가 패널을 닫았으면 다시 연다
          if (scene === "inbox" && document.getElementById("inbox-view")?.hidden) openInbox();
          if (scene === "cpu" && document.getElementById("cpu-pop")?.hidden) openCpu();
          document.body.dataset.demoReady = "1";   // render.py 가 이 표시를 기다린다
        }, 600);
      }, 500);
    }, 900);
  });
})();
