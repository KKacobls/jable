"""JSON task API and static asset server. The UI lives in frontend/."""
import json
import os
import queue
import re
import threading
import shutil
from datetime import datetime, timezone
from uuid import uuid4
from pathlib import Path

import requests
from flask import Flask, abort, jsonify, request, send_from_directory

from download import (ENCODER_LOCK, cache_playlist, delete_temp_files, download_video,
                      merge_files_to_mp4, segment_cache_status, valid_url)
from h265 import _codec, _program, encode_h265, encoder_active, encoder_control
from settings import ROOT, atomic_json_write, get_download_dir, get_settings, save_settings, video_locations, existing_video_locations

STATE_FILE = ROOT / 'state.json'
API_TASK_FIELDS = ('status', 'current', 'total', 'completed', 'file_exists', 'cover_url',
                   'stored_versions', 'download_current', 'download_total', 'download_state',
                   'compression_percent', 'compression_state', 'merge_percent', 'merge_seconds',
                   'can_start_compression', 'local_raw_exists', 'local_ts_exists')
IMAGE_DIR = ROOT / 'image'
IMAGE_DIR.mkdir(parents=True, exist_ok=True)
FRONTEND_DIR = ROOT / 'frontend'
NAME_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}$')

app = Flask(__name__, static_folder=None)
app.config.update(MAX_CONTENT_LENGTH=16000, JSON_AS_ASCII=False)

state_lock = threading.RLock()
try:
    download_progress = json.loads(STATE_FILE.read_text(encoding='utf-8')) if STATE_FILE.exists() else {}
    if not isinstance(download_progress,dict):
        raise ValueError('state.json 不是有效任務列表')
except (OSError,ValueError,json.JSONDecodeError) as ex:
    raise RuntimeError(f'無法讀取 state.json，請先備份修復：{ex}') from ex

job_queue = queue.Queue()
queued = set()
running = set()
manual_compressions = set()  # Reserved while an offline merge/encode thread is queued or running.
workers = []
workers_lock = threading.Lock()
# Priority playlist queue: cache signed M3U8 before downloading videos.
# Three daemon workers, bounded backlog: burst submissions are queued instead
# of silently skipping the prefetch when three network requests are busy.
_prefetch_queue = queue.Queue(maxsize=512)
_prefetch_start_lock = threading.Lock()
_prefetch_started = False


def _cache_task_playlist(name, hls_url):
    try:
        meta = cache_playlist(name, hls_url, get_download_dir())
        with state_lock:
            current = download_progress.get(name)
            if isinstance(current, dict) and current.get('hls_url') == hls_url:
                current['expected_segments'] = meta['expected_segments']
                current['playlist_cached_at'] = meta['cached_at']
                current.pop('playlist_cache_error', None)
                save_state()
        return meta
    except Exception as exc:
        with state_lock:
            current = download_progress.get(name)
            if isinstance(current, dict) and current.get('hls_url') == hls_url:
                current['playlist_cache_error'] = str(exc)[:250]
                save_state()
        raise


def _prefetch_playlist_worker():
    while True:
        name, hls_url = _prefetch_queue.get()
        try:
            with state_lock:
                current = download_progress.get(name)
                if not isinstance(current, dict) or current.get('hls_url') != hls_url:
                    continue  # Outdated source: avoid fetching stale signed URLs.
            _cache_task_playlist(name, hls_url)
        except Exception as exc:
            print(f'[{name}] M3U8 提前快取失敗：{exc}')
        finally:
            _prefetch_queue.task_done()


def _prefetch_playlist_async(name, hls_url):
    global _prefetch_started
    with _prefetch_start_lock:
        if not _prefetch_started:
            for index in range(3):
                threading.Thread(target=_prefetch_playlist_worker,
                                 name=f'm3u8-cache-{index+1}', daemon=True).start()
            _prefetch_started = True
    try:
        _prefetch_queue.put_nowait((name, hls_url))
        return True
    except queue.Full:
        return False  # Video worker still has its own manifest-fetch fallback.


def save_state():
    """Call while holding state_lock."""
    atomic_json_write(STATE_FILE,download_progress)


