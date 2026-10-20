import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
import subprocess
from urllib.error import URLError
from unittest.mock import patch

from access import AccessStore, COOKIE_NAME, PairingError, RemoteSettings
from companion import Bridge, CommandJournal, CompanionServer
from remote_access import named_configuration, preflight, public_status
from test_companion import CHAT, make_history


class AccessStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = [1000.0]
        self.store = AccessStore(Path(self.temp.name) / "access.sqlite", clock=lambda:self.now[0])

    def tearDown(self):
        self.temp.cleanup()

    def test_pairing_is_one_use_and_tokens_are_not_stored_in_plaintext(self):
        pairing = self.store.create_pairing()
        token, session = self.store.exchange(pairing["code"], "Test phone")
        self.assertEqual(self.store.authenticate(token)["id"], session["id"])
        with self.assertRaises(PairingError): self.store.exchange(pairing["code"], "Replayed")
        raw = self.store.path.read_bytes()
        self.assertNotIn(token.encode(), raw)
        self.assertNotIn(pairing["code"].replace("-", "").encode(), raw)

    def test_new_code_replaces_old_code_and_expired_code_fails(self):
        old = self.store.create_pairing()
        current = self.store.create_pairing()
        with self.assertRaises(PairingError): self.store.exchange(old["code"], "Old")
        self.now[0] += 301
        with self.assertRaises(PairingError): self.store.exchange(current["code"], "Expired")

    def test_revocation_and_session_expiry_take_effect(self):
        token, session = self.store.exchange(self.store.create_pairing()["code"], "Phone")
        self.store.revoke(session["id"])
        self.assertIsNone(self.store.authenticate(token))
        token, session = self.store.exchange(self.store.create_pairing()["code"], "Phone")
        self.now[0] = session["expires"] + 1
        self.assertIsNone(self.store.authenticate(token))

    def test_invalid_attempts_are_limited(self):
        for _ in range(15):
            with self.assertRaises(PairingError) as raised:self.store.exchange("WRONG", "Phone")
            self.assertFalse(raised.exception.limited)
        with self.assertRaises(PairingError) as raised:self.store.exchange("WRONG", "Phone")
        self.assertTrue(raised.exception.limited)
        self.now[0] += 301
        with self.assertRaises(PairingError) as raised:self.store.exchange("WRONG", "Phone")
        self.assertFalse(raised.exception.limited)

    def test_concurrent_exchange_only_creates_one_session(self):
        code = self.store.create_pairing()["code"]
        results = []
        def exchange():
            try:self.store.exchange(code, "Phone");results.append("ok")
            except PairingError:results.append("rejected")
        workers = [threading.Thread(target=exchange) for _ in range(2)]
        for worker in workers:worker.start()
        for worker in workers:worker.join()
        self.assertEqual(sorted(results), ["ok", "rejected"])
        self.assertEqual(len(self.store.devices()), 1)

    def test_gateway_origin_rejects_paths_credentials_and_plain_http(self):
        settings=RemoteSettings(Path(self.temp.name)/"remote.json")
        for value in ["http://phone.example","https://user:pass@phone.example","https://phone.example/path","https://phone.example:443","https://*.example"]:
            with self.assertRaises(ValueError):settings.set_origin(value)
        settings.set_origin("https://sample.trycloudflare.com")
        self.assertEqual(settings.origin(),"https://sample.trycloudflare.com")


