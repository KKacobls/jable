"""CPU-only HEVC transcoding, with an intact source until replacement is safe."""
import os
import shutil
import subprocess
import threading

try:
    import psutil
except ImportError:  # Show a helpful error until requirements are installed.
    psutil = None

_ENCODERS = {}
_ENCODERS_LOCK = threading.RLock()


def encoder_control(name, action):
    """Pause/resume a running FFmpeg process, not the download worker thread."""
    if action not in ("pause", "resume"):
        raise ValueError("操作必須為 pause 或 resume")
    if psutil is None:
        raise RuntimeError("缺少 psutil，請執行 python -m pip install -r requirements.txt")
    with _ENCODERS_LOCK:
        record = _ENCODERS.get(name)
        if not record or record["process"].poll() is not None:
            raise RuntimeError("目前沒有可以暫停或繼續的壓縮工作")
        want_paused = action == "pause"
        if record["paused"] != want_paused:
            target = psutil.Process(record["process"].pid)
            if want_paused:
                target.suspend()
            else:
                target.resume()
            record["paused"] = want_paused
        return "paused" if record["paused"] else "running"


def encoder_active(name):
    with _ENCODERS_LOCK:
        record = _ENCODERS.get(name)
        return bool(record and record["process"].poll() is None)


def resume_all_encoders():
    """Avoid leaving FFmpeg suspended when Python exits normally."""
    with _ENCODERS_LOCK:
        if psutil is None:
            return
        for record in _ENCODERS.values():
            if record["paused"] and record["process"].poll() is None:
                try:
                    psutil.Process(record["process"].pid).resume()
                except psutil.Error:
                    pass


import atexit
atexit.register(resume_all_encoders)
from pathlib import Path

from settings import DEFAULTS

COMPRESSION_LEVEL = DEFAULTS['compression_level']
# Higher level => higher CRF => smaller output, with more quality loss.
CPU_CRF_BY_LEVEL = {1: 20, 2: 22, 3: 24, 4: 26, 5: 28, 6: 30, 7: 32, 8: 34, 9: 36, 10: 38}


def _program(name):
    """Resolve FFmpeg tools from explicit paths, bin folder, or Windows PATH.

    If a tool was installed while Python was running, restart the server process.
    """
    if name not in ('ffmpeg', 'ffprobe'):
        return shutil.which(name)
    exe = name + ('.exe' if os.name == 'nt' else '')
    direct = os.environ.get(name.upper() + '_PATH', '').strip().strip('"')
    if direct and Path(direct).is_file():
        return str(Path(direct).resolve())
    bin_dir = os.environ.get('FFMPEG_BIN_DIR', '').strip().strip('"')
    if bin_dir and (Path(bin_dir) / exe).is_file():
        return str((Path(bin_dir) / exe).resolve())
    found = shutil.which(name)
    if found:
        return found
    companion = shutil.which('ffmpeg') or os.environ.get('FFMPEG_PATH', '').strip().strip('"')
    if companion:
        sibling = Path(companion).with_name(exe)
        if sibling.is_file():
            return str(sibling.resolve())
    return None


def _probe(path):
    ffprobe = _program('ffprobe')
    if not ffprobe:
        raise RuntimeError('找不到 ffprobe，請安裝完整的 FFmpeg。')
    result = subprocess.run([ffprobe,'-v','error','-show_entries','format=duration',
                             '-of','default=noprint_wrappers=1:nokey=1',str(path)],
                             capture_output=True,text=True,timeout=40,check=True)
    return max(0.0, float(result.stdout.strip()))


def _codec(path):
    ffprobe = _program('ffprobe')
    if not ffprobe:
        return ''
    try:
        return subprocess.check_output([ffprobe,'-v','error','-select_streams','v:0',
            '-show_entries','stream=codec_name','-of','default=noprint_wrappers=1:nokey=1',str(path)],
            text=True,timeout=40).strip().lower()
    except (OSError, subprocess.SubprocessError):
        return ''


def encode_h265(input_path, progress_callback=None, level=COMPRESSION_LEVEL,
                preset='fast', output_path=None, task_name=None):
    """Encode HEVC. When output_path is provided, NEVER replace the source.

    Return True only when the output is valid and smaller than the original.
    Retain an existing output until its replacement is fully validated.
    Without output_path, keep the previous replace-in-place behavior.
    """
    ffmpeg = _program('ffmpeg')
    if not ffmpeg or not _program('ffprobe'):
        raise RuntimeError('找不到 ffmpeg 或 ffprobe。安裝後請重啟終端機及伺服器，再按「重試錯誤任務」。')
    level = int(level)
    if level not in CPU_CRF_BY_LEVEL:
        raise ValueError('壓縮等級必須介於 1 到 10')
    if preset not in ('ultrafast','superfast','veryfast','faster','fast','medium'):
        raise ValueError('不支援的編碼速度')
    input_path = Path(input_path).resolve()
    if not input_path.is_file() or not _codec(input_path):
        raise RuntimeError('來源檔案不存在或不是有效影片')
    output_path = Path(output_path).resolve() if output_path is not None else input_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration = _probe(input_path)
    temp_path = Path(str(output_path) + '.h265-working.mp4')
    temp_path.unlink(missing_ok=True)
    command = [ffmpeg,'-hide_banner','-loglevel','error','-y','-i',str(input_path),
        '-map','0:v:0','-map','0:a?','-map_metadata','0',
        '-c:v','libx265','-preset',preset,'-crf',str(CPU_CRF_BY_LEVEL[level]),
        '-x265-params','pools=6:frame-threads=2:log-level=error',
        '-threads','6','-tag:v','hvc1','-c:a','aac','-b:a','96k',
        '-movflags','+faststart','-progress','pipe:1','-nostats',str(temp_path)]
    recent = []
    last = -1
    process = None
    try:
        process = subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                                   text=True,encoding='utf-8',errors='replace',bufsize=1)
        if task_name:
            with _ENCODERS_LOCK:
                _ENCODERS[task_name] = {"process": process, "paused": False}
        assert process.stdout is not None
        for raw in process.stdout:
            line = raw.strip()
            if line.startswith(('out_time_us=','out_time_ms=')) and duration:
                try:
                    current = int(line.split('=',1)[1]) / 1_000_000
                    percent = max(0,min(99,int(current / duration * 100)))
                    if percent != last and progress_callback:
                        progress_callback(percent)
                        last = percent
                except ValueError:
                    pass
            if line:
                recent.append(line)
                recent = recent[-15:]
        exitcode = process.wait()
        if exitcode != 0 or not temp_path.is_file() or temp_path.stat().st_size == 0 or _codec(temp_path) != 'hevc':
            raise RuntimeError('H.265 編碼失敗：' + '\n'.join(recent[-5:]))
        # Avoid creating larger "compressed" copies when they do not save space.
        if temp_path.stat().st_size >= input_path.stat().st_size:
            return False
        os.replace(temp_path, output_path)
        return True
    finally:
        if task_name:
            with _ENCODERS_LOCK:
                record = _ENCODERS.get(task_name)
                if record and record["process"] is process:
                    if record["paused"] and psutil is not None and process.poll() is None:
                        try:
                            psutil.Process(process.pid).resume()
                        except psutil.Error:
                            pass
                    _ENCODERS.pop(task_name, None)
        temp_path.unlink(missing_ok=True)