def finished(item):
    status = str(item.get('status','')).strip().lower()
    return status == '已完成' or status == 'completed' or status.startswith('下載完成')


def video_file(name):
    """Return an existing video, including original and legacy locations.

    Never mutate state.json or move files just to check existence.
    """
    candidates = video_locations(name)
    return next((p for p in (candidates['compressed'], candidates['uncompressed'], candidates['legacy'])
                 if p.is_file() and p.stat().st_size > 0), candidates['uncompressed'])


def local_cover_name(name):
    # Images from older installations are in image/<name>.jpg.
    for ext in ('.jpg', '.jpeg', '.png', '.webp'):
        path = IMAGE_DIR / (name + ext)
        if path.is_file():
            return path.name
    return None


def task_view(name, item):
    result = dict(item)
    result['completed'] = finished(item)
    result['file_exists'] = video_file(name).is_file()
    result['stored_versions'] = list(existing_video_locations(name))
    original = video_locations(name)['uncompressed']
    legacy = video_locations(name)['legacy']
    compressed = video_locations(name)['compressed']
    raw_present = any(p.is_file() and p.stat().st_size > 0 for p in (original, legacy))
    # A 100% progress bar can represent old cached counters, NOT a playable MP4.
    # Checking for at least one TS is cheap and does not touch the expired URL.
    root = get_download_dir()
    local_ts = any(any(folder.glob('*.ts')) for folder in
                   (root / name, root / 'uncompressed' / name))
    compressed_present = compressed.is_file() and compressed.stat().st_size > 0
    result['local_raw_exists'] = raw_present
    result['local_ts_exists'] = local_ts
    result['can_start_compression'] = (raw_present or local_ts) and not compressed_present and (name not in queued and
        name not in running and name not in manual_compressions and not encoder_active(name))
    # Older state.json files used current/total for both download and encode.
    # Derive separate progress from files without altering historical JSON.
    total = item.get('download_total') or item.get('expected_segments') or 1
    try:
        total = max(1, int(total))
    except (TypeError, ValueError):
        total = 1
    result['download_total'] = total
    result['download_current'] = (total if raw_present or finished(item) else
                                  min(total, max(0, int(item.get('download_current', 0) or 0))))
    result['download_state'] = item.get('download_state') or ('completed' if raw_present or finished(item) else 'pending')
    comp_state = item.get('compression_state') or ('completed' if compressed_present else 'pending')
    if comp_state == 'completed' and not compressed_present:
        comp_state = 'interrupted'  # Don't pretend a deleted MP4 is still compressed.
    if comp_state in ('queued', 'running', 'paused') and not (name in queued or
            name in running or name in manual_compressions or encoder_active(name)):
        comp_state = 'interrupted'  # State persisted across a server restart.
    result['compression_state'] = comp_state
    result['compression_percent'] = (0 if comp_state == 'interrupted' else
        item.get('compression_percent') if item.get('compression_percent') is not None else
        100 if compressed_present else 0)
    result['merge_percent'] = item.get('merge_percent')
    result['merge_seconds'] = item.get('merge_seconds')
    local_cover = local_cover_name(name)
    if local_cover:
        result['cover_url'] = '/image/' + local_cover
    elif valid_url(item.get('image_url', '')):
        result['cover_url'] = '/cover/' + name
    else:
        result['cover_url'] = None
    return result


def _worker():
    while True:
        name = job_queue.get()
        try:
            with state_lock:
                queued.discard(name)
                if name not in download_progress:
                    continue
                running.add(name)
                item = dict(download_progress[name])
            download_video(item['hls_url'],name,item.get('image_url',''),
                           download_progress,save_state,state_lock,
                           options=get_settings(),download_dir=get_download_dir())
        except Exception as exc:
            with state_lock:
                if name in download_progress:
                    download_progress[name]['status'] = '錯誤: ' + str(exc)[:300]
                    save_state()
        finally:
            with state_lock:
                running.discard(name)
            job_queue.task_done()


def start_workers():
    with workers_lock:
        target = get_settings()['max_parallel_downloads']
        while len(workers) < target:
            t = threading.Thread(target=_worker,name=f'jable-worker-{len(workers)+1}',daemon=True)
            t.start()
            workers.append(t)


