"""Regression tests for retaining both video variants and cached segment resumes."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import download
from settings import get_storage_dirs, video_locations


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.name = 'demo-001'
        self.state = {self.name: {'name': self.name, 'status': '排隊中',
                                  'hls_url': 'https://example.org/demo.m3u8',
                                  'current': 0, 'total': 1}}
        self.lock = threading.RLock()
        self.writes = []
        self.options = {'compression_enabled': True, 'compression_level': 5,
                        'encoder_preset': 'fast', 'segment_workers': 2}

    def save(self):
        self.writes.append(dict(self.state[self.name]))

    def fake_codec(self, path):
        path = Path(path)
        if not path.is_file() or not path.stat().st_size:
            return ''
        return 'hevc' if 'compressed' in path.parts else 'h264'

    def fake_download(self, hls, name, state, saver, lock, options, root, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'original-video')

    def fake_encode(self, input_path, progress_callback=None, level=5, preset='fast', output_path=None, task_name=None):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b'smaller')
        return True

    def run_job(self, options=None):
        return download.download_video('https://example.org/demo.m3u8',self.name,'',
              self.state,self.save,self.lock,options=options or self.options,download_dir=self.root)

    def test_keeps_original_and_compressed(self):
        with patch.object(download,'_program',return_value='/usr/bin/ffmpeg'), \
             patch.object(download,'_codec',side_effect=self.fake_codec), \
             patch.object(download,'_make_raw_video',side_effect=self.fake_download) as fetched, \
             patch.object(download,'encode_h265',side_effect=self.fake_encode) as encoded:
            self.run_job()
        paths=video_locations(self.name,self.root)
        self.assertEqual(paths['uncompressed'].read_bytes(),b'original-video')
        self.assertEqual(paths['compressed'].read_bytes(),b'smaller')
        self.assertFalse(paths['legacy'].exists())
        self.assertIn('下載完成',self.state[self.name]['status'])
        self.assertEqual(fetched.call_count,1)
        self.assertEqual(encoded.call_count,1)

    def test_disabled_compression_has_only_uncompressed(self):
        opts={**self.options, 'compression_enabled':False}
        with patch.object(download,'_program',return_value='ffmpeg'), \
             patch.object(download,'_codec',side_effect=self.fake_codec), \
             patch.object(download,'_make_raw_video',side_effect=self.fake_download), \
             patch.object(download,'encode_h265') as encoded:
            self.run_job(opts)
            encoded.assert_not_called()
        paths=video_locations(self.name,self.root)
        self.assertTrue(paths['uncompressed'].is_file())
        self.assertFalse(paths['compressed'].exists())

    def test_failed_encode_preserves_original_then_retry_without_download(self):
        with patch.object(download,'_program',return_value='ffmpeg'), \
             patch.object(download,'_codec',side_effect=self.fake_codec), \
             patch.object(download,'_make_raw_video',side_effect=self.fake_download) as fetched, \
             patch.object(download,'encode_h265',side_effect=RuntimeError('encoder failed')):
            self.run_job()
            self.assertTrue(self.state[self.name]['status'].startswith('錯誤:'))
            self.assertEqual(video_locations(self.name,self.root)['uncompressed'].read_bytes(),b'original-video')
        with patch.object(download,'_program',return_value='ffmpeg'), \
             patch.object(download,'_codec',side_effect=self.fake_codec), \
             patch.object(download,'_make_raw_video') as redownload, \
             patch.object(download,'encode_h265',side_effect=self.fake_encode):
            self.run_job()
            redownload.assert_not_called()
        self.assertTrue(video_locations(self.name,self.root)['compressed'].is_file())
        self.assertIn('下載完成',self.state[self.name]['status'])

    def test_legacy_mp4_is_copied_without_deleting_source(self):
        legacy=video_locations(self.name,self.root)['legacy']
        legacy.parent.mkdir(parents=True,exist_ok=True)
        legacy.write_bytes(b'legacy-video')
        with patch.object(download,'_program',return_value='ffmpeg'), \
             patch.object(download,'_codec',side_effect=self.fake_codec), \
             patch.object(download,'_make_raw_video') as fetched, \
             patch.object(download,'encode_h265',side_effect=self.fake_encode):
            self.run_job()
            fetched.assert_not_called()
        self.assertEqual(legacy.read_bytes(),b'legacy-video')
        self.assertEqual(video_locations(self.name,self.root)['uncompressed'].read_bytes(),b'legacy-video')

    def test_missing_tools_do_not_touch_source(self):
        original=video_locations(self.name,self.root)['uncompressed']
        original.parent.mkdir(parents=True,exist_ok=True)
        original.write_bytes(b'source')
        with patch.object(download,'_program',return_value=None), \
             patch.object(download,'_make_raw_video') as fetched:
            self.run_job()
            fetched.assert_not_called()
        self.assertEqual(original.read_bytes(),b'source')
        self.assertIn('FFmpeg',self.state[self.name]['status'])

    def test_offline_remux_reuses_verified_segments(self):
        folder=self.root/'uncompressed'/self.name
        folder.mkdir(parents=True)
        (folder/'00000.ts').write_bytes(b'segment-0')
        (folder/'00001.ts').write_bytes(b'segment-1')
        self.state[self.name]['expected_segments']=2
        output=folder/(self.name+'.mp4')
        def remux(ts, dest, progress_callback=None, total_seconds=None):
            self.assertEqual(len(ts),2)
            Path(dest).write_bytes(b'valid-remux')
        with patch.object(download,'_playlist',side_effect=AssertionError('should not fetch playlist')), \
             patch.object(download,'merge_files_to_mp4',side_effect=remux):
            download._make_raw_video('https://example.org/expired.m3u8',self.name,
                                     self.state,self.save,self.lock,self.options,self.root,output)
        self.assertEqual(output.read_bytes(),b'valid-remux')
        self.assertFalse((folder/'00000.ts').exists())
        self.assertFalse((folder/'00001.ts').exists())


if __name__ == '__main__':
    unittest.main()
