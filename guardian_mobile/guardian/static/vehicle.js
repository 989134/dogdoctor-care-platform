const v=id=>document.getElementById(id),instance=crypto.randomUUID();
let vehicleOutput=null;
let vehicleEnabled=false,vehicleStream=null,vehicleLink=null,vehicleCallId=null,vehicleBusy=false,vehicleGeneration=0;
v('vehicleKey').value=sessionStorage.getItem('guardianVehicleKey')||'';
v('vehicleVolume').value=sessionStorage.getItem('guardianVehicleVolume')||'1';
v('vehicleAudio').volume=Number(v('vehicleVolume').value);
function fillDevices(select,devices,kind,fallback){
 const remembered=sessionStorage.getItem(kind==='audioinput'?'guardianVehicleMic':'guardianVehicleSpeaker')||'';
 select.replaceChildren(new Option(fallback,''));
 devices.filter(device=>device.kind===kind).forEach((device,index)=>select.add(new Option(device.label||`${kind==='audioinput'?'麥克風':'喇叭'} ${index+1}`,device.deviceId)));
 if([...select.options].some(option=>option.value===remembered))select.value=remembered;
}
async function refreshVehicleDevices(){
 if(!navigator.mediaDevices?.enumerateDevices)throw Error('此瀏覽器無法列出音訊裝置。');
 const devices=await navigator.mediaDevices.enumerateDevices();
 fillDevices(v('vehicleMic'),devices,'audioinput','系統預設麥克風');fillDevices(v('vehicleSpeaker'),devices,'audiooutput','系統預設喇叭');
 const named=devices.some(device=>device.label);v('vehicleDeviceStatus').textContent=named?'音訊裝置已更新；啟用前請確認選擇正確。':'尚未取得裝置名稱；按「啟用通話」允許麥克風後會自動更新。';
}
async function selectVehicleSpeaker(){
 const sink=v('vehicleSpeaker').value;sessionStorage.setItem('guardianVehicleSpeaker',sink);
 if(!sink)return '';
 if(vehicleOutput&&typeof vehicleOutput.setSinkId==='function'){await vehicleOutput.setSinkId(sink);return ''}
 return '此版 Chromium 不支援網頁指定喇叭，已使用系統預設輸出；請在 Linux 音訊設定選擇 CD002-AUDIO。';
}
v('refreshVehicleDevices').onclick=()=>refreshVehicleDevices().catch(error=>v('vehicleDeviceStatus').textContent=careAudioError(error));
v('vehicleMic').onchange=()=>sessionStorage.setItem('guardianVehicleMic',v('vehicleMic').value);
v('vehicleSpeaker').onchange=()=>sessionStorage.setItem('guardianVehicleSpeaker',v('vehicleSpeaker').value);
async function vehicleApi(body){const response=await fetch('/api/intercom/device',{method:'POST',headers:{'Content-Type':'application/json','X-Vehicle-Key':v('vehicleKey').value.trim()},body:JSON.stringify({instance,...body}),signal:AbortSignal.timeout(8000)});const data=await response.json();if(!response.ok)throw Error(data.error||'車端驗證或連線失敗');return data}
function closeVehicleCall(){vehicleLink?.close();vehicleLink=null;vehicleCallId=null;vehicleStream?.getTracks().forEach(t=>t.enabled=false);v('vehiclePlay').hidden=true}
function disableVehicle(message='已停止接收並關閉麥克風。'){
 ++vehicleGeneration;vehicleEnabled=false;closeVehicleCall();vehicleStream?.getTracks().forEach(t=>t.stop());vehicleStream=null;vehicleOutput?.close().catch(()=>{});vehicleOutput=null;
 v('enableVehicle').disabled=false;v('disableVehicle').hidden=true;v('vehicleKey').disabled=false;v('vehicleStatus').textContent=message;
 fetch('/api/intercom/device',{method:'POST',headers:{'Content-Type':'application/json','X-Vehicle-Key':v('vehicleKey').value.trim()},body:JSON.stringify({instance,action:'leave'}),keepalive:true}).catch(()=>{});
}
v('enableVehicle').onclick=async()=>{
 const generation=++vehicleGeneration;v('enableVehicle').disabled=true;v('disableVehicle').hidden=false;
 try{if(!v('vehicleKey').value.trim())throw Error('請先輸入管理員產生的配對金鑰。');
  vehicleOutput=new AudioContext();await vehicleOutput.resume();
  const stream=await careMicrophone(v('vehicleMic').value);if(generation!==vehicleGeneration){stream.getTracks().forEach(t=>t.stop());return}vehicleStream=stream;stream.getAudioTracks().forEach(track=>track.addEventListener('ended',()=>{if(generation===vehicleGeneration)disableVehicle('車上麥克風已中斷，已停止接收通話。')}));stream.getTracks().forEach(t=>t.enabled=false);
  await refreshVehicleDevices();const activeMic=stream.getAudioTracks()[0]?.getSettings().deviceId||v('vehicleMic').value;sessionStorage.setItem('guardianVehicleMic',activeMic);if([...v('vehicleMic').options].some(option=>option.value===activeMic))v('vehicleMic').value=activeMic;
  const speakerWarning=await selectVehicleSpeaker();v('vehicleDeviceStatus').textContent=speakerWarning||`麥克風：${stream.getAudioTracks()[0]?.label||'已啟用'}；喇叭：${v('vehicleSpeaker').selectedOptions[0]?.text||'系統預設'}`;
  await vehicleApi({});if(generation!==vehicleGeneration)return;
  sessionStorage.setItem('guardianVehicleKey',v('vehicleKey').value.trim());v('vehicleKey').disabled=true;vehicleEnabled=true;v('vehicleStatus').textContent='車端已啟用，等待照護者通話；待機不傳送聲音。';
 }catch(error){if(generation===vehicleGeneration)disableVehicle(careAudioError(error))}
};
v('disableVehicle').onclick=()=>disableVehicle();
v('vehicleVolume').oninput=()=>{v('vehicleAudio').volume=Number(v('vehicleVolume').value);sessionStorage.setItem('guardianVehicleVolume',v('vehicleVolume').value)};
v('vehiclePlay').onclick=()=>vehicleOutput.resume().then(()=>v('vehiclePlay').hidden=true).catch(error=>v('vehicleStatus').textContent=careAudioError(error));
setInterval(async()=>{
 if(!vehicleEnabled||vehicleBusy)return;vehicleBusy=true;const generation=vehicleGeneration;
 try{const data=await vehicleApi({});if(generation!==vehicleGeneration)return;
  if(!data.call){if(vehicleCallId)closeVehicleCall();v('vehicleStatus').textContent='車端已啟用，等待照護者通話；待機不傳送聲音。';return}
  if(vehicleCallId===data.call.id)return;
  closeVehicleCall();vehicleCallId=data.call.id;const id=vehicleCallId;vehicleStream.getTracks().forEach(t=>t.enabled=true);
  v('vehicleStatus').textContent='照護者 '+data.call.user+' 正在連線…';
  vehicleLink=new CareAudioLink(vehicleStream,v('vehicleAudio'),data.ice_servers,state=>{
   if(state==='connected')v('vehicleStatus').textContent='雙向通話中 · 照護者 '+data.call.user;
   if(state==='failed'||state==='disconnected'){vehicleApi({action:'end',id}).catch(()=>{});closeVehicleCall();v('vehicleStatus').textContent='音訊連線中斷，已停止傳送聲音。'}
  },()=>{v('vehiclePlay').hidden=false;v('vehicleStatus').textContent='請按「開啟車上喇叭聲音」允許播放。'},vehicleOutput);
  await vehicleLink.pc.setRemoteDescription(data.call.offer);const answer=await vehicleLink.local('answer');if(generation!==vehicleGeneration)return;await vehicleApi({id,answer});
 }catch(error){if(generation===vehicleGeneration)disableVehicle(careAudioError(error))}finally{vehicleBusy=false}
},1500);
window.addEventListener('pagehide',()=>disableVehicle());
refreshVehicleDevices().catch(()=>{});
