const statusNames={acknowledged:'已收到',handling:'處理中',resolved:'已處理',active:'警報中',cancelled:'已取消',canceled:'已取消',review:'待人工確認',high:'高風險',medium:'中風險',low:'低風險',fall:'疑似跌倒',tracking_review:'跌倒待確認',prolonged_sitting:'久坐超過 10 秒',immobile:'久未移動',inactivity:'久未活動',abnormal:'異常狀況'};
const labelOf=v=>statusNames[v]||v;
let lastEvents='',serverOffset=0,refreshBusy=false;
const deliveredInPage=new Set();
const openedAlertVersions=new Set();
const careDrafts=new Map();
const urgentStages=['next_step_pending','awaiting_secondary','medical_pending','medical_notified'];
const visibleEventLimit=3;
function isPending(e){return e.workflow?!['resolved','elder_ok'].includes(e.workflow.stage):!['resolved','cancelled','canceled','closed'].includes(e.status)}
function urgent(e){return isPending(e)&&(e.risk_level==='high'||urgentStages.includes(e.workflow?.stage))}
function textElement(tag,text,className){const el=document.createElement(tag);el.textContent=text;if(className)el.className=className;return el}
function updateCountdowns(){document.querySelectorAll('[data-deadline]').forEach(el=>{const remaining=Math.max(0,Math.ceil(Number(el.dataset.deadline)-(Date.now()/1000+serverOffset)));el.textContent=remaining?'等待接手：'+Math.floor(remaining/60)+' 分 '+String(remaining%60).padStart(2,'0')+' 秒；逾時'+(el.dataset.target||'通知第二聯絡人'):'已逾時，等待伺服器更新升級狀態…'})}
setInterval(updateCountdowns,1000);
async function loadWorkflowStatus(){
  try{const s=await api('/api/workflow/status');$('workflowStatus').textContent='機器狗串接：'+(s.robot_configured?'已設定驗證，尚須設備回報確認連線':'尚未設定')+' · 對外通知：'+(s.notification_configured?'已設定服務，送達狀態以事件紀錄為準':'尚未設定；目前只提供登入頁面的站內通知')+' · 醫療機構通報：'+(s.medical_configured?'通報轉接服務已設定':'尚未設定接收管道')+'。';
  if(role==='admin'){
    const c=await api('/api/notification/config');
    for(const prefix of ['primary','secondary']){
      const select=$(prefix+'User');select.replaceChildren(new Option('選擇 App 帳號',''));
      for(const user of c.users)select.add(new Option(user.name+' · '+user.devices+' 個推播裝置',user.name));
      select.value=c[prefix+'_user'];$(prefix+'Phone').value=c[prefix+'_phone'];
    }
    $('secondaryWait').value=String(c.secondary_wait_seconds??600);$('notificationsEnabled').checked=c.enabled;
    $('emergencyPhone').value=c.emergency_phone;$('careAddress').value=c.care_address;$('callbackPhone').value=c.callback_phone;
    $('deliveryServiceStatus').textContent='App HTTPS 網址：'+(c.push_configured?'已設定':'尚未設定')+' · 簡訊服務：'+(c.sms_configured?'已設定':'尚未設定')+'。各聯絡人需自行啟用手機推播；設定完成不代表通知已送達。';
  }}catch(e){note(e.message)}
}
async function saveNotificationSettings(){
  await api('/api/notification/config',{primary_user:$('primaryUser').value,secondary_user:$('secondaryUser').value,primary_phone:$('primaryPhone').value.trim(),secondary_phone:$('secondaryPhone').value.trim(),enabled:$('notificationsEnabled').checked,emergency_phone:$('emergencyPhone').value.trim(),care_address:$('careAddress').value.trim(),callback_phone:$('callbackPhone').value.trim(),secondary_wait_seconds:Number($('secondaryWait').value)});
  await loadWorkflowStatus();note('App 推播＋簡訊聯絡設定已儲存');
}
async function refresh(){
  if(refreshBusy)return;
  refreshBusy=true;
  try{
    const d=await api('/api/events');serverOffset=d.server_time-Date.now()/1000;
    const recentEvents=[...d.events].sort((a,b)=>(b.workflow?.updated||b.updated_wall)-(a.workflow?.updated||a.updated_wall)).slice(0,visibleEventLimit);
    $('totalCount').textContent=recentEvents.length;$('pendingCount').textContent=recentEvents.filter(isPending).length;$('riskCount').textContent=recentEvents.filter(urgent).length;
    $('syncStatus').textContent='後台同步成功 · '+new Date().toLocaleTimeString();
    $('careAlerts').replaceChildren();
    for(const event of d.events.filter(e=>['notify_pending','awaiting_caregiver',...urgentStages].includes(e.workflow?.stage)&&!openedAlertVersions.has(e.id+':'+e.workflow?.version)).slice(0,visibleEventLimit)){
      const alert=textElement('div','','connection-status'+(urgent(event)?' urgent-message urgent-alert':''));
      alert.append(textElement('strong',labelOf(event.kind)+' · '+event.workflow.label));
      alert.append(textElement('p','事件 '+event.id+'：請進入事件頁面接手確認。'));
      const open=textElement('button','查看事件');open.onclick=()=>{openedAlertVersions.add(event.id+':'+event.workflow.version);alert.remove();showPage('events')};alert.append(open);$('careAlerts').append(alert);
    }
    const signature=$('eventFilter').value+JSON.stringify(d.events);
    if(signature!==lastEvents){
      lastEvents=signature;$('events').replaceChildren();
      const filter=$('eventFilter').value;
      const visible=d.events.filter(e=>filter==='all'||filter==='pending'&&isPending(e)||filter==='resolved'&&!isPending(e)||filter==='high'&&urgent(e));
      visible.sort((a,b)=>Number(urgent(b))-Number(urgent(a))||(b.workflow?.updated||b.updated_wall)-(a.workflow?.updated||a.updated_wall));
      if(!visible.length)$('events').append(textElement('p','目前沒有符合條件的照護事件。真實設備回報後，事件會顯示在這裡。','empty-state'));
      for(const e of visible.slice(0,visibleEventLimit))renderCareEvent(e);
    }
    for(const e of d.events){const w=e.workflow;const version=String(w?.version||e.updated_wall);if(initialized&&seen.get(e.id)!==version&&['notify_pending','awaiting_caregiver',...urgentStages].includes(w?.stage)){note(urgentStages.includes(w.stage)?'有事件升級為緊急待處理，請立即查看。':'有照護事件需要接手確認。')}seen.set(e.id,version);
      // Receipt is recorded only after a caregiver actually displays the alert.
      if(role!=='viewer'&&w?.can_receive_in_app&&document.visibilityState==='visible'&&!deliveredInPage.has(e.id+':'+w.version)){
        const receipt=e.id+':'+w.version;deliveredInPage.add(receipt);
        note('照護通知：'+labelOf(e.kind)+'。請進入「事件」手動接手；未接手會依設定升級通報。');
        try{await api('/api/workflow/action',{event:e.id,action:'notification_received',version:w.version});lastEvents=''}catch(error){deliveredInPage.delete(receipt);note(error.message)}
      }
    }
    initialized=true;updateCountdowns();
  }finally{refreshBusy=false}
}
function renderCareEvent(e){
  const w=e.workflow,el=textElement('article','','event care-event'+(urgentStages.includes(w?.stage)?' emergency':''));
  el.append(textElement('h3',labelOf(e.kind)+' · '+(w?.label||labelOf(e.status))));
  el.append(textElement('p',new Date(e.created_wall*1000).toLocaleString()+' · '+labelOf(e.risk_level)+' · '+e.id,'event-meta'));
  if(e.image_available){const img=document.createElement('img');img.src='/api/image/'+encodeURIComponent(e.id);img.alt='事件影像';el.append(img)}
  if(w){
    if(w.response)el.append(textElement('p','設備回報：'+w.response));
    if(w.deadline){const count=textElement('p','','care-countdown');count.dataset.deadline=w.deadline;count.dataset.target=w.stage==='awaiting_secondary'?'通報醫療機構':'通知第二聯絡人';el.append(count)}
    if(w.stage==='next_step_pending')el.append(textElement('p','緊急待處理：已排入第二聯絡人通知，仍需有人接手。通知不代表已取得協助。','urgent-message'));
    if(w.stage==='awaiting_secondary'&&!w.deadline)el.append(textElement('p','第二聯絡人已通知，但等候時間尚未設定；目前不會自動計時。','urgent-message'));
    if(['medical_pending','medical_notified'].includes(w.stage))el.append(textElement('p',w.stage==='medical_pending'?'醫療機構通報尚未確認送出，請查看管道設定與傳送狀態。':'通報已送出，不代表醫療機構已接案或派員，請持續追蹤。','urgent-message'));
    if(w.emergency_contact){
      const info=w.emergency_contact;
      el.append(textElement('p','通報地址：'+(info.care_address||'尚未填寫')+' · 回撥電話：'+(info.callback_phone||'尚未填寫')));
      if(info.emergency_phone&&/^\+?[0-9]{2,20}$/.test(info.emergency_phone)){
        const dial=textElement('a','撥打緊急救護專線 '+info.emergency_phone,'emergency-dial');dial.href='tel:'+info.emergency_phone;el.append(dial);
        el.append(textElement('p','此按鈕會開啟裝置撥號功能；不會自動把事件標為已通報。','helper'));
      }
      if(!w.automatic_call_configured)el.append(textElement('p','自動電話通報尚未完成設定，請人工撥號並記錄通話結果。','urgent-message'));
    }
    for(const cmd of w.commands){const name={robot:'呼叫長者',notify:'第一聯絡人通知',next_step:'第二聯絡人通知',medical:'醫療機構通報'}[cmd.kind];const status={pending:'待執行',in_progress:'設備已開始',sending:'傳送中',failed:'執行失敗，等待重試',completed:'已回報完成'}[cmd.status];el.append(textElement('p',name+'：'+status+(cmd.detail?' · '+cmd.detail:''),'command-state'))}
    for(const cmd of w.commands)for(const delivery of cmd.deliveries||[]){
      const states={pending:'待傳送',sending:'傳送中',accepted:'等待送達回報',delivered:'已送達，尚須手動接手',blocked:'尚未就緒',failed:'未送達',unknown:'結果不明，請人工確認',cancelled:'已停止'};
      el.append(textElement('p',({notify:'第一聯絡人',next_step:'第二聯絡人'}[cmd.kind]||'')+' · '+(delivery.channel==='push'?'App 推播':'簡訊')+'：'+(states[delivery.status]||delivery.status)+' · '+delivery.detail,'delivery-state '+delivery.status));
    }
    const history=document.createElement('details');history.append(textElement('summary','查看處理時間軸（'+w.history.length+'）'));const list=document.createElement('ol');list.className='care-timeline';
    for(const h of w.history)list.append(textElement('li',new Date(h.at*1000).toLocaleString()+' · '+h.actor+'\n'+h.note));history.append(list);el.append(history);
    if(role!=='viewer'){
      const actions={awaiting_caregiver:[['接手確認','acknowledge']],legacy_review:[['接手確認','acknowledge']],reviewing:[['確認無異常並結案','confirm_safe'],['確認有異常，通知第二聯絡人','confirm_issue']],next_step_pending:[['接手後續處理','take_over']],awaiting_secondary:[['第二聯絡人／現場人員接手','take_over']],medical_pending:[['我已接通救護專線並完成通報','record_medical_call'],['接手並記錄通報處理','take_over']],medical_notified:[['接手並追蹤醫療通報','take_over']],handling:[['填寫結果並結案','resolve']]}[w.stage]||[];
      if(actions.length){const label=textElement('label','確認依據／處理備註');const input=document.createElement('textarea');input.maxLength=1000;input.value=careDrafts.get(e.id)||'';input.oninput=()=>careDrafts.set(e.id,input.value);input.placeholder='例如：已聯絡長者並確認回應；請記錄實際觀察與採取的行動。';label.append(input);el.append(label);const controls=document.createElement('div');controls.className='care-actions';
        for(const [title,action] of actions){const b=textElement('button',title);b.onclick=async()=>{controls.querySelectorAll('button').forEach(x=>x.disabled=true);try{await api('/api/workflow/action',{event:e.id,action,version:w.version,note:input.value.trim()});careDrafts.delete(e.id);lastEvents='';await refresh();note('事件處理紀錄已儲存')}catch(err){note(err.message)}finally{controls.querySelectorAll('button').forEach(x=>x.disabled=false)}};controls.append(b)}el.append(controls)
      }
    }
  }
  $('events').append(el);
}
