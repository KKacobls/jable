# JableTVDownload：Tampermonkey 油猴腳本與圖片模式

版本：2026-11-04 v2.0（前後端分離；保留既有 `state.json`）

## 1. 在伺服器設定頁直接複製油猴腳本

1. 啟動伺服器：`python app.py`，預設 Port `6003`，免帳號密碼。
2. 打開 `https://j.xekro.top/settings`（或 `http://127.0.0.1:6003/settings`）。
3. 向下找到「Tampermonkey 油猴腳本」。填寫**下載伺服器網址**，例如 `https://j.xekro.top`。
4. 按「**複製完整油猴腳本**」，進入 Tampermonkey → 建立新腳本，貼上後儲存。
5. 瀏覽 `https://jable.tv/videos/<影片名稱>/`，右下角會出現控制面板。

腳本也獨立放在 `frontend/JableVideoInfo.user.js`，可從設定頁點「下載原始 .user.js」。如果選擇原始檔，預設伺服器網址為 `https://j.xekro.top`；可開啟油猴選單「設定下載伺服器網址」改成自己的 HTTPS 網域（網址變更後重新整理影片頁面）。

> 注意：Tampermonkey 第一次對指定網址跨網域連線，可能會請求允許連線。這是擴充功能自己的權限確認，不是伺服器設密碼或 IP 限制。腳本使用 `GM_xmlhttpRequest` 並設定 `@connect *`，避免一般網頁跨網域 CORS 限制。

## 2. 影片頁提供什麼功能

- **下載紀錄**：自動以影片名稱呼叫 `GET /api/tasks/<name>`，顯示「尚未下載」、「已完成（檔案存在）」、「已下載過（歷史保留）」或目前的下載狀態。
- **複製 JSON**：保留舊腳本的功能，複製 `hls_url`、`name`、`image_url` 三個欄位，格式如下。
- **送到伺服器**：明確點按才送出 `POST /api/download`，不會只因瀏覽影片頁就自動下載。
- **重新查詢**：不重整影片頁，只更新下載狀態。
- **設定網址**：可修改腳本使用的伺服器位置。

```json
{
  "hls_url": "https://example.com/video.m3u8",
  "name": "demo-001",
  "image_url": "https://example.com/cover.jpg"
}
```

當來源 HLS 遲延載入時，按「送到伺服器」會重新擷取一次，若仍找不到 HLS 則不會送出無效下載請求。

**重複下載判斷**：若 `state.json` 已記錄同名且狀態為已完成，後端回應 `200` 與 `added:false`，不會重新下載，原歷史紀錄及已存在檔案都不會被覆蓋。`file_exists:false` 只表示影片已移動／目前找不到檔案，仍視為「已下載過」。

## 3. 主頁圖片顯示參數

預設首頁不載入封面圖片（列表只有文字，適合遠端管理）。

| 網址 | 效果 |
| --- | --- |
| `https://j.xekro.top/` | 不顯示、不請求列表封面圖片 |
| `https://j.xekro.top/?images=1` | 在已完成／未完成任務卡片顯示封面 |
| `https://j.xekro.top/?anything` | 只要有非空查詢參數，也顯示列表圖片 |
| `https://j.xekro.top/gallery` | 獨立圖片圖庫，一直可以打開 |

首頁「顯示列表圖片／隱藏列表圖片」按鈕也可切換。圖片採瀏覽器 lazy loading；一般狀態更新只修改文字／進度，不重建卡片或強制刷新頁面，以免打斷複製內容。

## 4. 串接 API

後端使用同一個服務入口（例如 `https://j.xekro.top`），不需要另外開 Port。

| 動作 | 方法與路徑 | 備註 |
| --- | --- | --- |
| 查詢影片歷史 | `GET /api/tasks/<name>` | 已記錄時 `200`，不存在時 `404` |
| 查詢所有任務 | `GET /api/tasks` | 不返回 HLS 源網址 |
| 新增下載 | `POST /api/download` | `201` 新增、`200` 已存在、`422` 格式錯誤 |
| 重試下載 | `POST /api/retry/<name>` | 已完成且檔案存在不會重試 |

Python 範例：

```python
import requests
from urllib.parse import quote
base = "https://j.xekro.top"  # 可改成你自己的網址
name = "demo-001"
status = requests.get(f"{base}/api/tasks/{quote(name, safe='')}", timeout=15)
print(status.status_code, status.json())
video = {"name": name, "hls_url": "https://example.com/video.m3u8", "image_url": ""}
response = requests.post(f"{base}/api/download", json=video, timeout=15)
print(response.status_code, response.json())
```

詳細格式、回傳欄位、PowerShell 等範例參閱 `API_INTEGRATION.md`。

## 5. 更新注意

請先停止伺服器，再將 **更新包** 內容複製到 `D:\gpt-server\JableTVDownload-main\`，不要刪除或覆蓋 `state.json`、`settings.json`、`downloads/`、`image/`。更新包未包含這些歷史資料。

```powershell
cd D:\gpt-server\JableTVDownload-main
Copy-Item .\state.json .\state.backup.json
python -m pip install -r requirements.txt
python -m unittest -v tests.test_server
python app.py
```

Cloudflare Tunnel Service 指向 `http://127.0.0.1:6003`（cloudflared 與伺服器位於同一台 Windows 時）。前端和後端程式碼仍然分離，但由同一個 Port 託管。無應用層登入或 IP 白名單；**公開 Tunnel 網址的人可查詢及新增任務**，請確認你願意承擔這個存取範圍。
