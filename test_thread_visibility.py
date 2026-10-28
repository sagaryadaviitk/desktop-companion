"""Synthetic coverage for desktop session metadata and catalog visibility."""
from contextlib import closing
import hashlib
import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from companion import Bridge, CommandJournal, CompanionServer
from test_companion import CHAT, make_history


class ThreadVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.history=make_history(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def metadata_schema(self, *columns):
        with closing(sqlite3.connect(self.history.state)) as db, db:
            for column in columns:db.execute('ALTER TABLE threads ADD COLUMN '+column+' TEXT')

    def add(self, identity, *, source=None, thread_source=None, path=None, archived=0, title='Synthetic chat'):
        with closing(sqlite3.connect(self.history.state)) as db, db:
            db.execute('INSERT INTO threads(id,name,title,cwd,updated_at,updated_at_ms,is_pinned,archived,agent_path,recency_at_ms,project_id,model,git_branch) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (identity,title,title,'/repo/project',10,10000,0,archived,path,10000,'project-1','test-model','main'))
            columns={row[1] for row in db.execute('PRAGMA table_info(threads)')}
            for key,value in [('source',source),('thread_source',thread_source)]:
                if key in columns:db.execute('UPDATE threads SET '+key+'=? WHERE id=?',(value,identity))
        with closing(sqlite3.connect(self.history.history)) as db, db:
            db.execute('INSERT INTO thread_turns VALUES (?,?,?,?,?,?)',(identity,'turn-'+identity,'inProgress',10,0,1))
            db.execute('INSERT INTO thread_items VALUES (?,?,?,?,?,?,?,?)',(identity,'reply','agentMessage',json.dumps({'text':'Synthetic reply'}),10000,None,None,1))

    def ids(self, **kwargs):
        return {chat['id'] for chat in self.history.list_threads(**kwargs)}

    def test_guardian_is_hidden_from_every_catalog_scope_and_sources_stay_read_only(self):
        self.metadata_schema('source','thread_source')
        self.add('guardian-current',thread_source='guardian_review')
        self.add('guardian-legacy',thread_source='subagent',source=json.dumps({'subagent':{'other':'guardian'}}))
        self.add('guardian-archived',archived=1,path='/root/review',source=json.dumps({'subagent':{'other':'guardian'}}))
        paths=[self.history.state,self.history.history]
        before=[hashlib.sha256(path.read_bytes()).digest() for path in paths]
        for archived in (False,True):
            for children in (False,True):
                self.assertEqual(self.ids(include_archived=archived,include_children=children),{CHAT})
        # Retain direct read-only access for diagnostics, correctly labelled.
        self.assertTrue(self.history.read_thread('guardian-current')['subagent'])
        self.assertEqual(before,[hashlib.sha256(path.read_bytes()).digest() for path in paths])

    def test_null_path_children_use_both_metadata_sources_and_detail_matches_catalog(self):
        self.metadata_schema('source','thread_source')
        self.add('child-marker',thread_source='subagent')
        self.add('child-source',source=json.dumps({'subagent':{'thread_spawn':{'depth':1}}}))
        self.add('child-path',path='/root/worker')
        self.assertEqual(self.ids(),{CHAT})
        all_chats=self.history.list_threads(include_children=True)
        self.assertEqual({chat['id'] for chat in all_chats},{CHAT,'child-marker','child-source','child-path'})
        for chat in all_chats:
            self.assertEqual(chat['subagent'],chat['id']!=CHAT)
            self.assertEqual(self.history.read_thread(chat['id'])['subagent'],chat['subagent'])

    def test_user_automation_and_agent_created_threads_are_preserved_without_title_heuristics(self):
        self.metadata_schema('source','thread_source')
        for kind in ('user','automation','agent_created_thread',None):
            self.add('main-'+str(kind),source='vscode',thread_source=kind,title='Guardian review')
        self.assertEqual(len(self.ids()),5)
        self.assertFalse(any(chat['subagent'] for chat in self.history.list_threads()))

    def test_legacy_schema_and_individually_optional_source_columns(self):
        self.add('legacy-child',path='/root/worker')
        self.assertEqual(self.ids(),{CHAT})
        self.assertEqual(len(self.ids(include_children=True)),2)
        self.metadata_schema('source')
        self.add('source-guardian',source=json.dumps({'subagent':{'other':'guardian'}}))
        self.assertEqual(len(self.ids(include_children=True)),2)
        self.metadata_schema('thread_source')
        self.add('marker-guardian',thread_source='guardian_review')
        self.assertEqual(len(self.ids(include_children=True)),2)

    def test_thread_source_column_without_source_column(self):
        self.metadata_schema('thread_source')
        self.add('marker-guardian',thread_source='guardian_review')
        self.add('marker-child',thread_source='subagent')
        self.assertEqual(self.ids(),{CHAT})
        self.assertEqual(self.ids(include_children=True),{CHAT,'marker-child'})
        self.assertTrue(self.history.read_thread('marker-child')['subagent'])

    def test_invalid_or_future_source_shapes_do_not_break_the_catalog(self):
        self.metadata_schema('source','thread_source')
        for index,source in enumerate(('not-json','null','[]','42','"subagent"','{"subagent":null}','{"subagent":[]}')):
            self.add('odd-source-'+str(index),source=source)
        chats=self.history.list_threads(include_children=True)
        self.assertEqual(len(chats),8)
        self.assertTrue(all(isinstance(chat['subagent'],bool) for chat in chats))
        self.assertFalse(self.history.read_thread(CHAT)['subagent'])

    def test_internal_active_turns_cannot_starve_the_bounded_execution_candidates(self):
        self.metadata_schema('source','thread_source')
        for index in range(14):self.add('review-'+str(index),thread_source='guardian_review')
        with closing(sqlite3.connect(self.history.history)) as db, db:
            db.execute("UPDATE thread_turns SET status='inProgress' WHERE thread_id=?",(CHAT,))
        self.assertEqual(self.history.active_candidates(),[CHAT])
        for index in range(14):self.add('working-'+str(index),thread_source='user')
        candidates=self.history.active_candidates()
        self.assertEqual(len(candidates),12)
        self.assertTrue(all(identity.startswith('working-') for identity in candidates))

    def test_http_catalog_excludes_guardian_from_inbox_projects_and_conditional_refresh(self):
        self.metadata_schema('source','thread_source')
        self.add('guardian-api',thread_source='guardian_review')
        self.add('child-api',thread_source='subagent')
        server=CompanionServer(('127.0.0.1',0),self.history,Bridge(self.root,enabled=False),CommandJournal(self.root/'journal.sqlite'))
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        def request(etag=None):
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            connection.request('GET','/api/chats',headers={'If-None-Match':etag} if etag else {})
            response=connection.getresponse();result=response.status,response.getheader('ETag'),response.read();connection.close();return result
        try:
            code,etag,body=request()
            self.assertEqual(code,200)
            data=json.loads(body)
            self.assertEqual({chat['id'] for chat in data['chats']},{CHAT,'child-api'})
            self.assertEqual(next(project for project in data['projects'] if project['id']=='project:project-1')['chat_count'],2)
            self.assertNotIn('guardian-api',body.decode())
            # The initial catalog creates Companion receipts, so only a settled
            # snapshot can carry a conditional-refresh token.
            code,etag,_=request()
            self.assertEqual(code,200);self.assertIsNotNone(etag)
            self.assertEqual(request(etag)[0],304)
            self.add('guardian-later',source=json.dumps({'subagent':{'other':'guardian'}}))
            code,new_etag,body=request(etag)
            self.assertEqual(code,200);self.assertNotEqual(etag,new_etag)
            self.assertNotIn('guardian-later',body.decode())
            with closing(sqlite3.connect(server.inbox.path)) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM receipts WHERE id LIKE 'guardian-%'").fetchone()[0],0)
        finally:
            server.shutdown();server.server_close();worker.join()


if __name__=='__main__':unittest.main()