def enqueue(name):
    start_workers()
    with state_lock:
        if name not in download_progress or name in running or name in queued or name in manual_compressions:
            return False
        queued.add(name)
        download_progress[name]['status'] = '排隊中...'
        download_progress[name]['current'] = 0
        download_progress[name]['total'] = 1
        if download_progress[name].get('compression_state') in ('paused','running'):
            download_progress[name]['compression_state'] = 'interrupted'
        save_state()
    job_queue.put(name)
    return True


def resume_interrupted():
    with state_lock:
        to_resume = [n for n,item in download_progress.items()
                     if NAME_RE.fullmatch(n) and isinstance(item,dict)
                     and item.get('hls_url') and not finished(item)
                     and not str(item.get('status','')).startswith('錯誤')]
    for name in to_resume:
        enqueue(name)
    return len(to_resume)


@app.after_request
def secure_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    # Cross-origin browser clients may call the public JSON API without cookies.
    if request.path.startswith('/api/'):
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Accept'
        response.headers['Access-Control-Max-Age'] = '86400'
    if response.mimetype in ('text/html','application/json') or request.path.startswith('/assets/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


def parse_video_text(raw):
    try:
        obj = json.loads(raw)
        if not isinstance(obj,dict):
            raise ValueError('資料格式錯誤')
        hls_url = obj.get('hls_url') or obj.get('hlsUrl')
        name = obj.get('name')
        image_url = obj.get('image_url') or obj.get('image') or ''
    except json.JSONDecodeError:
        lines = [x.strip() for x in raw.splitlines() if x.strip()]
        if len(lines) < 4:
            raise ValueError('請使用 JSON 或原本的六行資料格式')
        mapping = {lines[i].lower():lines[i+1] for i in range(0,len(lines)-1,2)}
        hls_url = mapping.get('hls') or mapping.get('hlsurl') or mapping.get('hls_url')
        name = mapping.get('name')
        image_url = mapping.get('image') or mapping.get('image_url') or ''
    if not isinstance(name,str) or not NAME_RE.fullmatch(name):
        raise ValueError('名稱只能使用英文、數字、連字號和底線，最多 96 字元')
    if not valid_url(hls_url):
        raise ValueError('HLS 網址需是 HTTP(S) 連結')
    if image_url and not (valid_url(image_url) or image_url.startswith('/image/')):
        raise ValueError('圖片網址需是 HTTP(S) 連結')
    return hls_url,name,image_url


def add_video_task(raw):
    """Create only new tasks; preserve completed history and active jobs.

    Returns (result, message, name). Invalid input raises ValueError.
    """
    hls_url, name, image_url = parse_video_text(raw.strip())
    with state_lock:
        current = download_progress.get(name)
        if current and (name in queued or name in running or name in manual_compressions):
            return 'existing', '這個任務已在下載或排隊中，沒有重複新增。', name
        if current and finished(current):
            return 'existing', '這個名稱已經下載過，保留歷史紀錄，不會重複下載。', name
        # Preserve any segment count and downloaded TS when an errored task is
        # resubmitted with a fresh HLS URL. Never overwrite a completed record.
        if current and isinstance(current, dict):
            current.update(name=name, hls_url=hls_url, image_url=image_url,
                           status='排隊中...', current=0, total=1)
        else:
            download_progress[name] = {
                'name': name, 'hls_url': hls_url, 'image_url': image_url,
                'status': '排隊中...', 'current': 0, 'total': 1,
            }
        save_state()
        _prefetch_playlist_async(name, hls_url)
        enqueue(name)
    return 'added', '已排入下載佇列；正在提前保存 M3U8，可繼續貼上下一筆。', name


def _api_request_text():
    """Allow JSON clients, the old paste format and standard HTML forms."""
    if request.is_json:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise ValueError('JSON 請求內容必須是物件')
        if 'video_text' in payload:
            if not isinstance(payload['video_text'], str):
                raise ValueError('video_text 必須是文字')
            return payload['video_text']
        return json.dumps(payload, ensure_ascii=False)
    if request.mimetype == 'text/plain':
        return request.get_data(as_text=True)
    if request.mimetype in ('application/x-www-form-urlencoded', 'multipart/form-data'):
        if 'video_text' in request.form:
            return request.form['video_text']
        if 'name' in request.form or 'hls_url' in request.form:
            return json.dumps(request.form.to_dict(), ensure_ascii=False)
    raise ValueError('請以 JSON、text/plain 或表單 video_text 傳送下載資料')


@app.route('/api/download', methods=['POST', 'OPTIONS'])
@app.route('/api/tasks', methods=['GET', 'POST', 'OPTIONS'])
def api_tasks():
    """Stable integration endpoint. No login, no IP/Host/CORS restrictions."""
    if request.method == 'OPTIONS':
        return '', 204
    if request.method == 'GET':
        with state_lock:
            items = [dict(name=name, **{k:v for k,v in task_view(name,item).items()
                         if k in API_TASK_FIELDS})
                     for name,item in download_progress.items() if isinstance(item,dict)]
        return jsonify(ok=True, total=len(items), tasks=items)
    try:
        raw = _api_request_text()
        result, message, name = add_video_task(raw)
    except ValueError as exc:
        return jsonify(ok=False, clear_input=False, error='invalid_request', message=str(exc)), 422
    return jsonify(ok=True, added=result == 'added', clear_input=True,
                   name=name, message=message,
                   status_url=f'/api/tasks/{name}'), (201 if result == 'added' else 200)


@app.route('/api/tasks/<name>', methods=['GET', 'DELETE', 'OPTIONS'])
def api_task_detail(name):
    if request.method == 'OPTIONS':
        return '', 204
    if not NAME_RE.fullmatch(name):
        return jsonify(ok=False,error='not_found',message='找不到任務'),404
    if request.method == 'DELETE':
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict) or body.get('confirm') != name:
            return jsonify(ok=False,message='請在 confirm 欄位完整提供要刪除的影片名稱'),422
        with state_lock:
            if name in running or name in queued or name in manual_compressions or encoder_active(name):
                return jsonify(ok=False,message='任務執行中，請先完成或停止後再刪除'),409
            item = download_progress.get(name)
            if not isinstance(item,dict):
                return jsonify(ok=False,message='找不到任務'),404
            root = get_download_dir()
            trash = root / '.trash' / (name+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8])
            moved = []
            try:
                trash.mkdir(parents=True,exist_ok=False)
                for source,relative in ((root / 'uncompressed' / name, Path('uncompressed') / name),
                                        (root / 'compressed' / name, Path('compressed') / name),
                                        (root / name, Path('legacy') / name)):
                    if source.exists():
                        target = trash / relative
                        target.parent.mkdir(parents=True,exist_ok=True)
                        source.rename(target)
                        moved.append((source,target))
                for suffix in ('.jpg','.jpeg','.png','.webp'):
                    source = IMAGE_DIR / (name + suffix)
                    if source.is_file():
                        target = trash / 'images' / source.name
                        target.parent.mkdir(parents=True,exist_ok=True)
                        source.rename(target)
                        moved.append((source,target))
                atomic_json_write(trash / 'task.json',item)
                del download_progress[name]
                save_state()
            except Exception as exc:
                download_progress[name] = item
                for source,target in reversed(moved):
                    try:
                        source.parent.mkdir(parents=True, exist_ok=True)
                        target.rename(source)
                    except OSError:
                        pass
                return jsonify(ok=False,message='移到垃圾桶失敗，已盡量還原：'+str(exc)[:180]),500
        return jsonify(ok=True,name=name,deleted=True,
                       message='任務已移到 downloads/.trash，可重新提交相同影片下載'),200
    with state_lock:
        item = download_progress.get(name)
        if not isinstance(item,dict):
            return jsonify(ok=False,error='not_found',message='找不到任務'),404
        view = task_view(name,item)
        return jsonify(ok=True, task={k:view.get(k) for k in ('name', *API_TASK_FIELDS)})



