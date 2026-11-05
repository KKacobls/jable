"""Manual offline compression from existing MP4 or explicitly confirmed TS.
Run: python -m unittest -v tests.test_manual_compression
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import backend.api as server
from settings import video_locations as actual_video_locations, existing_video_locations as actual_existing_video_locations


class ManualCompressionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.name = 'pred-899'
        self.record = {self.name: {
            'name': self.name,
            'status': '錯誤: 410 Client Error: Gone',
            'hls_url': 'https://example.com/expired.m3u8',
            'current': 1, 'total': 1,
            'download_current': 1, 'download_total': 1,
            'compression_state': 'pending', 'compression_percent': 0,
        }}
        self.mock_state = patch.object(server, 'download_progress', self.record)
        self.mock_state.start()
        self.addCleanup(self.mock_state.stop)
        self.mock_root = patch.object(server, 'get_download_dir', return_value=self.root)
        self.mock_root.start()
        self.addCleanup(self.mock_root.stop)
        # video_locations is imported from settings and resolves its own default
        # download root, so patch it explicitly to keep tests in the temp folder.
        self.mock_locations = patch.object(server, 'video_locations',
            side_effect=lambda name: actual_video_locations(name, self.root))
        self.mock_locations.start()
        self.addCleanup(self.mock_locations.stop)
        self.mock_existing = patch.object(server, 'existing_video_locations',
            side_effect=lambda name: actual_existing_video_locations(name, self.root))
        self.mock_existing.start()
        self.addCleanup(self.mock_existing.stop)
        self.mock_save = patch.object(server, 'save_state')
        self.mock_save.start()
        self.addCleanup(self.mock_save.stop)
        server.manual_compressions.clear()
        self.addCleanup(server.manual_compressions.clear)
        self.client = server.app.test_client()

    def mp4(self):
        path = self.root / 'uncompressed' / self.name / (self.name+'.mp4')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'sample-mp4')
        return path

    def ts(self, indexes=(0, 1, 2)):
        folder = self.root / self.name
        folder.mkdir(parents=True, exist_ok=True)
        for i in indexes:
            (folder / f'{i:05d}.ts').write_bytes(b'sample-ts')
        return folder

    def test_stale_100_percent_is_not_mp4(self):
        with patch.object(server, '_program', return_value='ffmpeg'):
            res = self.client.post('/api/tasks/pred-899/compression/start')
        self.assertEqual(res.status_code, 409)
        self.assertIn('沒有可用 MP4 或 TS', res.json['message'])
        self.assertFalse(self.client.get('/api/tasks/pred-899').json['task']['can_start_compression'])

    def test_offline_mp4_can_be_compressed_with_expired_hls(self):
        self.mp4()
        with patch.object(server, '_program', return_value='ffmpeg'), \
             patch.object(server, '_codec', return_value='h264'), \
             patch.object(server.threading, 'Thread') as thread:
            res = self.client.post('/api/tasks/pred-899/compression/start')
        self.assertEqual(res.status_code, 202)
        self.assertTrue(res.json['queued'])
        thread.return_value.start.assert_called_once()
        self.assertIn(self.name, server.manual_compressions)

        def fake_encoder(raw, callback, **kwargs):
            self.assertEqual(raw, self.root / 'uncompressed' / self.name / (self.name+'.mp4'))
            self.assertEqual(kwargs['task_name'], self.name)
            callback(75)
            target = kwargs['output_path']
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(b'compressed')
            return True

        with patch.object(server, '_codec', return_value='h264'), \
             patch.object(server, 'encode_h265', side_effect=fake_encoder), \
             patch.object(server, 'get_settings', return_value={'compression_level':5,'encoder_preset':'fast'}), \
             patch.object(server, 'merge_files_to_mp4', side_effect=AssertionError('No remux for MP4')):
            server._manual_compression_worker(self.name, None, False)
        self.assertEqual(self.record[self.name]['compression_state'], 'completed')
        self.assertEqual(self.record[self.name]['compression_percent'], 100)
        self.assertTrue(self.mp4().exists())
        self.assertEqual((self.root/'compressed'/self.name/(self.name+'.mp4')).read_bytes(), b'compressed')
        self.assertNotIn(self.name, server.manual_compressions)

    def test_unknown_ts_requires_explicit_confirmation(self):
        folder = self.ts()
        self.assertTrue(self.client.get('/api/tasks/pred-899').json['task']['can_start_compression'])
        with patch.object(server, '_program', return_value='ffmpeg'), \
             patch.object(server.threading, 'Thread') as thread:
            no = self.client.post('/api/tasks/pred-899/compression/start')
            self.assertEqual(no.status_code, 409)
            self.assertTrue(no.json['needs_segment_confirmation'])
            self.assertEqual(no.json['available_segments'], 3)
            self.assertNotIn(self.name, server.manual_compressions)
            ok = self.client.post('/api/tasks/pred-899/compression/start', json={'confirm_segments':3})
        self.assertEqual(ok.status_code, 202)
        thread.return_value.start.assert_called_once()
        self.assertEqual(len(list(folder.glob('*.ts'))), 3)

    def test_ts_holes_prevent_merging_or_deletion(self):
        folder = self.ts(indexes=(0,2,3))
        with patch.object(server, '_program', return_value='ffmpeg'):
            res = self.client.post('/api/tasks/pred-899/compression/start', json={
                'confirm_segments':3, 'delete_ts_after_merge':True})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(len(list(folder.glob('*.ts'))), 3)
        self.assertNotIn(self.name, server.manual_compressions)

    def test_confirmed_offline_merge_preserves_ts_by_default(self):
        folder = self.ts()
        def fake_merge(files, output, progress_callback):
            self.assertEqual(len(files), 3)
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_bytes(b'merged')
            progress_callback(100, 8.0)
        def fake_encoder(raw, callback, **kwargs):
            output=kwargs['output_path']
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_bytes(b'compressed')
            callback(99)
            return True
        with patch.object(server, '_codec', return_value='h264'), \
             patch.object(server, 'merge_files_to_mp4', side_effect=fake_merge), \
             patch.object(server, 'encode_h265', side_effect=fake_encoder), \
             patch.object(server, 'get_settings', return_value={'compression_level':5,'encoder_preset':'fast'}):
            server._manual_compression_worker(self.name, 3, False)
        self.assertEqual(len(list(folder.glob('*.ts'))), 3)
        self.assertTrue((self.root/'uncompressed'/self.name/(self.name+'.mp4')).is_file())
        self.assertTrue((self.root/'compressed'/self.name/(self.name+'.mp4')).is_file())
        self.assertEqual(self.record[self.name]['compression_state'], 'completed')
        self.assertEqual(self.record[self.name]['download_state'], 'completed')

    def test_can_delete_ts_after_explicit_confirmed_remux(self):
        folder = self.ts()
        def fake_merge(files, output, progress_callback):
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_bytes(b'merged')
            progress_callback(100, 10.0)
        with patch.object(server, '_codec', return_value='hevc'), \
             patch.object(server, 'merge_files_to_mp4', side_effect=fake_merge):
            server._manual_compression_worker(self.name, 3, True)
        self.assertFalse(list(folder.glob('*.ts')))
        self.assertTrue((self.root/'compressed'/self.name/(self.name+'.mp4')).is_file())

    def test_active_manual_compression_rejects_retry_and_delete(self):
        self.mp4()
        server.manual_compressions.add(self.name)
        with patch.object(server, 'encoder_active', return_value=False):
            self.assertEqual(self.client.post('/api/tasks/pred-899/compression/start').status_code, 409)
            self.assertFalse(self.client.post('/api/retry/pred-899').json['queued'])
            self.assertEqual(self.client.delete('/api/tasks/pred-899', json={'confirm':self.name}).status_code, 409)


if __name__ == '__main__':
    unittest.main()
