class CareAudioLink {
  constructor(stream,audio,ice,onState,onBlocked,outputContext=null){
    this.stream=stream;this.audio=audio;this.pc=new RTCPeerConnection({iceServers:ice});
    stream.getTracks().forEach(track=>this.pc.addTrack(track,stream));
    this.pc.ontrack=event=>{const remote=event.streams[0]||new MediaStream([event.track]);if(outputContext){this.source=outputContext.createMediaStreamSource(remote);this.gain=outputContext.createGain();this.gain.gain.value=audio.volume;this.source.connect(this.gain);this.gain.connect(outputContext.destination);audio.onvolumechange=()=>{if(this.gain)this.gain.gain.value=audio.volume};if(outputContext.state!=='running')onBlocked()}else{audio.srcObject=remote;audio.play().catch(onBlocked)}};
    this.pc.onconnectionstatechange=()=>onState(this.pc.connectionState);
  }
  async local(type){
    await this.pc.setLocalDescription(await(type==='offer'?this.pc.createOffer():this.pc.createAnswer()));
    if(this.pc.iceGatheringState!=='complete')await new Promise((resolve,reject)=>{
      const done=()=>{clearTimeout(timeout);this.pc.removeEventListener('icegatheringstatechange',change)};
      const change=()=>{if(this.pc.iceGatheringState==='complete'){done();resolve()}};
      const timeout=setTimeout(()=>{done();reject(Error('音訊連線準備逾時，請檢查網路或 TURN 轉送設定。'))},12000);
      this.pc.addEventListener('icegatheringstatechange',change);
    });
    return {type:this.pc.localDescription.type,sdp:this.pc.localDescription.sdp};
  }
  close(){this.source?.disconnect();this.gain?.disconnect();this.audio.onvolumechange=null;this.pc.onconnectionstatechange=null;this.pc.close();this.audio.pause();this.audio.srcObject=null}
}
async function careMicrophone(deviceId=''){
  if(!window.isSecureContext||!navigator.mediaDevices?.getUserMedia)throw Error('麥克風需要 HTTPS 網址或本機 localhost，請使用手機可連線的 HTTPS 網址。');
  const audio={echoCancellation:true,noiseSuppression:true,autoGainControl:true};
  if(deviceId)audio.deviceId={exact:deviceId};
  return navigator.mediaDevices.getUserMedia({audio,video:false});
}
function careAudioError(error){return ({NotAllowedError:'麥克風權限未允許，請在瀏覽器設定開啟後重試。',NotFoundError:'找不到麥克風，請確認已連接音訊裝置。',NotReadableError:'麥克風正被其他程式佔用或無法讀取。'})[error.name]||error.message}