def _manual_state(name, **changes):
    """Update only existing task records; never recreate a deleted task."""
    with state_lock:
        if name not in download_progress:
            raise RuntimeError('任務已移除')
        download_progress[name].update(changes)
        save_state()


def _manual_compression_worker(name, expected_segments, delete_segments):
    """Use ONLY local MP4/TS. Never request an expired HLS playlist."""
    try:
        paths = video_locations(name)
        raw = next((p for p in (paths['uncompressed'], paths['legacy'])
                    if p.is_file() and p.stat().st_size > 0 and _codec(p)), None)
        if raw is None:
            status = segment_cache_status(name, get_download_dir(), expected_segments)
            if not status['complete']:
                raise RuntimeError('TS 分段不完整或有缺號；未封裝也未刪除任何 TS')
            count = status['expected_segments']
            folder = Path(status['folder'])
            ts_files = [folder / f'{i:05d}.ts' for i in range(count)]
            raw = paths['uncompressed']
            _manual_state(name, status='正在從本機 TS 封裝 MP4...',
                          download_state='merging', merge_percent=0,
                          download_current=count, download_total=count)
            last_report = [-1]
            def on_merge(percent, seconds):
                # Save percentages at most every five points; don't rewrite state.json on every frame.
                bucket = percent // 5 if percent is not None else None
                if bucket != last_report[0] or percent == 100:
                    last_report[0] = bucket
                    _manual_state(name, merge_percent=percent,
                                  merge_seconds=round(seconds, 1))
            merge_files_to_mp4(ts_files, raw, progress_callback=on_merge)
            # For unknown M3U8 lengths this is only done with user's explicit
            # confirmation; a clean ffprobe does not prove the movie has an end.
            if delete_segments:
                delete_temp_files(folder, ts_files)
            _manual_state(name, download_state='completed', merge_percent=100,
                          download_current=count, download_total=count)

        if not raw.is_file() or not _codec(raw):
            raise RuntimeError('本機找不到可播放的 MP4，請先完成 TS 封裝')
        compressed = paths['compressed']
        if compressed.is_file() and _codec(compressed) == 'hevc':
            _manual_state(name, status='下載完成（原本已有 H.265 壓縮影片）',
                          download_state='completed', compression_state='completed',
                          compression_percent=100)
            return
        if _codec(raw) == 'hevc':
            compressed.parent.mkdir(parents=True, exist_ok=True)
            temp = compressed.with_name(compressed.name + '.copying')
            try:
                shutil.copy2(raw, temp)
                os.replace(temp, compressed)
            finally:
                temp.unlink(missing_ok=True)
            _manual_state(name, status='下載完成（來源已是 H.265，已保留兩份）',
                          download_state='completed', compression_state='completed',
                          compression_percent=100)
            return
        options = get_settings()
        _manual_state(name, status='等待壓縮資源...', compression_state='queued')
        with ENCODER_LOCK:  # Share the CPU encoder slot with automatic downloads.
            _manual_state(name, status='正在 CPU H.265 壓縮...', compression_state='running')
            last_bucket = [-1]
            def on_encode(percent):
                bucket = percent // 5
                if bucket != last_bucket[0] or percent >= 99:
                    last_bucket[0] = bucket
                    _manual_state(name, compression_percent=percent)
            smaller = encode_h265(raw, on_encode, level=options['compression_level'],
                                  preset=options['encoder_preset'], output_path=compressed,
                                  task_name=name)
        if smaller:
            _manual_state(name, status='下載完成（手動 H.265 壓縮成功，原檔保留）',
                          download_state='completed', compression_state='completed',
                          compression_percent=100)
        else:
            _manual_state(name, status='下載完成（H.265 轉檔後較大，保留原檔）',
                          download_state='completed', compression_state='skipped',
                          compression_percent=100)
    except Exception as exc:
        with state_lock:
            item = download_progress.get(name)
            if item is not None:
                item['compression_state'] = 'error'
                item['status'] = '錯誤: 本機壓縮失敗：' + str(exc)[:250]
                save_state()
    finally:
        with state_lock:
            manual_compressions.discard(name)


