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
    renotify: true,                             // 갈아 끼워도 다시 울린다
    icon: '/icon-192.png',
    badge: '/icon-192.png',
    requireInteraction: waiting,                // 결재 대기는 눌러 치울 때까지 남는다
    data: { url: d.url || '/' },
  }));
});

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || '/';
  e.waitUntil((async () => {
    const all = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const c of all) {
      if ('focus' in c) { await c.focus(); return; }
    }
    await self.clients.openWindow(url);
  })());
});
