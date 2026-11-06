# Jable Server — Windows 10/11 前後端分離版

> **專案來源與授權**：本專案為 [hcjohn463/JableTVDownload](https://github.com/hcjohn463/JableTVDownload) 的獨立維護修改版本（衍生作品），原始專案作者註記為 **hcjohn463**，原始授權為 **Apache License 2.0**。本 Repository 並非原作者官方發布版本。詳見 [LICENSE](LICENSE) 與 [NOTICE](NOTICE)。

## 原始來源、修改項目與授權

- **上游原始碼**：[hcjohn463/JableTVDownload](https://github.com/hcjohn463/JableTVDownload)
- **原始專案作者註記**：hcjohn463（見上游 `main.py`）
- **授權**：沿用 Apache License, Version 2.0；原始專案的授權與適用的著作權／作者資訊仍有效
- **本修改版**：[KKacobls/jable](https://github.com/KKacobls/jable)，獨立維護，不代表原作者立場或官方版本

本版在原有基礎上擴充／改寫了 Flask JSON API、獨立 Web 前端、任務佇列與狀態保存、M3U8 快取與 TS 離線合併、H.265 雙版本與壓縮進度、手動壓縮、暫停／繼續壓縮以及可復原垃圾桶等功能。重用並有修改的原始檔案已加註修改聲明；未修改的上游檔案保留原樣。此 Repository 的 Git 歷史是修改版另行建立的，完整原始歷史請見上游專案。


**Windows 10 / Intel Core i7-11700、24 GB RAM、無 NVIDIA 獨立顯卡** 的影片下載管理介面。

## 快速啟動

```powershell
cd D:\gpt-server\JableTVDownload-main
python -m pip install -r requirements.txt
python -m unittest -v tests.test_server
python app.py
```

開啟 `http://127.0.0.1:6003`。內網用 `http://<主機的區網 IP>:6003`；Cloudflare Tunnel Service 指向 `http://127.0.0.1:6003`。
**不需要登入或密碼**。預設 `0.0.0.0:6003`；不需額外的前端 Port，Cloudflare 不需特殊路由規則。

## 前後端分離

- `frontend/` 是純 HTML/CSS/JS，**無 Jinja/`url_for`**。首頁沒有參數時不載入圖片；`/?images=1` 會顯示列表封面，圖庫頁也可載入圖片。
- `backend/api.py` 是 Flask JSON API，獨立管理下載佇列、狀態、設定與歷史紀錄。
- `app.py` 是相容的啟動入口；用 Waitress 同時掛載 API 與靜態檔案到 Port 6003。
- 前端通過 `/api/...` 操作後端；可修改 `frontend/config.js` 將前端部署到另外的主機／靜態服務（後端 CORS 預設允許跨網域）。
- 完整 API 使用說明：**[`API_INTEGRATION.md`](API_INTEGRATION.md)**；油猴整合：**[`TAMPERMONKEY_GUIDE.md`](TAMPERMONKEY_GUIDE.md)**。在 `/settings` 可直接複製完整油猴腳本。

## 使用體驗

- 成功新增（含已存在）會**立即清空貼上框**並維持焦點，方便連續貼上；失敗保留原文。
- 任務卡片分成未完成／已完成，狀態局部更新；**不會重整整頁**，選取文字時暫停狀態寫入以免打斷複製。
- 支援複製名稱、自動更新開關、立即更新、個別／錯誤任務重試。
- 壓縮設定可在線上調整。預設 CPU libx265 / CRF 28（等級 5/10）、preset fast，最多 2 個下載任務同時進行。

## 更新與保留歷史

**不要刪除或覆蓋** `state.json`、`settings.json`、`downloads/`、`image/`。更新包刻意不提供上述資料，原本已完成的歷史紀錄仍保留，**即使影片已移走也不會被自動清除或重新下載**。升級前先停止 Python 並備份 `state.json`，然後覆蓋程式。

完整 FFmpeg（`ffmpeg.exe` 和 `ffprobe.exe`）必須加入 PATH。下載資料建議放在實體硬碟較大的磁區；設定 `JABLE_DOWNLOAD_DIR` 可以搬移路徑，但請先手動搬移既有影片資料。Windows 長期運行需關閉睡眠模式。

## 外網存取注意

依使用者要求，本服務**無密碼、無 IP 白名單、無登入**。只要 Cloudflare 網址對外公開，訪客就能查閱任務、提交下載、重試及修改設定。此版本不會主動阻擋外部請求，但 Cloudflare Access、WAF 等平台規則另由控制台管理。

請只下載有合法存取與保存權限的內容。Windows 10 的一般支援已於 2025-10-14 結束，長期提供外部服務建議確認安全更新方案。

## FFmpeg 安裝、失敗重試與雙目錄輸出（2026-11-04）

Windows PowerShell 安裝（需網路）：

```powershell
winget install --id Gyan.FFmpeg --exact
```

安裝後 **關閉舊 PowerShell 重新開啟**，執行 `ffmpeg -version`、`ffprobe -version`。
也可雙擊 `CHECK_FFMPEG.bat` 檢查，或透過環境變數 `FFMPEG_BIN_DIR` 指向包含 `ffmpeg.exe`、`ffprobe.exe` 的資料夾（例如 `D:\tools\ffmpeg\bin`），再重新啟動伺服器。

啟動 `python app.py` 後，在首頁點 **重試所有錯誤**，或用 `POST /api/retry/errors`。如果已成功封裝未壓縮 MP4，下一次直接使用本機來源重新壓縮；如果留下完整且可驗證數量的 `.ts` 片段，新版可直接重新封裝。對於舊版沒有保存分段數的任務，可能需要從 M3U8 再次驗證；若簽名已過期，需要重新取得連結。

新任務一律保留原始 MP4：

```text
downloads/
  uncompressed/<video-name>/<video-name>.mp4  # 未壓縮來源永久保留
  compressed/<video-name>/<video-name>.mp4    # H.265，僅當新檔比原檔小時才保留
```

如果原本的來源就是 H.265，會複製一份到 `compressed/`，兩份內容相同。停用壓縮時只有 `uncompressed/` 有影片。`JABLE_DOWNLOAD_DIR` 可以修改最外層下載根目錄，例如 `D:\JableDownloads`，其下自動建立兩個子資料夾。

舊的 `downloads/<video-name>/<video-name>.mp4`、`state.json` **原地保留**，程式會同時檢查新舊路徑，避免把下載過的任務判成未下載。如果要整理舊檔，可以執行：

```powershell
python ORGANIZE_OLD_DOWNLOADS.py
python ORGANIZE_OLD_DOWNLOADS.py --apply
```

第一行只預覽，第二行才複製舊影片到新目錄；**不刪原始檔，也不改 state.json**。舊版已經以 H.265 覆蓋原始影片者，無法憑空還原成未壓縮版本，會依目前影片編碼分類。

注意：同時保留兩個版本會額外使用磁碟空間，請定期檢查 D 槽剩餘容量。服務完全免密碼，若公開 Cloudflare 網域，任何能連上該網址的人都能提交下載或重試。

## M3U8 過期與 TS 續傳（2026-11-04 修正）

新任務新增時會提前在背景下載媒體播放清單，保存為 `downloads/uncompressed/<name>/playlist.m3u8` 與 `playlist_source.json`。如果之前的 `00000.ts` 等分段已經完整，新版會離線封裝，不必重新向過期 URL 下載。部分分段可在提供新的 `hls_url` 後續傳。舊版在 `downloads/<name>/` 的 TS 會保留直到新版成功封裝 MP4 並通過 ffprobe 驗證，才清理該次使用的 TS。詳見 `M3U8_RECOVERY_GUIDE.md`。

命令列檢查：`python RECOVER_TS.py --name <name>`。若可靠地知道總段數，可用 `python RECOVER_TS.py --name <name> --expected 300 --merge`，完整時離線生成未壓縮 MP4，**不刪 TS 或歷史紀錄**。


## 雙進度條、暫停壓縮、垃圾桶（2026-11-04 新版）

任務列表現在顯示下載進度與 H.265 壓縮進度兩條進度條。壓縮進行中可用「暫停壓縮／繼續壓縮」控制 FFmpeg 進程（需要 `psutil`，已加入 requirements.txt）。暫停僅限本次伺服器執行期間；重新啟動 Python 後需重新開始壓縮，但原始 MP4 仍保留。

任務卡片有「垃圾桶」可確認後軟刪除，移到 `downloads/.trash/` 並移除任務項目，允許重新提交同名任務。垃圾桶內容仍占磁碟空間。完整 API、離線合併進度、TS 清理及 GitHub 部署請看 [`NEW_VERSION_GUIDE.md`](NEW_VERSION_GUIDE.md)、[`GITHUB_SETUP.md`](GITHUB_SETUP.md)。


## 手動開始壓縮（2026-11-05）

當任務遇到 M3U8 `410 Gone`，但本機已有 MP4，任務列表會顯示「開始壓縮」，直接使用現有影片進行 H.265 壓縮，**不會重抓網路影片**。如果只留有 TS，則顯示「合併 TS／壓縮」；有快取的完整 M3U8 可直接離線合併，否則須明確確認本機段數。沒有 M3U8 時，TS 預設保留，避免缺少片尾卻刪除唯一分段。API 與 PowerShell 範例見 [`API_INTEGRATION.md`](API_INTEGRATION.md)。
