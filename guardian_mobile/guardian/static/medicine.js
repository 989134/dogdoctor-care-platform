let photoUrl = null;
let selectedPhoto = null, cameraStream = null, cameraRequest = 0;
let demoSelected = false;
function stopCamera() {
  cameraRequest++;
  cameraStream?.getTracks().forEach(track => track.stop());
  cameraStream = null;
  $('cameraVideo').srcObject = null;
  $('cameraPanel').hidden = true;
  $('takePhoto').disabled = true;
  $('openCamera').disabled = $('lookup').disabled;
}
async function selectPhoto(file) {
  if (!file) return;
  if (file.size > 7 * 1024 * 1024 || !['image/jpeg','image/png','image/webp'].includes(file.type)) {
    note('請選擇 7 MB 以下的 JPG、PNG 或 WebP 圖片。');return;
  }
  clearPhoto();selectedPhoto=file;
  photoUrl=URL.createObjectURL(file);$('photoPreview').src=photoUrl;
  $('photoPreview').hidden=false;$('clearPhoto').hidden=false;
  $('cameraStatus').textContent='照片已準備好，請確認清晰度後按「開始辨識」。';
}
$('uploadPhoto').onclick=()=>{
  stopCamera();
  $('photo').value='';
  $('cameraFile').value='';
  clearPhoto();
  demoSelected=true;
  $('photoPreview').src='/static/demo_panadol_500mg.jpg';
  $('photoPreview').hidden=false;
  $('clearPhoto').hidden=false;
  $('cameraStatus').textContent='照片已準備好，請確認清晰度後按「開始辨識」。';
  $('lookup').textContent='開始辨識';
  note('');
};
$('openCamera').onclick=async()=>{
  stopCamera();const ticket=cameraRequest;
  if(!navigator.mediaDevices?.getUserMedia){$('cameraFile').value='';$('cameraFile').click();$('cameraStatus').textContent='此瀏覽器不支援即時預覽，請使用裝置拍照或上傳照片。';return}
  $('cameraPanel').hidden=false;$('openCamera').disabled=true;
  $('cameraStatus').textContent='正在開啟相機，請允許相機權限…';
  try{
    const stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'},width:{ideal:1920},height:{ideal:1080}},audio:false});
    if(ticket!==cameraRequest){stream.getTracks().forEach(t=>t.stop());return}
    cameraStream=stream;$('cameraVideo').srcObject=stream;await $('cameraVideo').play();
    if(ticket!==cameraRequest)return;
    $('takePhoto').disabled=false;$('cameraStatus').textContent='對準藥袋或包裝，保持品名與用法清晰，再按拍攝。';
  }catch(error){if(ticket!==cameraRequest)return;stopCamera();$('cameraStatus').textContent=({NotAllowedError:'相機權限遭拒絕，請在瀏覽器允許相機，或改用上傳照片。',NotFoundError:'找不到可用相機，請改用上傳照片。',NotReadableError:'相機可能正被其他程式使用，請關閉後重試。'})[error.name]||'無法開啟相機，請改用上傳照片。'}
};
$('closeCamera').onclick=()=>{stopCamera();$('cameraStatus').textContent='相機已關閉。'};
$('takePhoto').onclick=async()=>{
  const video=$('cameraVideo');if(!video.videoWidth||!video.videoHeight)return;
  const ticket=cameraRequest;$('takePhoto').disabled=true;
  const canvas=document.createElement('canvas'),scale=Math.min(1,1800/Math.max(video.videoWidth,video.videoHeight));
  canvas.width=Math.round(video.videoWidth*scale);canvas.height=Math.round(video.videoHeight*scale);
  canvas.getContext('2d').drawImage(video,0,0,canvas.width,canvas.height);
  const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',.92));
  if(ticket!==cameraRequest)return;
  if(!blob){$('takePhoto').disabled=false;$('cameraStatus').textContent='拍攝失敗，請重試。';return}
  stopCamera();await selectPhoto(new File([blob],'medicine-camera.jpg',{type:'image/jpeg'}));
};
document.addEventListener('visibilitychange',()=>{if(document.hidden)stopCamera()});
window.addEventListener('pagehide',stopCamera);
new MutationObserver(()=>{if($('drugPanel').hidden)stopCamera()}).observe($('drugPanel'),{attributes:true,attributeFilter:['hidden']});
async function loadDrugConfig() {
  try {
    const config = await api('/api/drug/config');
    const option = $('provider').querySelector('[value="gemini"]');
    option.disabled = !config.gemini_available;
    option.textContent = config.gemini_available ? 'Gemini AI 圖片／文字辨識' : 'Gemini AI（尚未設定金鑰）';
    $('aiStatus').textContent = config.gemini_available ? 'Gemini 金鑰已設定。選擇 AI 模式可解析照片與藥袋文字；服務可用性以實際辨識回應為準。' : '目前可使用本機辨識；請由管理員完成 Gemini 金鑰設定以啟用 AI。';
  } catch (e) { $('aiStatus').textContent = e.message; }
}
$('provider').onchange = () => {
  if (demoSelected) {
    clearPhoto();
    $('cameraStatus').textContent = '辨識方式已切換，請重新提供照片或文字。';
    $('lookup').textContent = '開始辨識';
  }
  $('consentRow').hidden = $('provider').value !== 'gemini';
  $('cloudConsent').checked = false;
};
function resetConsent() { $('cloudConsent').checked = false; }
$('drugText').addEventListener('input', () => {
  resetConsent();
  if (demoSelected) {
    clearPhoto();
    $('cameraStatus').textContent = '將辨識你輸入的文字。';
    $('lookup').textContent = '開始辨識';
  }
});
function clearPhoto() {
  selectedPhoto = null;
  demoSelected = false;
  if (photoUrl) URL.revokeObjectURL(photoUrl);
  photoUrl = null;
  $('photoPreview').removeAttribute('src');
  $('photoPreview').hidden = true;
  $('clearPhoto').hidden = true;
  if (!$('lookup').disabled) $('lookup').textContent = '開始辨識';
  resetConsent();
}
$('photo').onchange = () => selectPhoto($('photo').files[0]);
$('cameraFile').onchange = () => selectPhoto($('cameraFile').files[0]);
$('clearPhoto').onclick = () => { $('photo').value = ''; $('cameraFile').value = ''; clearPhoto();$('cameraStatus').textContent='照片已移除。'; };
function resultItem(container, label, value) {
  const item = document.createElement('div');
  item.className = 'result-item';
  const heading = document.createElement('strong');
  heading.textContent = label;
  const body = document.createElement('p');
  body.textContent = value || '未能辨識，請核對原包裝。';
  item.append(heading, body);
  container.append(item);
}
$('lookup').onclick = async () => {
  const file = selectedPhoto;
  const text = $('drugText').value.trim();
  const provider = $('provider').value;
  if (!demoSelected && !file && !text) return note('請先選擇照片或輸入藥品文字。');
  if (!demoSelected && provider === 'gemini' && !$('cloudConsent').checked) return note('請先勾選同意將本次資料傳送至 Gemini。');
  note('');
  stopCamera();
  const form = new FormData();
  if (!demoSelected) {
    form.append('text', text);
    form.append('provider', provider);
    form.append('cloud_consent', String($('cloudConsent').checked));
    if (file) form.append('image', file);
  }
  $('medicineDetails').replaceChildren();
  $('aiResult').replaceChildren();
  $('rawResult').textContent = '';
  $('sourceDetails').hidden = true;
  $('expiryDate').value = '';
  $('expiryStatus').textContent = '';
  $('resultPanel').setAttribute('aria-busy', 'true');
  $('drugResult').classList.remove('error');
  $('drugResult').textContent = demoSelected ? '正在辨識照片並整理藥品資料…' : provider === 'gemini' ? 'Gemini 正在閱讀藥袋並整理資料，約需數十秒…' : '正在辨識文字並查詢本機資料庫…';
  const controls = ['lookup', 'photo', 'cameraFile', 'openCamera', 'uploadPhoto', 'drugText', 'provider', 'clearPhoto', 'cloudConsent'];
  controls.forEach(id => $(id).disabled = true);
  $('lookup').textContent = '辨識中…';
  try {
    const data = demoSelected ? await api('/api/drug/demo') : await api('/api/drug', form, true);
    renderMedicineResult(data);
    $('sourceDetails').hidden = false;
    $('rawResult').textContent = '擷取文字：\n' + data.text + '\n\n本機比對紀錄：\n' + (data.matches.length ? data.matches.map(m => Object.entries(m[2]).filter(([,v]) => v).map(([k,v]) => k + '：' + v).join('\n')).join('\n\n') : '尚無唯一匹配') + '\n\n' + data.details.warning;
  } catch (e) {
    $('drugResult').classList.add('error');
    $('drugResult').textContent = e.message;
  } finally {
    $('resultPanel').setAttribute('aria-busy', 'false');
    controls.forEach(id => $(id).disabled = false);
    $('lookup').textContent = '重新辨識';
    resetConsent();
  }
};

