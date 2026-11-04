"""Windows smoke/integration tests: python -m unittest -v tests.test_server."""
import unittest
from unittest.mock import patch
from pathlib import Path
import backend.api as server
from backend.api import app, finished, parse_video_text
from settings import ROOT, DEFAULTS, validate_settings


class ApiAndFrontendTest(unittest.TestCase):
    def setUp(self):
        self.client=app.test_client()

    def test_static_frontend_and_no_template_variables(self):
        for url in ('/','/gallery','/settings','/api-docs'):
            r=self.client.get(url)
            self.assertEqual(r.status_code,200,url)
            html=r.get_data(as_text=True)
            self.assertIn('/assets/app.js',html)
            self.assertNotIn('{{',html)
            self.assertNotIn('{%',html)
        self.assertEqual(self.client.get('/assets/app.js').status_code,200)
        self.assertEqual(self.client.get('/assets/style.css').status_code,200)
        self.assertEqual(self.client.get('/assets/config.js').status_code,200)
        self.assertEqual(self.client.get('/assets/JableVideoInfo.user.js').status_code,200)
        self.assertEqual(self.client.get('/login').status_code,404)
        self.assertEqual(self.client.get('/logout').status_code,404)

    def test_main_page_has_no_image_tags_and_keeps_groups(self):
        html=self.client.get('/').get_data(as_text=True)
        # A hidden lightbox may contain an <img> without src. Only loaded src URLs
        # would make the no-images-by-default mode send actual image requests.
        self.assertNotRegex(html, r'<img[^>]+\bsrc\s*=')
        self.assertIn('未完成／進行中',html)
        self.assertIn('已完成',html)
        self.assertIn('id="create-form"',html)
        self.assertIn('id="auto-refresh"',html)
        self.assertIn('id="refresh-now"',html)
        self.assertNotIn('api_add_task',html)
        self.assertEqual(self.client.get('/?images=1').status_code,200)
        self.assertNotIn('清除已完成紀錄',html)
        self.assertEqual(self.client.get('/clear/all').status_code,404)

    def test_frontend_calls_backend_json_routes_only(self):
        script=self.client.get('/assets/app.js').get_data(as_text=True)
        self.assertIn("jsonFetch('/api/download'",script)
        self.assertIn("jsonFetch('/api/tasks'",script)
        self.assertIn("jsonFetch('/api/settings'",script)
        self.assertNotIn('location.reload',script)
        self.assertIn('selectedText()',script)
        self.assertIn("field.value = ''",script)
        self.assertNotIn('url_for(',script)

    def test_json_post_and_historical_duplicate(self):
        sample={'archived-001':{'name':'archived-001','status':'下載完成',
          'hls_url':'https://example.org/old-signed.m3u8','image_url':'',
          'current':20,'total':20}}
        with patch.object(server,'download_progress',sample),\
             patch.object(server,'save_state') as save,\
             patch.object(server,'enqueue',return_value=True) as enqueue:
            response=self.client.post('/api/download',json={
                'name':'new-001','hls_url':'https://example.org/new.m3u8','image_url':''})
            self.assertEqual(response.status_code,201)
            self.assertTrue(response.json['added'])
            self.assertTrue(response.json['clear_input'])
            self.assertEqual(response.json['status_url'],'/api/tasks/new-001')
            save.assert_called_once();enqueue.assert_called_once_with('new-001')
            duplicate=self.client.post('/api/download',json={
                'name':'archived-001','hls_url':'https://example.org/replacement.m3u8'})
            self.assertEqual(duplicate.status_code,200)
            self.assertFalse(duplicate.json['added'])
            self.assertEqual(sample['archived-001']['hls_url'], 'https://example.org/old-signed.m3u8')
            self.assertEqual(enqueue.call_count,1)
            tasks=self.client.get('/api/tasks')
            self.assertEqual(tasks.status_code,200)
            self.assertEqual(tasks.json['total'],2)
            self.assertIn('cover_url',tasks.json['tasks'][0])
            self.assertNotIn('old-signed',tasks.get_data(as_text=True))
            self.assertTrue(self.client.get('/api/tasks/archived-001').json['task']['completed'])
            self.assertEqual(self.client.get('/api/tasks/not-there').status_code,404)

    def test_text_and_invalid_requests(self):
        with patch.object(server,'download_progress',{}),\
             patch.object(server,'save_state'),\
             patch.object(server,'enqueue',return_value=True):
            old='hls\nhttps://example.org/a.m3u8\nname\nlegacy-001'
            response=self.client.post('/api/tasks',data={'video_text':old})
            self.assertEqual(response.status_code,201)
            self.assertEqual(response.json['name'],'legacy-001')
            bad=self.client.post('/api/download',json={'video_text':'invalid'})
            self.assertEqual(bad.status_code,422)
            self.assertFalse(bad.json['clear_input'])
            self.assertEqual(self.client.options('/api/download').status_code,204)
            self.assertEqual(self.client.options('/api/settings').status_code,204)
            self.assertIn('PUT',self.client.options('/api/settings').headers['Access-Control-Allow-Methods'])
            self.assertEqual(self.client.get('/api/health').status_code,200)

    def test_settings_json_api(self):
        defaults=self.client.get('/api/settings')
        self.assertEqual(defaults.status_code,200)
        self.assertTrue(defaults.json['ok'])
        self.assertIn('settings',defaults.json)
        with patch.object(server,'save_settings',return_value=dict(DEFAULTS)) as save,\
             patch.object(server,'start_workers'):
            response=self.client.put('/api/settings',json={'compression_level':5})
            self.assertEqual(response.status_code,200)
            save.assert_called_once_with({**defaults.json['settings'], 'compression_level':5})
        bad=self.client.put('/api/settings',json=['not-object'])
        self.assertEqual(bad.status_code,422)

    def test_retry_api_does_not_reset_completed_history(self):
        sample={'finished-001':{'name':'finished-001','status':'已完成',
            'hls_url':'https://example.org/x.m3u8','current':10,'total':10}}
        with patch.object(server,'download_progress',sample),\
             patch.object(server,'video_file',return_value=Path(__file__)):
            response=self.client.post('/api/retry/finished-001')
            self.assertEqual(response.status_code,200)
            self.assertFalse(response.json['queued'])
        self.assertEqual(self.client.post('/api/retry/not-there').status_code,404)

    def test_url_and_setting_validation(self):
        self.assertTrue(finished({'status':'已完成'}))
        self.assertTrue(finished({'status':'下載完成（H.265，第 5 級）'}))
        self.assertFalse(finished({'status':'錯誤: timeout'}))
        self.assertEqual(parse_video_text('hls\nhttps://example.org/a.m3u8\nname\ntest_001')[1],'test_001')
        self.assertEqual(validate_settings({})['compression_level'],5)
        with self.assertRaises(ValueError):
            validate_settings({'compression_level':11})


if __name__=='__main__':
    unittest.main()
