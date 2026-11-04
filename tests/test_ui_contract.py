"""Dependency-free UI/Tampermonkey contract checks: python -m unittest -v tests.test_ui_contract."""
from pathlib import Path
import re
import html
import unittest
from html.parser import HTMLParser

ROOT = Path(__file__).resolve().parents[1]


class MarkupInspector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.img_tags = 0

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if d.get('id'):
            self.ids.add(d['id'])
        if tag == 'img':
            self.img_tags += 1


class UiContractTest(unittest.TestCase):
    def test_home_image_opt_in_and_no_page_reload(self):
        index = (ROOT / 'frontend/index.html').read_text(encoding='utf-8')
        js = (ROOT / 'frontend/app.js').read_text(encoding='utf-8')
        parsed = MarkupInspector(); parsed.feed(index)
        self.assertEqual(parsed.img_tags, 0, '預設首頁不能直接請求圖片')
        self.assertIn('image-mode-link', parsed.ids)
        self.assertIn('SHOW_INLINE_IMAGES', js)
        self.assertIn('location.search.length > 1', js)
        self.assertIn("'/cover/'", js)
        self.assertIn('ensureTaskCover(card, item, position < 12)',js)
        self.assertIn('imageModeEnabled = !imageModeEnabled',js)
        self.assertIn('applyImageMode()',js)
        self.assertIn('image-debug',parsed.ids)
        self.assertIn('item.cover_url',js)
        self.assertNotIn('location.reload(', js)

    def test_settings_userscript_copier(self):
        html = (ROOT / 'frontend/settings.html').read_text(encoding='utf-8')
        js = (ROOT / 'frontend/app.js').read_text(encoding='utf-8')
        parsed = MarkupInspector(); parsed.feed(html)
        for element in ('userscript-server', 'userscript-code', 'userscript-copy'):
            self.assertIn(element, parsed.ids)
        self.assertIn('initUserscriptSettings()', js)
        self.assertNotIn("fetch('/assets/JableVideoInfo.user.js'", js)
        self.assertIn("const source = field.value;", js)
        self.assertIn('userscript-download', parsed.ids)
        embedded = re.search(r'<textarea id="userscript-code"[^>]*>(.*?)</textarea>', html, re.S)
        self.assertIsNotNone(embedded)
        source = (ROOT / 'frontend/JableVideoInfo.user.js').read_text(encoding='utf-8')
        self.assertEqual(__import__('html').unescape(embedded.group(1)), source)
        self.assertIn('navigator.clipboard', js)

    def test_userscript_endpoints_and_paste_contract(self):
        s = (ROOT / 'frontend/JableVideoInfo.user.js').read_text(encoding='utf-8')
        backend = (ROOT / 'backend/api.py').read_text(encoding='utf-8')
        for requirement in ('@grant        GM_xmlhttpRequest', '@grant        GM_setClipboard',
                            '@match        https://jable.tv/*', '@connect      *',
                            'const DEFAULT_SERVER_URL = "https://j.xekro.top";',
                            '/api/tasks/', '/api/download', 'GM_registerMenuCommand'):
            self.assertIn(requirement, s)
        self.assertIn("@app.route('/api/download'", backend)
        self.assertIn("@app.route('/api/tasks/<name>'", backend)
        self.assertIn("Access-Control-Allow-Origin", backend)
        self.assertIn("return 'existing'", backend, '重複下載必須保留既有紀錄')
        self.assertFalse(re.search(r'url_for\(', (ROOT / 'frontend/index.html').read_text(encoding='utf-8')))


if __name__ == '__main__':
    unittest.main()