def _start_manual_compression(name):
    """Manual retry/compression is always offline and never calls _playlist()."""
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify(ok=False, message='請提供 JSON 物件'), 422
    if not _program('ffmpeg') or not _program('ffprobe'):
        return jsonify(ok=False, message='找不到 FFmpeg 或 ffprobe，請先安裝後重啟伺服器'), 409
    with state_lock:
        item = download_progress.get(name)
        if not isinstance(item, dict):
            return jsonify(ok=False, message='找不到任務'), 404
        if name in queued or name in running or name in manual_compressions or encoder_active(name):
            return jsonify(ok=False, message='此任務正在下載或壓縮，請不要重複送出'), 409
        paths = video_locations(name)
        compressed = paths['compressed']
        if compressed.is_file() and _codec(compressed) == 'hevc':
            return jsonify(ok=True, queued=False, message='本機已有 H.265 影片，無需再次壓縮')
        raw_exists = any(p.is_file() and p.stat().st_size > 0 and _codec(p)
                         for p in (paths['uncompressed'], paths['legacy']))
        expected = None
        delete_after_merge = False
        if not raw_exists:
            ts = segment_cache_status(name, get_download_dir(), item.get('expected_segments'))
            count = ts['cached_segments']
            expected = ts['expected_segments']
            if not expected:
                if count == 0:
                    return jsonify(ok=False, message='本機沒有可用 MP4 或 TS；410 網址已過期，需要重新取得影片來源'), 409
                confirmed = payload.get('confirm_segments')
                if type(confirmed) is not int or confirmed != count:
                    return jsonify(ok=False, needs_segment_confirmation=True,
                                   available_segments=count,
                                   message=f'已找到 {count} 個本機 TS，但沒有 M3U8，無法保證影片完整；需先確認分段總數才能離線合併'), 409
                expected = count
            if not segment_cache_status(name, get_download_dir(), expected)['complete']:
                return jsonify(ok=False,
                               message=f'本機 TS 有缺號或不完整（預期 {expected} 段），已保留原始分段'), 409
            # Auto-delete only for known playlist lengths; otherwise user must
            # explicitly request it after acknowledging the completeness risk.
            delete_after_merge = (bool(item.get('expected_segments')) or ts['playlist_cached'] or
                                  payload.get('delete_ts_after_merge') is True)
        manual_compressions.add(name)
        item.update(status='等待本機合併／壓縮...', compression_state='queued',
                    compression_percent=0)
        save_state()
    thread = threading.Thread(target=_manual_compression_worker,
                              args=(name, expected, delete_after_merge),
                              daemon=True, name='manual-compress-'+name)
    try:
        thread.start()
    except Exception as exc:
        with state_lock:
            manual_compressions.discard(name)
            if name in download_progress:
                download_progress[name]['compression_state'] = 'error'
                download_progress[name]['status'] = '錯誤: 無法啟動手動壓縮：' + str(exc)[:180]
                save_state()
        return jsonify(ok=False,message='無法啟動手動壓縮'),500
    return jsonify(ok=True, queued=True, name=name,
                   message='已開始本機封裝／壓縮；不會重新要求過期 M3U8'), 202


