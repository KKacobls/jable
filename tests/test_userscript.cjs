// Node-only smoke test: node tests/test_userscript.cjs
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '..', 'frontend', 'JableVideoInfo.user.js'), 'utf8');
class Element {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.listeners = {}; this.className = '';
    this.textContent = ''; this.id = ''; this.style = {};
    this.classList = { toggle: () => {}, contains: () => false };
  }
  append(...children) { this.children.push(...children); }
  appendChild(child) { this.children.push(child); }
  addEventListener(event, fn) { this.listeners[event] = fn; }
  async click() { await this.listeners.click?.(); }
}
const body = new Element('body');
const head = new Element('head');
const metadata = {
  'link[rel="canonical"]': {getAttribute: () => 'https://jable.tv/videos/demo-001/'},
  'meta[property="og:image"]': {getAttribute: () => 'https://example.net/cover.jpg'}
};
const calls = [];
let clipboard = '';
const scriptNodes = [{textContent: "var hlsUrl = 'https://example.net/source.m3u8?token=abc';"}];
const context = {
  URL,
  location: {href: 'https://jable.tv/videos/demo-001/'},
  window: {isSecureContext: false},
  navigator: {},
  document: {
    readyState: 'complete', body, head,
    createElement: tag => new Element(tag),
    getElementById: id => body.children.find(el => el.id === id) || null,
    querySelectorAll: selector => selector === 'script' ? scriptNodes : [],
    querySelector: selector => metadata[selector] || null
  },
  GM_getValue: (key, fallback) => fallback,
  GM_setValue: () => {},
  GM_setClipboard: text => {clipboard = text;},
  GM_registerMenuCommand: () => {},
  GM_xmlhttpRequest: args => {
    calls.push(args);
    if (args.method === 'GET') args.onload({status: 200, responseText: JSON.stringify({ok:true,task:{completed:true,file_exists:false}})});
    else args.onload({status: 200, responseText: JSON.stringify({ok:true,added:false})});
  },
  setTimeout: fn => {fn();return 0;},
  prompt: () => null,
  alert: () => {},
  console
};
vm.runInNewContext(source, context, {filename:'JableVideoInfo.user.js'});
const panel = body.children.find(el => el.id === 'jable-video-sync-panel');
assert(panel, '應建立面板');
const status = panel.children.find(el => el.className === 'jable-sync-status');
const actions = panel.children.find(el => el.className === 'jable-sync-actions');
(async () => {
  await Promise.resolve();
  assert(status.textContent.includes('已下載過'), '應顯示歷史紀錄');
  assert(calls.some(call => call.method === 'GET' && call.url.endsWith('/api/tasks/demo-001')));
  await actions.children[0].click();
  const copied = JSON.parse(clipboard);
  assert.equal(copied.name, 'demo-001');
  assert.equal(copied.hls_url, 'https://example.net/source.m3u8?token=abc');
  await actions.children[1].click();
  const post = calls.find(call => call.method === 'POST');
  assert.equal(post.url, 'https://j.xekro.top/api/download');
  assert.equal(JSON.parse(post.data).name, 'demo-001');
  assert.equal(JSON.parse(post.data).image_url, 'https://example.net/cover.jpg');
  console.log('PASS: 自動查詢、保留複製 JSON、POST 下載三項功能');
})().catch(error => {console.error(error);process.exitCode = 1;});
