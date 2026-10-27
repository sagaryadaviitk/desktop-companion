from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from desktop_companion.access import AccessStore,RemoteSettings
from desktop_companion.configuration import Configuration
from desktop_companion import control
class AccessHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = [1000.0]
        self.store = AccessStore(self.root/'access.sqlite',clock=lambda:self.now[0])

    def tearDown(self):
        self.temp.cleanup()

    def test_revoked_and_expired_access_is_retained_without_recoverable_secrets(self):
        code=self.store.create_pairing()['code']
        token,session=self.store.exchange(code,'Test phone')
        self.store.revoke(session['id'])
        self.assertIsNone(self.store.authenticate(token))
        self.assertEqual(self.store.devices(),[])
        history=self.store.devices(include_history=True)
        self.assertEqual(history[0]['status'],'revoked')
        output=json.dumps(history)
        for secret in [token,code,session['csrf'],'token_hash']:
            self.assertNotIn(secret,output)
        second,session2=self.store.exchange(self.store.create_pairing()['code'],'Other phone')
        self.now[0]=session2['expires']+1
        self.assertIsNone(self.store.authenticate(second))
        self.assertEqual({item['status'] for item in self.store.devices(include_history=True)},{'revoked','expired'})

    def test_code_history_records_outcomes_without_code_values(self):
        first=self.store.create_pairing()
        second=self.store.create_pairing()
        self.store.cancel_pairing(first['id'])
        token,_=self.store.exchange(second['code'],'Phone')
        third=self.store.create_pairing()
        self.now[0]+=301
        self.store.cancel_pairing()
        history=self.store.pairing_history()
        self.assertEqual([row['status'] for row in history],['expired','used','replaced'])
        for secret in [first['code'],second['code'],third['code'],token,'code_hash']:
            self.assertNotIn(secret,json.dumps(history))

    def test_last_seen_is_updated_after_a_successful_authentication(self):
        token,_=self.store.exchange(self.store.create_pairing()['code'],'Phone')
        self.now[0]+=65
        self.store.authenticate(token)
        self.assertEqual(self.store.devices()[0]['last_seen'],self.now[0])

    def test_legacy_database_migration_preserves_existing_paired_browser(self):
        path=self.root/'legacy.sqlite'
        token='A'*43
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY,token_hash TEXT UNIQUE NOT NULL,csrf TEXT NOT NULL,name TEXT NOT NULL,created REAL NOT NULL,expires REAL NOT NULL)')
            db.execute('CREATE TABLE pairing (code_hash TEXT PRIMARY KEY,expires REAL NOT NULL)')
            db.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?)',('a'*24,AccessStore.digest(token),'fixture-csrf','Existing phone',900,2000))
        store=AccessStore(path,clock=lambda:1000)
        self.assertIsNotNone(store.authenticate(token))
        self.assertEqual(store.devices()[0]['name'],'Existing phone')
        store.revoke('a'*24)
        self.assertIsNone(store.authenticate(token))


class NativeControlTests(unittest.TestCase):
    def test_desktop_status_uses_main_executable_and_ignores_helpers(self):
        app=control.service.CONFIG.app_path()
        with patch('desktop_companion.service.subprocess.run',return_value=subprocess.CompletedProcess([],0,str(app/'Contents/Frameworks/Helper')+'\n')):
            self.assertFalse(control.service.desktop_app_running())
        with patch('desktop_companion.service.subprocess.run',return_value=subprocess.CompletedProcess([],0,str(app/'Contents/MacOS/ChatGPT')+'\n')):
            self.assertTrue(control.service.desktop_app_running())

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        # Never inherit remote enablement or the desktop app from the host Mac.
        configuration=Configuration(hostname='phone.example',tunnel_id='12345678-1234-1234-1234-123456789abc',desktop_app=str(self.root/'ChatGPT.app'))
        configured=patch.object(control.service,'CONFIG',configuration)
        configured.start();self.addCleanup(configured.stop)

    def tearDown(self):
        self.temp.cleanup()

    def test_status_is_available_when_the_server_is_down(self):
        failure=subprocess.CompletedProcess([],1,'','')
        with patch('desktop_companion.control.service.launchctl',return_value=failure),patch('desktop_companion.control.local_json',return_value=None),patch('desktop_companion.control.subprocess.run',return_value=failure),patch('desktop_companion.control.public_status') as public:
            value=control.read_snapshot(self.root)
        self.assertFalse(value['can_pair'])
        self.assertFalse(value['running'])
        self.assertEqual(value['components'][1]['state'],'stopped')
        public.assert_not_called()

    def test_snapshot_omits_session_tokens_and_local_csrf(self):
        store=AccessStore(self.root/'.state'/'access.sqlite')
        token,session=store.exchange(store.create_pairing()['code'],'Fixture phone')
        RemoteSettings(self.root/'.state'/'remote.json').set_origin('https://phone.example')
        running=subprocess.CompletedProcess([],0,'state = running\npid = 42\n','')
        server={'csrf':'private-local-csrf','mode':'local_preview','phone_setup_available':True,'control':{'available':True}}
        with patch('desktop_companion.control.service.launchctl',return_value=running),patch('desktop_companion.control.local_json',side_effect=[server,{'authentication_required':True}]),patch('desktop_companion.control.public_status',return_value=401),patch('desktop_companion.control.subprocess.run',return_value=subprocess.CompletedProcess([],0,'AC Power','')):
            value=control.read_snapshot(self.root)
        output=json.dumps(value)
        self.assertTrue(value['can_pair'])
        for secret in [token,session['csrf'],'private-local-csrf','token_hash']:
            self.assertNotIn(secret,output)

    def test_revoke_works_without_the_http_server(self):
        store=AccessStore(self.root/'.state'/'access.sqlite')
        token,session=store.exchange(store.create_pairing()['code'],'Fixture phone')
        with patch('desktop_companion.control.local_json') as local:
            self.assertTrue(control.perform('revoke',session['id'],self.root)['ok'])
        self.assertIsNone(store.authenticate(token))
        local.assert_not_called()

    def test_local_only_snapshot_cannot_pair_through_a_saved_remote_origin(self):
        RemoteSettings(self.root/'.state'/'remote.json').set_origin('https://phone.example')
        running=subprocess.CompletedProcess([],0,'state = running\npid = 42\n','')
        server={'mode':'local_preview','phone_setup_available':True,'control':{'available':True}}
        local_only=Configuration(desktop_app=str(self.root/'ChatGPT.app'))
        with patch.object(control.service,'CONFIG',local_only),patch('desktop_companion.control.service.launchctl',return_value=running),patch('desktop_companion.control.local_json',side_effect=[server,{'authentication_required':True}]),patch('desktop_companion.control.public_status') as public,patch('desktop_companion.control.subprocess.run',return_value=subprocess.CompletedProcess([],0,'AC Power','')):
            value=control.read_snapshot(self.root)
        self.assertFalse(value['can_pair']);self.assertEqual(value['tunnel']['state'],'disabled')
        public.assert_not_called()

    def test_component_web_restart_does_not_kickstart_the_supervisor(self):
        with patch('desktop_companion.control.service.launchctl',return_value=subprocess.CompletedProcess([],0,'')) as launch,patch('desktop_companion.control.service.request_web_restart') as restart:
            self.assertTrue(control.perform('restart_web',root=self.root)['ok'])
        restart.assert_called_once();self.assertEqual(launch.call_count,1)
        self.assertEqual(launch.call_args.args[0],'print')


if __name__=='__main__':unittest.main()
