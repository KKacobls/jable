# JableTVDownload 新版：雙進度、壓縮暫停／繼續、垃圾桶、合併進度

更新：2026-11-04

## 安裝

1. 先停止 `python app.py`，備份 `state.json`、`settings.json`。
2. **只覆蓋程式碼**；切勿將任何下載影片、TS 或備份紀錄提交到 GitHub。
3. 在 Tabby PowerShell 執行（每行可獨立執行）：

```powershell
cd D:\gpt-server\JableTVDownload-main
python -m pip install -r requirements.txt
python -m unittest -v tests.test_storage tests.test_playlist_cache tests.test_dual_progress tests.test_ui_contract tests.test_server
python app.py
```

原有 Port 6003、Cloudflare Tunnel、無密碼與 JSON API 仍維持。

## 兩條進度

- 「下載」：根據已完成 TS 數/總數，原始 MP4 可用時顯示 100%。
- 「壓縮」：依 FFmpeg `-progress` 回報 0-100%；會顯示等待中、執行中、已暫停、已完成、跳過或失敗。
- 離線 TS 封裝額外顯示「封裝」百分比，若沒有 M3U8，百分比使用抽樣 TS 時長估算；它只是預估值，不能證明影片分段完整。

## 暫停／繼續

壓縮開始後，按卡片上的「暫停壓縮」會暫停 FFmpeg 的處理程序，CPU 使用量會下降，MP4 暫存檔保留。按「繼續壓縮」會從同一個處理程序恢復，**不會重新編碼已處理的部分**。

API（無密碼；請自行在 Cloudflare 控制公開範圍）：

- `POST /api/tasks/<name>/compression/pause`
- `POST /api/tasks/<name>/compression/resume`

注意：此暫停**不是跨重開機續壓**。不要在暫停期間關閉 Python 或重新啟動 Windows；若服務重啟，請對該任務重試壓縮，已下載的原始 MP4 不用重抓。

## 垃圾桶

卡片上點「垃圾桶」，確認並完整輸入片名後，會將該任務狀態、封面和影片移到 `downloads/.trash/<片名>-時間戳/`。可重新提交同名任務，不影響其他影片。這是可復原軟刪除，**不會立刻回收磁碟空間**。正在執行或排隊的任務不能刪除。

API：`DELETE /api/tasks/<name>`，JSON body `{"confirm":"<name>"}`。

## TS 自動清理

正常下載器完成 FFmpeg 封裝並通過 ffprobe 檢查之後，會刪除**本次參與封裝的 `.ts`**；在成功前不刪。舊版 `RECOVER_TS.py` 的手動合併改為顯示進度，依然預設保留 TS；如已確定總分段數和影片結尾完整，可指定：

```powershell
python RECOVER_TS.py --name snos-399 --expected 1850 --merge --delete-ts
```

沒有原始 M3U8 時，單看檔名連續與數量仍不足以證明分段完整，手動清理需自行確認片尾。

## GitHub

源碼 `.gitignore` 排除 `state.json`、`settings.json`、`downloads/`、`image/`、暫存與密碼設定。建議建立**私人** `JableTVDownload` Repository，再將這份源碼推送；請勿上傳含影片與個人下載紀錄的全量 ZIP。