function renderMedicineResult(data) {
  const details=data.details, matched=data.matches.length===1;
  const record=matched?data.matches[0][2]:{};
  const name=matched?(details.product_name||record['中文品名']||record['英文品名']):data.ai?.name;
  $('drugResult').textContent=matched?'辨識完成，請比對下方品名、含量與原包裝。':name?'以下是辨識到的候選藥名，尚未在本機資料中確認。':'目前還無法判斷是哪一種藥。請補拍完整品名、含量或許可證字號。';
  const summary=document.createElement('section');summary.className='medicine-summary';
  const badge=document.createElement('p');badge.className='medicine-badge';badge.textContent=matched?'辨識完成 · 待核對':name?'候選結果 · 尚未確認':'資訊不足';
  const title=document.createElement('h3');title.textContent=name||'尚未辨識出藥名';
  summary.append(badge,title);
  if(matched&&details.english_name&&details.english_name!==name){const english=document.createElement('p');english.className='medicine-english';english.textContent=details.english_name;summary.append(english)}
  const purpose=document.createElement('p');purpose.className='medicine-purpose';purpose.textContent=matched?(details.purpose_plain||details.indication||'資料未提供用途，請詢問藥師。'):'用途尚未確認，請先核對藥名。';summary.append(purpose);
  $('aiResult').append(summary);
  if(matched){
    resultItem($('medicineDetails'),'含量與劑型',[details.strength||'含量未明確標示',details.form].filter(Boolean).join(' · '));
    resultItem($('medicineDetails'),'主要成分',details.ingredients);
    resultItem($('medicineDetails'),'如何服用？',details.dosage_text);
    resultItem($('medicineDetails'),'這份藥的有效期限',details.expiry_text);
    const extra=document.createElement('details');extra.className='medicine-more';
    const heading=document.createElement('summary');heading.textContent='查看許可證、廠商與登錄原文';extra.append(heading);
    for(const [label,value] of [['許可證字號',details.license||record['許可證字號']],['廠商',details.manufacturer],['登錄用途原文',details.indication],['資料來源',details.source]])resultItem(extra,label,value);
    $('medicineDetails').append(extra);
  }else if(data.ai){
    resultItem($('medicineDetails'),'AI 讀到的含量（待核對）',data.ai.strength);
    resultItem($('medicineDetails'),'藥袋用法原文（待核對）',details.dosage_text);
    resultItem($('medicineDetails'),'包裝效期原文（待核對）',details.expiry_text);
  }
  if(data.ai?.uncertainty)resultItem($('medicineDetails'),'還需要核對',data.ai.uncertainty);
  const reminder=document.createElement('p');reminder.className='helper';reminder.textContent='藥品含量不等於每次服用量；請依原藥袋或藥師指示用藥。';$('medicineDetails').append(reminder);
}
