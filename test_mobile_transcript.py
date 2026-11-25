import json
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from test_companion import CHAT, make_history


class MobileTranscriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.history = make_history(self.root)
        self.path = self.root / 'sessions' / 'rollout.jsonl'
        self.path.parent.mkdir()
        with closing(sqlite3.connect(self.history.state)) as db, db:
            db.execute('ALTER TABLE threads ADD COLUMN rollout_path TEXT')
            db.execute('UPDATE threads SET rollout_path=?', (str(self.path),))
        self.append({'type': 'session_meta', 'payload': {'id': CHAT}})

    def append(self, row):
        with self.path.open('a') as stream:
            stream.write(json.dumps(row) + '\n')

    def reply(self, ident, text, **extra):
        self.append({'type': 'response_item', 'timestamp': '2026-11-25T12:00:00Z',
                     'payload': {'type': 'message', 'id': ident, 'role': 'assistant',
                                 'phase': 'final_answer', 'content': [{'type': 'output_text', 'text': text}], **extra}})

    def test_open_chat_reads_newer_public_reply_then_reconciles_index(self):
        self.reply('new', 'Latest public reply')
        messages = self.history.read_thread(CHAT)['messages']
        self.assertEqual([m['id'] for m in messages], ['1', '4', 'new'])
        self.assertEqual(messages[-1]['text'], 'Latest public reply')
        with closing(sqlite3.connect(self.history.history)) as db, db:
            db.execute('INSERT INTO thread_items VALUES (?,?,?,?,?,?,?,?)',
                       (CHAT, 'new', 'agentMessage', json.dumps({'text':'Latest public reply'}), 1795608000000, None, None, 8))
        self.assertEqual([m['id'] for m in self.history.read_thread(CHAT)['messages']], ['1','4','new'])
        self.assertEqual([m['id'] for m in self.history.read_thread(CHAT, before=4)['messages']], ['1'])

    def test_only_public_messages_from_the_selected_session(self):
        self.reply('private', 'INTERNAL', role='developer')
        self.reply('tool', 'INTERNAL', recipient='tool')
        self.reply('analysis', 'INTERNAL', channel='analysis')
        self.reply('human', 'INTERNAL', role='user')
        self.reply('final', 'access_token=abcdefghijkl')
        result = json.dumps(self.history.read_thread(CHAT)['messages'])
        self.assertNotIn('INTERNAL', result)
        self.assertNotIn('abcdefghijkl', result)
        self.assertIn('[hidden]', result)
        self.path.write_text(self.path.read_text().replace('"id": "'+CHAT+'"','"id": "different"'))
        self.assertEqual(len(self.history.read_thread(CHAT)['messages']), 2)

    def test_accepted_steering_becomes_visible_pending_stays_hidden(self):
        with closing(sqlite3.connect(self.history.history)) as db, db:
            for i, state in enumerate(('pending','accepted'),10):
                body={'type':'steeringUserMessage','status':state,'input':[{'type':'text','text':state+' message'}]}
                db.execute('INSERT INTO thread_items VALUES (?,?,?,?,?,?,?,?)',
                           (CHAT,str(i),'steeringUserMessage',json.dumps(body),2000+i,None,None,i))
        thread=self.history.read_thread(CHAT)
        self.assertEqual(thread['messages'][-1]['text'],'accepted message')
        self.assertNotIn('pending message',json.dumps(thread['messages']))
        self.assertEqual(self.history.list_threads()[0]['preview'],'accepted message')

    def test_foreign_path_partial_record_and_rollback_are_ignored(self):
        self.reply('retracted','do not retain')
        self.append({'type':'event_msg','payload':{'type':'thread_rolled_back'}})
        self.reply('kept','Keep this')
        with self.path.open('a') as f:
            f.write('{"incomplete":')
        self.assertEqual([m['id'] for m in self.history.read_thread(CHAT)['messages']],['1','4','kept'])
        outside=self.root/'elsewhere.jsonl'
        self.path.rename(outside)
        self.path.symlink_to(outside)
        self.assertEqual(len(self.history.read_thread(CHAT)['messages']),2)

    def test_verified_user_text_partial_assistant_and_status_scope(self):
        from transcript import owner_tail,merge_tail
        self.reply('human', 'phone', role='user', content=[{'type':'input_text','text':'Human reply'}],
                   internal_chat_message_metadata_passthrough={'content_item_kinds':['user.text']})
        msg=self.history.read_thread(CHAT)['messages']
        self.assertEqual(msg[-1]['role'],'user')
        tail=owner_tail([{'turnStartedAtMs':1010,'items':[
            {'type':'agentMessage','id':'4','text':msg[1]['text']+' continued'},
            {'type':'reasoning','id':'invisible','text':'INTERNAL'},
            {'type':'steeringUserMessage','id':'draft','status':'pending','input':[{'type':'text','text':'INTERNAL'}]},
            {'type':'agentMessage','id':'middle','text':'Arrived via owner','startedAtMs':2000},
        ]}],self.history.public_text)
        merge_tail(msg,tail)
        self.assertEqual([m['id'] for m in msg],['1','4','middle','human'])
        self.assertTrue(msg[1]['text'].endswith(' continued'))
        merge_tail(msg,tail)
        self.assertEqual(len(msg),4)
        self.assertNotIn('INTERNAL',json.dumps(msg))

    def test_large_session_tail_skips_partial_lines_and_invalid_records(self):
        self.reply('early','outside bounded tail')
        with self.path.open('ab') as stream:
            stream.write(b'x' * (1024*1024+200) + b'\n')
            stream.write(b'bad json\n')
        self.reply('invalid-time','discard')
        self.path.write_bytes(self.path.read_bytes().replace(b'2026-11-25T12:00:00Z',b'bad-time'))
        self.reply('last','Retain me')
        self.assertEqual([m['id'] for m in self.history.read_thread(CHAT)['messages']],['1','4','last'])
        self.path.unlink()
        self.assertEqual(len(self.history.read_thread(CHAT)['messages']),2)
