// 웹푸시 서비스워커 — 브릿지가 보낸 결재·완료 알림을 폰 화면이 꺼져 있어도 띄운다. 페이지(push.js)가 등록한다.
// 루트 경로(/sw.js)에서 내려야 범위가 앱 전체다. 이 파일이 바뀌면 브라우저가 알아서 새로 받는다(Cache-Control: no-cache).
'use strict';

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));

self.addEventListener('push', (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = { body: e.data ? e.data.text() : '' }; }
  const waiting = d.kind === 'waiting';
  e.waitUntil(self.registration.showNotification(d.title || 'frogterminal', {
    body: d.body || '',
    tag: d.tag || 'frog',                       // 같은 창의 같은 알림은 겹쳐 쌓지 않고 갈아 끼운다
    renotify: d.kind !== 'done',                // 결재·전부끝남은 갈아 끼워도 다시 울린다 · 개별 완료는 조용히 갈아 끼운다
    icon: '/icon-192.png',
    badge: '/icon-192.png',
    requireInteraction: waiting,                // 결재 대기는 눌러 치울 때까지 남는다
    data: { url: d.url || '/', pid: d.pid || 0 },
  }));
});

// 알림을 누르면 그 창(pid)의 팝업으로 들어간다 — 열린 탭엔 메시지(ext.js 가 받아 연다), 앱이 꺼져 있으면 /?ext=<pid> 로 새로 연다
self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const data = e.notification.data || {};
  const url = data.url || '/';
  e.waitUntil((async () => {
    const all = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const c of all) {
      if (!('focus' in c)) continue;
      try { await c.focus(); } catch (_) { /* 앞으로 못 와도 메시지는 보낸다 */ }
      if (data.pid) c.postMessage({ type: 'frog-open-ext', pid: data.pid });
      return;
    }
    await self.clients.openWindow(url);
  })());
});
