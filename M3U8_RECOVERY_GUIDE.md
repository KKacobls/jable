# M3U8 410 Gone：提前保存播放清單與 TS 續傳

## 410 是什麼？

`410 Gone` 通常表示提供方已讓暫時性的 HLS 連結失效（也可能是影片/資源被移除）。**儲存 `.m3u8` 不等於分段網址永久有效**；M3U8 只是播放清單，裡面列出 TS 分段和可能需要的 AES 金鑰網址。分段及金鑰也有可能分別過期。

本次改版在新增任務後立即（非同步）快取「實際媒體 M3U8」，而不是等到前面的影片下載完才開始抓。這樣可以記住**正確的分段數量、順序、媒體清單原文和原始遠端基底 URL**。儲存位置：

```
downloads/
  uncompressed/
    <name>/
      playlist.m3u8
      playlist_source.json
      00000.ts
      00001.ts
      ...
      <name>.mp4
  compressed/
    <name>/
      <name>.mp4
```

舊版已下載的 TS 可能仍位於 `downloads/<name>/00000.ts`，新版會檢查舊路徑，**不主動刪除或搬移**。原本的 `state.json`、已下載 MP4 與封面都不要刪除。

## 何時可以直接接續？

- 若已知預期分段總數是 300，且 `00000.ts` 到 `00299.ts` 全部存在且非空：**不需要再連到過期 M3U8**，可直接用 FFmpeg 封裝 MP4。
- 若只抓了其中 250 個：可以**保留原來的 250 個分段**，取得同一部影片最新的 `hls_url`，只抓缺的 50 個；前提是新舊清單的分段數與序號相同，否則系統會拒絕混合，避免壞檔。
- 若舊版沒有記錄分段總數，也沒有 `playlist.m3u8`：單靠看見最後一個 `00249.ts` **無法確定已下載完整**。此時需要可信的預期分段總數，才可以安全合併。

## 透過網頁繼續

1. 停掉舊版伺服器並備份 `state.json`；將更新包 `JableTVDownload-main/` 裡的程式碼覆蓋現有資料夾，**不要覆蓋、刪除下載資料**。
2. `python -m pip install -r requirements.txt`，確認 `ffmpeg -version`、`ffprobe -version` 都成功。
3. `python app.py`，在首頁對失敗任務按「重新下載」或「重試所有錯誤」。
4. 若 TS 已經完整，程式直接離線封裝；若仍缺少分段且遠端回傳 410，需要在影片頁用油猴重新取得 `hls_url`，**貼上相同的 `name` + 新 `hls_url`**。新版對「未完成」的同名任務會保留分段紀錄並重新排隊；**已完成**的歷史任務不會重複下載。

## API（同一個 Port 6003，Cloudflare Tunnel 不須修改）

### 新增任務時自動提前快取 M3U8

```powershell
$base = 'http://127.0.0.1:6003' # 或 https://j.xekro.top
$body = @{name='demo-001'; hls_url='https://example.com/video.m3u8'; image_url=''} | ConvertTo-Json
Invoke-RestMethod -Uri "$base/api/download" -Method Post -ContentType 'application/json' -Body $body
```

收到新增任務後，伺服器會立即在背景快取 M3U8；UI 不需要等快取完成才可以貼下一部影片。預先快取失敗不會刪除現有分段。

### 明確要求重新快取某個任務目前的 M3U8

```powershell
Invoke-RestMethod -Uri "$base/api/tasks/demo-001/cache-playlist" -Method Post
```

### 查看舊 TS 還缺多少段（不連網）

```powershell
Invoke-RestMethod -Uri "$base/api/tasks/demo-001/segments"
```

回傳包含 `playlist_cached`、`expected_segments`、`cached_segments`、`complete` 和 `missing_preview`。此 API 不會改變下載紀錄或重新下載。

### 用最新的 HLS 來源續傳錯誤任務

```powershell
$new = @{hls_url='https://example.com/new-expiring-source.m3u8'} | ConvertTo-Json
Invoke-RestMethod -Uri "$base/api/tasks/demo-001/source" -Method Post -ContentType 'application/json' -Body $new
```

只有未完成而且沒有正在執行的同名任務可以更新來源。已下載分段和 `state.json` 的其他欄位保留；若新清單跟舊清單分段數或序號不同，系統會報錯，防止把不同版本分段混在一起。

## 沒有 M3U8 快取、但手上已有大量 TS？

先檢查本機分段：

```powershell
cd D:\gpt-server\JableTVDownload-main
python RECOVER_TS.py --name demo-001
```

如果你已**從可信的來源得知**原本共 300 段，可指定：

```powershell
python RECOVER_TS.py --name demo-001 --expected 300 --merge
```

它會先確認 `00000.ts`~`00299.ts` 全部存在且非空，再由 FFmpeg 產生 `downloads/uncompressed/demo-001/demo-001.mp4`。這個指令**不刪原始 TS、也不直接修改 `state.json`**；之後回到網頁按重試，會沿用該 MP4，處理壓縮與完成狀態。

> 若來源影片需要 AES-128，舊的 `.ts` 必須是**先前下載程式已經解密過**的分段才能直接合併。只有加密原始片段、缺金鑰或片段不完整時，不能保證離線恢復。

## 更新與檢查

```powershell
python -m unittest -v tests.test_storage tests.test_playlist_cache
python app.py
```

本次更新不包含 `state.json`、`settings.json`、`downloads/`、`image/`，不會覆蓋舊歷史。即使 410，一旦抓到的 TS 仍會保留到成功封裝為止；離線手動恢復模式連成功後也不會刪除 TS。
