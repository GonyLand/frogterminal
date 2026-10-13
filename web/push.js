// 알림 켜기/끄기/시험 — 서비스워커(sw.js) 등록 + 브라우저 푸시 구독을 브릿지(/api/push/*)에 알린다.
// 폰에서 한 번만 켜면 PC 클로드 창이 결재를 기다리거나 끝날 때 화면이 꺼져 있어도 알림이 온다(HTTPS 필요 — 테일넷 serve 가 해결).
(function () {
  'use strict';
  const row = document.getElementById('push-row');
  if (!row || !('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) return;   // 못 쓰는 브라우저엔 줄이 안 보인다
  const btn = document.getElementById('push-toggle');
  const test = document.getElementById('push-test');
  const msg = document.getElementById('push-msg');
  const JSON_POST = { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' } };

  function say(t) { msg.textContent = t || ''; }

  function keyBytes(b64) {
    const pad = '='.repeat((4 - (b64.length % 4)) % 4);
    const raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
  }

  async function currentSub() {
    const r = await navigator.serviceWorker.getRegistration('/');
    return r ? r.pushManager.getSubscription() : null;
  }

  async function tell(sub) {
    const r = await fetch('/api/push/subscribe', { ...JSON_POST, body: JSON.stringify({ subscription: sub.toJSON() }) });
    if (!r.ok) throw new Error('구독을 브릿지에 알리지 못했다 (' + r.status + ')');
  }

  async function paint() {
    let sub = null;
    try { sub = await currentSub(); } catch (_) { /* 등록 전 */ }
    const denied = Notification.permission === 'denied';
    btn.disabled = denied;
    btn.classList.toggle('on', !!sub && !denied);
    btn.textContent = denied ? '🔕 알림 차단됨' : sub ? '🔔 알림 켜짐 — 끄기' : '🔕 알림 켜기';
    test.hidden = !sub || denied;
    if (denied) say('브라우저 사이트 설정에서 알림을 허용해 줘');
    row.hidden = false;
    return sub;
  }

  async function enable() {
    if (await Notification.requestPermission() !== 'granted') return;
    const reg = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
    await navigator.serviceWorker.ready;
    const { key } = await (await fetch('/api/push/key', { credentials: 'same-origin' })).json();
    const opts = { userVisibleOnly: true, applicationServerKey: keyBytes(key) };
    let sub = await reg.pushManager.getSubscription();
    if (sub) {                                   // 서버 키가 바뀌었으면(vapid.pem 재생성) 낡은 구독은 못 쓴다
      const have = sub.options && sub.options.applicationServerKey;
      if (have && new Uint8Array(have).join() !== opts.applicationServerKey.join()) { await sub.unsubscribe(); sub = null; }
    }
    if (!sub) sub = await reg.pushManager.subscribe(opts);
    await tell(sub);
  }

  async function disable() {
    const sub = await currentSub();
    if (!sub) return;
    await fetch('/api/push/unsubscribe', { ...JSON_POST, body: JSON.stringify({ endpoint: sub.endpoint }) });
    await sub.unsubscribe();
  }

  btn.addEventListener('click', async () => {
    btn.disabled = true;
    say('');
    try {
      if (await currentSub()) await disable(); else await enable();
    } catch (e) {
      say('알림 설정 실패 — ' + (e && e.message ? e.message : e));
    }
    btn.disabled = false;
    paint();
  });

  test.addEventListener('click', async () => {
    test.disabled = true;
    say('');
    try {
      const r = await fetch('/api/push/test', { ...JSON_POST, body: '{}' });
      const j = await r.json();
      say(j.sent ? '시험 알림을 보냈다 — 곧 뜬다' : '보낼 구독이 없거나 푸시 서비스가 거절했다 (실패 ' + (j.failed || 0) + ')');
    } catch (e) {
      say('시험 실패 — ' + (e && e.message ? e.message : e));
    }
    test.disabled = false;
  });

  // 열 때마다: 이미 켜 둔 구독이면 브릿지 명부에 다시 알려 둔다(명부가 비워졌어도 복구) · 서비스워커도 최신으로
  (async () => {
    const sub = await paint();
    if (sub && Notification.permission === 'granted') {
      try { await tell(sub); navigator.serviceWorker.getRegistration('/').then((r) => r && r.update()); } catch (_) { /* 다음에 다시 */ }
    }
  })();
})();
