"""Non-destructive migration for older downloads/<name>/<name>.mp4.

Usage:
    python ORGANIZE_OLD_DOWNLOADS.py          # Preview only
    python ORGANIZE_OLD_DOWNLOADS.py --apply  # Copy old MP4s into new folders

This never changes state.json and never deletes a source file. Existing files
at the destination are not overwritten.
"""
import argparse
import re
import shutil
import sys
from pathlib import Path

from h265 import _codec, _program
from settings import get_download_dir, get_storage_dirs

VALID = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}$')


def organize(root, apply=False):
    root = Path(root)
    plain, hevc = get_storage_dirs(root)
    count = skipped = 0
    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or folder.name in ('compressed', 'uncompressed'):
            continue
        name = folder.name
        if not VALID.fullmatch(name):
            continue
        src = folder / (name + '.mp4')
        if not src.is_file() or src.stat().st_size <= 0:
            continue
        codec = _codec(src)
        if not codec:
            print(f'SKIP 不明影片格式：{src}')
            skipped += 1
            continue
        destination = hevc if codec == 'hevc' else plain
        target = destination / name / (name + '.mp4')
        if target.exists():
            print(f'SKIP 已存在：{target}')
            skipped += 1
            continue
        print(f'{"COPY" if apply else "PREVIEW"} {src} -> {target} (codec={codec})')
        if apply:
            target.parent.mkdir(parents=True,exist_ok=True)
            staging = target.with_name(target.name + '.copying')
            try:
                shutil.copy2(src,staging)
                # Collision guard: never overwrite the destination.
                if target.exists():
                    print('SKIP 目標檔案在搬移期間出現，保留原檔')
                    skipped += 1
                    continue
                staging.rename(target)
            finally:
                staging.unlink(missing_ok=True)
        count += 1
    return count, skipped


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Copy existing MP4 into compressed/uncompressed safely')
    parser.add_argument('--apply',action='store_true',help='Actually copy; default is preview-only')
    args = parser.parse_args()
    if not _program('ffprobe'):
        sys.exit('找不到 ffprobe，請安裝 FFmpeg 並重啟終端機，或設定 FFMPEG_BIN_DIR。')
    root = get_download_dir()
    count,skipped = organize(root,args.apply)
    print(f'{"已複製" if args.apply else "預計複製"} {count} 部，略過 {skipped} 部；舊檔案與 state.json 完全不變。')
