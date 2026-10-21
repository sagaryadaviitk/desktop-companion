from contextlib import closing
import hashlib
import http.client
import json
import io
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from companion import Bridge, CommandJournal, CompanionServer, redact
from local_assistant import ExoClient, LocalAssistant, LocalError
from local_settings import LocalSettings, SkillRegistry, DEFAULT_MODEL
from test_companion import make_history, CHAT


class FakeClient:
    replies = []
    received = []

    def __init__(self, config): pass
    def ready_models(self): return {DEFAULT_MODEL}
    def complete(self, model, messages, tools, on_delta):
        self.received.append(json.loads(json.dumps(messages)))
        result = self.replies.pop(0)
        if isinstance(result, Exception): raise result
        on_delta(result.get('content') or '')
        return result


def tool_call(identity='call-one', profile='fixture', method='GET', path='health'):
    return {'role': 'assistant', 'content': None, 'tool_calls': [{'id': identity, 'type': 'function',
        'function': {'name': 'api_request', 'arguments': json.dumps({'profile': profile, 'method': method, 'path': path})}}]}


class LocalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = LocalSettings(self.root)
        self.settings.save({'enabled': True, 'model': DEFAULT_MODEL, 'skill_roots': [], 'workspaces': [], 'api_profiles': {}})
        FakeClient.received = []
        FakeClient.replies = [{'role': 'assistant', 'content': 'A real fixture answer.'}]
        self.assistant = LocalAssistant(self.root, settings=self.settings, client_factory=FakeClient, redact=redact, auto_start=False)

    def tearDown(self):
        self.assistant.close()
        self.temp.cleanup()

    def create(self, **extra):
        payload = {'request_id': 'create-request-000001', 'model': DEFAULT_MODEL, 'skills': [], **extra}
        return self.assistant.create(payload)['chat']['id']

    def send(self, identity, message='Hello'):
        return self.assistant.send({'request_id': 'message-request-000001', 'chat_id': identity, 'message': message})

    def test_disabled_by_default_and_invalid_settings_preserve_prior_file(self):
        self.assertFalse(LocalSettings(self.root / 'missing').load()['enabled'])
        before = self.settings.path.read_bytes()
        with self.assertRaises(ValueError): self.settings.save({'enabled': True, 'endpoint': 'https://remote.invalid/v1'})
        self.assertEqual(before, self.settings.path.read_bytes())
        self.assertEqual(self.settings.path.stat().st_mode & 0o777, 0o600)
        for bad in ({'endpoint':42},{'endpoint':'http://127.0.0.1:bad/v1'},
                    {'workspaces':[{'id':42,'path':'/tmp'}]}):
            with self.assertRaises(ValueError):self.settings.save(bad)
            self.assertEqual(before,self.settings.path.read_bytes())

    def test_local_ids_reject_cloud_and_never_modify_native_history(self):
        native = self.root / 'native'; native.mkdir(); history = make_history(native)
        files = [native / 'state_5.sqlite', native / 'thread_history_1.sqlite']
        before = [hashlib.sha256(path.read_bytes()).digest() for path in files]
        identity = self.create()
        turn = self.send(identity)
        self.assistant.run_turn(identity, turn['turn_id'])
        self.assertTrue(identity.startswith('local-'))
        with self.assertRaises(LocalError): self.assistant.detail(CHAT)
        self.assertEqual(self.assistant.detail(identity)['chat']['status'], 'completed')
        self.assertIsNone(self.assistant.detail(identity)['pending'])
        self.assertNotIn('Review my code', json.dumps(FakeClient.received))
        self.assertEqual(before, [hashlib.sha256(path.read_bytes()).digest() for path in files])

    def test_duplicate_creation_and_message_return_same_receipt_without_replay(self):
        identity = self.create()
        self.assertEqual(identity, self.create())
        turn = self.send(identity)
        self.assertEqual(turn, self.send(identity))
        self.assistant.run_turn(identity, turn['turn_id'])
        self.assertEqual(turn, self.send(identity))
        detail = self.assistant.detail(identity)
        self.assertEqual(sum(m['role'] == 'user' for m in detail['messages']), 1)
        with self.assertRaises(LocalError): self.send(identity, 'A changed payload')

    def test_busy_other_chat_rejected_without_touching_cloud_command_journal(self):
        identity = self.create()
        self.send(identity)
        other = self.create(request_id='create-request-000002')
        with self.assertRaisesRegex(LocalError, 'working'): self.assistant.send({'request_id': 'message-request-000002', 'chat_id': other, 'message': 'Later'})
        journal = CommandJournal(self.root / '.state' / 'cloud.sqlite')
        self.assertEqual(journal.execute('cloud-command', {'x': 1}, lambda: {'state': 'sent'})['state'], 'sent')

    def test_restart_marks_active_turn_interrupted_and_never_enqueues_it(self):
        identity = self.create(); turn = self.send(identity)
        replacement = LocalAssistant(self.root, settings=self.settings, client_factory=FakeClient, auto_start=False)
        self.assertTrue(replacement.queue.empty())
        detail = replacement.detail(identity)
        self.assertEqual(detail['chat']['status'], 'interrupted')
        self.assertIn('not replayed', detail['error'])
        self.assertEqual(turn, replacement.send({'request_id': 'message-request-000001', 'chat_id': identity, 'message': 'Hello'}))
        replacement.close()

    def test_profile_secrets_redacted_from_title_history_and_model_input(self):
        credential = self.root / 'private-token'; credential.write_text('fake-long-office-token-12345'); credential.chmod(0o600)
        config = self.settings.load(); config['api_profiles'] = {'fixture': {'base_url': 'http://127.0.0.1:9/', 'methods': ['GET'],
            'secret_headers': {'Authorization': {'file': str(credential), 'prefix': 'Bearer '}}}}
        self.settings.save(config)
        identity = self.create(title='fake-long-office-token-12345')
        turn = self.send(identity, 'Please inspect fake-long-office-token-12345')
        self.assistant.run_turn(identity, turn['turn_id'])
        for value in (json.dumps(self.assistant.detail(identity)), json.dumps(FakeClient.received)):
            self.assertNotIn('fake-long-office-token-12345', value)
        with closing(sqlite3.connect(self.assistant.path)) as db:
            self.assertNotIn('fake-long-office-token-12345', str(db.execute('SELECT * FROM messages').fetchall()))

    def test_tool_result_is_recorded_and_returned_to_model_and_identical_action_deduplicates(self):
        class Tools:
            calls = 0
            def __init__(self, config): pass
            def redact(self, text): return text
            def definitions(self): return [{'type':'function','function':{'name':'api_request'}}]
            def public_profiles(self): return []
            def execute(self, name, args, call_id=None):
                Tools.calls += 1
                return {'ok': True, 'state': 'completed', 'status': 200, 'body': 'unique-fixture-7788'}
        self.assistant.tools_factory = Tools
        FakeClient.replies = [tool_call(), tool_call('call-two'), {'role': 'assistant', 'content': 'The diagnostic returned unique-fixture-7788.'}]
        identity = self.create(); turn = self.send(identity)
        self.assistant.run_turn(identity, turn['turn_id'])
        self.assertEqual(Tools.calls, 1)
        self.assertEqual(self.assistant.detail(identity)['chat']['status'], 'completed')
        self.assertEqual(sum(m['role'] == 'tool' for m in FakeClient.received[-1]), 2)

    def test_uncertain_tool_result_stops_loop_and_is_not_retried(self):
        class Tools:
            def __init__(self, config): pass
            def redact(self, text): return text
            def definitions(self): return [{'type':'function','function':{'name':'api_request'}}]
            def public_profiles(self): return []
            def execute(self, name, args, call_id=None): return {'ok': False, 'state': 'uncertain'}
        self.assistant.tools_factory = Tools; FakeClient.replies = [tool_call()]
        identity = self.create(); turn = self.send(identity); self.assistant.run_turn(identity, turn['turn_id'])
        self.assertEqual(len(FakeClient.received), 1)
        self.assertIn('uncertain', self.assistant.detail(identity)['error'])

    def test_incomplete_failed_tool_group_is_not_replayed_into_followup_context(self):
        class Tools:
            def __init__(self, config): pass
            def redact(self, text): return text
            def definitions(self): return [{'type':'function','function':{'name':'api_request'}}]
            def public_profiles(self): return []
            def execute(self, name, args, call_id=None): return {'ok':False,'state':'uncertain'}
        self.assistant.tools_factory=Tools
        response=tool_call();response['tool_calls'].extend(tool_call('never-executed')['tool_calls'])
        FakeClient.replies=[response,{'role':'assistant','content':'Review the uncertain result.'}]
        identity=self.create();turn=self.send(identity);self.assistant.run_turn(identity,turn['turn_id'])
        next_turn=self.assistant.send({'request_id':'message-request-000002','chat_id':identity,'message':'What happened?'})
        self.assistant.run_turn(identity,next_turn['turn_id'])
        self.assertNotIn('never-executed',json.dumps(FakeClient.received[-1]))
        self.assertIn('uncertain',json.dumps(FakeClient.received[-1]))

    def test_chat_only_model_receives_no_tool_definitions(self):
        config=self.settings.load();config['models'].append('chat-only');self.settings.save(config)
        offered=[]
        class Client(FakeClient):
            def complete(self,model,messages,tools,on_delta):
                offered.extend(tools)
                return {'role':'assistant','content':'A text answer.'}
        self.assistant.client_factory=Client
        identity=self.create(model='chat-only');turn=self.send(identity);self.assistant.run_turn(identity,turn['turn_id'])
        self.assertEqual(offered,[])
        self.assertEqual(self.assistant.detail(identity)['chat']['status'],'completed')

    def test_selected_skill_loaded_read_only_and_reference_cannot_escape(self):
        root = self.root / 'skills'; skill = root / 'office-debug'; skill.mkdir(parents=True)
        source = '---\nname: office-debug\ndescription: Read a fixture diagnostic.\n---\nUse the supplied api_request tool.\n'
        (skill / 'SKILL.md').write_text(source)
        (skill / 'notes.md').write_text('REFERENCE_FIXTURE')
        config = self.settings.load(); config['skill_roots'] = [str(root)]; self.settings.save(config)
        registry = SkillRegistry([str(root)]); selected = registry.public()[0]['id']
        with self.assertRaises(ValueError): registry.select([{}])
        with self.assertRaises(ValueError): registry.read_reference(selected, '../outside.md')
        identity = self.create(skills=[selected]); turn = self.send(identity); self.assistant.run_turn(identity, turn['turn_id'])
        self.assertIn(source, FakeClient.received[0][0]['content'])
        self.assertEqual(source, (skill / 'SKILL.md').read_text())

    def test_symlink_store_is_refused(self):
        root = self.root / 'symlink-root'; (root / '.state').mkdir(parents=True)
        original = self.root / 'untouched'; original.write_text('do-not-touch')
        (root / '.state' / 'local-assistant.sqlite').symlink_to(original)
        with self.assertRaises(LocalError): LocalAssistant(root, auto_start=False)
        self.assertEqual(original.read_text(), 'do-not-touch')

    def test_failed_generation_is_visible_and_never_retried(self):
        FakeClient.replies = [LocalError('Fixture failed without retry.')]
        identity = self.create(); turn = self.send(identity); self.assistant.run_turn(identity, turn['turn_id'])
        self.assertEqual(len(FakeClient.received), 1)
        self.assertEqual(self.assistant.detail(identity)['chat']['status'], 'failed')


