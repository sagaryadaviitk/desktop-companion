import http.client
import http.server
from contextlib import closing
import json
import os
from pathlib import Path
import socket
import sqlite3
import ssl
import tempfile
import threading
import unittest
from unittest.mock import patch

from local_tools import LocalTools


class FakeAPI(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'

    def log_message(self, *_args):
        pass

    def handle_request(self):
        length = int(self.headers.get('Content-Length', '0'))
        body = self.rfile.read(length)
        record = {'method': self.command, 'path': self.path, 'body': body.decode(),
                  'auth': self.headers.get('Authorization'), 'key': self.headers.get('X-API-Key'),
                  'cookie': self.headers.get('Cookie'), 'fixed': self.headers.get('X-Team')}
        with self.server.lock:
            self.server.requests.append(record)
        if self.server.journal_path.exists():
            with closing(sqlite3.connect(self.server.journal_path)) as conn:
                self.server.observed_states.append(conn.execute('SELECT state FROM api_calls ORDER BY created DESC LIMIT 1').fetchone()[0])
        endpoint = self.path.split('?', 1)[0]
        if endpoint.endswith('/close'):
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        if endpoint.endswith('/hold'):
            self.server.entered.set()
            self.server.release.wait(3)
        status = 302 if endpoint.endswith('/redirect') else 401 if endpoint.endswith('/error') else 200
        payload = json.dumps(record).encode()
        if endpoint.endswith('/large'):
            payload = b'a' * 4096 + b'test-office-token'
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Set-Cookie', 'session=test-cookie-secret')
        self.send_header('X-Request-ID', 'echo-test-office-token')
        if status == 302:
            self.send_header('Location', '/outside?token=test-office-token')
        self.end_headers()
        if self.command != 'HEAD':
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

    do_GET = handle_request
    do_HEAD = handle_request
    do_POST = handle_request
    do_PUT = handle_request
    do_PATCH = handle_request
    do_DELETE = handle_request


class LocalToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.token = self.workspace / 'office-token.txt'
        self.token.write_text('test-api-key\n')
        self.token.chmod(0o600)
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), FakeAPI)
        self.server.requests = []
        self.server.observed_states = []
        self.server.lock = threading.Lock()
        self.server.entered = threading.Event()
        self.server.release = threading.Event()
        self.server.journal_path = self.root / 'state' / 'local-api-calls.sqlite3'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.env = patch.dict(os.environ, {'LOCAL_TOOLS_TEST_TOKEN': 'test-office-token',
                                          'LOCAL_TOOLS_TEST_COOKIE': 'session=test-cookie-secret; csrf=test-csrf-secret'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.config = {'state_dir': str(self.root / 'state'),
                       'api_profiles': {'office': {
                           'base_url': 'http://127.0.0.1:' + str(self.server.server_port) + '/v1',
                           'methods': ['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE'],
                           'headers': {'X-Team': 'team-a'},
                           'secret_headers': {'Authorization': {'env': 'LOCAL_TOOLS_TEST_TOKEN', 'prefix': 'Bearer '},
                                              'X-API-Key': {'file': str(self.token)},
                                              'Cookie': {'env': 'LOCAL_TOOLS_TEST_COOKIE'}}}},
                       'workspaces': [{'id': 'project', 'name': 'Project', 'path': str(self.workspace)}]}
        self.tools = LocalTools(self.config)

    def stop_server(self):
        self.server.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, path='echo', method='GET', call_id=None, **kwargs):
        return self.tools.execute('api_request', {'profile': 'office', 'method': method, 'path': path, **kwargs}, call_id)

    def assert_no_credentials(self, value):
        raw = json.dumps(value)
        for secret in ('test-office-token', 'test-api-key', 'test-cookie-secret', 'test-csrf-secret'):
            self.assertNotIn(secret, raw)

    def test_private_auth_injection_redaction_and_durable_reservation(self):
        result = self.request(query={'name': 'a&b', 'tags': ['x', 'y'], 'ok': True}, call_id='read-1')
        self.assertTrue(result['ok'], result)
        record = self.server.requests[0]
        self.assertEqual(record['auth'], 'Bearer test-office-token')
        self.assertEqual(record['key'], 'test-api-key')
        self.assertIn('session=test-cookie-secret', record['cookie'])
        self.assertEqual(record['fixed'], 'team-a')
        self.assertEqual(record['path'], '/v1/echo?name=a%26b&tags=x&tags=y&ok=true')
        self.assertEqual(self.server.observed_states, ['in_flight'])
        self.assertEqual(result['state'], 'completed')
        self.assertNotIn('set-cookie', result['headers'])
        self.assert_no_credentials(result)
        self.assert_no_credentials(self.tools.public_profiles())
        self.assert_no_credentials(self.tools.definitions())
        self.assertNotIn(str(self.token), json.dumps(self.tools.public_profiles()))
        self.assertEqual(self.server.journal_path.stat().st_mode & 0o777, 0o600)
        with closing(sqlite3.connect(self.server.journal_path)) as conn:
            self.assertEqual(conn.execute('SELECT state FROM api_calls').fetchone()[0], 'completed')
            self.assert_no_credentials(conn.execute('SELECT result_json FROM api_calls').fetchone()[0])

    def test_all_explicit_office_methods_work_and_unlisted_method_is_blocked(self):
        for method in ('GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE'):
            with self.subTest(method=method):
                result = self.request(method=method, json_body={'value': 1} if method != 'HEAD' else {})
                self.assertTrue(result['ok'], result)
                self.assertEqual(self.server.requests[-1]['method'], method)
        self.assertFalse(self.request(method='OPTIONS')['ok'])
        self.assertEqual(len(self.server.requests), 6)

    def test_completed_duplicate_returns_result_without_repeating_network(self):
        first = self.request(method='POST', body='change', call_id='post-once')
        second = self.request(method='POST', body='change', call_id='post-once')
        self.assertTrue(first['ok'])
        self.assertTrue(second['duplicate'])
        self.assertEqual(first['body'], second['body'])
        changed = self.request(method='DELETE', call_id='post-once')
        self.assertFalse(changed['ok'])
        self.assertTrue(changed['duplicate'])
        self.assertEqual(len(self.server.requests), 1)

    def test_in_flight_call_is_not_replayed_even_by_another_instance(self):
        results = []
        worker = threading.Thread(target=lambda: results.append(self.request('hold', 'POST', 'shared-call')))
        worker.start()
        self.assertTrue(self.server.entered.wait(2))
        duplicate = LocalTools(self.config).execute('api_request',
            {'profile': 'office', 'method': 'POST', 'path': 'hold'}, 'shared-call')
        self.assertEqual(duplicate['state'], 'in_flight')
        self.assertTrue(duplicate['duplicate'])
        self.server.release.set()
        worker.join(3)
        self.assertEqual(len(self.server.requests), 1)
        self.assertTrue(results[0]['ok'])

    def test_dropped_response_is_uncertain_and_never_retried_after_reload(self):
        result = self.request('close', 'POST', 'lost-ack')
        self.assertFalse(result['ok'])
        self.assertEqual(result['state'], 'uncertain')
        duplicate = LocalTools(self.config).execute('api_request',
            {'profile': 'office', 'method': 'POST', 'path': 'close'}, 'lost-ack')
        self.assertEqual(duplicate['state'], 'uncertain')
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(len(self.server.requests), 1)

    def test_redirect_is_terminal_and_not_followed_or_retried(self):
        result = self.request('redirect', call_id='redirect')
        self.assertTrue(result['redirect_blocked'])
        self.assertEqual(result['status'], 302)
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(len(self.server.requests), 1)
        self.assertNotIn('location', result['headers'])
        self.assert_no_credentials(result)

    def test_http_error_response_is_redacted_and_not_retried(self):
        result = self.request('error', 'POST', 'http-error')
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 401)
        self.assertEqual(result['state'], 'completed')
        self.assert_no_credentials(result)
        self.assertEqual(len(self.server.requests), 1)

    def test_path_escapes_are_rejected_and_query_cannot_escape_prefix(self):
        for path in ('/outside', '//evil.test/', 'http://evil.test/', '../outside', 'a/../outside',
                     '%2e%2e/outside', '%252e%252e/outside', 'a%2f..%2foutside',
                     '..;param/outside', 'x\\..\\outside', 'echo?token=x', 'echo#fragment',
                     'x%0d%0aHeader:value'):
            with self.subTest(path=path):
                result = self.request(path)
                self.assertFalse(result['ok'], result)
                self.assertEqual(result['state'], 'not_sent')
        self.assertEqual(self.server.requests, [])
        self.assertTrue(self.request(query={'next': '//evil.test/../', 'q': '../outside'})['ok'])
        self.assertTrue(self.server.requests[-1]['path'].startswith('/v1/echo?'))

    def test_private_network_http_and_malformed_profiles_do_not_connect(self):
        for base in ('http://192.168.1.2/api', 'http://office.example/api',
                     'http://127.0.0.1:bad/api', 'http://user:password@127.0.0.1/api',
                     'http://127.0.0.1/api?secret=value', 'ftp://127.0.0.1/api'):
            with self.subTest(base=base):
                self.config['api_profiles']['office']['base_url'] = base
                self.assertFalse(self.request()['ok'])
                public = self.tools.public_profiles()
                self.assertNotIn('password', json.dumps(public))
                self.assertTrue(public[0]['configuration_error'])
        self.assertEqual(self.server.requests, [])

    def test_https_private_ip_uses_verified_context_and_optional_ca(self):
        self.config['api_profiles']['office'].update(base_url='https://10.0.0.1/api', ca_file=str(self.root / 'office-ca.pem'))
        context = ssl.create_default_context()
        with patch('local_tools.ssl.create_default_context', return_value=context) as create, \
             patch('local_tools.http.client.HTTPSConnection', side_effect=OSError('secret test-office-token')) as connect:
            result = self.request(call_id='tls-call')
        create.assert_called_once_with(cafile=str(self.root / 'office-ca.pem'))
        self.assertIs(connect.call_args.kwargs['context'], context)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertEqual(result['state'], 'uncertain')
        self.assert_no_credentials(result)

    def test_size_caps_and_conflicting_payloads_do_not_send(self):
        self.config['max_request_bytes'] = 8
        self.assertFalse(self.request(method='POST', body='x' * 9)['ok'])
        self.assertFalse(self.request(method='POST', body='x', json_body={})['ok'])
        self.assertFalse(self.request(query={'bad': {'nested': True}})['ok'])
        self.assertEqual(self.server.requests, [])
        self.config['max_response_bytes'] = 32
        responses = []
        getresponse = http.client.HTTPConnection.getresponse
        def capture(connection):
            response = getresponse(connection)
            responses.append(response)
            return response
        with patch('local_tools.http.client.HTTPConnection.getresponse', capture):
            result = self.request('large')
        self.assertTrue(result['truncated'])
        self.assertEqual(result['body'], '')
        self.assertTrue(responses[0].closed)
        self.assertTrue(responses[0].isclosed())
        self.assertEqual(len(self.server.requests), 1)
        self.assert_no_credentials(result)

    def test_header_injection_reserved_fields_and_missing_auth_are_blocked(self):
        profile = self.config['api_profiles']['office']
        for headers in ({'X-Team': 'x\r\nAuthorization: injected'}, {'Host': 'evil.test'},
                        {'Authorization': 'Bearer test-office-token'}):
            with self.subTest(headers=headers):
                profile['headers'] = headers
                self.assertFalse(self.request()['ok'])
        profile['headers'] = {}
        with patch.dict(os.environ, {'LOCAL_TOOLS_TEST_TOKEN': ''}):
            result = self.request()
        self.assertIn('Background services', result['error'])
        self.assertEqual(self.server.requests, [])

    def test_private_file_permissions_and_common_credentials_are_not_exposed(self):
        self.token.chmod(0o644)
        self.assertFalse(self.request()['ok'])
        self.assertEqual(self.server.requests, [])
        self.token.chmod(0o600)
        (self.workspace / '.env').write_text('UNCONFIGURED_SECRET=do-not-read')
        (self.workspace / 'config.local.json').write_text('{"token":"do-not-read"}')
        for path in ('.env', 'config.local.json', 'office-token.txt'):
            with self.subTest(path=path):
                self.assertFalse(self.tools.execute('read_file', {'workspace': 'project', 'path': path})['ok'])
        listing = self.tools.execute('list_files', {'workspace': 'project'})
        self.assertEqual(listing['entries'], [])

    def test_scoped_text_reads_redact_secrets_and_never_execute_scripts(self):
        script = self.workspace / 'script.py'
        script.write_text('raise Exception("never execute")\n# test-office-token test-cookie-secret')
        result = self.tools.execute('read_file', {'workspace': 'project', 'path': 'script.py'})
        self.assertTrue(result['ok'], result)
        self.assertIn('raise Exception', result['content'])
        self.assert_no_credentials(result)
        self.assertFalse(self.tools.execute('run_shell', {'command': 'anything'})['ok'])
        (self.workspace / 'skill').mkdir()
        (self.workspace / 'skill' / 'SKILL.md').write_text('Read-only skill instructions')
        self.assertTrue(self.tools.execute('read_file', {'workspace': 'project', 'path': 'skill/SKILL.md'})['ok'])

    def test_file_paths_cannot_escape_through_dot_segments_or_symlinks(self):
        outside = self.root / 'outside.txt'
        outside.write_text('do-not-read')
        (self.workspace / 'escape.txt').symlink_to(outside)
        (self.workspace / 'escape-dir').symlink_to(self.root, target_is_directory=True)
        (self.workspace / 'inside.txt').write_text('inside')
        (self.workspace / 'inside-link').symlink_to(self.workspace / 'inside.txt')
        for path in ('../outside.txt', str(outside), 'escape.txt', 'escape-dir/outside.txt', 'inside-link'):
            with self.subTest(path=path):
                self.assertFalse(self.tools.execute('read_file', {'workspace': 'project', 'path': path})['ok'])
        self.assertFalse(self.tools.execute('list_files', {'workspace': 'project', 'path': 'escape-dir'})['ok'])
        self.assertTrue(self.tools.execute('read_file', {'workspace': 'project', 'path': 'inside.txt'})['ok'])

    def test_file_size_binary_and_listing_limits(self):
        self.config.update(max_file_bytes=16, max_list_entries=2)
        for index in range(3):
            (self.workspace / ('file' + str(index))).write_text('short')
        (self.workspace / 'large').write_text('x' * 17)
        (self.workspace / 'binary').write_bytes(b'bad\0file')
        for path in ('large', 'binary'):
            self.assertFalse(self.tools.execute('read_file', {'workspace': 'project', 'path': path})['ok'])
        listing = self.tools.execute('list_files', {'workspace': 'project'})
        self.assertTrue(listing['truncated'])
        self.assertEqual(len(listing['entries']), 2)

    def test_empty_profiles_are_helpful_and_do_not_create_journal(self):
        tools = LocalTools({'state_dir': str(self.root / 'unused')})
        result = tools.execute('api_request', {'profile': 'missing', 'method': 'GET', 'path': 'status'})
        self.assertIn('No API profiles', result['error'])
        self.assertFalse((self.root / 'unused').exists())

    def test_journal_commit_failure_stays_uncertain_and_prevents_replay(self):
        with patch.object(self.tools.journal, 'finish', side_effect=sqlite3.OperationalError('disk full')):
            first = self.request(method='POST', call_id='journal-failure')
        self.assertEqual(first['state'], 'uncertain')
        self.assertFalse(first['ok'])
        duplicate = self.request(method='POST', call_id='journal-failure')
        self.assertEqual(duplicate['state'], 'in_flight')
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(len(self.server.requests), 1)


if __name__ == '__main__':
    unittest.main()