@app.route('/api/tasks/<name>/compression/<action>', methods=['POST', 'OPTIONS'])
def api_compression_control(name, action):
    if request.method == 'OPTIONS':
        return '', 204
    if not NAME_RE.fullmatch(name) or action not in ('start', 'pause', 'resume'):
        return jsonify(ok=False,message='找不到工作或操作'),404
    if action == 'start':
        return _start_manual_compression(name)
    with state_lock:
        item = download_progress.get(name)
        if not isinstance(item,dict):
            return jsonify(ok=False,message='找不到任務'),404
        try:
            state = encoder_control(name, action)
        except Exception as exc:
            return jsonify(ok=False,message=str(exc)[:180]),409
        item['compression_state'] = state
        item['status'] = '壓縮已暫停（可按繼續）' if state == 'paused' else '正在 CPU H.265 壓縮...'
        save_state()
    return jsonify(ok=True,name=name,compression_state=state,
                   message='壓縮已暫停' if state=='paused' else '壓縮已繼續')


@app.route('/api/tasks/<name>/segments', methods=['GET', 'OPTIONS'])
def api_segment_status(name):
    """Inspect local TS without making any remote request."""
    if request.method == 'OPTIONS':
        return '', 204
    if not NAME_RE.fullmatch(name):
        return jsonify(ok=False, message='找不到任務'), 404
    with state_lock:
        item = download_progress.get(name)
        if not isinstance(item, dict):
            return jsonify(ok=False, message='找不到任務'), 404
        expected = item.get('expected_segments')
    data = segment_cache_status(name, get_download_dir(), expected)
    data.pop('folder', None)  # Do not reveal the server's absolute disk paths through the public API.
    return jsonify(ok=True, name=name, **data)