class NamedTunnelTests(unittest.TestCase):
    def test_verification_rejects_private_addresses_without_network_requests(self):
        with patch("remote_access.subprocess.run") as run:
            for address in ["127.0.0.1","10.1.2.3","169.254.169.254","::1"]:
                with self.assertRaises(ValueError):public_status("https://agents.example.com",address)
        run.assert_not_called()

    def test_failed_tls_verification_cannot_be_reported_as_authenticated_gate(self):
        failure=subprocess.CompletedProcess([],60,"000","SSL certificate verification failed")
        with patch("remote_access.subprocess.run",return_value=failure) as run:
            with self.assertRaises(URLError):public_status("https://agents.example.com","1.1.1.1")
        command=run.call_args.args[0]
        self.assertNotIn("--insecure",command)
        self.assertNotIn("-k",command)
        self.assertIn("agents.example.com:443:1.1.1.1",command)
        self.assertEqual(command[-1],"https://agents.example.com/api/chats")

    def test_named_ingress_exposes_only_requested_host_and_keeps_credentials_external(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            tunnel_id = "11111111-2222-3333-4444-555555555555"
            credentials = root / (tunnel_id + ".json")
            credentials.write_text('test credential fixture')
            original = credentials.read_bytes()
            config = named_configuration(root, "agents.example.com", tunnel_id, 8766, root)
            value = json.loads(config.read_text())
            self.assertEqual(value["ingress"], [{"hostname":"agents.example.com","service":"http://127.0.0.1:8766"},{"service":"http_status:404"}])
            self.assertEqual(value["credentials-file"],str(credentials))
            self.assertEqual(credentials.read_bytes(),original)
            self.assertNotIn(original,config.read_bytes())
            self.assertEqual(config.stat().st_mode & 0o777,0o600)

    def test_invalid_named_tunnel_targets_do_not_write_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            tunnel_id = "11111111-2222-3333-4444-555555555555"
            for hostname,identifier in [("agents.example.com/path",tunnel_id),("*.example.com",tunnel_id),("agents.example.com","../../other")]:
                with self.assertRaises(ValueError):named_configuration(root,hostname,identifier,8766,root)
            with self.assertRaises(ValueError):named_configuration(root,"agents.example.com",tunnel_id,8766,root)
            self.assertFalse((root/"cloudflared-named.yml").exists())


class GatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.history = make_history(root)
        cls.access = AccessStore(root / "access.sqlite")
        cls.settings = RemoteSettings(root / "remote.json")
        cls.settings.set_origin("https://phone.example")
        bridge = Bridge(root, enabled=False)
        journal = CommandJournal(root / "commands.sqlite")
        cls.remote = CompanionServer(("127.0.0.1",0), cls.history, bridge, journal, access=cls.access, remote=True, settings=cls.settings)
        cls.local = CompanionServer(("127.0.0.1",0), cls.history, bridge, journal, access=cls.access, settings=cls.settings, gateway_port=cls.remote.server_port)
        cls.workers = [threading.Thread(target=server.serve_forever, daemon=True) for server in [cls.local,cls.remote]]
        for worker in cls.workers:worker.start()

    @classmethod
    def tearDownClass(cls):
        for server in [cls.local,cls.remote]:server.shutdown();server.server_close()
        for worker in cls.workers:worker.join()
        cls.temp.cleanup()

    def request(self, path, method="GET", payload=None, cookie=None, headers=None, local=False):
        server = self.local if local else self.remote
        values = {} if local else {"Host":"phone.example", "X-Forwarded-Proto":"https", "Origin":"https://phone.example"}
        values.update(headers or {})
        if cookie: values["Cookie"] = cookie
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=4)
        connection.request(method,path,body=json.dumps(payload) if payload is not None else None,headers=values)
        response = connection.getresponse()
        result = (response.status,dict(response.getheaders()),response.read())
        connection.close()
        return result

    def pair(self):
        code = self.access.create_pairing()["code"]
        response = self.request("/api/auth/login", "POST", {"code":code,"name":"Test browser"})
        self.assertEqual(response[0],200)
        cookie = response[1]["Set-Cookie"].split(";",1)[0]
        return cookie,response[1]["Set-Cookie"]

    def test_anonymous_reads_and_commands_never_reach_history_or_bridge(self):
        with patch.object(self.history,"list_threads") as read, patch.object(self.remote.bridge,"send") as send, patch("companion.cached_power_state") as power_read:
            for path in ["/api/chats","/api/chat?id="+CHAT,"/api/status","/api/components","/api/devices","/api/photos/"+'a'*32]:
                self.assertEqual(self.request(path)[0],401)
            for path in ['/api/command','/api/read-state','/api/model','/api/questions/answer','/api/queue/steer','/api/queue/recovery/dismiss','/api/photos','/api/photos/remove']:
                self.assertEqual(self.request(path,"POST",{"thread_id":CHAT,"message":"hello"})[0],401)
        read.assert_not_called();send.assert_not_called();power_read.assert_not_called()

    def test_login_shell_has_no_conversation_text(self):
        response = self.request("/")
        self.assertEqual(response[0],200)
        self.assertIn(b"Pair this browser",response[2])
        self.assertNotIn(b"My existing chat",response[2])

    def test_valid_cookie_allows_history_and_has_secure_flags(self):
        cookie,header = self.pair()
        self.assertTrue(cookie.startswith(COOKIE_NAME+"="))
        for flag in ["Secure","HttpOnly","SameSite=Strict","Path=/"]:self.assertIn(flag,header)
        response = self.request("/api/chats",cookie=cookie)
        self.assertEqual(response[0],200)
        self.assertEqual(json.loads(response[2])["chats"][0]["id"],CHAT)

    def test_remote_pairing_authority_is_unavailable_even_when_authenticated(self):
        cookie,_ = self.pair()
        status = json.loads(self.request("/api/status",cookie=cookie)[2])
        self.assertFalse(status["phone_setup_available"])
        headers = {"X-Companion-CSRF":status["csrf"]}
        self.assertEqual(self.request("/api/pairing","POST",cookie=cookie,headers=headers)[0],404)
        self.assertEqual(self.request("/api/devices",cookie=cookie)[0],404)
        self.assertEqual(self.request("/api/pairing","POST",cookie=cookie,headers={**headers,"Host":f"127.0.0.1:{self.remote.server_port}"})[0],403)

    def test_revoke_from_mac_invalidates_browser(self):
        cookie,_ = self.pair()
        token = cookie.split("=",1)[1]
        session = self.access.authenticate(token)
        headers = {"X-Companion-CSRF":self.local.csrf}
        result = self.request("/api/devices/revoke","POST",{"device_id":session["id"]},headers=headers,local=True)
        self.assertEqual(result[0],200)
        self.assertEqual(self.request("/api/chats",cookie=cookie)[0],401)

    def test_authenticated_command_requires_session_csrf(self):
        cookie,_ = self.pair()
        payload = {"thread_id":CHAT,"message":"Do the next check","command_id":"remote-command-1234567890"}
        self.assertEqual(self.request("/api/command","POST",payload,cookie=cookie)[0],403)
        status = json.loads(self.request("/api/status",cookie=cookie)[2])
        with patch.object(self.remote.bridge,"send",return_value={"state":"queued"}) as send:
            response = self.request("/api/command","POST",payload,cookie=cookie,headers={"X-Companion-CSRF":status["csrf"]})
        self.assertEqual(response[0],200);send.assert_called_once_with(CHAT,"Do the next check")

    def test_component_status_requires_pairing_and_omits_access_records(self):
        cookie,_=self.pair()
        snapshot={'components':[],'summary':{'state':'warning'},'tunnel':{'public_ready':False},'checked_at':100,'devices':['PRIVATE_DEVICE'],'pairing_history':['PRIVATE_CODE_HISTORY']}
        with patch('control.read_snapshot',return_value=snapshot):
            status,_,body=self.request('/api/components',cookie=cookie)
        self.assertEqual(status,200);value=json.loads(body)
        self.assertEqual(set(value),{'components','summary','tunnel','checked_at'})
        self.assertNotIn('PRIVATE',body.decode())

    def test_logout_invalidates_existing_cookie(self):
        cookie,_ = self.pair()
        status = json.loads(self.request("/api/status",cookie=cookie)[2])
        result = self.request("/api/auth/logout","POST",cookie=cookie,headers={"X-Companion-CSRF":status["csrf"]})
        self.assertEqual(result[0],200);self.assertIn("Max-Age=0",result[1]["Set-Cookie"])
        self.assertEqual(self.request("/api/chats",cookie=cookie)[0],401)

    def test_forged_origin_and_non_https_forwarding_are_rejected(self):
        cookie,_ = self.pair()
        for headers in [{"Origin":"https://evil.example"},{"X-Forwarded-Proto":"http"},{"Sec-Fetch-Site":"cross-site"},{"Host":"evil.example"}]:
            self.assertEqual(self.request("/api/chats",cookie=cookie,headers=headers)[0],403)

    def test_pairing_code_cannot_be_replayed(self):
        code = self.access.create_pairing()["code"]
        self.assertEqual(self.request("/api/auth/login","POST",{"code":code})[0],200)
        self.assertEqual(self.request("/api/auth/login","POST",{"code":code})[0],401)

    def test_locked_gateway_preflight_uses_no_external_connection(self):
        self.assertEqual(preflight(self.local.server_port,self.settings),self.remote.server_port)
        self.assertEqual(self.settings.origin(),"https://phone.example")

    def test_unconfigured_gateway_is_closed(self):
        self.settings.set_origin(None)
        try:self.assertEqual(self.request("/api/chats")[0],403)
        finally:self.settings.set_origin("https://phone.example")


if __name__ == "__main__":unittest.main()
