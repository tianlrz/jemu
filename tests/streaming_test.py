"""Service robustness: a stalled media stream must not block other requests; bad input gets a JSON error."""
from pathlib import Path
import json
import re
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


class StalledStream(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); root = Path(self.temp.name)
        self.env = {**os.environ, 'JEMU_HOME': str(root / 'home')}
        source = root / 'src'; source.mkdir()
        self.video = os.urandom(8 * 1024 * 1024)
        (source / 'video.mp4').write_bytes(self.video)
        (source / 'index.html').write_text('<!doctype html><video src="video.mp4"></video>', encoding='utf8')
        self.url = self.cli('start', '--port', '0')['url']
        self.deck = self.cli('decks', 'import', str(source), '--entry', 'index.html', '--name', 'stream')['id']

    def tearDown(self):
        self.cli('stop', '--force')
        # On Windows the stopping service may still hold .runtime/error.log for a moment.
        for _ in range(50):
            try:
                self.temp.cleanup(); return
            except PermissionError:
                time.sleep(0.1)
        self.temp.cleanup()

    def cli(self, *args):
        run = subprocess.run([sys.executable, str(ROOT / 'jemu.py'), '--json', *args], capture_output=True, text=True, encoding='utf8', env=self.env, timeout=60)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        return json.loads(run.stdout)['data']

    def test_health_answers_while_a_stream_is_not_read(self):
        port = int(self.url.rsplit(':', 1)[1])
        stalled = socket.create_connection(('127.0.0.1', port))
        try:
            stalled.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
            stalled.sendall(f'GET /documents/{self.deck}/video.mp4 HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nRange: bytes=0-\r\n\r\n'.encode())
            time.sleep(0.5)  # the server is now blocked writing to this client
            started = time.monotonic()
            with urllib.request.urlopen(self.url + '/api/health', timeout=5) as response:
                self.assertTrue(json.load(response)['ok'])
            self.assertLess(time.monotonic() - started, 2)
        finally:
            stalled.close()
        with urllib.request.urlopen(f'{self.url}/documents/{self.deck}/video.mp4', timeout=20) as response:
            self.assertEqual(response.read(), self.video)
        request = urllib.request.Request(f'{self.url}/documents/{self.deck}/video.mp4', headers={'Range': 'bytes=10-19'})
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 206); self.assertEqual(response.read(), self.video[10:20])

    def test_malformed_request_gets_an_error_not_a_dropped_connection(self):
        token = re.search(r"DECKROOM_TOKEN='([^']+)'", urllib.request.urlopen(self.url + '/').read().decode()).group(1)
        for body, status in [({'files': 'x', 'entry': 1}, 500), ({}, 400)]:
            request = urllib.request.Request(self.url + '/api/import', data=json.dumps(body).encode(), headers={'X-Deckroom-Token': token, 'Content-Type': 'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(request, timeout=5)
            self.assertEqual(caught.exception.code, status); self.assertIn('error', json.loads(caught.exception.read()))
        with urllib.request.urlopen(self.url + '/api/health', timeout=5) as response:self.assertTrue(json.load(response)['ok'])


if __name__ == '__main__':
    unittest.main()
