"""Server defaults tuned for an Intel Core i7-11700 / 24 GiB RAM host."""
import json
import os
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SETTINGS_PATH = ROOT / 'settings.json'
DEFAULTS = {
    'compression_enabled': True,
    'compression_level': 5,
    'encoder_preset': 'fast',
    'segment_workers': 6,
    'max_parallel_downloads': 2,
}
_LOCK = threading.RLock()


def validate_settings(value):
    if not isinstance(value, dict):
        raise ValueError('設定必須是 JSON 物件')
    settings = dict(DEFAULTS)
    for k in DEFAULTS:
        if k not in value:
            continue
        v = value[k]
        if k == 'compression_enabled':
            if type(v) is not bool:
                raise ValueError('compression_enabled 必須是布林值')
        elif k == 'encoder_preset':
            if v not in ('ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium'):
                raise ValueError('編碼速度只支援 ultrafast 到 medium')
        else:
            if type(v) is not int:
                raise ValueError(f'{k} 必須是整數')
            low, high = {'compression_level':(1,10),'segment_workers':(1,10),'max_parallel_downloads':(1,3)}[k]
            if not low <= v <= high:
                raise ValueError(f'{k} 應介於 {low} 到 {high} 之間')
        settings[k] = v
    return settings


def atomic_json_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, prefix='.tmp-', suffix='.json', delete=False) as f:
            name = f.name
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def get_settings():
    with _LOCK:
        if not SETTINGS_PATH.exists():
            return dict(DEFAULTS)
        try:
            return validate_settings(json.loads(SETTINGS_PATH.read_text(encoding='utf-8')))
        except (OSError, json.JSONDecodeError, ValueError) as e:
            raise RuntimeError(f'無法讀取 settings.json：{e}') from e


def save_settings(value):
    with _LOCK:
        result = validate_settings(value)
        atomic_json_write(SETTINGS_PATH, result)
        return result


def get_download_dir():
    """Set JABLE_DOWNLOAD_DIR to use the roomier data drive on Windows."""
    target = os.environ.get('JABLE_DOWNLOAD_DIR', '').strip()
    path = Path(target).expanduser() if target else ROOT / 'downloads'
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_storage_dirs(download_dir=None):
    """Storage hierarchy: downloads/uncompressed and downloads/compressed.

    Paths are kept under the same download root. Existing legacy files
    at downloads/<name>/<name>.mp4 remain untouched and discoverable.
    """
    root = Path(download_dir).resolve() if download_dir is not None else get_download_dir()
    original = root / 'uncompressed'
    compressed = root / 'compressed'
    original.mkdir(parents=True, exist_ok=True)
    compressed.mkdir(parents=True, exist_ok=True)
    return original, compressed


def video_locations(name, download_dir=None):
    root = Path(download_dir).resolve() if download_dir is not None else get_download_dir()
    return {
        'uncompressed': root / 'uncompressed' / name / (name + '.mp4'),
        'compressed': root / 'compressed' / name / (name + '.mp4'),
        'legacy': root / name / (name + '.mp4'),
    }


def existing_video_locations(name, download_dir=None):
    paths = video_locations(name, download_dir)
    return {label: path for label,path in paths.items() if path.is_file() and path.stat().st_size > 0}
