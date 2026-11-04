"""Compatibility entrypoint: python app.py (Windows / Cloudflare port 6003)."""
import os
from backend.api import app, start_workers, resume_interrupted

if __name__ == '__main__':
    try:
        from waitress import serve
    except ImportError:
        raise SystemExit('請先安裝依賴：python -m pip install -r requirements.txt')
    start_workers()
    resumed = resume_interrupted()
    host = os.environ.get('JABLE_HOST', '0.0.0.0')
    port = int(os.environ.get('JABLE_PORT', '6003'))
    print(f'Jable Server: http://127.0.0.1:{port} / LAN: http://<本機IP>:{port}')
    print(f'免密碼；已排入 {resumed} 個中斷任務；Cloudflare Tunnel -> http://127.0.0.1:{port}')
    serve(app, host=host, port=port, threads=8)
