// ==UserScript==
// @name         Jable Video Info - 下載紀錄與一鍵送出
// @namespace    https://j.xekro.top/
// @version      2.0.0
// @description  擷取 HLS/名稱/封面、複製 JSON、檢查是否下載過、送出到自己的下載伺服器
// @author       kk
// @match        https://jable.tv/*
// @match        https://www.jable.tv/*
// @run-at       document-idle
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_setClipboard
// @grant        GM_registerMenuCommand
// @connect      *
// ==/UserScript==

(function () {
  'use strict';

  // 可直接編輯，也可在 Tampermonkey 腳本選單中選「設定下載伺服器網址」。
  const DEFAULT_SERVER_URL = "https://j.xekro.top";
  const STORAGE_KEY = 'JABLE_SERVER_URL';

  function serverBase() {
    const value = String(GM_getValue(STORAGE_KEY, DEFAULT_SERVER_URL) || '').trim().replace(/\/+$/, '');
    try {
      const url = new URL(value);
      if (!['http:', 'https:'].includes(url.protocol)) throw new Error('protocol');
      return value;
    } catch {
      throw new Error('請先在油猴選單設定有效的 http(s) 伺服器網址');
    }
  }

  function configureServer() {
    const oldValue = String(GM_getValue(STORAGE_KEY, DEFAULT_SERVER_URL) || '');
    const entered = prompt('下載伺服器網址（不含結尾 /），例如 https://j.xekro.top', oldValue);
    if (entered === null) return;
    try {
      const parsed = new URL(entered.trim());
      if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error('不支援的協定');
      GM_setValue(STORAGE_KEY, entered.trim().replace(/\/+$/, ''));
      alert('已儲存伺服器網址，重新整理影片頁面後套用。');
    } catch {
      alert('網址無效，請輸入 http:// 或 https:// 開頭的網址。');
    }
  }
  GM_registerMenuCommand('設定下載伺服器網址', configureServer);

  function extractVideoInfo() {
    let hlsUrl = '';
    for (const script of document.querySelectorAll('script')) {
      const content = script.textContent || '';
      const match = content.match(/\b(?:var|let|const)\s+hlsUrl\s*=\s*['"](https?:\/\/[^'"\s]+?\.m3u8(?:\?[^'"]*)?)['"]/i);
      if (match) { hlsUrl = match[1]; break; }
    }
    const canonical = document.querySelector('link[rel="canonical"]')?.getAttribute('href') || location.href;
    const nameMatch = canonical.match(/\/videos\/([a-zA-Z0-9_-]+)(?:\/|[?#]|$)/i);
    const imageUrl = document.querySelector('meta[property="og:image"]')?.getAttribute('content') || '';
    return {
      hls_url: hlsUrl,
      name: nameMatch ? nameMatch[1] : '',
      image_url: imageUrl
    };
  }

  function requestJson(method, path, body) {
    return new Promise((resolve, reject) => {
      let base;
      try { base = serverBase(); } catch (err) { reject(err); return; }
      GM_xmlhttpRequest({
        method,
        url: base + path,
        timeout: 15000,
        headers: { 'Accept': 'application/json', ...(body ? {'Content-Type': 'application/json'} : {}) },
        data: body ? JSON.stringify(body) : undefined,
        onload: result => {
          let payload;
          try { payload = JSON.parse(result.responseText); }
          catch { reject(new Error(`伺服器回傳非 JSON（HTTP ${result.status}）`)); return; }
          if (result.status === 404) { resolve({missing: true}); return; }
          if (result.status < 200 || result.status >= 300 || payload.ok === false) {
            reject(new Error(payload.message || `HTTP ${result.status}`)); return;
          }
          resolve(payload);
        },
        ontimeout: () => reject(new Error('連線逾時，請確認伺服器網址與 Cloudflare Tunnel')),
        onerror: () => reject(new Error('無法連線，請確認油猴跨網域權限與伺服器狀態'))
      });
    });
  }

  async function copyText(content) {
    if (navigator.clipboard && window.isSecureContext) {
      try { await navigator.clipboard.writeText(content); return; }
      catch { /* 交由油猴剪貼簿 API 處理 */ }
    }
    GM_setClipboard(content, 'text');
  }

  function makeButton(label) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'jable-sync-button';
    button.textContent = label;
    return button;
  }

  function installWidget() {
    if (document.getElementById('jable-video-sync-panel')) return;
    const info = extractVideoInfo();
    if (!info.name) return; // 非單部影片頁不注入面板
    const style = document.createElement('style');
    style.textContent = `
      #jable-video-sync-panel { position:fixed;right:16px;bottom:18px;z-index:2147483647;
        width:min(330px,calc(100vw - 28px));padding:14px;background:#142137;color:#f2f6fc;
        border:1px solid #426086;border-radius:13px;box-shadow:0 12px 40px #0008;
        font:14px/1.5 system-ui,'Microsoft JhengHei',sans-serif;}
      #jable-video-sync-panel * {box-sizing:border-box}
      #jable-video-sync-panel .jable-sync-title {font-weight:750;font-size:15px;overflow-wrap:anywhere;margin-bottom:6px}
      #jable-video-sync-panel .jable-sync-status {min-height:40px;color:#cbd8ef;overflow-wrap:anywhere}
      #jable-video-sync-panel .jable-sync-actions {display:flex;flex-wrap:wrap;gap:7px;margin-top:10px}
      #jable-video-sync-panel .jable-sync-button {flex:1 1 45%;background:#326bd1;color:white;
        border:0;border-radius:8px;padding:8px 10px;font-size:13px;cursor:pointer}
      #jable-video-sync-panel .jable-sync-button:disabled {opacity:.55;cursor:not-allowed}
      #jable-video-sync-panel .jable-sync-minimize {background:none;border:0;color:#c5d9ff;
        float:right;cursor:pointer}
      #jable-video-sync-panel.is-minimized .jable-sync-status,
      #jable-video-sync-panel.is-minimized .jable-sync-actions {display:none}
    `;
    document.head.appendChild(style);

    const panel = document.createElement('div');
    panel.id = 'jable-video-sync-panel';
    const toggle = document.createElement('button');
    toggle.className = 'jable-sync-minimize'; toggle.type = 'button'; toggle.textContent = '收合';
    toggle.title = '收合或展開';
    toggle.addEventListener('click', () => {
      panel.classList.toggle('is-minimized');
      toggle.textContent = panel.classList.contains('is-minimized') ? '展開' : '收合';
    });
    const title = document.createElement('div');
    title.className = 'jable-sync-title'; title.textContent = `Jable：${info.name}`;
    const status = document.createElement('div');
    status.className = 'jable-sync-status'; status.textContent = '下載紀錄：查詢中…';
    const actions = document.createElement('div'); actions.className = 'jable-sync-actions';
    const copy = makeButton('複製 JSON');
    const send = makeButton('送到伺服器');
    const check = makeButton('重新查詢');
    const config = makeButton('設定網址');
    actions.append(copy, send, check, config);
    panel.append(toggle, title, status, actions);
    document.body.appendChild(panel);

    const message = text => { status.textContent = text; };
    async function checkStatus() {
      check.disabled = true;
      message('下載紀錄：查詢中…');
      try {
        const data = await requestJson('GET', '/api/tasks/' + encodeURIComponent(info.name));
        if (data.missing) message('下載紀錄：尚未下載');
        else if (data.task?.completed) {
          message(data.task.file_exists ? '下載紀錄：已完成（檔案存在）' : '下載紀錄：已下載過（歷史已保留，檔案可能已搬走）');
        } else message('下載紀錄：' + (data.task?.status || '已有任務'));
      } catch (err) { message('查詢失敗：' + err.message); }
      finally { check.disabled = false; }
    }
    copy.addEventListener('click', async () => {
      Object.assign(info, extractVideoInfo());
      try { await copyText(JSON.stringify(info, null, 2)); message('已複製 JSON，可貼到伺服器。'); }
      catch (err) { message('複製失敗：' + err.message); }
    });
    send.addEventListener('click', async () => {
      // 只有使用者點擊才送出，避免瀏覽影片時意外下載。
      Object.assign(info, extractVideoInfo()); // 影片稍晚才載入 HLS 時再擷取一次
      if (!info.hls_url || !info.name) {
        message('目前找不到 HLS 網址或影片名稱，請等播放來源載入後再按一次。');return;
      }
      send.disabled = true;
      message('正在送出下載任務…');
      try {
        const data = await requestJson('POST', '/api/download', info);
        message(data.added ? '已加入下載佇列！' : '伺服器已有此任務／已下載過，不重複下載。');
        // 確認傳送結果後更新歷史狀態，不重整網頁。
        setTimeout(() => { void checkStatus(); }, 1200);
      } catch (err) { message('送出失敗：' + err.message); }
      finally { send.disabled = false; }
    });
    check.addEventListener('click', () => { void checkStatus(); });
    config.addEventListener('click', configureServer);
    void checkStatus(); // 即使 HLS 載入較慢，仍先查詢既有下載紀錄
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => setTimeout(installWidget, 500), {once:true});
  else setTimeout(installWidget, 500);
})();
