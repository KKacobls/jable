"""Dual progress, compression controls and reversible trash regression tests."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import backend.api as server


class DualProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.name = 'test-001'
        self.progress = {self.name: {
            'name': self.name, 'hls_url': 'https://example.com/master.m3u8',
            'status': '正在 CPU H.265 壓縮...', 'current': 25, 'total': 100,
            'download_current': 10, 'download_total': 10, 'download_state':'completed',
            'compression_percent': 25, 'compression_state': 'running'}}
        self.client = server.app.test_client()
        self.state = patch.object(server, 'download_progress', self.progress)
        self.state.start()
        self.addCleanup(self.state.stop)

    def test_two_independent_progress_fields(self):
        with patch.object(server, 'get_download_dir', return_value=self.root), \
             patch.object(server, 'save_state'):
            res = self.client.get('/api/tasks')
        self.assertEqual(res.status_code, 200)
        item = res.json['tasks'][0]
        self.assertEqual(item['download_current'], 10)
        self.assertEqual(item['download_total'], 10)
        self.assertEqual(item['compression_percent'], 25)
        self.assertEqual(item['compression_state'], 'running')

    def test_pause_resume_requires_live_process(self):
        with patch.object(server,'encoder_control',side_effect=['paused','running']) as control, \
             patch.object(server,'save_state'):
            paused=self.client.post('/api/tasks/test-001/compression/pause')
            self.assertEqual(paused.status_code,200)
            self.assertEqual(self.progress[self.name]['compression_state'],'paused')
            resumed=self.client.post('/api/tasks/test-001/compression/resume')
            self.assertEqual(resumed.status_code,200)
            self.assertEqual(self.progress[self.name]['compression_state'],'running')
        self.assertEqual(control.call_count,2)

    def test_trash_requires_exact_confirmation_and_restores_safely(self):
        source=self.root/'uncompressed'/self.name
        source.mkdir(parents=True)
        (source/(self.name+'.mp4')).write_bytes(b'mp4-not-real')
        with patch.object(server,'get_download_dir',return_value=self.root), \
             patch.object(server,'IMAGE_DIR',self.root/'images'), \
             patch.object(server,'save_state'), \
             patch.object(server,'encoder_active',return_value=False):
            bad=self.client.delete('/api/tasks/test-001',json={'confirm':'wrong'})
            self.assertEqual(bad.status_code,422)
            self.assertTrue(source.is_dir())
            ok=self.client.delete('/api/tasks/test-001',json={'confirm':self.name})
        self.assertEqual(ok.status_code,200)
        self.assertNotIn(self.name,self.progress)
        self.assertFalse(source.exists())
        archived=list((self.root/'.trash').glob('test-001-*/uncompressed/test-001/test-001.mp4'))
        self.assertEqual(len(archived),1)

    def test_running_tasks_are_not_deletable(self):
        with patch.object(server,'get_download_dir',return_value=self.root), \
             patch.object(server,'encoder_active',return_value=True):
            res=self.client.delete('/api/tasks/test-001',json={'confirm':self.name})
        self.assertEqual(res.status_code,409)
        self.assertIn(self.name,self.progress)


if __name__=='__main__':
    unittest.main()
