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

## 追蹤原始開源專案（不直接合併）

本 Repo 為 [hcjohn463/JableTVDownload](https://github.com/hcjohn463/JableTVDownload) 的 Apache-2.0 授權修改版，原作者與變更說明參見 [README.md](README.md) 和 [NOTICE](NOTICE)。

如需查看原作者的新版本，可在已連接 `origin` 的 Windows 專案執行：

```powershell
git remote add upstream https://github.com/hcjohn463/JableTVDownload.git
git fetch upstream
```

這兩個命令只新增遠端來源並下載更新資訊，**不會覆蓋本機程式**。本修改版目前與上游沒有共同的 Git 提交歷史，請先逐檔比較，再選擇性移植變更；不要直接執行 `git merge upstream/main`。
