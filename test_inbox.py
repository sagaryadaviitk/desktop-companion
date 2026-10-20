import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import http.client
import threading

from inbox import Inbox
from test_companion import CHAT,make_history
from companion import Bridge,CommandJournal,CompanionServer


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.inbox=Inbox(self.root/'inbox.sqlite')
    def tearDown(self):self.temp.cleanup()
    def chat(self,revision='a',native=None,identifier='chat'):
        return {'id':identifier,'message_revision':revision,'desktop_unread':native}

    def test_initial_history_is_not_all_marked_unread_but_native_unread_is_preserved(self):
        values=self.inbox.decorate([self.chat(identifier='old'),self.chat(native=True,identifier='unread')])
        self.assertFalse(values[0]['unread']);self.assertTrue(values[1]['unread'])

    def test_new_reply_and_new_chat_are_unread_then_durable_mark_clears_exact_revision(self):
        self.inbox.decorate([self.chat()])
        self.assertTrue(self.inbox.decorate([self.chat('b')])[0]['unread'])
        self.assertTrue(self.inbox.decorate([self.chat(identifier='new')])[0]['unread'])
        self.inbox.mark('chat','b')
        other=Inbox(self.root/'inbox.sqlite')
        self.assertFalse(other.decorate([self.chat('b')])[0]['unread'])
        self.assertTrue(other.decorate([self.chat('c')])[0]['unread'])

    def test_marking_older_revision_does_not_clear_new_reply(self):
        self.inbox.decorate([self.chat()]);self.inbox.mark('chat','a')
        self.assertTrue(self.inbox.decorate([self.chat('b')])[0]['unread'])

    def test_an_older_inflight_read_cannot_overwrite_a_newer_browser_receipt(self):
        captured=threading.Event();release=threading.Event();new_started=threading.Event();results=[]
        def old_revision():
            captured.set();self.assertTrue(release.wait(2));return 'a'
        def old_reader():results.append(self.inbox.mark_current('chat','a',old_revision))
        def new_reader():
            new_started.set();results.append(self.inbox.mark_current('chat','b',lambda:'b'))
        first=threading.Thread(target=old_reader);second=threading.Thread(target=new_reader)
        first.start();self.assertTrue(captured.wait(2));second.start();self.assertTrue(new_started.wait(2));release.set()
        first.join(2);second.join(2);self.assertFalse(first.is_alive());self.assertFalse(second.is_alive())
        self.assertEqual(results,[True,True]);self.assertFalse(self.inbox.decorate([self.chat('b')])[0]['unread'])
        self.assertFalse(self.inbox.mark_current('chat','a',lambda:'b'))
        self.assertFalse(self.inbox.decorate([self.chat('b')])[0]['unread'])

    def test_desktop_read_receipt_and_companion_read_are_respected(self):
        self.assertTrue(self.inbox.decorate([self.chat(native=True)])[0]['unread'])
        self.inbox.mark('chat','a')
        self.assertFalse(self.inbox.decorate([self.chat(native=True)])[0]['unread'])
        self.assertFalse(self.inbox.decorate([self.chat('b',native=False)])[0]['unread'])

    def test_explicit_unread_and_no_assistant_reply(self):
        self.inbox.decorate([self.chat(None)]);self.assertFalse(self.inbox.decorate([self.chat(None)])[0]['unread'])
        self.inbox.mark('chat',None,True);self.assertTrue(self.inbox.decorate([self.chat(None,native=False)])[0]['unread'])

    def test_viewed_revision_persists_through_catalog_refresh_and_never_clears_a_newer_reply(self):
        history=make_history(self.root);revision=history.read_thread(CHAT)['message_revision']
        self.inbox.mark(CHAT,revision,True)
        server=CompanionServer(('127.0.0.1',0),history,Bridge(self.root,enabled=False),CommandJournal(self.root/'journal.sqlite'),inbox=self.inbox)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        def request(path,body=None):
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            conn.request('POST' if body else 'GET',path,body=json.dumps(body) if body else None,headers={'X-Companion-CSRF':server.csrf})
            response=conn.getresponse();result=response.status,json.loads(response.read());conn.close();return result
        def mark(value):return request('/api/read-state',{'thread_id':CHAT,'revision':value,'operation':'read'})
        try:
            self.assertTrue(request('/api/chats')[1]['chats'][0]['unread'])
            before=[hashlib.sha256(path.read_bytes()).digest() for path in (history.state,history.history)]
            self.assertEqual(mark(revision)[0],200)
            self.assertFalse(request('/api/chats')[1]['chats'][0]['unread'])
            self.assertFalse(Inbox(self.inbox.path).decorate(history.list_threads())[0]['unread'])
            self.assertEqual(before,[hashlib.sha256(path.read_bytes()).digest() for path in (history.state,history.history)])
            with sqlite3.connect(history.history) as db:db.execute('UPDATE thread_items SET item_json=? WHERE item_id=?',(json.dumps({'text':'A newer reply arrived'}),'4'))
            newer=history.read_thread(CHAT)['message_revision']
            self.assertNotEqual(newer,revision);self.assertTrue(request('/api/chats')[1]['chats'][0]['unread'])
            self.assertEqual(mark(revision)[0],409);self.assertTrue(request('/api/chats')[1]['chats'][0]['unread'])
            self.assertEqual(mark(newer)[0],200);self.assertFalse(request('/api/chats')[1]['chats'][0]['unread'])
        finally:server.shutdown();server.server_close();worker.join()

    def test_history_previews_and_revisions_are_safe_and_track_streamed_edits(self):
        history=make_history(self.root);sources=[history.state,history.history]
        before=[hashlib.sha256(path.read_bytes()).digest() for path in sources]
        chat=history.list_threads()[0];revision=chat['message_revision']
        self.assertIn('done',chat['preview']);self.assertNotIn('PRIVATE',json.dumps(chat))
        self.assertEqual(revision,history.read_thread(CHAT)['message_revision'])
        self.assertEqual(before,[hashlib.sha256(path.read_bytes()).digest() for path in sources])
        with sqlite3.connect(history.history) as db:
            db.execute("UPDATE thread_items SET item_json=? WHERE item_id='4'",(json.dumps({'text':'Updated text Authorization: Bearer secretvalue123'}),))
        updated=history.list_threads()[0]
        self.assertNotEqual(revision,updated['message_revision']);self.assertNotIn('secretvalue123',updated['preview'])

    def test_native_receipts_are_optional_and_read_only(self):
        history=make_history(self.root)
        self.assertIsNone(history.list_threads()[0]['desktop_unread'])
        with sqlite3.connect(history.state) as db:
            db.execute('CREATE TABLE thread_read_receipts(thread_id TEXT,first_unread_turn TEXT)')
            db.execute('INSERT INTO thread_read_receipts VALUES (?,?)',(CHAT,'turn-1'))
        self.assertTrue(history.list_threads()[0]['desktop_unread'])

    def test_desktop_read_receipt_for_an_older_turn_cannot_clear_newer_history(self):
        history=make_history(self.root)
        with sqlite3.connect(history.state) as db:
            db.execute('CREATE TABLE thread_read_receipts(thread_id TEXT,first_unread_turn TEXT,last_published_turn TEXT)')
            db.execute('INSERT INTO thread_read_receipts VALUES (?,NULL,?)',(CHAT,'older-turn'))
        self.assertIsNone(history.list_threads()[0]['desktop_unread'])
        with sqlite3.connect(history.state) as db:db.execute('UPDATE thread_read_receipts SET last_published_turn=?',('turn-1',))
        self.assertFalse(history.list_threads()[0]['desktop_unread'])
