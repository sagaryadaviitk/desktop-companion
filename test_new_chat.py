import hashlib
from contextlib import closing
import http.client
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock,patch
from types import SimpleNamespace
from photos import PhotoStore,PhotoError
from test_photos import photo_data
from companion import History

from companion import Bridge, CommandJournal, CompanionServer
from new_chat import NewChat,prompt_title
from runtime import RuntimeUnavailable
from test_companion import make_history


IDENTITY='12345678-1234-4321-8234-123456789abc'


class NewChatTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        self.history=make_history(self.root)
        self.folder=self.root/'project';self.folder.mkdir()
        with closing(sqlite3.connect(self.root/'state_5.sqlite')) as db,db:
            db.execute('INSERT INTO project_roots VALUES (?,?,0)',('project-1',str(self.folder)))
        self.calls=[];self.fail_name=False;self.bad_id=False
        outer=self
        class Client:
            def __init__(self,command,timeout):outer.command=command
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def call(self,method,params):
                outer.calls.append((method,params))
                if method=='thread/start':
                    if not outer.bad_id:
                        with closing(sqlite3.connect(outer.root/'state_5.sqlite')) as db,db:
                            db.execute('INSERT INTO threads SELECT ?,?,title,?,updated_at,updated_at_ms,is_pinned,archived,agent_path,recency_at_ms,?,model,git_branch FROM threads LIMIT 1',(IDENTITY,'',params['cwd'],params.get('projectId')))
                    return {'thread':{'id':'../bad' if outer.bad_id else IDENTITY}}
                if method=='thread/name/set':
                    if outer.fail_name:raise RuntimeUnavailable('Naming failed')
                    with closing(sqlite3.connect(outer.root/'state_5.sqlite')) as db,db:db.execute('UPDATE threads SET name=? WHERE id=?',(params['name'],params['threadId']))
                    return {}
                raise AssertionError('Creation must never run or resume a chat')
        self.opener=Mock()
        self.creator=NewChat(self.root,self.root/'personal',Client,'/test/codex',self.opener)

    def tearDown(self):self.temp.cleanup()

    def test_saved_project_binding_and_only_empty_chat_methods(self):
        target=next(t for t in self.creator.targets() if t['project_key']=='project:project-1')
        result=self.creator.create(target['id'],'Phone chat')
        self.assertEqual(result['state'],'created');self.assertTrue(result['desktop_opened'])
        self.assertEqual(self.command,['/test/codex','app-server','--stdio'])
        self.assertEqual(self.calls,[('thread/start',{'cwd':str(self.folder),'projectId':'project-1','historyMode':'paginated','ephemeral':False}),('thread/name/set',{'threadId':IDENTITY,'name':'Phone chat'})])
        self.opener.assert_called_once_with(IDENTITY)

    def test_public_targets_do_not_expose_paths_and_source_is_read_only(self):
        state=self.root/'state_5.sqlite';before=hashlib.sha256(state.read_bytes()).hexdigest()
        targets=self.creator.targets();self.assertEqual(len(targets),2)
        self.assertNotIn(str(self.root),json.dumps(targets))
        self.assertEqual(before,hashlib.sha256(state.read_bytes()).hexdigest())

    def test_personal_target_creates_only_its_private_workspace(self):
        result=self.creator.create('personal')
        self.assertEqual(result['thread_id'],IDENTITY)
        self.assertEqual((self.root/'personal').stat().st_mode&0o777,0o700)
        self.assertIsNone(self.calls[0][1]['projectId']);self.assertTrue(result['project_verified'])

    def test_prompt_names_are_short_and_photo_only_has_a_name(self):
        self.assertEqual(prompt_title('  ## Can you explain this error?  '),'Can you explain this error')
        self.assertLessEqual(len(prompt_title('A long question '*30)),72)
        self.assertEqual(prompt_title('',True),'Photo question')

    def test_rename_changes_only_the_existing_native_name_and_verifies_it(self):
        self.creator.create('personal','Original');self.calls=[]
        self.assertEqual(self.creator.rename(IDENTITY,'Updated name')['state'],'updated')
        self.assertEqual(self.calls,[('thread/name/set',{'threadId':IDENTITY,'name':'Updated name'})])
        for value in ['', '  ', 'bad\nname', 'x'*121]:
            with self.assertRaises(ValueError):self.creator.rename(IDENTITY,value)

    def test_missing_path_unknown_target_and_invalid_title_do_not_create(self):
        for target,title in [('../../repo','Title'),('personal','a'*121),('personal','line\nbreak'),('personal',{})]:
            with self.assertRaises(ValueError):self.creator.create(target,title)
        target=self.creator.targets()[-1];self.folder.rmdir()
        with self.assertRaises(ValueError):self.creator.create(target['id'])
        self.assertEqual(self.calls,[])

    def test_creation_is_successful_even_if_naming_or_navigation_fails(self):
        self.fail_name=True;self.opener.side_effect=subprocess.TimeoutExpired('open',5)
        result=self.creator.create('personal','Title')
        self.assertEqual(result['state'],'created');self.assertFalse(result['named']);self.assertFalse(result['desktop_opened'])
        self.assertEqual(sum(method=='thread/start' for method,_ in self.calls),1)

    def test_invalid_creation_acknowledgement_never_opens_arbitrary_link(self):
        self.bad_id=True
        with self.assertRaises(RuntimeUnavailable):self.creator.create('personal')
        self.opener.assert_not_called()

    def test_legacy_personal_folder_is_not_inferred_into_its_parent_project(self):
        personal=self.root/'project/.state/chat-workspace';personal.mkdir(parents=True)
        with closing(sqlite3.connect(self.root/'state_5.sqlite')) as db,db:db.execute('UPDATE threads SET project_id=NULL,cwd=?',(str(personal),))
        history=History(self.root,personal_workspaces=[personal])
        self.assertEqual(history.list_threads()[0]['project_keys'],['personal'])
        self.assertEqual(history.list_threads()[0]['workspace'],'Personal')

    def test_prompt_first_creation_photo_binding_and_retries_send_exactly_once(self):
        store=PhotoStore(self.root/'photos');runtime=SimpleNamespace(capabilities=lambda _: {'send_now':True},send_now=Mock(return_value={'state':'sent','message':'Started'}))
        bridge=SimpleNamespace(availability=lambda:{'available':True})
        server=CompanionServer(('127.0.0.1',0),self.history,bridge,CommandJournal(self.root/'cmd.sqlite'),new_chat=self.creator,photos=store,runtime=runtime)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        def request(path,payload):
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            conn.request('POST',path,body=json.dumps(payload),headers={'X-Companion-CSRF':server.csrf})
            response=conn.getresponse();result=(response.status,json.loads(response.read()));conn.close();return result
        try:
            draft='new-chat-draft-123456789';photo=request('/api/photos',{'draft_id':draft,'data_url':photo_data(),'name':'Test.png'})[1]
            payload={'command_id':'prompt-first-request-12345','target_id':'personal','draft_id':draft,'message':'Explain the issue in this photo?','photos':[photo['id']]}
            first=request('/api/chats/create',payload);second=request('/api/chats/create',payload)
            self.assertEqual(first,second);self.assertEqual(first[0],200);self.assertEqual(first[1]['delivery']['state'],'sent')
            self.assertEqual(self.creator.metadata(IDENTITY)['name'],'Explain the issue in this photo')
            self.assertIsNone(self.creator.metadata(IDENTITY)['project_id'])
            runtime.send_now.assert_called_once_with(IDENTITY,payload['message'],photos=store.resolve([photo['id']],IDENTITY,'local'))
            self.assertEqual(sum(method=='thread/start' for method,_ in self.calls),1)
            self.assertEqual(request('/api/chats/create',{**payload,'message':'Edited'})[0],400)
            self.assertEqual(request('/api/chat/rename',{'thread_id':IDENTITY,'command_id':'rename-request-12345678','title':'My renamed photo question'})[0],200)
            self.assertEqual(self.creator.metadata(IDENTITY)['name'],'My renamed photo question')
        finally:server.shutdown();server.server_close();worker.join()

    def test_pending_connection_can_retry_but_unknown_delivery_never_replays(self):
        bridge=SimpleNamespace(availability=lambda:{'available':True})
        create=self.creator.create
        self.creator.create=lambda *args:{**create(*args),'project_verified':False}
        runtime=SimpleNamespace(capabilities=lambda _: {'send_now':False},send_now=Mock(side_effect=RuntimeUnavailable('Lost acknowledgement')))
        server=CompanionServer(('127.0.0.1',0),self.history,bridge,CommandJournal(self.root/'cmd.sqlite'),new_chat=self.creator,runtime=runtime)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        def request():
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3);payload={'command_id':'pending-first-request-12345','target_id':'personal','draft_id':'photo-draft-123456789','message':'First question','photos':[]}
            conn.request('POST','/api/chats/create',body=json.dumps(payload),headers={'X-Companion-CSRF':server.csrf});response=conn.getresponse();result=json.loads(response.read());conn.close();return result
        try:
            first=request();self.assertTrue(first['project_verified']);self.assertEqual(first['delivery']['state'],'pending');runtime.send_now.assert_not_called()
            runtime.capabilities=lambda _: {'send_now':True}
            self.assertEqual(request()['delivery']['state'],'unknown')
            self.assertEqual(request()['delivery']['state'],'unknown');runtime.send_now.assert_called_once()
            self.assertEqual(sum(method=='thread/start' for method,_ in self.calls),1)
        finally:server.shutdown();server.server_close();worker.join()

    def test_project_verification_checks_both_native_assignment_and_folder(self):
        self.creator.create('personal')
        self.assertTrue(self.creator.verify_target(IDENTITY,'personal'))
        with closing(sqlite3.connect(self.root/'state_5.sqlite')) as db,db:db.execute('UPDATE threads SET cwd=? WHERE id=?',(str(self.folder),IDENTITY))
        self.assertFalse(self.creator.verify_target(IDENTITY,'personal'))
        with closing(sqlite3.connect(self.root/'state_5.sqlite')) as db,db:db.execute('UPDATE threads SET cwd=?,project_id=? WHERE id=?',(str(self.root/'personal'),'project-1',IDENTITY))
        self.assertFalse(self.creator.verify_target(IDENTITY,'personal'))

    def test_new_photo_draft_cannot_be_taken_from_another_owner_or_draft(self):
        store=PhotoStore(self.root/'photos');photo=store.upload('draft:own-draft','phone-a',photo_data())
        for draft,owner in [('draft:other','phone-a'),('draft:own-draft','phone-b')]:
            with self.assertRaises(PhotoError):store.bind_draft([photo['id']],draft,IDENTITY,owner)
        paths=store.bind_draft([photo['id']],'draft:own-draft',IDENTITY,'phone-a')
        self.assertEqual(store.bind_draft([photo['id']],'draft:own-draft',IDENTITY,'phone-a'),paths)
        with self.assertRaises(PhotoError):store.bind_draft([photo['id']],'draft:own-draft','another-chat','phone-a')

    def test_post_creation_verification_or_photo_errors_keep_the_known_chat(self):
        store=PhotoStore(self.root/'photos');draft='photo-draft-recovery-12345'
        photo=store.upload('draft:'+draft,'local',photo_data())
        store.bind_draft=Mock(side_effect=PhotoError('Draft expired'))
        runtime=SimpleNamespace(capabilities=lambda _: {'send_now':True},send_now=Mock())
        server=CompanionServer(('127.0.0.1',0),self.history,SimpleNamespace(availability=lambda:{'available':True}),CommandJournal(self.root/'cmd.sqlite'),new_chat=self.creator,photos=store,runtime=runtime)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        def request():
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            payload={'command_id':'post-create-recovery-12345','target_id':'personal','draft_id':draft,'message':'Explain this photo','photos':[photo['id']]}
            conn.request('POST','/api/chats/create',body=json.dumps(payload),headers={'X-Companion-CSRF':server.csrf});response=conn.getresponse();result=(response.status,json.loads(response.read()));conn.close();return result
        try:
            first=request();self.assertEqual(first[0],200);self.assertEqual(first[1]['thread_id'],IDENTITY);self.assertEqual(first[1]['delivery']['state'],'blocked')
            with patch.object(self.creator,'metadata',side_effect=RuntimeUnavailable('History not ready')):
                second=request();self.assertEqual(second[1]['thread_id'],IDENTITY);self.assertEqual(second[1]['delivery']['state'],'pending')
            self.assertEqual(request(),first);runtime.send_now.assert_not_called()
            self.assertEqual(sum(method=='thread/start' for method,_ in self.calls),1)
        finally:server.shutdown();server.server_close();worker.join()

    def test_http_auth_csrf_field_rejection_and_creation_dedup(self):
        bridge=Bridge(self.root,enabled=False);bridge.availability=lambda:{'available':True}
        server=CompanionServer(('127.0.0.1',0),self.history,bridge,CommandJournal(self.root/'commands.sqlite'),new_chat=self.creator)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        def request(payload,headers=None):
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            conn.request('POST','/api/chats/create',body=json.dumps(payload),headers=headers or {})
            response=conn.getresponse();result=(response.status,json.loads(response.read()));conn.close();return result
        try:
            payload={'command_id':'new-chat-test-command-123','target_id':'personal','title':'From phone'}
            self.assertEqual(request(payload)[0],403)
            self.assertEqual(request(payload,{'Origin':'https://attacker.example','X-Companion-CSRF':server.csrf})[0],403)
            headers={'X-Companion-CSRF':server.csrf}
            self.assertEqual(request({**payload,'cwd':'/private'},headers)[0],400)
            first=request(payload,headers);second=request(payload,headers)
            self.assertEqual(first,second);self.assertEqual(first[1]['state'],'created')
            self.assertEqual(sum(method=='thread/start' for method,_ in self.calls),1)
            self.assertEqual(request({**payload,'title':'Changed'},headers)[0],400)
            self.bad_id=True
            unknown={**payload,'command_id':'unconfirmed-create-command-1'}
            self.assertEqual(request(unknown,headers)[1]['state'],'unknown')
            self.assertEqual(request(unknown,headers)[1]['state'],'unknown')
            self.assertEqual(sum(method=='thread/start' for method,_ in self.calls),2)
        finally:server.shutdown();server.server_close();worker.join()
