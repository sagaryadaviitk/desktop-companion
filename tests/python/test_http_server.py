import http.client
from http.server import BaseHTTPRequestHandler
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from desktop_companion.access import AccessStore, RemoteSettings
from desktop_companion.companion import CommandJournal, CompanionServer
from desktop_companion.http_server import LoopbackHTTPServer


class LoopbackBindingTests(unittest.TestCase):
    def test_both_companion_listeners_bind_without_reverse_dns_and_gateway_stays_private(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            access = AccessStore(root / 'access.sqlite')
            settings = RemoteSettings(root / 'remote.json')
            settings.set_origin('https://phone.example')
            journal = CommandJournal(root / 'commands.sqlite')
            servers = []
            workers = []
            try:
                with patch('socket.getfqdn', side_effect=AssertionError('reverse DNS was called')):
                    remote = CompanionServer(('127.0.0.1', 0), Mock(), Mock(), journal,
                                             access=access, remote=True, settings=settings)
                    servers.append(remote)
                    local = CompanionServer(('127.0.0.1', 0), Mock(), Mock(), journal,
                                            access=access, settings=settings, gateway_port=remote.server_port)
                    servers.append(local)
                    for server in servers:
                        self.assertEqual(server.server_address[0], '127.0.0.1')
                        self.assertEqual(server.server_name, '127.0.0.1')
                        self.assertGreater(server.server_port, 0)
                        self.assertEqual(server.server_address[1], server.server_port)
                        worker = threading.Thread(target=server.serve_forever, daemon=True)
                        worker.start()
                        workers.append(worker)
                    connection = http.client.HTTPConnection('127.0.0.1', remote.server_port, timeout=3)
                    try:
                        connection.request('GET', '/api/chats', headers={'Host': 'phone.example',
                                           'Origin': 'https://phone.example', 'X-Forwarded-Proto': 'https'})
                        response = connection.getresponse()
                        self.assertEqual(response.status, 401)
                        response.read()
                    finally:
                        connection.close()
            finally:
                for server in servers[:len(workers)]:
                    server.shutdown()
                for server in servers:
                    server.server_close()
                for worker in workers:
                    worker.join(timeout=3)

    def test_non_numeric_host_uses_standard_http_server_binding(self):
        with patch('socket.getfqdn', return_value='fixture.example') as resolve:
            server = LoopbackHTTPServer(('localhost', 0), BaseHTTPRequestHandler)
            try:
                self.assertEqual(server.server_name, 'fixture.example')
                resolve.assert_called_once()
            finally:
                server.server_close()


if __name__ == '__main__':
    unittest.main()