class RouteTests(unittest.TestCase):
    setUp = LocalTests.setUp
    tearDown = LocalTests.tearDown
    def test_local_routes_keep_csrf_and_native_history_read_only(self):
        native = self.root / 'native'; native.mkdir(); history = make_history(native)
        bridge = Bridge(native, enabled=False)
        server = CompanionServer(('127.0.0.1', 0), history, bridge, CommandJournal(self.root / 'cloud-commands.sqlite'), local_assistant=self.assistant)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def request(method, path, body=None, csrf=None):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            headers = {'Content-Type': 'application/json'}
            if csrf: headers['X-Companion-CSRF'] = csrf
            connection.request(method, path, json.dumps(body) if body else None, headers)
            response = connection.getresponse(); status = response.status; data = response.read(); connection.close()
            return status, data
        try:
            self.assertEqual(request('GET', '/local')[0], 200)
            self.assertNotIn(b'__BUILD__', request('GET', '/local')[1])
            self.assertTrue(json.loads(request('GET', '/api/local/status')[1])['ready'])
            payload = {'request_id': 'route-create-00000001', 'model': DEFAULT_MODEL, 'skills': []}
            self.assertEqual(request('POST', '/api/local/chats', payload)[0], 403)
            status, raw = request('POST', '/api/local/chats', payload, server.csrf)
            self.assertEqual(status, 200); identity = json.loads(raw)['chat']['id']
            self.assertEqual(request('GET', '/api/local/chat?id=' + CHAT)[0], 400)
            self.assertEqual(history.read_thread(CHAT)['title'], 'My existing chat')
            self.assertEqual(request('GET', '/api/local/chat?id=' + identity)[0], 200)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=3)


