let setupRequired=false,loginBusy=false,loginRetryAt=0,authReady=false;
function updateLoginButton(){
  const seconds=Math.max(0,Math.ceil((loginRetryAt-Date.now())/1000));
  $('loginButton').disabled=!authReady||loginBusy||(!setupRequired&&seconds>0);
  $('loginButton').textContent=!authReady?'正在載入…':loginBusy?'處理中…':!setupRequired&&seconds>0?'請等待 '+seconds+' 秒':setupRequired?'建立帳號並登入':'登入';
}
setInterval(updateLoginButton,1000);
async function loadAuthStatus(){
  const response=await fetch('/api/auth/status');
  if(!response.ok)throw Error('無法讀取登入狀態，請重新整理或確認服務已啟動。');
  const state=await response.json();authReady=true;setupRequired=state.setup_required;
  loginRetryAt=Date.now()+state.retry_after*1000;
  $('loginTitle').textContent=setupRequired?'首次使用：建立管理員帳號':'登入照護網頁';
  $('loginHelp').textContent=setupRequired?'尚未建立帳號。請自訂帳號與至少 12 字元的密碼，建立成功後會直接登入。':'使用您先前建立的帳號密碼登入，不需要設定碼。';
  $('setupFields').hidden=!setupRequired;
  $('confirmPassword').required=setupRequired;
  $('password').minLength=setupRequired?12:1;
  $('password').autocomplete=setupRequired?'new-password':'current-password';
  if(!setupRequired)$('setup').value='';
  $('setupCodeDetails').open=setupRequired&&!$('setup').value;
  $('setupCodeSummary').textContent=$('setup').value?'本機設定碼已帶入':'本機設定碼（必填）';
  updateLoginButton();
}
$('setupFile').onchange=async()=>{
  const file=$('setupFile').files[0];if(!file)return;
  if(file.size>4096){$('loginError').textContent='請選擇 setup-token.txt 設定碼檔案。';return}
  $('setup').value=(await file.text()).trim();$('setupCodeSummary').textContent='本機設定碼已載入';$('setupCodeDetails').open=false;
};
$('loginForm').onsubmit=async event=>{
  event.preventDefault();if(loginBusy)return;
  $('loginError').textContent='';note('');
  if(setupRequired&&$('password').value.length<12){$('loginError').textContent='密碼至少需要 12 個字元。';$('password').focus();return}
  if(setupRequired&&$('password').value!==$('confirmPassword').value){$('loginError').textContent='兩次輸入的密碼不一致，請重新確認。';$('confirmPassword').focus();return}
  if(setupRequired&&!$('setup').value.trim()){$('loginError').textContent='尚未提供本機設定碼，請選取 setup-token.txt 或使用本機設定連結。';$('setupCodeDetails').open=true;return}
  loginBusy=true;updateLoginButton();
  try{
    const response=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:$('name').value.trim(),password:$('password').value,setup:setupRequired?$('setup').value.trim():''})});
    const data=await response.json();
    if(!response.ok){if(data.retry_after)loginRetryAt=Date.now()+data.retry_after*1000;throw Error(data.error||'登入失敗')}
    $('password').value='';$('confirmPassword').value='';$('setup').value='';$('setupFile').value='';loginRetryAt=0;
    await enter(data);note('已登入');
  }catch(error){$('loginError').textContent=error.message}
  finally{loginBusy=false;updateLoginButton()}
};
(async()=>{
  // A fragment is provided only by a trusted local launch link, never by a public API.
  const fragment=new URLSearchParams(location.hash.slice(1));
  if(fragment.has('setup')){$('setup').value=fragment.get('setup');history.replaceState(null,'',location.pathname+location.search)}
  try{await loadAuthStatus();const response=await fetch('/api/me');if(response.ok)await enter(await response.json())}
  catch(error){$('loginError').textContent=error.message;$('loginButton').disabled=true}
})();
