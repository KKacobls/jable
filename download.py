"""Resumable HLS segment download, valid MP4 remux and single-CPU HEVC pass."""
import os
import json
from datetime import datetime, timezone
import ipaddress
import time
import subprocess
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlparse

import m3u8
import requests
from Crypto.Cipher import AES

from h265 import _codec, _program, _probe, encode_h265
from settings import atomic_json_write, get_download_dir, get_settings, get_storage_dirs, video_locations

HEADERS = {'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36'}
ENCODER_LOCK = threading.Semaphore(1)
PLAYLIST_LOCK = threading.RLock()


def valid_url(value):
    """Restrict API-supplied URLs to ordinary http(s) with a host."""
    if not isinstance(value, str) or len(value) > 4096:
        return False
    u = urlparse(value)
    if u.scheme not in ('http','https') or not u.hostname or u.username or u.password:
        return False
    hostname = u.hostname.lower().rstrip('.')
    if hostname in ('localhost','localhost.localdomain') or hostname.endswith(('.local','.internal','.localhost')):
        return False
    try:
        return ipaddress.ip_address(hostname).is_global
    except ValueError:
        return True


def _get(url, timeout=25):
    if not valid_url(url):
        raise ValueError('影片連結必須是有效 HTTP(S) 網址')
    for attempt in range(3):
        try:
            res = requests.get(url, headers=HEADERS, timeout=timeout)
            if not valid_url(res.url):
                res.close()
                raise ValueError('重導向網址不允許存取本機或內網 IP')
            res.raise_for_status()
            return res
        except (requests.Timeout, requests.ConnectionError) as exc:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
    raise RuntimeError('下載失敗')


def _playlist(url):
    for _ in range(4):
        play = m3u8.loads(_get(url).text)
        if play.segments:
            return play,url
        variants = getattr(play,'playlists',[]) or []
        if not variants:
            break
        best = max(variants,key=lambda p: getattr(getattr(p,'stream_info',None),'bandwidth',0) or 0)
        url = urljoin(url,best.uri)
    raise ValueError('M3U8 找不到可下載的影片片段')



def _cache_dir(name, download_root=None):
    """Private task folder: not exposed by the frontend's /assets route."""
    root = Path(download_root).resolve() if download_root is not None else get_download_dir()
    return root / 'uncompressed' / name


def _cached_playlist(name, download_root=None):
    """Read an original media playlist with its *remote base URL*.

    Playlist URIs may be relative. The saved remote URL is essential; resolving
    relative segments against a local file path would silently corrupt downloads.
    """
    folder = _cache_dir(name, download_root)
    meta_path, content_path = folder / 'playlist_source.json', folder / 'playlist.m3u8'
    if not meta_path.is_file() or not content_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
        url = meta.get('playlist_url')
        if not valid_url(url):
            return None
        playlist = m3u8.loads(content_path.read_text(encoding='utf-8'))
        if not playlist.segments or len(playlist.segments) != meta.get('expected_segments'):
            return None
        return playlist, url, meta
    except (ValueError, OSError, UnicodeError, TypeError, KeyError, json.JSONDecodeError):
        return None


def _has_segment_files(name, download_root=None):
    root = Path(download_root).resolve() if download_root is not None else get_download_dir()
    return any(folder.is_dir() and any(folder.glob('*.ts'))
               for folder in (_cache_dir(name, root), root / name))


