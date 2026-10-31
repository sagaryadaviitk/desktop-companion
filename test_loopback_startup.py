"""Regression: numeric loopback startup must not depend on reverse DNS."""
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from access import AccessStore, RemoteSettings
from companion import CommandJournal, CompanionServer


class LoopbackStartupTests(unittest.TestCase):
    def test_local_and_authenticated_gateway_bind_without_reverse_dns(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for remote in (False,True):
                with self.subTest(remote=remote):
                    journal=CommandJournal(root/('remote.sqlite' if remote else 'local.sqlite'))
                    access=AccessStore(root/'access.sqlite') if remote else None
                    settings=RemoteSettings(root/'remote.json') if remote else None
                    with patch('socket.getfqdn',side_effect=AssertionError('Reverse DNS must not run')):
                        server=CompanionServer(('127.0.0.1',0),None,None,journal,access=access,remote=remote,settings=settings)
                    try:
                        self.assertEqual(server.server_name,'127.0.0.1')
                        self.assertEqual(server.server_port,server.socket.getsockname()[1])
                        self.assertGreater(server.server_port,0)
                        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
                        try:
                            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
                            connection.request('GET','/healthz')
                            response=connection.getresponse();data=json.loads(response.read());connection.close()
                            self.assertEqual(response.status,200)
                            self.assertEqual(data['authentication_required'],remote)
                        finally:server.shutdown();worker.join(timeout=3)
                    finally:server.server_close()

    def test_non_loopback_and_hostname_bind_keep_standard_behavior(self):
        for host in ('localhost','0.0.0.0','192.0.2.1'):
            with self.subTest(host=host):
                server=CompanionServer.__new__(CompanionServer)
                server.server_address=(host,0)
                with patch.object(ThreadingHTTPServer,'server_bind') as bind:
                    server.server_bind()
                    bind.assert_called_once_with()


if __name__=='__main__':unittest.main()
