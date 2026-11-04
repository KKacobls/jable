# GitHub 原始碼與更新說明

GitHub Repo: https://github.com/KKacobls/jable

## 在 Windows 新電腦下載

```powershell
git clone https://github.com/KKacobls/jable.git
cd jable
python -m pip install -r requirements.txt
python app.py
```

## 現有 Windows 伺服器更新

**不要直接對包含既有影片的資料夾使用 `git clean -fdx` 或 `git reset --hard`。**

1. 停止 `python app.py`，備份 `state.json`、`settings.json`、`downloads/`、`image/`。
2. 如果現有 `D:\gpt-server\JableTVDownload-main` 並非 Git clone 版本，請先在其他資料夾 `git clone`，再複製程式檔更新。不要直接刪除舊目錄。
3. 若原資料夾已設定並追蹤此 Repo，可使用 `git pull --ff-only` 更新。
4. 更新依賴後重新啟動 `python app.py`。

原始碼 Repo 不包含使用者的歷史紀錄、影片、TS、下載設定及封面。請勿將這些資料手動提交到公開 Repo。