def _atomic_text(path, content):
    """Write a complete manifest or nothing, including after an interruption."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.writing')
    try:
        with temp.open('w', encoding='utf-8', newline='\n') as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def save_playlist_snapshot(name, playlist, playlist_url, input_url, download_root=None):
    """Persist M3U8 *before* downloading TS files; keep old TS files intact.

    Signed segment/key URLs may still expire. This snapshot establishes the
    expected segment count and allows completely cached TS to remux offline.
    """
    root = Path(download_root).resolve() if download_root is not None else get_download_dir()
    if not valid_url(playlist_url) or not valid_url(input_url) or not playlist.segments:
        raise ValueError('M3U8 快取資料無效')
    if any(getattr(seg, 'init_section', None) for seg in playlist.segments):
        raise ValueError('目前不支援 fMP4 / EXT-X-MAP 影片分段')
    count = len(playlist.segments)
    sequence = int(getattr(playlist, 'media_sequence', 0) or 0)
    with PLAYLIST_LOCK:
        old = _cached_playlist(name, root)
        if old and _has_segment_files(name, root):
            old_meta = old[2]
            if (old_meta.get('expected_segments') != count or
                    int(old_meta.get('media_sequence', 0)) != sequence):
                raise RuntimeError('新舊 M3U8 的分段數或序號不同；已保留原 TS，拒絕混合不同影片分段。')
        folder = _cache_dir(name, root)
        # Save the actual media playlist, not only the expiring master URL.
        _atomic_text(folder / 'playlist.m3u8', playlist.dumps())
        meta = {'input_hls_url': input_url, 'playlist_url': playlist_url,
                'expected_segments': count, 'media_sequence': sequence,
                'cached_at': datetime.now(timezone.utc).isoformat()}
        atomic_json_write(folder / 'playlist_source.json', meta)
    return meta


def cache_playlist(name, hls_url, download_root=None):
    """Fetch and preserve the resolved media playlist immediately on task submit."""
    if not valid_url(hls_url):
        raise ValueError('無效的 M3U8 網址')
    playlist, playlist_url = _playlist(hls_url)
    return save_playlist_snapshot(name, playlist, playlist_url, hls_url, download_root)


def segment_cache_status(name, download_root=None, expected=None):
    """Inspect saved .ts files without fetching expired remote URLs."""
    root = Path(download_root).resolve() if download_root is not None else get_download_dir()
    cached = _cached_playlist(name, root)
    if cached is not None:
        expected = cached[2]['expected_segments']
    raw_folder, legacy_folder = _cache_dir(name, root), root / name
    folder = (legacy_folder if legacy_folder.is_dir() and any(legacy_folder.glob('*.ts'))
              and not any(raw_folder.glob('*.ts')) else raw_folder)
    indices = {int(p.stem) for p in folder.glob('*.ts') if p.stem.isdecimal()
               and p.is_file() and p.stat().st_size > 0}
    missing = ([i for i in range(expected) if i not in indices][:30]
               if isinstance(expected, int) and expected > 0 else [])
    return {'playlist_cached': cached is not None, 'expected_segments': expected,
            'cached_segments': len(indices), 'complete':
            isinstance(expected, int) and expected > 0 and all(i in indices for i in range(expected)),
            'missing_preview': missing, 'folder': str(folder)}


def _download_segment(url, path, key=None, iv=None):
    tmp = Path(str(path) + '.part')
    tmp.unlink(missing_ok=True)
    try:
        blob = _get(url, timeout=40).content
        if key is not None:
            if len(key) != 16 or iv is None or len(iv) != 16 or len(blob) % 16:
                raise ValueError('AES-128 金鑰、IV 或分段長度無效')
            blob = AES.new(key,AES.MODE_CBC,iv).decrypt(blob)
            padding = blob[-1] if blob else 0
            if 1 <= padding <= 16 and blob.endswith(bytes([padding]) * padding):
                blob = blob[:-padding]
        if not blob:
            raise ValueError('下載到空影片分段')
        tmp.write_bytes(blob)
        os.replace(tmp,path)
        return True
    finally:
        tmp.unlink(missing_ok=True)


def estimate_segments_duration(ts_files):
    """Estimate duration for old TS folders with no saved M3U8.

    This is only an approximation for UI progress; never use it to claim
    segments are complete or to justify deletion.
    """
    import statistics
    if not ts_files:
        return None
    indexes = sorted({0, len(ts_files)//4, len(ts_files)//2, len(ts_files)*3//4, len(ts_files)-1})
    samples = []
    for i in indexes:
        try:
            value = _probe(ts_files[i])
            if value > 0:
                samples.append(value)
        except (OSError, ValueError, subprocess.SubprocessError, RuntimeError):
            pass
    return statistics.median(samples) * len(ts_files) if samples else None


def merge_files_to_mp4(ts_files, output_path, progress_callback=None, total_seconds=None):
    """Remux verified sequential TS with FFmpeg, optionally reporting progress.

    The output is published atomically only after FFmpeg and ffprobe succeed.
    """
    ffmpeg = _program('ffmpeg')
    if not ffmpeg or not _program('ffprobe'):
        raise RuntimeError('需要 FFmpeg 與 ffprobe 才能封裝 MP4，請先安裝。')
    output = Path(output_path)
    output.parent.mkdir(parents=True,exist_ok=True)
    temp_output = Path(str(output)+'.working.mp4')
    manifest_path = None
    if total_seconds is None and progress_callback:
        total_seconds = estimate_segments_duration(ts_files)
    try:
        for f in ts_files:
            if not Path(f).is_file() or Path(f).stat().st_size == 0:
                raise RuntimeError(f'缺少分段：{f}')
        with tempfile.NamedTemporaryFile('w',encoding='utf-8',suffix='.txt',prefix='concat-',
                                         dir=output.parent,delete=False) as manifest:
            manifest_path = Path(manifest.name)
            for f in ts_files:
                escaped = Path(f).resolve().as_posix().replace("'", "'\\''")
                manifest.write("file '" + escaped + "'\n")
        temp_output.unlink(missing_ok=True)
        command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
                   '-f', 'concat', '-safe', '0', '-i', str(manifest_path),
                   '-map', '0:v:0', '-map', '0:a?', '-c', 'copy',
                   '-movflags', '+faststart', '-progress', 'pipe:1', '-nostats',
                   str(temp_output)]
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding='utf-8', errors='replace', bufsize=1)
        previous = -1
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if not progress_callback:
                    continue
                if line.startswith(('out_time_us=', 'out_time_ms=')):
                    try:
                        seconds = max(0.0, int(line.split('=',1)[1]) / 1_000_000)
                        # An unknown duration cannot be represented as a true percentage.
                        percent = max(0,min(99,int(seconds / total_seconds * 100))) if total_seconds else None
                        if percent != previous:
                            progress_callback(percent, seconds)
                            previous = percent
                    except ValueError:
                        pass
            stderr = proc.stderr.read() if proc.stderr is not None else ''
            code = proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        if code != 0 or not temp_output.is_file() or temp_output.stat().st_size == 0:
            raise RuntimeError('FFmpeg 封裝失敗：' + (stderr[-800:] if stderr else '輸出無效'))
        if not _codec(temp_output) or _probe(temp_output) <= 0:
            raise RuntimeError('FFmpeg 封裝後未通過 ffprobe 驗證，保留原 TS。')
        # Only exact playlist-derived durations, not sampling estimates, are
        # sufficient to reject a suspiciously short MP4.
        os.replace(temp_output,output)
        if progress_callback:
            progress_callback(100, _probe(output))
    finally:
        temp_output.unlink(missing_ok=True)
        if manifest_path:
            manifest_path.unlink(missing_ok=True)


def delete_temp_files(folder_path, ts_files):
    for f in ts_files:
        try:
            Path(f).unlink(missing_ok=True)
        except OSError as e:
            print(f'清除暫存片段失敗 {f}: {e}')


def _safe_state(progress, lock, saver, name, **changes):
    with lock:
        if name not in progress:
            raise RuntimeError('任務已移除')
        progress[name].update(changes)
        saver()


def _valid_cached_segments(folder, expected):
    """Only resume an offline remux when we know *all* segment positions."""
    if not isinstance(expected, int) or expected <= 0:
        return None
    result = [folder / f'{i:05d}.ts' for i in range(expected)]
    return result if all(p.is_file() and p.stat().st_size > 0 for p in result) else None


def _make_raw_video(hls_url, name, progress, saver, lock, options, download_root, raw_path):
    """Download HLS segments or reuse verified cached segments; remux to raw MP4."""
    raw_folder = raw_path.parent
    raw_folder.mkdir(parents=True, exist_ok=True)
    legacy_folder = download_root / name
    # Use saved .ts segments from older versions instead of downloading them twice.
    folder = (legacy_folder if legacy_folder.is_dir() and any(legacy_folder.glob('*.ts'))
              and not any(raw_folder.glob('*.ts')) else raw_folder)
    with lock:
        expected = progress[name].get('expected_segments')
    cached = _cached_playlist(name, download_root)
    if cached and cached[2].get('input_hls_url') == hls_url:
        expected = cached[2]['expected_segments']
        with lock:
            if progress[name].get('expected_segments') != expected:
                progress[name]['expected_segments'] = expected
                saver()
    ts_files = _valid_cached_segments(folder, expected)
    if ts_files is None:
        _safe_state(progress,lock,saver,name,status='正在準備 M3U8 分段...',current=0,total=1, download_state='preparing')
        if cached and cached[2].get('input_hls_url') == hls_url:
            playlist,playlist_url = cached[:2]
        else:
            try:
                playlist,playlist_url = _playlist(hls_url)
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 410:
                    raise RuntimeError('M3U8 已過期（410）。本機 TS 已保留；若分段不完整，請從影片頁取得新的 hls_url 後呼叫更新來源 API。') from exc
                raise
            save_playlist_snapshot(name,playlist,playlist_url,hls_url,download_root)
        if any(getattr(seg,'init_section',None) for seg in playlist.segments):
            raise ValueError('目前不支援 fMP4 / EXT-X-MAP 影片分段')
        keys = {}
        specs = []
        seq = int(getattr(playlist,'media_sequence',0) or 0)
        for i, segment in enumerate(playlist.segments):
            path = folder / f'{i:05d}.ts'
            if path.exists() and path.stat().st_size > 0:
                specs.append((None,path,None,None))
                continue
            keyinfo = getattr(segment,'key',None)
            if keyinfo is None and getattr(playlist,'keys',None):
                keyinfo = playlist.keys[0]
            key = iv = None
            if keyinfo and getattr(keyinfo,'method',None) not in (None,'NONE'):
                if keyinfo.method != 'AES-128':
                    raise ValueError(f'不支援的 HLS 加密方式：{keyinfo.method}')
                key_url = urljoin(playlist_url,keyinfo.uri)
                if key_url not in keys:
                    keys[key_url] = _get(key_url).content
                key = keys[key_url]
                explicit_iv = getattr(keyinfo,'iv',None)
                iv = (bytes.fromhex(explicit_iv.removeprefix('0x')) if explicit_iv else (seq+i).to_bytes(16,'big'))
            specs.append((urljoin(playlist_url,segment.uri),path,key,iv))
        existing = sum(1 for url,*_ in specs if url is None)
        total = len(specs)
        if total == 0:
            raise RuntimeError('M3U8 沒有影片分段')
        _safe_state(progress,lock,saver,name,status='正在下載影片...',current=existing,total=total,
                    expected_segments=total, download_current=existing, download_total=total, download_state='downloading')
        pending = [(u,p,k,iv) for u,p,k,iv in specs if u is not None]
        failures = []
        completed = existing
        # Probe the first missing segment before launching many concurrent
        # requests against an already-expired signed HLS URL (HTTP 410).
        if pending:
            first_url, first_path, first_key, first_iv = pending.pop(0)
            try:
                _download_segment(first_url, first_path, first_key, first_iv)
                completed += 1
                with lock:
                    progress[name]['current'] = completed
                    progress[name]['download_current'] = completed
                    saver()
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code in (403, 410):
                    raise RuntimeError('影片分段網址已過期（HTTP %s），已下載的 TS 保留；請取得新 hls_url，呼叫 /api/tasks/<name>/source 續傳。' % exc.response.status_code) from exc
                raise
        if pending:
            with ThreadPoolExecutor(max_workers=int(options['segment_workers'])) as pool:
                futures = {pool.submit(_download_segment,u,p,k,iv):(u,p) for u,p,k,iv in pending}
                for f in as_completed(futures):
                    try:
                        f.result()
                        completed += 1
                        with lock:
                            progress[name]['current'] = completed
                            progress[name]['download_current'] = completed
                            if completed % 20 == 0 or completed == total:
                                saver()
                    except Exception as e:
                        failures.append(str(e))
        if failures:
            hint = ('；分段網址可能已過期，請使用 /api/tasks/<name>/source 更新 hls_url。'
                    if any('410' in x or '403' in x for x in failures) else '')
            raise RuntimeError(f'{len(failures)} 個分段失敗；已保留成功的 TS 可續傳。第一個錯誤：{failures[0]}{hint}')
        ts_files = [item[1] for item in specs]
    else:
        _safe_state(progress,lock,saver,name,status='使用已下載分段重新封裝...',
                    current=len(ts_files),total=len(ts_files), download_current=len(ts_files), download_total=len(ts_files), download_state='merging')
    _safe_state(progress,lock,saver,name,status='正在封裝 MP4...',current=0,total=1,
                download_current=len(ts_files),download_total=len(ts_files), download_state='merging', merge_percent=0)
    manifest_duration = None
    if cached:
        try:
            manifest_duration = sum(float(seg.duration or 0) for seg in cached[0].segments) or None
        except (TypeError, ValueError):
            pass
    def merge_progress(percent, seconds):
        with lock:
            if name in progress:
                progress[name]['merge_percent'] = percent
                progress[name]['merge_seconds'] = round(seconds, 1)
                if percent is None or percent == 100 or percent % 5 == 0:
                    saver()
    merge_files_to_mp4(ts_files,raw_path, progress_callback=merge_progress, total_seconds=manifest_duration)
    # After successful remux + ffprobe validation, remove only the exact TS
    # files used for this output. Never touch playlists or state.json.
    delete_temp_files(folder,ts_files)
    _safe_state(progress,lock,saver,name,status='MP4 封裝完成',
                download_current=len(ts_files),download_total=len(ts_files),
                download_state='completed',merge_percent=100)


def download_video(hls_url, name, image_url, download_progress, save_state_func, lock,
                   options=None, download_dir=None):
    """Keep source MP4 in uncompressed/ and smaller HEVC MP4 in compressed/.

    Historical downloads/<name>/<name>.mp4 and state.json are never deleted.
    """
    options = options or get_settings()
    root = Path(download_dir).resolve() if download_dir is not None else get_download_dir()
    original_root, compressed_root = get_storage_dirs(root)
    paths = video_locations(name,root)
    original = paths['uncompressed']
    compressed = paths['compressed']
    legacy = paths['legacy']
    try:
        if not _program('ffmpeg') or not _program('ffprobe'):
            raise RuntimeError('需要 FFmpeg 和 ffprobe。安裝後請重新開啟終端機並重啟 Python，再按「重試錯誤任務」。')
        original.parent.mkdir(parents=True, exist_ok=True)
        if not original.is_file() or not _codec(original):
            # An older version may have already produced a complete MP4.
            if legacy.is_file() and _codec(legacy):
                temp = original.with_name(original.name + '.copying')
                try:
                    shutil.copy2(legacy,temp)
                    os.replace(temp,original)
                finally:
                    temp.unlink(missing_ok=True)
            else:
                _make_raw_video(hls_url,name,download_progress,save_state_func,lock,
                                options,root,original)
        if not original.is_file() or not _codec(original):
            raise RuntimeError('未壓縮 MP4 不存在或無法播放；已保留下載分段。')
        with lock:
            item = download_progress.get(name)
            if item is not None:
                total = item.get('download_total') or item.get('expected_segments') or 1
                item.update(download_current=total, download_total=total, download_state='completed')
                save_state_func()
        if options['compression_enabled']:
            if compressed.is_file() and _codec(compressed) == 'hevc':
                _safe_state(download_progress,lock,save_state_func,name,
                            compression_percent=100, compression_state='completed')
                status = '下載完成（未壓縮原檔與 H.265 版均已保留）'
            elif _codec(original) == 'hevc':
                # H.265 from source is already HEVC; keep a separate copy.
                compressed.parent.mkdir(parents=True,exist_ok=True)
                temp = compressed.with_name(compressed.name+'.copying')
                try:
                    shutil.copy2(original,temp)
                    os.replace(temp,compressed)
                finally:
                    temp.unlink(missing_ok=True)
                _safe_state(download_progress,lock,save_state_func,name,
                            compression_percent=100, compression_state='completed')
                status = '下載完成（來源已是 H.265，兩份均保留）'
            else:
                _safe_state(download_progress,lock,save_state_func,name,
                            status='等待壓縮資源...', compression_state='queued', compression_percent=0)
                with ENCODER_LOCK:
                    _safe_state(download_progress,lock,save_state_func,name,
                                status='正在 CPU H.265 壓縮...',compression_state='running',
                                compression_percent=0)
                    last_saved = [-1]
                    def update(percent):
                        with lock:
                            if name in download_progress:
                                download_progress[name]['compression_percent'] = percent
                                if percent // 5 != last_saved[0]:
                                    last_saved[0] = percent // 5
                                    save_state_func()
                    smaller = encode_h265(original,update,level=options['compression_level'],
                                          preset=options['encoder_preset'],output_path=compressed,
                                          task_name=name)
                _safe_state(download_progress,lock,save_state_func,name,
                            compression_state='completed' if smaller else 'skipped',
                            compression_percent=100)
                status = (f'下載完成（原檔與 H.265 第 {options["compression_level"]} 級均保留）'
                          if smaller else '下載完成（壓縮後較大，僅保留原檔）')
        else:
            _safe_state(download_progress,lock,save_state_func,name,
                        compression_state='disabled', compression_percent=0)
            status = '下載完成（未壓縮原檔）'
        _safe_state(download_progress,lock,save_state_func,name,
                    status=status,current=100,total=100)
        print(f'[{name}] {status}')
    except Exception as e:
        print(f'[{name}] 任務失敗：{e}')
        with lock:
            if name in download_progress:
                download_progress[name]['status'] = '錯誤: ' + str(e)[:300]
                if download_progress[name].get('compression_state') in ('running', 'paused', 'queued'):
                    download_progress[name]['compression_state'] = 'error'
                save_state_func()
