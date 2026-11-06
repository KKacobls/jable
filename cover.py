# Based on hcjohn463/JableTVDownload (Apache-2.0).
# Modified: cover download now accepts an image URL directly.
import requests
import os


def getCover(image_url, folder_path):
    """
    直接從圖片網址下載封面
    
    Args:
        image_url: 圖片的完整網址
        folder_path: 保存圖片的資料夾路徑
    """
    # 生成封面檔名
    cover_name = f"{os.path.basename(folder_path)}.jpg"
    cover_path = os.path.join(folder_path, cover_name)
    
    try:
        # 直接下載圖片
        r = requests.get(image_url)
        r.raise_for_status()  # 檢查HTTP錯誤
        
        # 保存圖片
        with open(cover_path, "wb") as cover_fh:
            for chunk in r.iter_content(chunk_size=1024):
                if chunk:
                    cover_fh.write(chunk)
        
        print(f"cover downloaded as {cover_name}")
        
    except requests.exceptions.RequestException as e:
        print(f"unable to download cover: {e}")
    except Exception as e:
        print(f"error saving cover: {e}")
