# JableTVDownload：前後端分離與 HTTP API 串接教學

版本：2026-11-04（Frontend/Backend 分離版）

## 1. 架構與啟動

```text
JableTVDownload-main/
├── app.py                  # Windows 啟動入口：python app.py
├── backend/
│   ├── __init__.py
│   └── api.py              # Flask JSON API、下載佇列、舊任務紀錄
├── frontend/
│   ├── index.html          # 下載任務 UI
│   ├── gallery.html        # 封面圖庫（只在此頁載入圖片）
│   ├── settings.html       # 設定 UI
│   ├── api-docs.html       # API 教學 UI
│   ├── config.js           # 前端 API 來源網址
│   ├── app.js              # fetch + 前端互動
│   └── style.css           # UI 樣式
├── tests/test_server.py
├── download.py / settings.py / ...
├── state.json              # 你的歷史任務紀錄，**不可刪除**
└── settings.json           # 目前設定（存在時保留）
```

前端是獨立靜態網站，不使用 Jinja 模板或 Flask `url_for()`，所有資料都透過 JSON API 取得。
為方便部署，Flask 同時**託管靜態前端檔案**（不是由 Flask 渲染 HTML），因此預設只有一個 Port **6003**。如果想把前端放到別的靜態網站，只要修改 `frontend/config.js` 的 `window.JABLE_API_BASE`，改成後端的 HTTPS 網址即可。

Windows 啟動：

```powershell
cd D:\gpt-server\JableTVDownload-main
python -m pip install -r requirements.txt
python -m unittest -v tests.test_server
python app.py
```

本機：`http://127.0.0.1:6003`；內網：`http://<伺服器的區網 IP>:6003`。
Cloudflare Tunnel 的 Service 設為：`http://127.0.0.1:6003`（在伺服器同一台電腦啟動 `cloudflared` 時）。無須另外暴露前端 Port。

> 本專案依你的需求**不需要帳號、密碼、Token 或 IP 白名單**，CORS 允許跨網域請求。若透過公開的 Cloudflare 網域提供服務，任何能存取該網址的人都能新增、重試下載及修改設定。

## 2. API 路由

| 方法 | 路徑 | 用途 |
| --- | --- | --- |
| `POST` | `/api/download` | **推薦：加入下載任務** |
| `POST` | `/api/tasks` | 舊版相容的新增任務入口 |
| `GET` | `/api/tasks` | 全部任務簡要狀態，不含來源 HLS 連結 |
| `GET` | `/api/tasks/<name>` | 查詢指定名稱的任務 |
| `POST` | `/api/retry/<name>` | 手動重新排入某一筆（已完成且檔案存在時不會重試） |
| `POST` | `/api/retry/errors` | 重試所有錯誤任務 |
| `GET` | `/api/settings` | 取得壓縮與並行設定 |
| `PUT` 或 `POST` | `/api/settings` | 儲存設定（JSON） |
| `GET` | `/api/health` | 存活檢查 |
| `GET` | `/status` | 保留舊版前端需要的狀態格式 |

## 3. 新增下載

HTTP `POST /api/download`，標頭 `Content-Type: application/json`：

```json
{
  "name": "demo-001",
  "hls_url": "https://example.com/video.m3u8",
  "image_url": "https://example.com/cover.jpg"
}
```

`image_url` 可省略或設為空字串。`name` 只接受英文、數字、連字號 `-`、底線 `_`，最多 96 字元。
`hls_url` 必須為有效的 HTTP(S) 網址；不接受內網／本機來源網址，以避免伺服器被拿去存取內部服務。這是**影片來源網址**的檢查，**不是訪客 IP 限制**。

也接受你原本的貼上格式（`text/plain`），或 `application/json` 的 `video_text` 欄位：

```text
hls
https://example.com/video.m3u8
name
demo-001
image
https://example.com/cover.jpg
```

**回應：**

