"""Inspect or recover TS files offline without deleting any segment or history.

Usage:
  python RECOVER_TS.py --name example-001
  python RECOVER_TS.py --name example-001 --expected 125 --merge

If no manifest was saved by older versions, you must supply --expected from a
trusted source. A contiguous list ending in 00042.ts does NOT prove completion.
"""
import argparse
import json
import sys
from pathlib import Path

from download import merge_files_to_mp4, segment_cache_status, delete_temp_files
from settings import get_download_dir, video_locations


def main(argv=None):
    parser = argparse.ArgumentParser(description='離線檢查 / 合併已下載的 00000.ts 分段')
    parser.add_argument('--name', required=True, help='影片任務名稱，例如 demo-001')
    parser.add_argument('--expected', type=int, help='你已驗證的 M3U8 分段總數')
    parser.add_argument('--merge', action='store_true', help='合併並顯示進度；預設保留 TS')
    parser.add_argument('--delete-ts', action='store_true', help='合併並完成 ffprobe 驗證後刪除 TS（請先確認總段數）')
    args = parser.parse_args(argv)
    import re
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}', args.name):
        parser.error('名稱只能使用英數字、連字號與底線')
    if args.expected is not None and args.expected <= 0:
        parser.error('--expected 必須為正整數')
    root = get_download_dir()
    expected = args.expected
    if expected is None:
        # Earlier downloaders recorded total count in state.json even without
        # saving the original playlist. Read only; never alter history here.
        state_path = Path(__file__).resolve().parent / 'state.json'
        if state_path.is_file():
            try:
                item = json.loads(state_path.read_text(encoding='utf-8')).get(args.name, {})
                value = item.get('expected_segments') if isinstance(item, dict) else None
                expected = value if type(value) is int and value > 0 else None
            except (ValueError, OSError, AttributeError, TypeError):
                pass
    result = segment_cache_status(args.name, root, expected=expected)
    print('分段資料夾:', result['folder'])
    print('M3U8 已快取:', result['playlist_cached'])
    print('本機分段數:', result['cached_segments'])
    print('預期總數:', result['expected_segments'] or '未知')
    print('已完整:', result['complete'])
    if result['missing_preview']:
        print('缺少分段（最多列 30 個）:', result['missing_preview'])
    if not args.merge:
        print('僅檢查，沒有變更任何檔案。要合併請加 --merge。')
        return 0
    if not result['complete']:
        print('拒絕合併：分段完整性無法確認。請取得原本分段總數。', file=sys.stderr)
        return 2
    total = result['expected_segments']
    if args.expected is not None and args.expected != total:
        print('你指定的分段數與已保存的 M3U8 不同，拒絕合併。', file=sys.stderr)
        return 2
    folder = Path(result['folder'])
    output = video_locations(args.name, root)['uncompressed']
    files = [folder / f'{i:05d}.ts' for i in range(total)]
    print('開始封裝：', output, flush=True)
    seen = [-1]
    def show_progress(percent, seconds):
        display = f'{percent}%' if percent is not None else '總時間未知'
        current = percent if percent is not None else int(seconds)
        if current != seen[0]:
            print(f'\r封裝進度：{display}，已處理 {seconds:.1f} 秒',end='',flush=True)
            seen[0] = current
    merge_files_to_mp4(files, output, progress_callback=show_progress)
    print('\n成功，MP4 通過 ffprobe。')
    if args.delete_ts:
        delete_temp_files(folder, files)
        print('已清除合併使用的 TS；state.json 與 M3U8 保留。')
    else:
        print('原本的 TS 仍保留。下次如已確認片尾無缺失，可用 --delete-ts。')
    print('重新啟動伺服器並按「重新下載」，程式會沿用 MP4 完成後續步驟。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
