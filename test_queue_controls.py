import base64
from contextlib import closing
import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from companion import CommandJournal,CompanionServer
from desktop_runtime import change_queued_message,queue_photo_message,QueueNotClaimed
from questions import answer_envelope,replies
from runtime import RuntimeUnavailable
from promotions import QueuePromotions
from test_companion import make_history,CHAT


class QueueProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.photo=Path(self.temp.name)/'owned.jpg';self.photo.write_bytes(b'owned JPEG bytes')
        self.calls=[];self.queued={'id':'queue-1','clientUserMessageId':'client-1','input':[{'type':'text','text':'Original','text_elements':[]},{'type':'image','url':'data:image/jpeg;base64,'+base64.b64encode(self.photo.read_bytes()).decode()}]}
        self.item={'id':'queue-1','client_id':'client-1','text':'Original','photos':[str(self.photo)],'image_urls':[self.queued['input'][1]['url']],'source_photos':[('image',self.queued['input'][1]['url'])]}
        outer=self
        class Client:
            def __init__(self,*args,**kwargs):pass
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def call(self,method,params):
                outer.calls.append((method,params))
                if method=='thread/queue/list':return {'data':[outer.queued]}
                if method=='thread/queue/delete':return {'deleted':True}
                if method in ('thread/queue/update','thread/queue/add'):
                    identity=params.get('clientUserMessageId',outer.queued['clientUserMessageId'])
                    inputs=[part if part['type']!='localImage' else outer.queued['input'][1] for part in params['input']]
                    return {'queuedSubmission':{'id':'queue-1','clientUserMessageId':identity,'input':inputs}}
                raise AssertionError('Unexpected non-queue operation')
        self.client=Client

    def test_normalized_photo_queue_acknowledgement_is_verified_by_bytes(self):
        result=queue_photo_message(CHAT,'Original',[str(self.photo)],'/test/codex',self.client)
        self.assertEqual(result['state'],'queued');self.assertEqual([m for m,_ in self.calls],['thread/queue/add'])
        self.queued['input'][1]['url']='data:image/jpeg;base64,'+base64.b64encode(b'different photo').decode()
        with self.assertRaisesRegex(RuntimeUnavailable,'content acknowledgement'):queue_photo_message(CHAT,'Original',[str(self.photo)],'/test/codex',self.client)
        self.queued['input'][1]['url']='https://external.example/image'
        with self.assertRaises(RuntimeUnavailable):queue_photo_message(CHAT,'Original',[str(self.photo)],'/test/codex',self.client)



    def test_edit_keeps_identity_and_photo_and_delete_uses_only_the_verified_entry(self):
        changes=QueuePromotions(Path(self.temp.name)/'changes.sqlite')
        result=change_queued_message(CHAT,self.item,'Edited',executable='/test/codex',client_factory=self.client,recovery=changes)
        self.assertEqual(result['state'],'updated')
        self.assertEqual([m for m,_ in self.calls],['thread/queue/list','thread/queue/update'])
        self.assertEqual(self.calls[-1][1]['input'][1],self.queued['input'][1])
        saved=changes.pending(CHAT,lambda *_:False,str)
        self.assertEqual(saved[0]['text'],'Original');self.assertTrue(saved[0]['confirmed']);self.assertEqual(saved[0]['kind'],'edit')
        changes.dismiss(CHAT,saved[0]['id']);self.assertEqual(changes.pending(CHAT,lambda *_:False,str),[])
        result=change_queued_message(CHAT,self.item,None,executable='/test/codex',client_factory=self.client,recovery=changes)
        self.assertEqual(result['state'],'deleted');self.assertEqual(changes.pending(CHAT,lambda *_:False,str)[0]['title'],'Deleted queued message')

    def test_a_changed_native_item_is_rejected_before_update_or_delete(self):
        self.queued['input'][0]['text']='Edited on the Mac'
        for message in ['Edited',None]:
            with self.assertRaisesRegex(QueueNotClaimed,'edited'):
                change_queued_message(CHAT,self.item,message,executable='/test/codex',client_factory=self.client)
        self.assertEqual([m for m,_ in self.calls],['thread/queue/list','thread/queue/list'])

    def test_unverified_mutation_retains_recovery_without_claiming_success(self):
        changes=QueuePromotions(Path(self.temp.name)/'changes.sqlite')
        base=self.client
        class LostAcknowledgement(base):
            def call(self,method,params):
                result=super().call(method,params)
                if method=='thread/queue/update':result['queuedSubmission']['clientUserMessageId']='wrong-identity'
                return result
        with self.assertRaises(RuntimeUnavailable):change_queued_message(CHAT,self.item,'Edited',executable='/test/codex',client_factory=LostAcknowledgement,recovery=changes)
        saved=changes.pending(CHAT,lambda *_:False,str)[0]
        self.assertFalse(saved['confirmed']);self.assertEqual(saved['text'],'Original')
        class MalformedAcknowledgement(base):
            def call(self,method,params):
                return {'queuedSubmission':[]} if method=='thread/queue/update' else super().call(method,params)
        with self.assertRaises(RuntimeUnavailable):change_queued_message(CHAT,self.item,'Edited',executable='/test/codex',client_factory=MalformedAcknowledgement,recovery=changes)

    def test_old_confirmed_backups_expire_without_dropping_uncertain_versions_or_photos(self):
        changes=QueuePromotions(Path(self.temp.name)/'changes.sqlite')
        photo=Path(self.temp.name)/('a'*32+'.jpg');photo.write_bytes(b'protected photo')
        item={**self.item,'photos':[str(photo)]}
        old=changes.capture_change(CHAT,item,'delete');changes.stage(old,'deleted')
        unknown=changes.capture_change(CHAT,item,'edit');changes.stage(unknown,'change_unknown')
        with closing(sqlite3.connect(changes.path)) as db,db:db.execute('UPDATE promotions SET created=0')
        changes.capture_change(CHAT,item,'edit')
        saved=changes.pending(CHAT,lambda *_:True,str)
        self.assertNotIn(old,[q['id'] for q in saved]);self.assertIn(unknown,[q['id'] for q in saved])
        self.assertTrue(photo.exists());self.assertEqual(next(q for q in saved if q['id']==unknown)['photos'][0]['id'],'a'*32)


class QueueRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.history=make_history(self.root);self.calls=[];self.fail=False
        self.queue=self.root/'queue_1.sqlite'
        with sqlite3.connect(self.queue) as db:
            db.execute('CREATE TABLE queued_items(id TEXT,thread_id TEXT,payload_json TEXT,created_at_ms INTEGER,queue_order INTEGER)')
            db.execute('INSERT INTO queued_items VALUES (?,?,?,?,?)',('queued-1',CHAT,self.payload('Original'),2000,1))
        bridge=SimpleNamespace(availability=lambda:{'available':True},executable='/test/codex')
        self.server=CompanionServer(('127.0.0.1',0),self.history,bridge,CommandJournal(self.root/'commands.sqlite'))
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True);self.worker.start()
        self.patch=patch('companion.change_queued_message',side_effect=self.change);self.patch.start()

    def tearDown(self):
        self.patch.stop();self.server.shutdown();self.server.server_close();self.worker.join();self.temp.cleanup()

    @staticmethod
    def payload(text):return json.dumps({'UserInput':{'client_id':'client-1','content':[{'type':'text','text':text,'text_elements':[]}]}})

    def change(self,thread,item,message,**kwargs):
        self.calls.append((thread,item,message))
        if self.fail:raise RuntimeUnavailable('Acknowledgement lost')
        with sqlite3.connect(self.queue) as db:
            if message is None:db.execute('DELETE FROM queued_items WHERE id=?',(item['id'],))
            else:db.execute('UPDATE queued_items SET payload_json=? WHERE id=?',(self.payload(message),item['id']))
        return {'state':'deleted' if message is None else 'updated','message':'Saved'}

    def request(self,operation,payload,csrf=True):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3);c.request('POST','/api/queue/'+operation,json.dumps(payload),{'X-Companion-CSRF':self.server.csrf} if csrf else {})
        r=c.getresponse();result=r.status,json.loads(r.read());c.close();return result

    def command(self):return {'command_id':'queue-request-12345678','thread_id':CHAT,'queue_id':'queued-1','revision':self.history.pending_instructions(CHAT)[0]['revision'],'message':'Edited'}

    def test_edit_is_revision_guarded_authenticated_and_idempotent(self):
        item=self.history.pending_instructions(CHAT)[0]
        self.assertTrue(item['can_steer'] and item['can_edit'] and item['can_delete']);self.assertEqual(item['controls_reason'],'')
        payload=self.command();self.assertEqual(self.request('edit',payload,False)[0],403);self.assertEqual(self.request('edit',{**payload,'revision':'0'*64})[0],409);self.assertEqual(self.calls,[])
        payload['command_id']='valid-edit-request-123';self.assertEqual(self.request('edit',payload)[0],200);self.assertEqual(self.request('edit',payload)[0],200);self.assertEqual(len(self.calls),1)
        self.assertEqual(self.history.pending_instructions(CHAT)[0]['editable_text'],'Edited')

    def test_uncertain_edit_is_never_automatically_replayed(self):
        self.fail=True;payload=self.command();self.assertEqual(self.request('edit',payload)[1]['state'],'unknown');self.assertEqual(self.request('edit',payload)[1]['state'],'unknown');self.assertEqual(len(self.calls),1)

    def test_question_edit_preserves_envelope_and_delete_allows_a_new_answer(self):
        question={'id':'question-1','title':'Where?'}
        with sqlite3.connect(self.queue) as db:db.execute('UPDATE queued_items SET payload_json=?',(self.payload(answer_envelope(question,'Old answer')),))
        payload=self.command();self.assertEqual(self.request('edit',payload)[0],200)
        answer=replies(self.calls[-1][2])[0];self.assertEqual(answer,{'questionItemId':'question-1','question':'Where?','answer':'Edited'})
        self.server.journal.execute('answer-one',{},lambda:{'state':'queued','queue_item_id':'queued-1'})
        self.server.journal.execute('unrelated-command',{},lambda:{'state':'queued','queue_item_id':'queued-1'})
        payload=self.command();payload['command_id']='delete-request-12345';self.assertEqual(self.request('delete',payload)[0],200)
        self.assertIsNone(self.server.journal.lookup('answer-one'));self.assertIsNotNone(self.server.journal.lookup('unrelated-command'));self.assertEqual(self.history.pending_instructions(CHAT),[])


if __name__=='__main__':unittest.main()