- `201`：新任務已排入下載佇列，例如 `{ "ok": true, "added": true, "clear_input": true, "name": "demo-001", "status_url": "/api/tasks/demo-001" }`。
- `200`：已經有相同名稱的已完成或進行中紀錄，`added:false`，不會重複下載；`clear_input:true`。
- `422`：資料錯誤，`clear_input:false` 且包含 `message`；請保留使用者貼上內容。

注意：`201` 只代表**成功排入佇列**，不是影片已經下載完成。完成與否請用任務查詢 API。

## 4. 查詢與續傳

`GET /api/tasks` 會回傳：

```json
{
  "ok": true,
  "total": 1,
  "tasks": [{
    "name": "demo-001",
    "status": "排隊中...",
    "current": 0,
    "total": 1,
    "completed": false,
    "file_exists": false
  }]
}
```

`GET /api/tasks/demo-001` 回傳單筆的 `task` 物件，沒有找到則是 HTTP `404`。`completed` 表示**歷史任務被標記完成**，`file_exists` 表示原 MP4 檔目前仍在磁碟中。兩個值不同是正常情況，例如下載完後你手動搬走影片。

本程式會保留 `state.json` 已下載歷史，不會因 MP4 不存在就自動刪除歷史或重複下載。需要重試時呼叫 `POST /api/retry/<name>`。

## 5. Python 串接範例

先安裝 `python -m pip install requests`：

```python
import requests
from urllib.parse import quote

BASE = "http://127.0.0.1:6003"  # 或 https://你的-cloudflare-網域

payload = {
    "name": "demo-001",
    "hls_url": "https://example.com/video.m3u8",
    "image_url": ""
}
result = requests.post(f"{BASE}/api/download", json=payload, timeout=15)
print(result.status_code, result.json())

if result.ok:
    name = quote(payload["name"], safe="")
    status = requests.get(f"{BASE}/api/tasks/{name}", timeout=15)
    print(status.json())
```

## 6. PowerShell 串接範例

```powershell
$base = "http://127.0.0.1:6003"
$payload = @{
    name = "demo-001"
    hls_url = "https://example.com/video.m3u8"
    image_url = ""
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$base/api/download" -ContentType "application/json; charset=utf-8" -Body $payload
Invoke-RestMethod -Method Get -Uri "$base/api/tasks/demo-001"
```

## 7. JavaScript（自訂前端）

```javascript
const base = 'https://你的-cloudflare-網域';
const payload = {name:'demo-001',hls_url:'https://example.com/video.m3u8',image_url:''};
const response = await fetch(`${base}/api/download`, {
  method: 'POST',
  headers: {'Content-Type':'application/json'},
  body: JSON.stringify(payload)
});
const result = await response.json();
if (!response.ok) throw new Error(result.message || response.status);
console.log(result);
```

前端成功送出後**清空貼上欄位**，失敗則保留；每 5 秒使用 API 更新*必要*的任務進度，不整頁重新載入，選取文字期間暫停更新以免影響複製。

## 8. 壓縮設定

`GET /api/settings` 回傳 `{ "ok": true, "settings": {...}, "download_dir": "..." }`。
修改用 `PUT /api/settings`：

```json
{
  "compression_enabled": true,
  "compression_level": 5,
  "encoder_preset": "fast",
  "segment_workers": 6,
  "max_parallel_downloads": 2
}
```

設定將存入 `settings.json`。降低同時下載數後需重啟服務；已開始執行的任務不會中途改變壓縮方式。

## 9. 更新前備份與測試

先停止舊程式並備份：

```powershell
Copy-Item .\state.json .\state.backup.json
if (Test-Path .\settings.json) { Copy-Item .\settings.json .\settings.backup.json }
```

**升級 ZIP 不包含** `state.json`、`settings.json`、`downloads/`，請保留現有檔案；將新版程式覆蓋相同路徑即可。舊的 `templates/`、`static/` 目錄新版已不使用，可以保留而不影響執行。測試：