class StreamTests(unittest.TestCase):
    def test_exo_null_tool_calls_are_valid_text_deltas_and_request_stays_loopback(self):
        chunks=[{'choices':[{'delta':{'content':'Hello','tool_calls':None},'finish_reason':None}]},
                {'choices':[{'delta':{},'finish_reason':'stop'}]}]
        data=b''.join(b'data: '+json.dumps(chunk).encode()+b'\n\n' for chunk in chunks)+b'data: [DONE]\n\n'
        calls=[]
        client=ExoClient({'endpoint':'http://127.0.0.1:52415/v1'})
        def open_request(request,**kwargs):
            calls.append(request)
            return io.BytesIO(data)
        client.opener=SimpleNamespace(open=open_request)
        deltas=[]
        result=client.complete(DEFAULT_MODEL,[{'role':'user','content':'Hi'}],[],deltas.append)
        self.assertEqual(result['content'],'Hello');self.assertEqual(deltas,['Hello']);self.assertEqual(len(calls),1)
        self.assertEqual(calls[0].full_url,'http://127.0.0.1:52415/v1/chat/completions')
        self.assertFalse(json.loads(calls[0].data)['enable_thinking'])

    def test_unfinished_stream_is_an_error_and_never_retried(self):
        client=ExoClient({'endpoint':'http://127.0.0.1:52415/v1'});calls=[]
        client.opener=SimpleNamespace(open=lambda *a,**k:(calls.append(True) or io.BytesIO(b'data: {"choices":[]}\n\n')))
        with self.assertRaises(LocalError):client.complete(DEFAULT_MODEL,[],[],lambda _:None)
        self.assertEqual(len(calls),1)


if __name__ == '__main__': unittest.main()
