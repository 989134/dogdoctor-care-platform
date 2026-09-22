let appInstallPrompt=null,pushConfiguration=null;
const standalone=()=>matchMedia('(display-mode: standalone)').matches||navigator.standalone===true;
const appleMobile=()=>/iPhone|iPad|iPod/.test(navigator.userAgent)||(navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1);
const serviceWorkerReady=('serviceWorker'in navigator&&window.isSecureContext)?navigator.serviceWorker.register('/sw.js').then(()=>navigator.serviceWorker.ready).catch(()=>null):Promise.resolve(null);
window.addEventListener('beforeinstallprompt',event=>{event.preventDefault();appInstallPrompt=event;$('installApp').textContent='安裝汪無一失 App'});
window.addEventListener('appinstalled',()=>{$('installStatus').textContent='已完成安裝，可從主畫面開啟。';appInstallPrompt=null});
$('installApp').onclick=async()=>{
  if(standalone())return $('installStatus').textContent='您已從 App 主畫面開啟。';
  if(appInstallPrompt){await appInstallPrompt.prompt();const result=await appInstallPrompt.userChoice;$('installStatus').textContent=result.outcome==='accepted'?'已接受安裝，請查看主畫面。':'您已取消安裝，可稍後再試。';appInstallPrompt=null;return}
  $('installStatus').textContent=appleMobile()?'請使用 Safari：分享 → 加入主畫面，再從主畫面開啟 App。':'請使用 Chrome 的選單 → 安裝應用程式／新增至主畫面。若沒有此選項，請確認使用 HTTPS 網址。';
};
async function loadPushStatus(){
  try{
    pushConfiguration=await api('/api/push/config');
    $('publicAppStatus').replaceChildren();
    if(pushConfiguration.public_url){const a=document.createElement('a');a.href=pushConfiguration.public_url;a.textContent='手機 App 網址：'+pushConfiguration.public_url;$('publicAppStatus').append(a)}
    else $('publicAppStatus').textContent='目前為本機網頁。手機安裝及背景推播需先設定手機可連線的 HTTPS 網址；127.0.0.1 不能供其他手機連線。';
    $('installStatus').textContent=standalone()?'目前以 App 模式開啟。':'支援 Android 與 iPhone 加入主畫面。';
    $('notify').hidden=role==='viewer';$('disablePush').hidden=true;
    if(role==='viewer'){$('pushStatus').textContent='此為唯讀帳號，無法綁定照護通知。';return}
    if(!window.isSecureContext||!('PushManager'in window)||!('Notification'in window)){$('pushStatus').textContent='此環境尚無法使用推播。請用支援的手機瀏覽器開啟 HTTPS 網址；iPhone 需先加入主畫面。';return}
    const registration=await serviceWorkerReady;
    const subscription=await registration?.pushManager.getSubscription();
    const bound=subscription?await api('/api/push/device',{endpoint:subscription.endpoint}):{bound:false};
    $('disablePush').hidden=!bound.bound;
    $('pushStatus').textContent=bound.bound?'此裝置已綁定目前帳號的 App 推播。':Notification.permission==='denied'?'通知權限已被封鎖，請在瀏覽器／手機設定允許後再啟用。':'此裝置尚未綁定目前帳號。請按「啟用此裝置 App 推播」。';
    $('pushStatus').textContent+=' 此帳號共 '+pushConfiguration.devices+' 個已綁定裝置。';
  }catch(error){$('pushStatus').textContent='無法讀取通知設定：'+error.message}
}
function decodePushKey(value){const binary=atob(value.replace(/-/g,'+').replace(/_/g,'/')+'='.repeat((4-value.length%4)%4));return Uint8Array.from(binary,c=>c.charCodeAt(0))}
$('notify').onclick=async()=>{
  try{
    if(!window.isSecureContext||!('Notification'in window)||!('PushManager'in window))throw Error('請使用 HTTPS 網址；iPhone 請先加入主畫面再開啟。');
    if(appleMobile()&&!standalone())throw Error('請先在 Safari「分享 → 加入主畫面」，再從主畫面啟用推播。');
    if(!pushConfiguration?.configured)throw Error('尚未設定手機可連線的 HTTPS 網址，完成部署後才能啟用背景推播。');
    const permission=await Notification.requestPermission();
    if(permission!=='granted')throw Error('尚未取得通知權限。若已封鎖，請至裝置的通知設定允許。');
    const registration=await serviceWorkerReady;if(!registration)throw Error('App 背景服務未載入，請重新整理後再試。');
    let subscription=await registration.pushManager.getSubscription();
    if(!subscription)subscription=await registration.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:decodePushKey(pushConfiguration.public_key)});
    await api('/api/push/subscribe',subscription.toJSON());await loadPushStatus();await loadWorkflowStatus();note('此裝置已綁定 App 推播；收到通知後請開啟事件並手動接手。');
  }catch(error){note(error.message)}
};
$('disablePush').onclick=async()=>{
  try{const registration=await serviceWorkerReady;const subscription=await registration?.pushManager.getSubscription();if(subscription){await api('/api/push/unsubscribe',{endpoint:subscription.endpoint});await subscription.unsubscribe()}await loadPushStatus();await loadWorkflowStatus();note('已停用此裝置推播。')}catch(error){note(error.message)}
};
