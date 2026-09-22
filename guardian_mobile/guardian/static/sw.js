const CACHE='guardian-offline-v2-name';
self.addEventListener('install',event=>{event.waitUntil(caches.open(CACHE).then(cache=>cache.add('/static/offline.html')).then(()=>self.skipWaiting()))});
self.addEventListener('activate',event=>{event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(key=>key.startsWith('guardian-offline-')&&key!==CACHE).map(key=>caches.delete(key)))).then(()=>self.clients.claim()))});
// Never cache authenticated pages, medication records, images or API responses.
self.addEventListener('fetch',event=>{if(event.request.mode==='navigate'&&event.request.method==='GET')event.respondWith(fetch(event.request).catch(()=>caches.match('/static/offline.html')))});
self.addEventListener('push',event=>{
  event.waitUntil((async()=>{
    let data={};try{data=event.data?.json()||{}}catch(_){}
    await self.registration.showNotification('汪無一失',{body:'有照護事件需要您接手確認，請開啟 App。',icon:'/static/icon-192.png',badge:'/static/icon-192.png',tag:data.tag||'care-alert',data:{url:'/?page=events'},requireInteraction:true});
    if(typeof data.receipt==='string')await fetch('/api/push/received',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({receipt:data.receipt})}).catch(()=>{});
  })());
});
self.addEventListener('notificationclick',event=>{
  event.notification.close();
  // Opening a notification never acknowledges responsibility for a care event.
  event.waitUntil((async()=>{const clients=await self.clients.matchAll({type:'window',includeUncontrolled:true});for(const client of clients){if(new URL(client.url).origin===self.location.origin){await client.navigate('/?page=events');return client.focus()}}return self.clients.openWindow('/?page=events')})());
});
