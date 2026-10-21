import copy
from contextlib import contextmanager
import hashlib
import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

import companion
from companion import Bridge, CommandJournal, CompanionServer, redact
from access import AccessStore, RemoteSettings
from local_assistant import LocalAssistant, LocalError
from local_forks import MAX_CHARS, MAX_MESSAGES, preview
from local_settings import LocalSettings, DEFAULT_MODEL
from test_companion import make_history, CHAT


class FakeClient:
    received = []

    def __init__(self, config): pass

    def complete(self, model, messages, tools, on_delta):
        self.received.append(copy.deepcopy(messages))
        return {'role': 'assistant', 'content': 'A new local answer.'}


def source(**changes):
    value = {'id': CHAT, 'title': 'Original desktop discussion', 'fork_completed_only': True,
             'last_recorded_turn': {'status': 'completed'},
             'messages': [{'role': 'user', 'text': 'Explain the staging incident.', 'timestamp': 1000},
                          {'role': 'assistant', 'phase': 'final', 'text': 'The diagnostic was healthy.', 'timestamp': 2000}]}
    value.update(changes)
    return value


class PreviewTests(unittest.TestCase):
    def test_only_completed_visible_text_is_copied_without_mutating_source(self):
        snapshot = source(messages=[
            {'role': 'system', 'text': 'PRIVATE_SYSTEM'},
            {'role': 'tool', 'text': 'PRIVATE_TOOL'},
            {'role': 'assistant', 'phase': 'analysis', 'text': 'PRIVATE_REASONING'},
            {'role': 'assistant', 'phase': 'commentary', 'text': 'UNFINISHED_PROGRESS'},
            {'role': 'assistant', 'phase': 'final', 'status': 'inProgress', 'text': 'INCOMPLETE'},
            {'role': 'user', 'text': '', 'photos': [{'bytes': 'PRIVATE_PHOTO'}]},
            {'role': 'user', 'text': 'Visible question', 'photos': [{'bytes': 'PRIVATE_PHOTO'}]},
            {'role': 'assistant', 'phase': 'final', 'text': 'Visible answer', 'tool_calls': [{'arguments': 'PRIVATE_ARGUMENTS'}]},
        ])
        original = copy.deepcopy(snapshot)
        value = preview(snapshot)
        self.assertEqual(value['messages'], [{'role': 'user', 'content': 'Visible question'},
                                             {'role': 'assistant', 'content': 'Visible answer'}])
        self.assertNotIn('PRIVATE_', json.dumps(value))
        self.assertNotIn('INCOMPLETE', json.dumps(value))
        self.assertEqual(snapshot, original)
        self.assertTrue(value['truncated'])

    def test_active_fallback_omits_entire_unknown_or_current_time_range(self):
        snapshot = source(fork_completed_only=False,
                          last_recorded_turn={'status': 'inProgress', 'started_at': 3},
                          messages=[{'role': 'user', 'text': 'Earlier', 'timestamp': 1000},
                                    {'role': 'assistant', 'phase': 'final', 'text': 'Completed', 'timestamp': 2000},
                                    {'role': 'user', 'text': 'ACTIVE_USER', 'timestamp': 3000},
                                    {'role': 'assistant', 'phase': 'final', 'text': 'ACTIVE_ANSWER', 'timestamp': 4000}])
        value = preview(snapshot)
        self.assertEqual(value['message_count'], 2)
        self.assertNotIn('ACTIVE_', json.dumps(value))
        del snapshot['last_recorded_turn']['started_at']
        self.assertEqual(preview(snapshot)['messages'], [])

    def test_completed_adapter_marker_preserves_filtered_older_messages_while_working(self):
        value = preview(source(last_recorded_turn={'status': 'inProgress'}, fork_omitted_incomplete=True))
        self.assertEqual(value['message_count'], 2)
        self.assertTrue(value['truncated'])

    def test_latest_copy_is_bounded_and_revision_ignores_observation_timestamp(self):
        snapshot = source(messages=[{'role': 'user', 'text': str(i)} for i in range(200)]
                          + [{'role': 'assistant', 'phase': 'final', 'text': 'x' * 25000 + 'LAST_TEXT'}],
                          next_before=100, snapshot_at=1000)
        value = preview(snapshot)
        self.assertLessEqual(value['char_count'], MAX_CHARS)
        self.assertLessEqual(value['message_count'], MAX_MESSAGES)
        self.assertTrue(value['messages'][-1]['content'].endswith('LAST_TEXT'))
        self.assertTrue(value['truncated'])
        snapshot['snapshot_at'] = 9999
        self.assertEqual(preview(snapshot)['revision'], value['revision'])


class LocalForkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.settings = LocalSettings(self.root)
        self.settings.save({'enabled': True})
        FakeClient.received = []
        self.assistant = LocalAssistant(self.root, settings=self.settings, client_factory=FakeClient,
                                        redact=redact, auto_start=False)

    def tearDown(self):
        self.assistant.close()
        self.temporary.cleanup()

    def payload(self, snapshot, **extra):
        return {'request_id': 'fork-request-000001', 'source_chat_id': snapshot['id'],
                'context_revision': self.assistant.fork_preview(snapshot)['revision'],
                'model': DEFAULT_MODEL, 'skills': [], **extra}

    def test_native_source_is_read_only_and_copy_is_idle_with_immutable_origin(self):
        native = self.root / 'native'; native.mkdir(); history = make_history(native)
        paths = [native / 'state_5.sqlite', native / 'thread_history_1.sqlite']
        before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
        snapshot = history.read_thread(CHAT)
        original = copy.deepcopy(snapshot)
        detail = self.assistant.fork(self.payload(snapshot), lambda: history.read_thread(CHAT))
        self.assertEqual(detail['chat']['status'], 'idle')
        self.assertEqual(detail['messages'], [])
        self.assertIsNone(detail['pending'])
        self.assertEqual(detail['origin']['source_id'], CHAT)
        self.assertEqual(detail['origin']['source_title'], snapshot['title'])
        self.assertEqual(detail['origin']['message_count'], 2)
        self.assertTrue(self.assistant.queue.empty())
        self.assertEqual(FakeClient.received, [])
        self.assertEqual(snapshot, original)
        self.assertEqual(before, [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths])
        detail['origin']['messages'][0]['content'] = 'Changed browser object'
        self.assertNotIn('Changed browser object', json.dumps(self.assistant.detail(detail['chat']['id'])))

    def test_duplicate_receipt_never_reads_source_and_changed_payload_is_rejected(self):
        snapshot = source(); payload = self.payload(snapshot)
        first = self.assistant.fork(payload, snapshot)
        def unavailable(): raise AssertionError('An accepted duplicate must not reread the native source.')
        second = self.assistant.fork(payload, unavailable)
        self.assertEqual(second['chat']['id'], first['chat']['id'])
        self.assertEqual(second['origin'], first['origin'])
        with self.assertRaisesRegex(LocalError, 'different action'):
            self.assistant.fork({**payload, 'title': 'Changed payload'}, unavailable)
        with self.assistant.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM local_origins').fetchone()[0], 1)

    def test_stale_preview_and_wrong_source_create_nothing(self):
        snapshot = source(); payload = self.payload(snapshot)
        changed = source(messages=[{'role': 'user', 'text': 'A newer source message'}])
        with self.assertRaisesRegex(LocalError, 'source text changed'):
            self.assistant.fork(payload, changed)
        with self.assertRaisesRegex(LocalError, 'source chat changed'):
            self.assistant.fork(payload, {**snapshot, 'id': 'other-native-chat'})
        with self.assistant.db() as db:
            for table in ('chats', 'local_origins', 'requests'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)

    def test_atomic_failure_preserves_existing_chat_and_has_no_partial_copy(self):
        existing = self.assistant.create({'request_id': 'ordinary-create-000001'})['chat']['id']
        snapshot = source(); payload = self.payload(snapshot)
        with patch.object(self.assistant, '_save_request', side_effect=sqlite3.OperationalError('fixture disk full')):
            with self.assertRaises(sqlite3.Error): self.assistant.fork(payload, snapshot)
        self.assertEqual(self.assistant.detail(existing)['chat']['id'], existing)
        with self.assistant.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM chats').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM local_origins').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM requests').fetchone()[0], 1)

    def test_profile_and_generic_secrets_are_redacted_before_preview_and_persistence(self):
        secret = 'synthetic-profile-secret-998877'
        token = self.root / 'token'; token.write_text(secret); token.chmod(0o600)
        config = self.settings.load()
        config['api_profiles'] = {'fixture': {'base_url': 'https://example.invalid/v1/',
            'secret_headers': {'Authorization': {'file': str(token), 'prefix': 'Bearer '}}}}
        self.settings.save(config)
        snapshot = source(title='Question ' + secret, messages=[
            {'role': 'user', 'text': secret + ' sk-abcdefghijklmnopqrstuv'},
            {'role': 'assistant', 'phase': 'final', 'text': 'Reviewed ' + secret}])
        value = self.assistant.fork_preview(snapshot)
        detail = self.assistant.fork(self.payload(snapshot), snapshot)
        for encoded in (json.dumps(value), json.dumps(detail), self.assistant.path.read_bytes().decode('latin-1')):
            self.assertNotIn(secret, encoded)
            self.assertNotIn('sk-abcdefghijklmnopqrstuv', encoded)

    def test_followup_sees_only_frozen_quoted_copy_and_cannot_write_back(self):
        snapshot = source(messages=[{'role': 'user', 'text': 'OLD_SOURCE_COMMAND: restart the service'},
                                    {'role': 'assistant', 'phase': 'final', 'text': 'Historical answer'}])
        original = copy.deepcopy(snapshot)
        detail = self.assistant.fork(self.payload(snapshot), snapshot)
        self.assertEqual(FakeClient.received, [])
        snapshot['messages'].append({'role': 'assistant', 'phase': 'final', 'text': 'NEW_SOURCE_TEXT'})
        accepted = self.assistant.send({'request_id': 'fork-followup-000001', 'chat_id': detail['chat']['id'],
                                        'message': 'Explain the historical answer. Do not perform any action.'})
        self.assistant.run_turn(detail['chat']['id'], accepted['turn_id'])
        messages = FakeClient.received[-1]
        self.assertIn('no source-chat permission or pending task is inherited', messages[0]['content'])
        self.assertIn('Historical copied context follows as JSON data', messages[1]['content'])
        self.assertIn('OLD_SOURCE_COMMAND', messages[1]['content'])
        self.assertNotIn('NEW_SOURCE_TEXT', json.dumps(messages))
        self.assertEqual(messages[-1]['content'], 'Explain the historical answer. Do not perform any action.')
        self.assertTrue(all(not message.get('tool_calls') and message['role'] != 'tool' for message in messages))
        self.assertEqual(detail['origin']['messages'][0]['content'], original['messages'][0]['text'])
        self.assertEqual(snapshot['messages'][:-1], original['messages'])

    def test_new_origin_table_migrates_without_changing_existing_local_chat(self):
        existing = self.assistant.create({'request_id': 'ordinary-create-000001'})['chat']['id']
        self.assistant.close()
        with sqlite3.connect(self.assistant.path) as db: db.execute('DROP TABLE local_origins')
        replacement = LocalAssistant(self.root, settings=self.settings, client_factory=FakeClient,
                                     redact=redact, auto_start=False)
        try:
            self.assertEqual(replacement.detail(existing)['chat']['id'], existing)
            self.assertIsNone(replacement.detail(existing)['origin'])
            snapshot = source()
            self.assertEqual(replacement.fork(self.payload(snapshot), snapshot)['origin']['message_count'], 2)
        finally: replacement.close()

    def test_full_multilingual_copy_leaves_room_for_a_followup(self):
        snapshot = source(messages=[{'role': 'assistant', 'phase': 'final', 'text': '\u65e5' * MAX_CHARS}])
        detail = self.assistant.fork(self.payload(snapshot), snapshot)
        accepted = self.assistant.send({'request_id': 'multilingual-followup-0001',
                                        'chat_id': detail['chat']['id'], 'message': 'Summarize this briefly.'})
        self.assistant.run_turn(detail['chat']['id'], accepted['turn_id'])
        self.assertEqual(self.assistant.detail(detail['chat']['id'])['chat']['status'], 'completed')
        self.assertIn('\u65e5' * MAX_CHARS, FakeClient.received[-1][1]['content'])

    def test_concurrent_receipt_wins_even_if_second_source_changed_during_read(self):
        snapshot = source(); payload = self.payload(snapshot)
        both_read = threading.Barrier(2)
        first_done = threading.Event()
        results = []; errors = []
        def first_source():
            both_read.wait(timeout=3)
            return snapshot
        def changed_source():
            both_read.wait(timeout=3)
            if not first_done.wait(timeout=3): raise RuntimeError('Fixture did not accept the first copy.')
            return source(messages=[{'role': 'user', 'text': 'Source changed after acceptance'}])
        def run(loader, mark=False):
            try: results.append(self.assistant.fork(payload, loader))
            except Exception as error: errors.append(error)
            finally:
                if mark: first_done.set()
        threads = [threading.Thread(target=run, args=(first_source, True)),
                   threading.Thread(target=run, args=(changed_source,))]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=5)
        self.assertFalse(errors)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]['chat']['id'], results[1]['chat']['id'])
        with self.assistant.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM local_origins').fetchone()[0], 1)


class NativeSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.history = make_history(self.root)

    def tearDown(self): self.temporary.cleanup()

    def add_turn(self, ordinal, status, label):
        with sqlite3.connect(self.root / 'thread_history_1.sqlite') as db:
            db.execute('INSERT INTO thread_turns VALUES (?,?,?,?,?,?)',
                       (CHAT, 'turn-' + str(ordinal), status, ordinal, ordinal + 1 if status == 'completed' else None, ordinal))
            for offset, kind, value in ((0, 'userMessage', {'content': [{'type': 'text', 'text': label + '_USER'},
                                                                     {'type': 'image', 'data': 'PRIVATE_PHOTO_BYTES'}]}),
                                        (1, 'agentMessage', {'text': label + '_ANSWER', 'phase': 'final'})):
                db.execute('INSERT INTO thread_items VALUES (?,?,?,?,?,?,?,?)',
                           (CHAT, str(ordinal + offset), kind, json.dumps(value), ordinal * 1000, None, None, ordinal + offset))

    def test_nearest_preceding_completed_turn_filter_is_read_only_and_omits_photos(self):
        self.add_turn(5, 'failed', 'FAILED')
        self.add_turn(7, 'completed', 'COMPLETED')
        self.add_turn(9, 'inProgress', 'ACTIVE')
        class Photos:
            def for_input(self, *args): raise AssertionError('Copying text must not resolve photo files.')
        self.history.photos = Photos()
        paths = [self.root / 'state_5.sqlite', self.root / 'thread_history_1.sqlite']
        before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
        snapshot = self.history.local_fork_snapshot(CHAT)
        value = preview(snapshot)
        self.assertTrue(snapshot['fork_completed_only'])
        self.assertTrue(snapshot['fork_omitted_incomplete'])
        self.assertEqual(value['message_count'], 4)
        encoded = json.dumps(value)
        self.assertIn('COMPLETED_ANSWER', encoded)
        for omitted in ('FAILED_', 'ACTIVE_', 'PRIVATE_PHOTO_BYTES', 'PRIVATE_INTERNAL_REASONING', 'PRIVATE_TOOL_OUTPUT'):
            self.assertNotIn(omitted, encoded)
        self.assertEqual(before, [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths])

    def test_source_pagination_is_bounded_before_preview(self):
        with sqlite3.connect(self.root / 'thread_history_1.sqlite') as db:
            for ordinal in range(10, 310):
                db.execute('INSERT INTO thread_items VALUES (?,?,?,?,?,?,?,?)',
                           (CHAT, str(ordinal), 'userMessage', json.dumps({'content': [{'type': 'text', 'text': 'Message ' + str(ordinal)}]}),
                            ordinal * 1000, None, None, ordinal))
        snapshot = self.history.local_fork_snapshot(CHAT)
        self.assertEqual(len(snapshot['messages']), 120)
        self.assertIsNotNone(snapshot['next_before'])
        self.assertTrue(preview(snapshot)['truncated'])
        self.assertIn('Message 309', json.dumps(snapshot))

    def test_failed_middle_turn_is_reported_even_when_latest_turn_completed(self):
        self.add_turn(5, 'failed', 'FAILED')
        self.add_turn(7, 'completed', 'COMPLETED')
        snapshot = self.history.local_fork_snapshot(CHAT)
        self.assertEqual(snapshot['last_recorded_turn']['status'], 'completed')
        self.assertTrue(snapshot['fork_omitted_incomplete'])
        value = preview(snapshot)
        self.assertTrue(value['truncated'])
        self.assertNotIn('FAILED_', json.dumps(value))
        self.assertIn('Some source text was left out.', value['notice'])

    def test_turn_status_and_messages_share_one_read_snapshot(self):
        self.add_turn(9, 'inProgress', 'ACTIVE')
        path = self.root / 'thread_history_1.sqlite'
        writer = sqlite3.connect(path)
        self.addCleanup(writer.close)
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('UPDATE thread_turns SET status=status')
        writer.commit()
        original = companion.readonly_db
        changed = []
        @contextmanager
        def interleaved(source_path):
            with original(source_path) as connection:
                class Connection:
                    def execute(self, sql, arguments=()):
                        cursor = connection.execute(sql, arguments)
                        if source_path.resolve() == path.resolve() and sql.startswith('SELECT turn_id,status,started_at,completed_at,rollout_ordinal'):
                            with sqlite3.connect(path) as writer:
                                writer.execute("UPDATE thread_turns SET status='completed' WHERE turn_id='turn-9'")
                            changed.append(True)
                        return cursor
                yield Connection()
        with patch('companion.readonly_db', interleaved):
            snapshot = self.history.local_fork_snapshot(CHAT)
        self.assertEqual(changed, [True])
        self.assertEqual(snapshot['last_recorded_turn']['status'], 'inProgress')
        self.assertNotIn('ACTIVE_', json.dumps(snapshot))


