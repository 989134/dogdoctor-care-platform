# Gemini 藥袋辨識

後端也支援持久本機設定：`guardian_data/gemini-api-key.txt` 儲存金鑰，`guardian_data/gemini-model.txt` 儲存模型名稱。這些檔案不由網頁提供，且資料夾已排除版本控制；本次金鑰檔已限制為本機使用者與執行環境可讀。環境變數優先於檔案設定；變更後需重啟服務。勿分享金鑰檔案。

前端已提供本機 OCR 與 Gemini AI 兩種模式。Gemini 從照片／文字擷取品名、含量、許可證、藥袋用法原文與包裝效期，再以擷取欄位查詢本機許可證資料。沒有唯一匹配時仍顯示 AI 候選與缺漏資訊，但不宣稱已確認藥品。僅有藥丸外觀時不推測品名。

## 啟用方式（PowerShell）

於專案根目錄執行，金鑰只在當次程序環境中設定，不放在網頁或版本控制中。以下使用本次已安裝相依套件的 `.venv-test`；也可換成您自己的 Python 虛擬環境。

```powershell
$geminiSecret = Read-Host '輸入 Gemini API key' -AsSecureString
$env:GEMINI_API_KEY = [System.Net.NetworkCredential]::new('', $geminiSecret).Password
$env:GEMINI_MODEL = 'gemini-flash-lite-latest'
try {
    .\.venv-test\Scripts\python.exe .\guardian_portal\run_guardian.py
} finally {
    Remove-Item Env:GEMINI_API_KEY -ErrorAction SilentlyContinue
    $geminiSecret.Dispose()
}
```

打開 http://127.0.0.1:5006，登入後選擇「藥品」→「Gemini AI 圖片／文字辨識」。上傳清晰的單一藥袋、遮蔽個人資料，勾選傳送同意，再按「開始辨識」。金鑰變更需重新啟動服務。此程式不自動讀取 `.env`。

模型名稱可透過 `GEMINI_MODEL` 變更，須為帳號有權限、支援圖片及 JSON structured output 的 Gemini 模型。金鑰可在 [Google AI Studio](https://aistudio.google.com/apikey) 管理；API 計費及資料處理條款依您的 Google 專案設定。

## 行為與界線

- 金鑰只由 Flask 伺服器持有；瀏覽器只能讀到服務是否已設定。
- AI 模式必須明確同意傳送本次文字／圖片至 Google。圖片會旋轉、縮小並重新編碼後傳送，圖片中的個人資料仍須由使用者遮住。本機模式不呼叫 Google。
- 本程式不儲存辨識圖片或 AI 原始回應。稽核僅記錄呼叫者、時間及模式；Google 端如何處理資料依其服務條款。
- AI 文字與本機登錄資料分開標示；本機資料非即時。AI 無法保證辨識正確，不補出用法、不將含量當作服用量、不將許可證有效日期當成包裝效期。
- 辨識結果不會自動寫入藥品清單，仍須逐字核對並手動儲存。
- 未設定金鑰可使用本機模式。AI 逾時、配額不足、拒絕回應或格式不完整時顯示錯誤，保留輸入供重試／切換模式。
- 每帳號每分鐘最多 6 次 AI 要求，單一服務程序同時最多 2 次；多程序部署時須另設共享限流。
- 請上傳 7 MB 以下 JPG／PNG／WebP，伺服器最多接受 1600 萬像素。AI 網路逾時為 45 秒。

## 驗證

```powershell
Set-Location guardian_portal
..\.venv-test\Scripts\python.exe -m unittest test_guardian test_gemini -v
```

測試涵蓋權限、CSRF、傳送同意、未設金鑰、結構化回應、錯誤遮蔽、AI 候選不自動保存、圖片驗證及限流。自動測試的 Gemini 回應使用模擬，不會呼叫付費服務；正式金鑰和實拍藥袋仍需實際驗收。

串接參考：[Gemini generateContent API](https://ai.google.dev/api/generate-content)、[結構化輸出設定](https://ai.google.dev/gemini-api/docs/migrate-to-interactions)。
