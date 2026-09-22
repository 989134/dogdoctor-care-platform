let callLink=null,callStream=null,callId=null,callGeneration=0,callPolling=false,callStarting=false,callStartedAt=0;
async function loadIntercomStatus(){
  if($('portal').hidden)return;
  document.querySelector('[data-target="intercom"]').hidden=role==='viewer';
  if(role==='viewer'){$('intercomPanel').hidden=true;return;}
  $('vehiclePairing').hidden=role!=='admin';$('startCall').hidden=role==='viewer';
  if(callId||callStarting)return;
  try{const s=await api('/api/intercom/status');$('startCall').disabled=!s.online||s.busy;
    $('callStatus').textContent=s.busy?'車端正在與另一位照護者通話。':s.online?'車端已啟用，可以開始通話。'+(s.turn_configured?'':' 跨網路連線若失敗，需設定 TURN 轉送服務。'):s.paired?'車端尚未啟用或已離線，請在樹莓派接收頁按「啟用通話」。':'車端尚未配對，請由管理員設定樹莓派接收頁。';
  }catch(error){$('callStatus').textContent='無法確認車端狀態：'+error.message;$('startCall').disabled=true}
}
function stopCall(message='通話已結束。'){
  ++callGeneration;callStarting=false;const id=callId;callId=null;callLink?.close();callLink=null;callStream?.getTracks().forEach(t=>t.stop());callStream=null;
  $('startCall').hidden=role==='viewer';$('startCall').disabled=false;for(const key of ['muteCall','endCall','playCall'])$(key).hidden=true;$('muteCall').textContent='關閉我的麥克風';$('muteCall').setAttribute('aria-pressed','false');$('callStatus').textContent=message;
  if(id)fetch('/api/intercom/calls/'+id,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF':csrf},body:JSON.stringify({action:'end'}),keepalive:true}).catch(()=>{});
}
$('startCall').onclick=async()=>{
  if(callStarting||callId)return;const generation=++callGeneration;callStarting=true;$('startCall').disabled=true;$('endCall').hidden=false;$('callStatus').textContent='正在取得麥克風權限…';
  try{
    const stream=await careMicrophone();if(generation!==callGeneration){stream.getTracks().forEach(t=>t.stop());return}callStream=stream;stream.getAudioTracks().forEach(track=>track.addEventListener('ended',()=>{if(generation===callGeneration)stopCall('麥克風已中斷，通話已結束。')}));
    const cfg=await api('/api/intercom/status');if(generation!==callGeneration)return;if(!cfg.online||cfg.busy)throw Error('車端目前無法接聽，請確認已啟用且沒有其他通話。');
    callLink=new CareAudioLink(stream,$('callAudio'),cfg.ice_servers,state=>{
      if(state==='connected'){$('callStatus').textContent='雙向通話連線中，您可以透過車上喇叭說話。';$('muteCall').hidden=false}
      if(state==='disconnected')$('callStatus').textContent='音訊連線中斷，正在確認網路…';
      if(state==='failed')stopCall('通話連線失敗，請檢查車端網路與 TURN 設定。');
    },()=>{$('playCall').hidden=false;$('callStatus').textContent='瀏覽器尚未播放音訊，請按「播放長者聲音」。'});
    $('callStatus').textContent='正在連接車上喇叭與麥克風…';const offer=await callLink.local('offer');if(generation!==callGeneration)return;
    const result=await api('/api/intercom/calls',{offer});
    if(generation!==callGeneration){await api('/api/intercom/calls/'+result.id,{action:'end'});return}
    callId=result.id;callStartedAt=Date.now();callStarting=false;$('startCall').hidden=true;
  }catch(error){if(generation===callGeneration)stopCall(careAudioError(error))}
};
$('endCall').onclick=()=>stopCall();
$('muteCall').onclick=()=>{if(!callStream)return;const track=callStream.getAudioTracks()[0];track.enabled=!track.enabled;$('muteCall').textContent=track.enabled?'關閉我的麥克風':'開啟我的麥克風';$('muteCall').setAttribute('aria-pressed',String(!track.enabled))};
$('playCall').onclick=()=>$('callAudio').play().then(()=>{$('playCall').hidden=true}).catch(error=>note(careAudioError(error)));
$('pairVehicle').onclick=async()=>{
  if(!confirm('產生新金鑰會中止目前通話，原車端金鑰將失效。要繼續嗎？'))return;
  try{const result=await api('/api/intercom/pair',{});$('vehiclePairKey').value=result.token;$('vehiclePairKey').hidden=false;$('copyVehicleKey').hidden=false;$('vehiclePairHelp').textContent='請將此金鑰貼到車端接收頁，勿提供給其他人。';await loadIntercomStatus()}catch(error){note(error.message)}
};
$('copyVehicleKey').onclick=async()=>{try{await navigator.clipboard.writeText($('vehiclePairKey').value);$('vehiclePairHelp').textContent='已複製，請貼至樹莓派的車端接收頁。'}catch(_){$('vehiclePairKey').type='text';$('vehiclePairKey').select();$('vehiclePairHelp').textContent='請手動複製選取的金鑰。'}};
setInterval(async()=>{
  if(callPolling)return;callPolling=true;const pollingId=callId;
  try{if(callId){const id=callId,d=await Promise.race([api('/api/intercom/calls/'+id),new Promise((_,reject)=>setTimeout(()=>reject(Error('通話伺服器無回應')),8000))]);if(id!==callId)return;if(d.answer&&!callLink.pc.remoteDescription)await callLink.pc.setRemoteDescription(d.answer);if(Date.now()-callStartedAt>45000&&callLink.pc.connectionState!=='connected')stopCall('音訊連線逾時，已關閉麥克風；請確認網路及車端設定。')}else await loadIntercomStatus()}
  catch(error){if(callId&&callId===pollingId)stopCall('通話已中斷：'+error.message)}finally{callPolling=false}
},2000);
window.addEventListener('pagehide',()=>stopCall());
document.addEventListener('visibilitychange',()=>{if(document.hidden&&(callId||callStarting))stopCall('App 已離開前景，通話已結束並關閉麥克風。')});