@app.route('/api/tasks/<name>/cache-playlist', methods=['POST', 'OPTIONS'])
def api_cache_playlist(name):
    """Explicitly cache current HLS playlist before its temporary URL expires."""
    if request.method == 'OPTIONS':
        return '', 204
    if not NAME_RE.fullmatch(name):
        return jsonify(ok=False, message='找不到任務'), 404
    with state_lock:
        current = download_progress.get(name)
        if not isinstance(current, dict):
            return jsonify(ok=False, message='找不到任務'), 404
        source = current.get('hls_url')
    try:
        meta = _cache_task_playlist(name, source)
    except Exception as exc:
        return jsonify(ok=False, message='M3U8 快取失敗：' + str(exc)[:250]), 422
    return jsonify(ok=True, name=name, expected_segments=meta['expected_segments'],
                   cached_at=meta['cached_at'], message='M3U8 已保存到本機 playlist.m3u8')


@app.route('/api/tasks/<name>/source', methods=['POST', 'OPTIONS'])
def api_update_source(name):
    """Replace *only* an expired HLS source and resume missing segments."""
    if request.method == 'OPTIONS':
        return '', 204
    if not NAME_RE.fullmatch(name):
        return jsonify(ok=False, message='找不到任務'), 404
    payload = request.get_json(silent=True)
    url = payload.get('hls_url') if isinstance(payload, dict) else None
    if not valid_url(url):
        return jsonify(ok=False, message='請提供有效的 hls_url'), 422
    with state_lock:
        current = download_progress.get(name)
        if not isinstance(current, dict):
            return jsonify(ok=False, message='找不到任務'), 404
        if name in queued or name in running or name in manual_compressions:
            return jsonify(ok=False, message='任務正在下載或壓縮中，請等目前工作結束再更新來源'), 409
        if finished(current):
            return jsonify(ok=False, message='歷史紀錄標示為已完成，不會自動重新下載'), 409
        current['hls_url'] = url
        current['status'] = '排隊中...'
        current.pop('playlist_cache_error', None)
        save_state()
        _prefetch_playlist_async(name, url)
        enqueue(name)
    return jsonify(ok=True, queued=True, name=name,
                   message='已更新 HLS 來源；本機 TS 保留並將優先續傳')


@app.route('/api/health', methods=['GET', 'OPTIONS'])
def api_health():
    if request.method == 'OPTIONS':
        return '', 204
    from h265 import _program
    return jsonify(ok=True, service='JableTVDownload', version='2026-11-05-manual-offline-compression-v6',
                   ffmpeg_available=bool(_program('ffmpeg')),
                   ffprobe_available=bool(_program('ffprobe')))


@app.route('/', methods=['GET'])
def index():
    return send_from_directory(FRONTEND_DIR, 'index.html')


@app.route('/gallery', methods=['GET'])
def gallery():
    return send_from_directory(FRONTEND_DIR, 'gallery.html')


@app.route('/settings', methods=['GET'])
def settings_page():
    return send_from_directory(FRONTEND_DIR, 'settings.html')


@app.route('/api-docs', methods=['GET'])
def api_docs_page():
    return send_from_directory(FRONTEND_DIR, 'api-docs.html')


@app.route('/assets/<path:filename>', methods=['GET'])
def frontend_assets(filename):
    return send_from_directory(FRONTEND_DIR, filename)


