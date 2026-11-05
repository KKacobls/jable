'use strict';
// Standalone vanilla-JS frontend. Only speaks JSON HTTP APIs; no Flask/Jinja state.
const API_BASE = String(window.JABLE_API_BASE || '').replace(/\/+$/, '');
const api = path => `${API_BASE}${path}`;
let submitting = false;
let refreshing = false;
const intervalMs = 5000;
// By request, any nonempty query string enables inline thumbnails; no query = zero cover requests.
const SHOW_INLINE_IMAGES = Boolean(window.location.search && window.location.search.length > 1);
let imageModeEnabled = SHOW_INLINE_IMAGES;
let latestTasks = [];
let imageLoadFailures = 0;
const byId = id => document.getElementById(id);

function selectedText() {
  const selection = window.getSelection?.();
  return Boolean(selection && !selection.isCollapsed && String(selection).length);
}
function setText(element, value) {
  if (element && element.textContent !== String(value)) element.textContent = String(value);
}
function feedback(message, type='ok', target='submit-feedback') {
  const node = byId(target);
  if (!node) return;
  node.hidden = false;
  node.className = `submit-feedback ${type}`;
  setText(node, message);
}
async function jsonFetch(path, options={}) {
  const response = await fetch(api(path), {cache:'no-store', ...options});
  let data;
  try { data = await response.json(); }
  catch { throw new Error(`HTTP ${response.status}：非 JSON 回應`); }
  if (!response.ok || data.ok === false) {
    const error = new Error(data.message || `HTTP ${response.status}`);
    error.details = data;
    throw error;
  }
  return data;
}
function ensureDualProgress(card) {
  if (!card || card.querySelector('.compression-progress')) return;
  const row = card.querySelector('.progress-row');
  if (row && !row.querySelector('.download-progress-label')) {
    const label = document.createElement('strong');
    label.className = 'download-progress-label'; label.textContent = '下載';
    row.prepend(label);
  }
  const track = card.querySelector('.track');
  const section = document.createElement('div');
  section.className = 'compression-progress';
  const heading = document.createElement('div');
  heading.className = 'progress-row';
  const label = document.createElement('strong'); label.textContent = '壓縮';
  const count = document.createElement('span'); count.className = 'compression-state-text';
  const percent = document.createElement('span'); percent.className = 'compression-percent';
  heading.append(label, count, percent);
  const barTrack = document.createElement('div'); barTrack.className = 'track compression-track';
  const bar = document.createElement('div'); bar.className = 'bar compression-bar';
  barTrack.appendChild(bar);
  section.append(heading, barTrack);
  track?.after(section);
  const actions = card.querySelector('.task-buttons');
  for (const [action,text] of [['start','開始壓縮'],['pause','暫停壓縮'],['resume','繼續壓縮']]) {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'btn small secondary';
    button.dataset.compressionAction = action;
    button.textContent = text; button.hidden = true;
    actions?.prepend(button);
  }
  const trash = document.createElement('button');
  trash.type = 'button'; trash.className = 'btn small subtle task-delete';
  trash.dataset.deleteName = card.dataset.taskName;
  trash.textContent = '🗑 垃圾桶';
  actions?.appendChild(trash);
}
function updateDualProgress(card,item) {
  ensureDualProgress(card);
  const rawTotal = Math.max(1,Number(item.download_total)||1);
  const rawCurrent = Math.min(rawTotal,Math.max(0,Number(item.download_current)||0));
  const rawPercent = Math.min(100,100*rawCurrent/rawTotal);
  setText(card.querySelector('.count'), `${rawCurrent} / ${rawTotal}`);
  setText(card.querySelector('.percent'), `${rawPercent.toFixed(1)}%`);
  const rawBar = card.querySelector('.track:not(.compression-track) .bar');
  if (rawBar) rawBar.style.width = rawPercent + '%';
  const compState = String(item.compression_state || 'pending');
  const pct = Math.min(100,Math.max(0,Number(item.compression_percent)||0));
  let text = {running:'壓縮中',paused:'已暫停',queued:'等待中',completed:'已完成',
              disabled:'未啟用',skipped:'未產生較小檔案',error:'壓縮失敗',pending:'尚未開始'}[compState] || compState;
  setText(card.querySelector('.compression-state-text'),text);
  setText(card.querySelector('.compression-percent'),pct.toFixed(1)+'%');
  const cbar=card.querySelector('.compression-bar');
  if (cbar) cbar.style.width=pct+'%';
  const start=card.querySelector('[data-compression-action="start"]');
  if (start) {
    start.hidden = !item.can_start_compression || ['queued','running','paused'].includes(compState);
    const label = item.local_raw_exists ? '開始壓縮' : '合併 TS／壓縮';
    setText(start,label);
    start.title = item.local_raw_exists ? '直接壓縮現有 MP4，不重新下載' :
      '從本機 TS 嘗試離線合併，不會存取過期的 M3U8';
  }
  const pause=card.querySelector('[data-compression-action="pause"]');
  const resume=card.querySelector('[data-compression-action="resume"]');
  if (pause) pause.hidden=compState!=='running';
  if (resume) resume.hidden=compState!=='paused';
  const label=card.querySelector('.download-progress-label');
  if (label) setText(label,item.download_state==='merging' && item.merge_percent!==null && item.merge_percent!==undefined
    ? `TS 已下載 · 封裝 ${item.merge_percent}%` :
    (!item.file_exists && rawPercent >= 100 ? '分段計數 100%（尚未有 MP4）' : '下載'));
}
function taskCard(name) {
  const template = byId('task-card-template');
  const card = template?.content.firstElementChild?.cloneNode(true);
  if (!card) return null;
  card.dataset.taskName = name;
  ensureDualProgress(card);
  const link = card.querySelector('.task-name');
  setText(link,name);
  link.href = 'https://jable.tv/videos/' + encodeURIComponent(name) + '/';
  card.querySelector('[data-retry-name]').dataset.retryName = name;
  card.querySelector('[data-copy-name]').dataset.copyName = name;
  return card;
}
// Images are an explicit opt-in. A query string (including ?images=1) turns them on.
// Visible placeholders and manual retry make a failed request diagnosable instead of silent.
function ensureTaskCover(card, item, eager=false) {
  if (!imageModeEnabled || !card) return;
  card.classList.add('has-cover');
  let container = card.querySelector('.task-cover');
  if (!container) {
    container = document.createElement('div');
    container.className = 'task-cover';
    const image = document.createElement('img');
    image.className = 'task-thumb';
    image.loading = eager ? 'eager' : 'lazy';
    image.decoding = 'async';
    image.alt = item.name + ' 封面';
    // Do not hide a successful image because an older request failed.
    image.addEventListener('load', () => {
      image.classList.remove('hidden');
      container.classList.add('loaded');
      container.classList.remove('failed');
      const error = container.querySelector('.task-cover-error');
      if (error) error.classList.add('hidden');
    });
    image.addEventListener('error', () => {
      // Only the actual failed URL gets marked as failed.
      image.classList.add('hidden');
      container.classList.remove('loaded');
      container.classList.add('failed');
      const error = container.querySelector('.task-cover-error');
      if (error) {
        error.classList.remove('hidden');
        error.textContent = '封面載入失敗';
      }
      const retry = container.querySelector('.cover-retry');
      if (retry) retry.classList.remove('hidden');
      imageLoadFailures++;
      setText(byId('image-debug'), `有 ${imageLoadFailures} 次封面請求失敗；可點「重試封面」`);
    });
    const missing = document.createElement('span');
    missing.className = 'task-cover-error hidden';
    missing.setAttribute('role','status');
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.className = 'cover-retry hidden';
    retry.textContent = '重試封面';
    retry.title = '重新向伺服器載入圖片';
    retry.addEventListener('click', () => {
      const path = container.dataset.coverUrl;
      if (!path) return;
      retry.classList.add('hidden');
      missing.textContent = '重新載入中…';
      missing.classList.remove('hidden');
      const url = new URL(api(path), window.location.href);
      url.searchParams.set('_image_retry', String(Date.now()));
      image.src = url.toString();
    });
    container.append(image, missing, retry);
    card.prepend(container);
  }
  const image = container.querySelector('.task-thumb');
  const missing = container.querySelector('.task-cover-error');
  const retry = container.querySelector('.cover-retry');
  const url = item.cover_url === undefined ? '/cover/' + encodeURIComponent(item.name) : (item.cover_url || '');
  if (container.dataset.coverUrl === url) return;
  container.dataset.coverUrl = url;
  retry.classList.add('hidden');
  container.classList.remove('loaded','failed');
  if (url) {
    missing.textContent = '封面載入中…';
    missing.classList.remove('hidden');
    image.classList.remove('hidden');
    image.loading = eager ? 'eager' : 'lazy';
    image.src = api(url);
  } else {
    image.removeAttribute('src');
    image.classList.add('hidden');
    missing.textContent = '沒有封面資料';
    missing.classList.remove('hidden');
  }
}
function applyImageMode() {
  const link = byId('image-mode-link');
  const hint = byId('image-mode-hint');
  const debug = byId('image-debug');
  if (hint) hint.hidden = !imageModeEnabled;
  if (debug) debug.hidden = !imageModeEnabled;
  if (link) {
    link.href = imageModeEnabled ? location.pathname : '?images=1';
    setText(link, imageModeEnabled ? '隱藏列表圖片' : '顯示列表圖片');
  }
  if (!imageModeEnabled) {
    document.querySelectorAll('.task-cover').forEach(node => node.remove());
    document.querySelectorAll('.task.has-cover').forEach(node => node.classList.remove('has-cover'));
    return;
  }
  latestTasks.forEach((item,i) => {
    const card = Array.from(document.querySelectorAll('.task[data-task-name]')).find(el=>el.dataset.taskName === item.name);
    if (card) ensureTaskCover(card, item, i < 12);
  });
}
function updateCounts() {
  const pending = byId('pending-tasks')?.children.length || 0;
  const finished = byId('finished-tasks')?.children.length || 0;
  setText(byId('pending-count'),pending);
  setText(byId('finished-count'),finished);
  byId('pending-empty')?.classList.toggle('hidden',pending !== 0);
  byId('finished-empty')?.classList.toggle('hidden',finished !== 0);
}
async function refreshTasks(force=false) {
  if (!byId('global-progress-text') || refreshing || submitting) return;
  if (selectedText()) return; // Do not interrupt copying or selection.
  if (!force && (document.hidden || !byId('auto-refresh')?.checked)) return;
  refreshing = true;
  try {
    const data = await jsonFetch('/api/tasks');
    if (selectedText() || submitting) return;
    const existing = new Map(Array.from(document.querySelectorAll('.task[data-task-name]'))
      .map(card => [card.dataset.taskName, card]));
    const tasks = (data.tasks || []).slice().sort((a,b)=>b.name.localeCompare(a.name));
    latestTasks = tasks;
    let done=0;
    for (const [position,item] of tasks.entries()) {
      if (item.completed) done++;
      const list = byId(item.completed?'finished-tasks':'pending-tasks');
      let card = existing.get(item.name);
      if (!card) {
        card = taskCard(item.name);
        if (!card) continue;
        list.appendChild(card);
      } else if (card.parentElement !== list) {
        list.appendChild(card);
      }
      existing.delete(item.name);
      ensureTaskCover(card, item, position < 12);
      setText(card.querySelector('.status'),item.status || '未知狀態');
      updateDualProgress(card,item);
      card.querySelector('.missing-file')?.classList.toggle('hidden',!(item.completed && !item.file_exists));
    }
    for(const card of existing.values()) card.remove();
    updateCounts();
    setText(byId('global-progress-text'),`${done} / ${tasks.length} 已完成`);
    const progressBar = byId('global-progress-bar');
    if(progressBar) progressBar.style.width = (tasks.length?100*done/tasks.length:0)+'%';
  } catch(error) {
    setText(byId('global-progress-text'),'更新失敗：'+error.message);
  } finally { refreshing=false; }
}
function initTaskPage() {
  const form = byId('create-form');
  if (!form) return;
  const imageModeLink = byId('image-mode-link');
  imageModeLink?.addEventListener('click', event => {
    event.preventDefault();
    imageModeEnabled = !imageModeEnabled;
    // Explicit toggles do not reload the page or interrupt a text selection.
    history.replaceState(null, '', imageModeEnabled ? location.pathname + '?images=1' : location.pathname);
    imageLoadFailures = 0;
    applyImageMode();
  });
  applyImageMode();
  const field = form.querySelector('textarea[name="video_text"]');
  const button = byId('submit-task');
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const original = field.value;
    if (submitting || !original.trim()) return;
    submitting = true;
    button.disabled = true;
    setText(button,'加入中…');
    try {
      const result = await jsonFetch('/api/download',{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({video_text:original})
      });
      // Preserve text the user entered while this request was running.
      if (field.value === original) field.value = '';
      else if (field.value.startsWith(original)) field.value = field.value.slice(original.length).replace(/^\n+/, '');
      field.focus({preventScroll:true});
      feedback(result.message || '已送出',result.added?'ok':'warn');
    } catch(error) {
      feedback('提交失敗：'+error.message+'。內容已保留。','warn');
    } finally {
      submitting = false;
      button.disabled = false;
      setText(button,'＋ 新增任務');
      void refreshTasks(true);
    }
  });
  field.addEventListener('keydown',event=>{
    if(event.key==='Enter' && (event.ctrlKey||event.metaKey) && !event.isComposing) {
      event.preventDefault();form.requestSubmit();
    }
  });
  try { byId('auto-refresh').checked = localStorage.getItem('jable-auto-refresh') !== 'off'; }
  catch { /* restricted browser storage */ }
  byId('auto-refresh')?.addEventListener('change',event=>{
    try {localStorage.setItem('jable-auto-refresh',event.target.checked?'on':'off');}
    catch { /* restricted browser storage */ }
    if (event.target.checked) void refreshTasks(true);
  });
  byId('refresh-now')?.addEventListener('click',()=>void refreshTasks(true));
  byId('retry-errors')?.addEventListener('click',async()=>{
    try { const data=await jsonFetch('/api/retry/errors',{method:'POST'}); feedback(data.message); void refreshTasks(true); }
    catch(error) { feedback(error.message,'warn'); }
  });
  void refreshTasks(true);
  setInterval(()=>void refreshTasks(),intervalMs);
}
// Compatible with older index.html and user-customized index.html. If an
// existing lightbox lives in HTML, leave its own event handlers untouched.
function initTaskCoverZoom() {
  if (byId('task-image-viewer')) return;
  const viewer=document.createElement('div');
  viewer.className='task-image-viewer';
  viewer.id='task-image-viewer'; viewer.hidden=true;
  viewer.setAttribute('role','dialog');viewer.setAttribute('aria-modal','true');
  const closeButton=document.createElement('button');
  closeButton.className='viewer-close';closeButton.type='button';closeButton.textContent='關閉 ×';
  const full=document.createElement('img');full.alt='放大封面';
  const title=document.createElement('div');title.className='viewer-name';
  viewer.append(closeButton,full,title);document.body.appendChild(viewer);
  let oldOverflow='';
  function close() {viewer.hidden=true;full.removeAttribute('src');document.body.style.overflow=oldOverflow;}
  document.addEventListener('click',event=>{
    const image=event.target.closest?.('.task-cover img.task-thumb');
    if (!image || !image.naturalWidth) return;
    full.src=image.currentSrc||image.src;
    title.textContent=image.closest('.task')?.dataset.taskName||'';
    oldOverflow=document.body.style.overflow;
    document.body.style.overflow='hidden';
    viewer.hidden=false;
  });
  closeButton.addEventListener('click',close);
  viewer.addEventListener('click',event=>{if(event.target===viewer)close();});
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!viewer.hidden)close();});
}
function initTaskActions() {
  document.addEventListener('click',async event=>{
    const button = event.target.closest?.('[data-copy-name]');
    if (button) {
      const text = button.dataset.copyName;
      try {
        if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
        else {
          const node=document.createElement('textarea');
          node.value=text;node.style.position='fixed';node.style.opacity='0';
          document.body.appendChild(node);node.select();
          const ok=document.execCommand('copy');node.remove();
          if(!ok) throw new Error('剪貼簿被瀏覽器限制');
        }
        feedback(`已複製：${text}`);
      } catch { feedback('複製失敗，可直接選取名稱後複製。','warn'); }
      return;
    }
    const control = event.target.closest?.('[data-compression-action]');
    if (control) {
      const card = control.closest('.task');
      const name = card?.dataset.taskName;
      if (!name) return;
      control.disabled = true;
      try {
        const path = '/api/tasks/'+encodeURIComponent(name)+'/compression/'+
          encodeURIComponent(control.dataset.compressionAction);
        let result;
        try {
          result = await jsonFetch(path,{method:'POST'});
        } catch (error) {
          if (control.dataset.compressionAction !== 'start' || !error.details?.needs_segment_confirmation) throw error;
          const count = error.details.available_segments;
          if (!window.confirm(`本機找到 ${count} 個 TS，沒有原始 M3U8，無法確定片尾是否完整。\n\n確定要假設這就是完整分段，離線合併並壓縮嗎？`)) return;
          const remove = window.confirm('合併通過 FFprobe 後要刪除原本的 TS 嗎？\n\n注意：無 M3U8 時無法確認影片片尾完整。建議按「取消」保留 TS，播放影片確認後再刪。');
          result = await jsonFetch(path,{
            method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({confirm_segments:count,delete_ts_after_merge:remove})
          });
        }
        feedback(result.message || '已更新壓縮狀態');
        void refreshTasks(true);
      } catch(error) {feedback('操作失敗：'+error.message,'warn');}
      finally {control.disabled=false;}
      return;
    }
    const trash = event.target.closest?.('[data-delete-name]');
    if (trash) {
      const name = trash.dataset.deleteName;
      if (!window.confirm(`將 ${name} 移入垃圾桶嗎？任務與影片會移到 downloads/.trash；正在執行的任務無法刪除。`)) return;
      const typed = window.prompt('再次確認：請輸入完整影片名稱', '');
      if (typed !== name) {feedback('片名不相符，已取消。','warn'); return;}
      trash.disabled = true;
      try {
        const result=await jsonFetch('/api/tasks/'+encodeURIComponent(name),{
          method:'DELETE', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({confirm:name})
        });
        feedback(result.message || '已移入垃圾桶');
        void refreshTasks(true);
      } catch(error) {feedback('移除失敗：'+error.message,'warn');}
      finally {trash.disabled=false;}
      return;
    }
    const retry = event.target.closest?.('[data-retry-name]');
    if (!retry) return;
    const name = retry.dataset.retryName;
    retry.disabled = true;
    try {
      const data=await jsonFetch('/api/retry/'+encodeURIComponent(name),{method:'POST'});
      feedback(data.message,data.queued?'ok':'warn');
      void refreshTasks(true);
    } catch(error) { feedback(error.message,'warn'); }
    finally {retry.disabled=false;}
  });
}
async function initGallery() {
  const grid=byId('gallery-grid');
  if(!grid) return;
  try {
    const data=await jsonFetch('/api/tasks');
    grid.replaceChildren();
    const tasks=(data.tasks||[]).slice().sort((a,b)=>b.name.localeCompare(a.name));
    if (!tasks.length) {const p=document.createElement('p');p.className='empty';p.textContent='還沒有任務';grid.appendChild(p);return;}
    for(const item of tasks) {
      const article=document.createElement('article');article.className='gallery-card';
      const button=document.createElement('button');button.type='button';button.className='gallery-preview';
      button.setAttribute('aria-label','放大 '+item.name+' 的圖片');
      const image=document.createElement('img');image.loading='lazy';image.alt=item.name+' 封面';
      image.src=api(item.cover_url || '/cover/'+encodeURIComponent(item.name));
      image.addEventListener('error',()=>{image.alt='圖片無法載入';});
      button.appendChild(image);article.appendChild(button);
      const info=document.createElement('div');info.className='gallery-info';
      const title=document.createElement('strong');title.textContent=item.name;
      const status=document.createElement('span');status.textContent=item.completed?'已完成':'未完成';
      info.append(title,status);article.appendChild(info);grid.appendChild(article);
      button.addEventListener('click',()=>{
        byId('lightbox-img').src=image.src;
        byId('lightbox').hidden=false;
      });
    }
  } catch(error) {grid.textContent='圖片列表取得失敗：'+error.message;}
}
function initLightbox() {
  const lightbox=byId('lightbox');
  if (!lightbox) return;
  const close=()=>{lightbox.hidden=true;byId('lightbox-img').removeAttribute('src');};
  lightbox.addEventListener('click',event=>{if(event.target===lightbox)close();});
  byId('lightbox-close')?.addEventListener('click',close);
  document.addEventListener('keydown',event=>{if(event.key==='Escape')close();});
}
async function initSettings() {
  const form=byId('settings-form');
  if(!form) return;
  const slider=byId('compression-level');
  slider.addEventListener('input',()=>setText(byId('level-label'),slider.value));
  const getValue = name => form.elements.namedItem(name);
  try {
    const data=await jsonFetch('/api/settings');
    const config=data.settings;
    getValue('compression_enabled').checked=config.compression_enabled;
    for(const name of ['compression_level','encoder_preset','segment_workers','max_parallel_downloads']) getValue(name).value=config[name];
    setText(byId('level-label'),config.compression_level);
    setText(byId('download-dir'),data.download_dir);
  } catch(error) { feedback('設定載入失敗：'+error.message,'warn','settings-feedback'); }
  form.addEventListener('submit',async event=>{
    event.preventDefault();
    const button=byId('save-settings');button.disabled=true;
    const payload={
      compression_enabled:getValue('compression_enabled').checked,
      compression_level:Number(getValue('compression_level').value),
      encoder_preset:getValue('encoder_preset').value,
      segment_workers:Number(getValue('segment_workers').value),
      max_parallel_downloads:Number(getValue('max_parallel_downloads').value)
    };
    try {
      const data=await jsonFetch('/api/settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      feedback(data.message || '設定已儲存','ok','settings-feedback');
    } catch(error) { feedback('儲存失敗：'+error.message,'warn','settings-feedback'); }
    finally {button.disabled=false;}
  });
}
// Userscript is already embedded in settings.html; no network fetch or missing asset dependency.
function initUserscriptSettings() {
  const field = byId('userscript-code');
  const serverField = byId('userscript-server');
  if (!field || !serverField) return;
  const hint = byId('userscript-feedback');
  const status = (message, isError=false) => {
    if (!hint) return;
    hint.hidden = false;
    hint.textContent = message;
    hint.className = 'submit-feedback ' + (isError ? 'warn' : 'ok');
  };
  const defaultServer = /^https?:$/.test(location.protocol) ? location.origin : 'https://j.xekro.top';
  try { serverField.value = localStorage.getItem('jable-userscript-server') || defaultServer; }
  catch { serverField.value = defaultServer; }
  // This value is inlined at build time, so settings still works when /assets/*.user.js is unavailable.
  const source = field.value;
  if (!source.includes('// ==UserScript==') || !source.includes('DEFAULT_SERVER_URL')) {
    status('內嵌腳本不完整，請重新覆蓋新版 frontend/settings.html。',true);
    return;
  }
  function updateCode() {
    let url = serverField.value.trim().replace(/\/+$/, '');
    if (!url) url = 'https://j.xekro.top';
    try {
      const parsed = new URL(url);
      if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) throw new Error('invalid URL');
      field.value = source.replace(
        /const DEFAULT_SERVER_URL = [^;]+;/,
        'const DEFAULT_SERVER_URL = ' + JSON.stringify(url) + ';'
      );
      try { localStorage.setItem('jable-userscript-server', url); } catch { /* storage unavailable */ }
      if (hint) hint.hidden = true;
    } catch {
      field.value = source;
      status('伺服器網址無效；請填寫 http(s) 網址後再複製。', true);
    }
  }
  serverField.addEventListener('input',updateCode);
  updateCode();
  byId('userscript-copy')?.addEventListener('click', async () => {
    if (!field.value) {status('腳本為空，請重新上傳設定頁。', true);return;}
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(field.value);
      else throw new Error('clipboard unavailable');
      status('已複製完整油猴腳本，貼到 Tampermonkey 新增腳本即可。');
    } catch {
      field.focus();field.select();
      try {
        if (!document.execCommand('copy')) throw new Error('copy blocked');
        status('已複製完整油猴腳本。');
      } catch {status('瀏覽器拒絕自動複製；程式碼已全選，請按 Ctrl+C。',true);}
    }
  });
  byId('userscript-download')?.addEventListener('click', () => {
    const blob = new Blob([field.value],{type:'text/javascript;charset=utf-8'});
    const address = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = address;
    link.download = 'JableVideoInfo.user.js';
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(()=>URL.revokeObjectURL(address), 3000);
    status('已下載目前網址設定的完整 .user.js 檔案。');
  });
}

document.addEventListener('DOMContentLoaded',()=>{
  initTaskPage();initTaskCoverZoom();initTaskActions();void initGallery();initLightbox();void initSettings();void initUserscriptSettings();
});
