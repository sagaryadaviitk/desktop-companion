"""Exercise the exported app through real HTTP with synthetic local dependencies."""

import http.client
import json
import os
from pathlib import Path
import re
import select
import subprocess
import sys
import tempfile
import unittest
import zipfile

from desktop_companion.access import RemoteSettings
from desktop_companion.manage import bundle
from test_companion import CHAT, make_history


class ExportedAppEndToEndTests(unittest.TestCase):
    def test_exported_server_pairs_queues_once_and_revokes(self):
        with tempfile.TemporaryDirectory(prefix='companion-e2e-') as temporary:
            sandbox = Path(temporary)
            archive = sandbox / 'source.zip'
            bundle(archive)
            with zipfile.ZipFile(archive) as source:
                source.extractall(sandbox / 'extracted')
            root = sandbox / 'extracted' / 'desktop_companion'
            self.assertFalse((root / '.state').exists())
            self.assertFalse((root / 'config.local.json').exists())

            fake_home = sandbox / 'home'
            history_home = fake_home / '.codex'
            history_home.mkdir(parents=True)
            make_history(history_home)
            fake_bin = sandbox / 'bin'
            fake_bin.mkdir()
            calls = sandbox / 'queue-calls.jsonl'
            cli = fake_bin / 'codex'
            cli.write_text('''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if args == ['queue', '--help']:
    print('--thread --message')
elif len(args) == 5 and args[0] == 'queue' and args[1] == '--thread' and args[3] == '--message':
    with open(os.environ['COMPANION_TEST_CALL_LOG'], 'a') as log:
        log.write(json.dumps({'thread': args[2], 'message': args[4]}) + '\\n')
    print('Queued message fake-queue-1 for thread ' + args[2] + '.')
else:
    sys.exit(1)
''')
            cli.chmod(0o700)
            env = dict(os.environ)
            env.update(HOME=str(fake_home), CODEX_HOME=str(history_home),
                       PATH=str(fake_bin) + os.pathsep + '/usr/bin:/bin',
                       COMPANION_TEST_CALL_LOG=str(calls), PYTHONDONTWRITEBYTECODE='1')
            env.pop('PYTHONPATH', None)
            process = subprocess.Popen(
                [sys.executable, '-u', str(root / 'companion.py'), '--port', '0',
                 '--remote-port', '0', '--codex-home', str(history_home)],
                cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True,
            )
            try:
                ready, _, _ = select.select([process.stdout], [], [], 15)
                self.assertTrue(ready, 'Exported server did not start within 15 seconds')
                line = process.stdout.readline()
                match = re.search(r'http://127\.0\.0\.1:(\d+)', line)
                self.assertIsNotNone(match, 'Server startup did not identify its local port')
                local_port = int(match.group(1))

                def request(port, path, method='GET', payload=None, headers=None):
                    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
                    try:
                        conn.request(method, path, body=json.dumps(payload) if payload is not None else None,
                                     headers=headers or {})
                        response = conn.getresponse()
                        return response.status, dict(response.getheaders()), response.read()
                    finally:
                        conn.close()

                status, _, html = request(local_port, '/')
                self.assertEqual(status, 200)
                self.assertIn(b'<html', html.lower())
                self.assertEqual(request(local_port, '/app.js')[0], 200)
                local_status = json.loads(request(local_port, '/api/status')[2])
                local_csrf = local_status['csrf']
                gateway_port = json.loads(request(local_port, '/api/remote/status')[2])['gateway_port']
                self.assertTrue(json.loads(request(gateway_port, '/healthz')[2])['authentication_required'])

                # Simulate the trusted local tunnel's origin forwarding. This tests
                # HTTP authentication, not Cloudflare, TLS or a physical phone.
                RemoteSettings(root / '.state' / 'remote.json').set_origin('https://phone.example')
                gateway_headers = {'Host': 'phone.example', 'Origin': 'https://phone.example',
                                   'X-Forwarded-Proto': 'https'}
                self.assertEqual(request(gateway_port, '/api/chats', headers=gateway_headers)[0], 401)
                pairing = request(local_port, '/api/pairing', 'POST', {},
                                  {'X-Companion-CSRF': local_csrf})
                self.assertEqual(pairing[0], 200)
                code = json.loads(pairing[2])['code']
                login = request(gateway_port, '/api/auth/login', 'POST',
                                {'code': code, 'name': 'Synthetic browser'}, gateway_headers)
                self.assertEqual(login[0], 200)
                cookie_header = login[1]['Set-Cookie']
                for flag in ('Secure', 'HttpOnly', 'SameSite=Strict'):
                    self.assertIn(flag, cookie_header)
                cookie = cookie_header.split(';', 1)[0]
                paired = {**gateway_headers, 'Cookie': cookie}
                chats = request(gateway_port, '/api/chats', headers=paired)
                self.assertEqual(chats[0], 200)
                self.assertEqual(json.loads(chats[2])['chats'][0]['id'], CHAT)
                remote_status = json.loads(request(gateway_port, '/api/status', headers=paired)[2])
                payload = {'thread_id': CHAT, 'message': 'synthetic e2e message',
                           'command_id': 'isolated-e2e-command-12345', 'mode': 'queue'}
                self.assertEqual(request(gateway_port, '/api/command', 'POST', payload, paired)[0], 403)
                authorized = {**paired, 'X-Companion-CSRF': remote_status['csrf']}
                first = request(gateway_port, '/api/command', 'POST', payload, authorized)
                second = request(gateway_port, '/api/command', 'POST', payload, authorized)
                self.assertEqual(first[0], 200)
                self.assertEqual(json.loads(first[2])['state'], 'queued')
                self.assertEqual(json.loads(first[2]), json.loads(second[2]))
                self.assertEqual(len(calls.read_text().splitlines()), 1)
                self.assertEqual(request(gateway_port, '/api/auth/logout', 'POST', {}, authorized)[0], 200)
                self.assertEqual(request(gateway_port, '/api/chats', headers=paired)[0], 401)
            finally:
                process.terminate()
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()