@app.route('/status', methods=['GET'])
def get_status():
    """Compatibility endpoint for existing integrations."""
    with state_lock:
        output = {}
        for name, info in download_progress.items():
            if isinstance(info, dict):
                view = task_view(name, info)
                output[name] = {k:view.get(k) for k in API_TASK_FIELDS}
        return jsonify(output)


@app.route('/api/settings', methods=['GET', 'PUT', 'POST', 'OPTIONS'])
def api_settings():
    if request.method == 'OPTIONS':
        return '', 204
    if request.method == 'GET':
        return jsonify(ok=True, settings=get_settings(), download_dir=str(get_download_dir()),
                       uncompressed_dir=str(get_download_dir() / 'uncompressed'),
                       compressed_dir=str(get_download_dir() / 'compressed'))
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        return jsonify(ok=False, message='設定須以 JSON 物件傳送'), 422
    try:
        saved = save_settings({**get_settings(), **value})
        start_workers()
    except (ValueError, RuntimeError) as exc:
        return jsonify(ok=False, message=str(exc)), 422
    return jsonify(ok=True, settings=saved,
                   message='已儲存；降低同時下載數需重新啟動伺服器。')


@app.route('/api/retry/<name>', methods=['POST', 'OPTIONS'])
@app.route('/retry/<name>', methods=['POST', 'OPTIONS'])
def retry_download(name):
    if request.method == 'OPTIONS':
        return '', 204
    if not NAME_RE.fullmatch(name):
        return jsonify(ok=False, message='找不到任務'), 404
    with state_lock:
        item = download_progress.get(name)
        if not isinstance(item, dict):
            return jsonify(ok=False, message='找不到任務'), 404
        if name in running or name in queued or name in manual_compressions:
            return jsonify(ok=True, queued=False, message='任務已在執行')
        if finished(item) and video_file(name).is_file():
            return jsonify(ok=True, queued=False, message='影片檔案已存在，無需重試')
        enqueue(name)
    return jsonify(ok=True, queued=True, message='已重新加入下載佇列')


@app.route('/api/retry/errors', methods=['POST', 'OPTIONS'])
@app.route('/retry/errors', methods=['POST', 'OPTIONS'])
def retry_errors():
    if request.method == 'OPTIONS':
        return '', 204
    with state_lock:
        names = [name for name,item in download_progress.items()
                 if isinstance(item,dict) and str(item.get('status','')).startswith('錯誤')]
    count = sum(enqueue(name) for name in names)
    return jsonify(ok=True, queued=count, message=f'已排入 {count} 個錯誤任務')


@app.route('/cover/<name>')
def cover(name):
    if not NAME_RE.fullmatch(name):
        abort(404)
    with state_lock:
        item = download_progress.get(name)
        if not item:
            abort(404)
        image_url = item.get('image_url','')
    supported = {'image/jpeg':'.jpg','image/png':'.png','image/webp':'.webp'}
    local_name = local_cover_name(name)
    path = IMAGE_DIR / local_name if local_name else None
    if path is None:
        if not valid_url(image_url):
            abort(404)
        try:
            with requests.get(image_url,timeout=(5,15),stream=True) as res:
                res.raise_for_status()
                if not valid_url(res.url):
                    abort(404)
                content_type = res.headers.get('Content-Type','').split(';',1)[0].lower().strip()
                ext = supported.get(content_type)
                if not ext:
                    abort(404)
                data = bytearray()
                for chunk in res.iter_content(65536):
                    data.extend(chunk)
                    if len(data) > 5_000_000:
                        abort(413)
                if not data:
                    abort(404)
                path = IMAGE_DIR / (name + ext)
                temp = Path(str(path)+'.part')
                try:
                    temp.write_bytes(data)
                    os.replace(temp,path)
                finally:
                    temp.unlink(missing_ok=True)
        except requests.RequestException:
            abort(404)
    return send_from_directory(IMAGE_DIR,path.name,max_age=3600)


@app.route('/image/<path:filename>')
def existing_image(filename):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,96}\.(?:jpg|jpeg|png|webp)',filename,flags=re.IGNORECASE):
        abort(404)
    return send_from_directory(IMAGE_DIR,filename,max_age=3600)