```powershell
python -m unittest -v tests.test_server
python app.py
# 新開 PowerShell 視窗
Invoke-RestMethod http://127.0.0.1:6003/api/health
Invoke-RestMethod http://127.0.0.1:6003/api/tasks
```

如果未安裝 Flask 或 m3u8，先執行 `python -m pip install -r requirements.txt`。

## 10. 油猴腳本與圖片模式（2026-11-04 新增）

- 開啟 `/settings`，向下找到 **Tampermonkey 油猴腳本**，輸入伺服器網址，再點 **複製完整油猴腳本**。
- 程式碼來源：`frontend/JableVideoInfo.user.js`；也可直接安裝 `/assets/JableVideoInfo.user.js`。
- 油猴在影片頁以 `GET /api/tasks/<name>` 查詢歷史，支援複製 JSON、手動送出 `POST /api/download`、重新查詢、修改伺服器網址。
- `https://j.xekro.top/` 預設不載入列表圖片；`https://j.xekro.top/?images=1` 或任何非空查詢參數會顯示列表封面。
- **完整使用說明：[`TAMPERMONKEY_GUIDE.md`](TAMPERMONKEY_GUIDE.md)**。

## 新增：雙目錄與 FFmpeg 狀態（2026-11-04）

`GET /api/health` 增加 `ffmpeg_available` 與 `ffprobe_available`，可檢查伺服器是否找到工具。

`GET /api/settings` 增加 `uncompressed_dir` 和 `compressed_dir` 兩個完整路徑。

`GET /api/tasks` 和 `GET /api/tasks/<name>` 增加 `stored_versions` 陣列，內容可能是 `uncompressed`、`compressed`、`legacy`，表示實際存在的檔案路徑分類；保留既有 `completed`、`file_exists` 等欄位供舊程式使用。

錯誤任務重新執行：

```python
import requests
base='https://j.xekro.top'
response = requests.post(base + '/api/retry/errors',timeout=15)
print(response.status_code,response.json())
```

雙份影片與 FFmpeg 安裝詳見 `FFMPEG_STORAGE_GUIDE.md`。歷史紀錄 `state.json` 永遠不要覆蓋。

## 9. M3U8 短期網址、提前快取與 TS 續傳（2026-11-04 更新）

新增下載任務 `POST /api/download` 時會立即背景快取媒體 `playlist.m3u8` 和 `playlist_source.json`，並記住 `expected_segments`；不必等到下載工作者有空才能抓播放清單。這不保證 TS 分段網址不會過期。

| 方法 | 路由 | 用途 |
| --- | --- | --- |
| `POST` | `/api/tasks/<name>/cache-playlist` | 立即保存任務目前的 M3U8 原文、遠端基底與分段數 |
| `GET` | `/api/tasks/<name>/segments` | 本機 TS 完整性、預期分段數及缺少片段（不訪問來源伺服器） |
| `POST` | `/api/tasks/<name>/source` | 對未完成任務設定新的 `hls_url` 並續傳；保留已存在 TS 和下載歷史 |

使用範例與安全注意事項請參閱 [`M3U8_RECOVERY_GUIDE.md`](M3U8_RECOVERY_GUIDE.md)。


## 10. 雙進度、壓縮暫停／繼續與垃圾桶

`GET /api/tasks` 的每個任務會包含 `download_current`, `download_total`, `download_state`, `compression_percent`, `compression_state`, `merge_percent`。

- `POST /api/tasks/<name>/compression/pause`: 暫停目前 FFmpeg 壓縮程序
- `POST /api/tasks/<name>/compression/resume`: 繼續目前 FFmpeg 壓縮程序
- `DELETE /api/tasks/<name>`，JSON `{"confirm":"<name>"}`：將任務、影片與封面移入 `downloads/.trash`；執行中拒絕。

可接受壓縮狀態包含 `pending`、`queued`、`running`、`paused`、`completed`、`skipped`、`disabled`、`error`、`interrupted`。暫停不是跨伺服器重啟的續傳機制。
