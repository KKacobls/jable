# 列表封面／設定頁修正版（2026-11-04）

## 更新內容

- `https://j.xekro.top/`：首頁預設不建立封面元素，也不請求封面圖片。
- `https://j.xekro.top/?images=1`：在已完成／未完成任務列表顯示封面；有任何非空查詢參數也會開啟。
- 「顯示／隱藏列表圖片」會直接切換，不用重新整理整個頁面；網址同步變更。
- 圖片區塊改成 CSS Grid，避免和任務文字互相重疊、受 float 影響。
- 圖片載入失敗會顯示明確訊息及「重試封面」按鈕；不會默默消失。
- 首頁 JS/CSS 的網址版本更新，後端 `/assets/` 採不快取設定，避免 Cloudflare 或瀏覽器繼續載入舊版前端。
- 設定頁保留內嵌油猴腳本，以及「複製完整油猴腳本」／「下載目前設定版 .user.js」按鈕。
- 不更動 API、下載歷史或影片；更新包**刻意不包含** `state.json`、`settings.json`、`downloads/`、`image/`、密碼或快取檔。

## Windows 更新步驟

1. 停止 `python app.py`。
2. 建議先在原專案資料夾執行：`Copy-Item .\state.json .\state.backup.json`。
3. 解開本次 ZIP，將其中 `JableTVDownload-main` 的程式碼覆蓋到 `D:\gpt-server\JableTVDownload-main`，**不要刪掉原本的專案資料夾**。
4. 重新執行 `python app.py`。Cloudflare Tunnel 仍連到 `http://127.0.0.1:6003`。
5. 開啟 `https://j.xekro.top/?images=1`，必要時用 Ctrl+Shift+R 強制更新快取。

## 若仍看不到圖片

1. 在 `https://j.xekro.top/?images=1` 按下「顯示列表圖片」／「隱藏列表圖片」切換，確認會出現封面區塊。
2. 如果封面區塊顯示「封面載入失敗」，可以按「重試封面」。
3. 開啟瀏覽器開發者工具（F12）→ Network → Img，檢查 `/image/<影片名稱>.jpg` 是否回傳 HTTP 200。若回傳 404，請確認伺服器的 `image/` 資料夾保留了該檔案。
4. 在 Network 檢查 `/assets/app.js?v=20261104-images-fix4` 是否為新版本，而不是舊版快取。

## 驗證範圍

已通過 Python 靜態檢查、JavaScript 語法檢查、前端 contract 測試，以及 Chromium DOM 模擬：無參數不顯示封面、開啟後載入 2/2 張、本頁關閉後移除圖片；設定頁內嵌油猴腳本顯示正常。

實際 Cloudflare 線上網站與 Windows 主機尚未能在本環境直接確認；若本機驗證正常但線上仍不顯示，請依 Network 回傳狀態協助區分是快取、路由或來源圖片問題。
