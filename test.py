# test.py

# 匯入我們需要測試的函式以及必要的模d組
from download import download_video
import threading
import json

# --- 模擬 app.py 中的共享狀態 ---

# 1. download_progress: 一個字典，用於儲存下載進度。
#    在真實應用中，這是所有線程共享的。
download_progress = {}

# 2. state_lock: 一個線程鎖，用於保護對 download_progress 的並行訪問。
state_lock = threading.Lock()

# 3. save_state_func: 一個假的儲存函式。
#    在真實應用中，這個函式會將 download_progress 寫入 JSON 檔案。
#    在測試中，我們讓它印出一條訊息，表示它被呼叫了。
def fake_save_state():
    # 為了不讓終端機被洗版，可以先註解掉，或在需要時打開
    # print("[測試] save_state_func 被呼叫。")
    pass

# --- 設置您要測試的下載資訊 ---

# 請將以下三個變數替換為您要下載的實際資訊
# 範例1 (無加密):
# hls_url = "https://qabo-ahha.mushroomtrack.com/hls/I6-JZhb23FuZQ_Ywk6bbMw/1749970896/0/309/309.m3u8"
# name = "sdmu-672-c"
# image_url = "https://assets-cdn.jable.tv/contents/videos_screenshots/0/309/preview.jpg"

# 範例2 (有加密):
text='''hlsUrl
https://asf-doc.mushroomtrack.com/hls/DCJN-3WOno85mf2ktxRLeQ/1750658811/51000/51306/51306.m3u8
name
mukc-100
image
https://assets-cdn.jable.tv/contents/videos_screenshots/51000/51306/preview.jpg'''.split('\n')
hls_url = text[1]
name = text[3]
image_url = text[5]


# --- 執行測試 ---

if __name__ == '__main__':
    print("--- 開始單個下載測試 ---")

    # 在呼叫 download_video 之前，需要先手動將任務加入進度字典
    # 這一步模擬了 app.py 在啟動線程前的操作
    download_progress[name] = {
        'status': '排隊中...',
        'current': 0,
        'total': 1,
        'name': name,
        'image_url': image_url,
        'hls_url': hls_url
    }
    
    # 直接呼叫下載函式
    # 由於這是在主線程中直接執行，程式會在這裡等待直到下載完成或失敗。
    download_video(
        hls_url=hls_url,
        name=name,
        image_url=image_url,
        download_progress=download_progress,
        save_state_func=fake_save_state,
        lock=state_lock
    )

    print("\n--- 測試執行完畢 ---")
    print("最終任務狀態:")
    # 印出最終的結果
    print(json.dumps(download_progress, indent=4, ensure_ascii=False))