class ForkRouteTests(unittest.TestCase):
    setUp = LocalForkTests.setUp
    tearDown = LocalForkTests.tearDown

    @contextmanager
    def server(self, *, enabled=True, remote=False):
        native = Path(tempfile.mkdtemp(prefix='native-', dir=self.root))
        history = make_history(native)
        settings = RemoteSettings(self.root / 'remote.json') if remote else None
        access = AccessStore(self.root / 'access.sqlite') if remote else None
        if settings: settings.set_origin('https://phone.example')
        server = CompanionServer(('127.0.0.1', 0), history, Bridge(native, enabled=False),
                                  CommandJournal(self.root / 'cloud-commands.sqlite'),
                                  local_assistant=self.assistant if enabled else None,
                                  settings=settings, access=access, remote=remote)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        try: yield server, history
        finally:
            server.shutdown(); server.server_close(); worker.join(timeout=3)

    def request(self, server, path, *, method='GET', body=None, csrf=None, headers=None):
        values = {'Content-Type': 'application/json'}
        if server.remote:
            values.update(Host='phone.example', Origin='https://phone.example', **{'X-Forwarded-Proto': 'https'})
        if csrf: values['X-Companion-CSRF'] = csrf
        values.update(headers or {})
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=4)
        connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=values)
        response = connection.getresponse(); status = response.status; raw = response.read(); connection.close()
        return status, json.loads(raw)

    def test_preview_and_copy_keep_csrf_version_and_idempotent_source_recovery(self):
        with self.server() as (server, history):
            status, value = self.request(server, '/api/local/fork-preview?source_chat_id=' + CHAT)
            self.assertEqual(status, 200)
            payload = {'request_id': 'route-fork-request-000001', 'source_chat_id': CHAT,
                       'context_revision': value['revision'], 'model': DEFAULT_MODEL, 'skills': []}
            with patch.object(history, 'local_fork_snapshot', side_effect=AssertionError('Source must not be read before CSRF/version checks.')):
                self.assertEqual(self.request(server, '/api/local/fork', method='POST', body=payload)[0], 403)
                self.assertEqual(self.request(server, '/api/local/fork', method='POST', body=payload,
                                               csrf=server.csrf, headers={'X-Companion-UI': 'obsolete-build'})[0], 409)
            status, created = self.request(server, '/api/local/fork', method='POST', body=payload, csrf=server.csrf)
            self.assertEqual(status, 200)
            self.assertEqual(created['origin']['source_id'], CHAT)
            self.assertEqual(created['chat']['status'], 'idle')
            self.assertTrue(self.assistant.queue.empty())
            self.assertEqual(FakeClient.received, [])
            with patch.object(history, 'local_fork_snapshot', side_effect=AssertionError('Accepted recovery must not read an unavailable source.')):
                status, recovered = self.request(server, '/api/local/fork', method='POST', body=payload, csrf=server.csrf)
            self.assertEqual(status, 200)
            self.assertEqual(recovered['chat']['id'], created['chat']['id'])
            self.assertEqual(recovered['origin'], created['origin'])

    def test_stale_source_is_rejected_and_preview_requires_one_source(self):
        with self.server() as (server, history):
            self.assertEqual(self.request(server, '/api/local/fork-preview')[0], 400)
            self.assertEqual(self.request(server, '/api/local/fork-preview?source_chat_id=' + CHAT + '&source_chat_id=' + CHAT)[0], 400)
            _, value = self.request(server, '/api/local/fork-preview?source_chat_id=' + CHAT)
            with sqlite3.connect(history.history) as db:
                db.execute('UPDATE thread_items SET item_json=? WHERE item_id=?',
                           (json.dumps({'text': 'A newer visible answer', 'phase': 'final'}), '4'))
            status, result = self.request(server, '/api/local/fork', method='POST', csrf=server.csrf,
                                           body={'request_id': 'stale-fork-request-000001', 'source_chat_id': CHAT,
                                                 'context_revision': value['revision']})
            self.assertEqual(status, 400)
            self.assertIn('changed', result['error'])
            self.assertEqual(self.assistant.chats()['chats'], [])

    def test_read_only_and_unpaired_requests_cannot_read_source_or_create_copy(self):
        with self.server(enabled=False) as (server, history):
            with patch.object(history, 'local_fork_snapshot', side_effect=AssertionError('Read-only local mode must not read a source.')):
                self.assertEqual(self.request(server, '/api/local/fork-preview?source_chat_id=' + CHAT)[0], 409)
                self.assertEqual(self.request(server, '/api/local/fork', method='POST', body={}, csrf=server.csrf)[0], 409)
        with self.server(remote=True) as (server, history):
            with patch.object(history, 'local_fork_snapshot', side_effect=AssertionError('Unpaired requests must not read a source.')):
                self.assertEqual(self.request(server, '/api/local/fork-preview?source_chat_id=' + CHAT)[0], 401)
                self.assertEqual(self.request(server, '/api/local/fork', method='POST', body={})[0], 401)


if __name__ == '__main__': unittest.main()
