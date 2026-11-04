"""M3U8 expiry and offline TS recovery regression tests."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import download
import backend.api as server


class FakePlaylist:
    def __init__(self, count=3, sequence=0):
        self.segments = [SimpleNamespace(uri=f'{i}.ts', key=None, init_section=None)
                         for i in range(count)]
        self.media_sequence = sequence
        self.keys = []
        self.serialized = '#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-MEDIA-SEQUENCE:' + str(sequence) + '\n' + ''.join(f'#EXTINF:4,\n{i}.ts\n' for i in range(count)) + '#EXT-X-ENDLIST\n'

    def dumps(self):
        return self.serialized


class PlaylistCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.name = 'movie-001'
        self.url = 'https://media.example.com/video/file.m3u8'
        self.state = {self.name: {'status': '錯誤: expired', 'hls_url': self.url, 'total': 1}}
        self.lock = threading.RLock()
        self.options = {'segment_workers': 2}

    def save(self):
        pass

    def _cache(self, count=3, sequence=0):
        play = FakePlaylist(count, sequence)
        with patch.object(download, '_playlist', return_value=(play, self.url)):
            meta = download.cache_playlist(self.name, self.url, self.root)
        return meta

    def test_manifest_written_before_ts_download(self):
        meta = self._cache()
        self.assertEqual(meta['expected_segments'], 3)
        folder = self.root / 'uncompressed' / self.name
        self.assertTrue((folder / 'playlist.m3u8').exists())
        self.assertTrue((folder / 'playlist_source.json').exists())
        self.assertFalse(list(folder.glob('*.ts')))
        data = json.loads((folder / 'playlist_source.json').read_text())
        self.assertEqual(data['playlist_url'], self.url)

    def test_offline_remux_uses_manifest_without_network(self):
        self._cache()
        folder = self.root / 'uncompressed' / self.name
        for i in range(3):
            (folder / f'{i:05d}.ts').write_bytes(b'TS' + bytes([i]))
        output = folder / 'movie-001.mp4'
        def fake_merge(files, path, progress_callback=None, total_seconds=None):
            self.assertEqual(len(files), 3)
            Path(path).write_bytes(b'MP4')
        with patch.object(download, '_playlist', side_effect=AssertionError('network should not be used')), \
             patch.object(download, 'merge_files_to_mp4', side_effect=fake_merge):
            download._make_raw_video(self.url, self.name, self.state, self.save,
                                    self.lock, self.options, self.root, output)
        self.assertEqual(output.read_bytes(), b'MP4')
        self.assertEqual(self.state[self.name]['expected_segments'], 3)
        self.assertFalse(list(folder.glob('*.ts')))

    def test_incomplete_segments_are_preserved_on_failed_download(self):
        folder = self.root / 'uncompressed' / self.name
        folder.mkdir(parents=True)
        (folder / '00000.ts').write_bytes(b'existing-segment')
        self.state[self.name]['expected_segments'] = 4
        with patch.object(download, '_playlist', side_effect=RuntimeError('410 Gone')):
            with self.assertRaisesRegex(RuntimeError, '410'):
                download._make_raw_video(self.url, self.name, self.state, self.save,
                                        self.lock, self.options, self.root, folder / 'movie-001.mp4')
        self.assertEqual((folder / '00000.ts').read_bytes(), b'existing-segment')

    def test_new_source_different_segment_count_rejected_without_deleting_ts(self):
        self._cache(count=3)
        folder = self.root / 'uncompressed' / self.name
        (folder / '00000.ts').write_bytes(b'old')
        with self.assertRaisesRegex(RuntimeError, '分段數'):
            download.save_playlist_snapshot(self.name, FakePlaylist(4), self.url,
                                            self.url, self.root)
        self.assertEqual((folder / '00000.ts').read_bytes(), b'old')
        self.assertEqual(json.loads((folder / 'playlist_source.json').read_text())['expected_segments'], 3)

    def test_segments_status_no_network(self):
        self._cache(count=4)
        folder = self.root / 'uncompressed' / self.name
        (folder / '00000.ts').write_bytes(b'a')
        (folder / '00002.ts').write_bytes(b'b')
        details = download.segment_cache_status(self.name, self.root)
        self.assertEqual(details['expected_segments'], 4)
        self.assertEqual(details['cached_segments'], 2)
        self.assertFalse(details['complete'])
        self.assertEqual(details['missing_preview'], [1, 3])

    def test_api_exposes_cached_segment_status_and_source_replacement(self):
        info = {'movie-001': {'name':'movie-001', 'status':'錯誤: expired',
                              'hls_url': self.url, 'expected_segments':3}}
        with patch.object(server, 'download_progress', info), \
             patch.object(server, 'get_download_dir', return_value=self.root), \
             patch.object(server, 'save_state'), \
             patch.object(server, '_prefetch_playlist_async', return_value=True), \
             patch.object(server, 'enqueue', return_value=True):
            client = server.app.test_client()
            st = client.get('/api/tasks/movie-001/segments')
            self.assertEqual(st.status_code, 200)
            self.assertEqual(st.json['expected_segments'], 3)
            res = client.post('/api/tasks/movie-001/source', json={
                'hls_url': 'https://new-media.example.com/video/new.m3u8'})
            self.assertEqual(res.status_code, 200)
            self.assertEqual(info['movie-001']['expected_segments'], 3)
            self.assertEqual(info['movie-001']['hls_url'], 'https://new-media.example.com/video/new.m3u8')


if __name__ == '__main__':
    unittest.main()
