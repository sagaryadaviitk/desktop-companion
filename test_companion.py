import hashlib
import http.client
import json
import os
import re
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from companion import Bridge, CommandJournal, CompanionServer, ControlError, History, redact, visible_user_text, is_scheduled_input, initialize_remote_origin
from access import RemoteSettings


CHAT = "test-chat-1234567890"
HEARTBEAT='<heartbeat>\n<automation_id>test-monitor</automation_id>\n<current_time_iso>2026-10-20T18:17:41.696Z</current_time_iso>\n<instructions>INTERNAL_SCHEDULE_TRIGGER</instructions>\n</heartbeat>'


def make_history(root):
    state = sqlite3.connect(root / "state_5.sqlite")
    state.execute("CREATE TABLE threads(id TEXT,name TEXT,title TEXT,cwd TEXT,updated_at INTEGER,updated_at_ms INTEGER,is_pinned INTEGER,archived INTEGER,agent_path TEXT,recency_at_ms INTEGER,project_id TEXT,model TEXT,git_branch TEXT)")
    state.execute("INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (CHAT,"My existing chat","Original title","/repo/project",1,1000,0,0,"/root",1000,"project-1","test-model","main"))
    state.execute("CREATE TABLE projects(id TEXT,name TEXT,position INTEGER,created_at_ms INTEGER)")
    state.execute("CREATE TABLE project_roots(project_id TEXT,path TEXT,position INTEGER)")
    state.execute("INSERT INTO projects VALUES ('project-1','My project',0,1000)")
    state.execute("INSERT INTO projects VALUES ('empty-project','Empty project',1,1000)")
    state.commit(); state.close()
    history = sqlite3.connect(root / "thread_history_1.sqlite")
    history.execute("CREATE TABLE thread_turns(thread_id TEXT,turn_id TEXT,status TEXT,started_at INTEGER,completed_at INTEGER,rollout_ordinal INTEGER)")
    history.execute("INSERT INTO thread_turns VALUES (?,?,?,?,?,?)", (CHAT,"turn-1","completed",1,2,1))
    history.execute("CREATE TABLE thread_items(thread_id TEXT,item_id TEXT,item_type TEXT,item_json TEXT,created_at_ms INTEGER,started_at_ms INTEGER,completed_at_ms INTEGER,rollout_ordinal INTEGER)")
    for ordinal, kind, payload in [
        (1,"userMessage",{"content":[{"type":"text","text":"Review my code"}]}),
        (2,"reasoning",{"text":"PRIVATE_INTERNAL_REASONING"}),
        (3,"commandExecution",{"text":"PRIVATE_TOOL_OUTPUT"}),
        (4,"agentMessage",{"text":"<script>alert('x')</script> done","phase":"final"}),
    ]:
        history.execute("INSERT INTO thread_items VALUES (?,?,?,?,?,?,?,?)",(CHAT,str(ordinal),kind,json.dumps(payload),1000+ordinal,None,None,ordinal))
    history.commit(); history.close()
    return History(root)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.history=make_history(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_only_visible_messages_and_original_names(self):
        thread=self.history.read_thread(CHAT)
        self.assertEqual(thread["title"],"My existing chat")
        self.assertEqual(len(thread["messages"]),2)
        serialized=json.dumps(thread)
        self.assertNotIn("PRIVATE_INTERNAL_REASONING",serialized)
        self.assertNotIn("PRIVATE_TOOL_OUTPUT",serialized)
        self.assertEqual(thread["last_recorded_turn"]["status"],"completed")

    def test_recent_uses_latest_recorded_action_including_tools_and_queued_input(self):
        with sqlite3.connect(self.root/'state_5.sqlite') as db:
            for identity in ('tool-chat','queued-chat','pinned-chat'):
                db.execute('INSERT INTO threads SELECT ?,name,title,cwd,updated_at,updated_at_ms,1,archived,agent_path,9000,project_id,model,git_branch FROM threads WHERE id=?',(identity,CHAT))
        with sqlite3.connect(self.root/'thread_history_1.sqlite') as db:
            db.execute('INSERT INTO thread_items VALUES (?,?,?,?,?,?,?,?)',('tool-chat','tool','mcpToolCall','{}',10000,11000,13000,1))
        with sqlite3.connect(self.root/'queue_1.sqlite') as db:
            db.execute('CREATE TABLE queued_items(id TEXT,thread_id TEXT,payload_json TEXT,created_at_ms INTEGER,updated_at_ms INTEGER,queue_order INTEGER)')
            db.execute("INSERT INTO queued_items VALUES ('queued','queued-chat','{}',12000,14000,1)")
        chats=self.history.list_threads()
        self.assertEqual([c['id'] for c in chats],['queued-chat','tool-chat','pinned-chat',CHAT])
        self.assertEqual(chats[0]['last_activity_at'],14000)
        self.assertEqual(chats[1]['last_activity_at'],13000)

    def test_reading_does_not_change_source_databases(self):
        paths=[self.root/"state_5.sqlite",self.root/"thread_history_1.sqlite"]
        before=[hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
        self.history.list_threads();self.history.read_thread(CHAT)
        self.assertEqual(before,[hashlib.sha256(p.read_bytes()).hexdigest() for p in paths])

    def test_missing_database_is_not_created(self):
        missing=self.root/"missing";missing.mkdir()
        with self.assertRaises(sqlite3.Error):History(missing).list_threads()
        self.assertEqual(list(missing.iterdir()),[])

    def test_arbitrary_paths_and_injection_rejected(self):
        for bad in ["../../auth.json","x' OR 1=1 --","",CHAT+"/"]:
            with self.assertRaises(KeyError):self.history.read_thread(bad)

    def test_common_credentials_are_redacted(self):
        for value in ["sk-"+"a"*30,"xoxb-"+"1"*30,"github_pat_"+"x"*30]:
            self.assertNotIn(value,redact("test "+value))
        self.assertNotIn("secretvalue123",redact('Authorization: Bearer secretvalue123'))
        self.assertNotIn("secretvalue123",redact('{"password":"secretvalue123"}'))
        self.assertNotIn("AAAABBBB",redact("-----BEGIN RSA PRIVATE KEY-----\nAAAABBBB\n-----END RSA PRIVATE KEY-----"))

    def test_pending_instructions_are_labelled_separately_from_history(self):
        connection=sqlite3.connect(self.root/"queue_1.sqlite")
        connection.execute("CREATE TABLE queued_items(id TEXT,thread_id TEXT,payload_json TEXT,created_at_ms INTEGER,queue_order INTEGER)")
        payload={"UserInput":{"content":[{"type":"text","text":"Do the next check"}]}}
        connection.execute("INSERT INTO queued_items VALUES (?,?,?,?,?)",("queue-1",CHAT,json.dumps(payload),2000,1))
        connection.commit();connection.close()
        thread=self.history.read_thread(CHAT)
        self.assertEqual(thread["pending_instructions"][0]["text"],"Do the next check")
        self.assertEqual(len(thread["messages"]),2)

    def test_history_pages_preserve_order_without_skips_or_overlap(self):
        connection=sqlite3.connect(self.root/"thread_history_1.sqlite")
        for ordinal in range(5,260):
            connection.execute("INSERT INTO thread_items VALUES (?,?,?,?,?,?,?,?)",(CHAT,str(ordinal),"agentMessage",json.dumps({"text":f"Message {ordinal}"}),1000+ordinal,None,None,ordinal))
        connection.commit();connection.close()
        pages=[];cursor=None
        while True:
            page=self.history.read_thread(CHAT,before=cursor)
            ordinals=[item["ordinal"] for item in page["messages"]]
            self.assertEqual(ordinals,sorted(ordinals))
            pages.insert(0,ordinals)
            cursor=page["next_before"]
            if cursor is None:break
        combined=[ordinal for page in pages for ordinal in page]
        self.assertEqual(combined,[1,4]+list(range(5,260)))
        self.assertEqual(len(combined),len(set(combined)))

    def test_long_reply_is_not_silently_truncated(self):
        text="Complete response "*2000+"END_OF_RESPONSE"
        connection=sqlite3.connect(self.root/"thread_history_1.sqlite")
        connection.execute("UPDATE thread_items SET item_json=? WHERE item_id='4'",(json.dumps({"text":text}),))
        connection.commit();connection.close()
        self.assertEqual(self.history.read_thread(CHAT)["messages"][-1]["text"],text)

    def test_machine_envelopes_are_cleaned_without_changing_user_examples(self):
        envelope='<in-app-browser-context source="ambient-ui-state">\nAmbient content\n</in-app-browser-context>\n\n## My request:\nShow my chats'
        self.assertEqual(visible_user_text(envelope),"Show my chats")
        example="Here is an example: "+envelope
        self.assertEqual(visible_user_text(example),example)
        self.assertEqual(visible_user_text("<environment_context>metadata</environment_context>"),"")
        reply='<send_user_message_question_reply>'+json.dumps([{"question":"Where?","answer":"Anywhere"}])+'</send_user_message_question_reply>'
        self.assertEqual(visible_user_text(reply),"Where?\n\nAnywhere")
        self.assertEqual(visible_user_text("<send_user_message_question_reply>invalid</send_user_message_question_reply>"),"<send_user_message_question_reply>invalid</send_user_message_question_reply>")

    def test_invalid_history_cursor_rejected(self):
        for cursor in [0,-1,True,"4",2**63]:
            with self.assertRaises(ValueError):self.history.read_thread(CHAT,before=cursor)

    def test_scheduler_provenance_and_exact_legacy_envelopes_are_hidden(self):
        envelope={'content':[{'type':'text','text':HEARTBEAT}]}
        self.assertTrue(is_scheduled_input(envelope))
        self.assertTrue(is_scheduled_input({'internal_chat_message_metadata_passthrough':{'content_item_kinds':['user.heartbeat']},'content':[{'type':'text','text':'Internal trigger without XML'}]}))
        for text in ['My heartbeat is normal','Explain this example:\n'+HEARTBEAT,'```xml\n'+HEARTBEAT+'\n```','> '+HEARTBEAT,HEARTBEAT.replace('<automation_id>test-monitor</automation_id>','')]:
            self.assertFalse(is_scheduled_input({'content':[{'type':'text','text':text}]}))
        self.assertFalse(is_scheduled_input({**envelope,'clientId':'human-receipt'}))
        self.assertFalse(is_scheduled_input({**envelope,'internal_chat_message_metadata_passthrough':{'content_item_kinds':['user.text']}}))
        self.assertFalse(is_scheduled_input({'content':envelope['content']+[{'type':'localImage','path':'example.jpg'}]}))

    def test_hidden_scheduler_input_never_leaks_into_messages_or_latest_preview(self):
        revision=self.history.read_thread(CHAT)['message_revision']
        with sqlite3.connect(self.history.history) as db:
            for ordinal in (5,6):db.execute('INSERT INTO thread_items VALUES (?,?,?,?,?,?,?,?)',(CHAT,str(ordinal),'userMessage',json.dumps({'clientId':None,'content':[{'type':'text','text':HEARTBEAT}]}),1000+ordinal,None,None,ordinal))
        page=self.history.read_thread(CHAT);chat=self.history.list_threads()[0]
        self.assertEqual(len(page['messages']),2);self.assertNotIn('INTERNAL_SCHEDULE_TRIGGER',json.dumps(page));self.assertEqual(chat['preview_role'],'assistant');self.assertIn('done',chat['preview']);self.assertEqual(chat['last_message_at'],1004);self.assertEqual(chat['message_revision'],revision)

    def test_scheduler_filter_applies_to_older_pages_and_keeps_assistant_results(self):
        with sqlite3.connect(self.history.history) as db:
            for ordinal in range(5,260):
                kind='agentMessage' if ordinal%30==0 else 'userMessage'
                value={'text':f'Monitor result {ordinal}'} if kind=='agentMessage' else {'content':[{'type':'text','text':HEARTBEAT}]}
                db.execute('INSERT INTO thread_items VALUES (?,?,?,?,?,?,?,?)',(CHAT,str(ordinal),kind,json.dumps(value),1000+ordinal,None,None,ordinal))
        cursor=None;messages=[]
        while True:
            page=self.history.read_thread(CHAT,before=cursor);messages=page['messages']+messages;cursor=page['next_before']
            if cursor is None:break
        self.assertEqual([item['ordinal'] for item in messages],[1,4]+list(range(30,260,30)))
        self.assertNotIn('INTERNAL_SCHEDULE_TRIGGER',json.dumps(messages))

    def test_catalog_has_no_hundred_chat_cap_and_preserves_archive_and_child_scope(self):
        connection=sqlite3.connect(self.root/"state_5.sqlite")
        for index in range(130):
            connection.execute("INSERT INTO threads SELECT ?,name,title,cwd,updated_at,updated_at_ms,is_pinned,?,?,recency_at_ms,project_id,model,git_branch FROM threads WHERE id=?",(f"other-chat-{index}",int(index==129),"/root/child" if index==128 else "/root",CHAT))
        connection.commit();connection.close()
        self.assertEqual(len(self.history.list_threads()),129)
        all_chats=self.history.list_threads(include_archived=True,include_children=True)
        self.assertEqual(len(all_chats),131)
        projects=self.history.list_projects(all_chats)
        self.assertEqual(projects[0]["chat_count"],131)
        self.assertEqual(projects[0]["archived_count"],1)
        self.assertEqual(projects[1]["name"],"Empty project")
        self.assertEqual(projects[1]["chat_count"],0)
        self.assertTrue(self.history.read_thread("other-chat-129")["archived"])
        self.assertTrue(self.history.read_thread("other-chat-128")["subagent"])

    def test_legacy_chats_match_the_most_specific_project_root(self):
        connection=sqlite3.connect(self.root/"state_5.sqlite")
        connection.execute("UPDATE threads SET project_id=NULL,cwd='/repo/project/nested'")
        connection.execute("INSERT INTO project_roots VALUES ('empty-project','/repo',0)")
        connection.execute("INSERT INTO project_roots VALUES ('project-1','/repo/project',0)")
        connection.commit();connection.close()
        self.assertEqual(self.history.list_threads()[0]["project_key"],"project:project-1")
        self.assertEqual(self.history.read_thread(CHAT)["project_key"],"project:project-1")

    def test_projects_sharing_a_folder_keep_the_same_legacy_chats(self):
        connection=sqlite3.connect(self.root/"state_5.sqlite")
        connection.execute("UPDATE threads SET project_id=NULL")
        connection.execute("INSERT INTO project_roots VALUES ('empty-project','/repo/project',0)")
        connection.execute("INSERT INTO project_roots VALUES ('project-1','/repo/project',0)")
        connection.commit();connection.close()
        chats=self.history.list_threads()
        self.assertEqual(set(chats[0]["project_keys"]),{"project:project-1","project:empty-project"})
        self.assertEqual([p["chat_count"] for p in self.history.list_projects(chats)],[1,1])

    def test_activity_allowlist_omits_private_payloads_and_redacts_recorded_patches(self):
        connection=sqlite3.connect(self.root/"thread_history_1.sqlite")
        payloads=[("mcpToolCall",{"status":"failed","arguments":{"secret":"PRIVATE_ARGUMENT"},"result":"PRIVATE_RESULT","tool":"PRIVATE_TOOL_NAME"}),
                  ("commandExecution",{"status":"completed","command":"PRIVATE_COMMAND","aggregatedOutput":"PRIVATE_LOG"}),
                  ("fileChange",{"status":"completed","changes":[{"path":"source.py","diff":"+ api_key = 'github_pat_"+"x"*30+"'"}]}),
                  ("plan",{"text":"1. Verify\n2. Deliver"})]
        for ordinal,(kind,payload) in enumerate(payloads,5):
            connection.execute("INSERT INTO thread_items VALUES (?,?,?,?,?,?,?,?)",(CHAT,str(ordinal),kind,json.dumps(payload),1000+ordinal,None,None,ordinal))
        connection.commit();connection.close()
        thread=self.history.read_thread(CHAT)
        serialized=json.dumps(thread)
        for private in ["PRIVATE_ARGUMENT","PRIVATE_RESULT","PRIVATE_TOOL_NAME","PRIVATE_COMMAND","PRIVATE_LOG","github_pat_"+"x"*30]:self.assertNotIn(private,serialized)
        self.assertEqual(thread["changes"][0]["path"],"source.py")
        self.assertIn("hidden",thread["changes"][0]["diff"])
        self.assertEqual(thread["plans"][0]["text"],"1. Verify\n2. Deliver")


class OriginContinuityTests(unittest.TestCase):
    def test_web_only_start_preserves_only_a_fresh_matching_live_controller(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);settings=RemoteSettings(root/'remote.json');config=SimpleNamespace(remote_enabled=True,hostname='phone.example')
            (root/'service-runtime.json').write_text(json.dumps({'children':{'tunnel':1234}}))
            good={'phase':'ready','connections':4,'controller_pid':1234,'checked_at':1000}
            for change,enabled,alive,expected in [({},True,True,True),({'checked_at':900},True,True,False),({'controller_pid':4321},True,True,False),({'connections':0},True,True,False),({},False,True,False),({},True,False,False)]:
                with self.subTest(change=change,enabled=enabled,alive=alive):
                    settings.set_origin('https://phone.example');config.remote_enabled=enabled;(root/'tunnel-status.json').write_text(json.dumps({**good,**change}))
                    with patch('companion.os.kill',side_effect=None if alive else ProcessLookupError):
                        self.assertEqual(initialize_remote_origin(settings,root,config,now=1010),expected)
                    self.assertEqual(settings.origin(),'https://phone.example' if expected else None)
            settings.set_origin('https://old.example');config.remote_enabled=True
            with patch('companion.os.kill') as probe:self.assertFalse(initialize_remote_origin(settings,root,config,now=1010));probe.assert_not_called()


class JournalTests(unittest.TestCase):
    def test_replay_does_not_send_again_and_conflicts_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            journal=CommandJournal(Path(temp)/"journal.sqlite");calls=[]
            def send():calls.append(1);return {"state":"accepted"}
            payload={"thread_id":CHAT,"message":"private instruction"}
            self.assertEqual(journal.execute("command-1",payload,send)["state"],"accepted")
            self.assertEqual(journal.execute("command-1",payload,send)["state"],"accepted")
            self.assertEqual(len(calls),1)
            with self.assertRaises(ValueError):journal.execute("command-1",{"message":"different"},send)
            self.assertNotIn(b"private instruction",(Path(temp)/"journal.sqlite").read_bytes())

    def test_unknown_delivery_is_never_automatically_retried(self):
        with tempfile.TemporaryDirectory() as temp:
            journal=CommandJournal(Path(temp)/"journal.sqlite");calls=[]
            def send():calls.append(1);raise ControlError("No acknowledgement",uncertain=True)
            self.assertEqual(journal.execute("command-2",{},send)["state"],"unknown")
            self.assertEqual(journal.execute("command-2",{},send)["state"],"unknown")
            self.assertEqual(len(calls),1)

    def test_only_known_pre_submission_rejection_can_be_retried_explicitly(self):
        with tempfile.TemporaryDirectory() as temp:
            journal=CommandJournal(Path(temp)/'journal.sqlite');calls=[]
            def not_claimed():calls.append(1);raise ControlError('Original is still queued',retryable=True)
            self.assertTrue(journal.execute('safe-retry',{},not_claimed)['retryable'])
            self.assertEqual(len(calls),1)
            def accepted():calls.append(1);return {'state':'steered'}
            self.assertEqual(journal.execute('safe-retry',{},accepted)['state'],'steered')
            journal.execute('safe-retry',{},accepted);self.assertEqual(len(calls),2)


class BridgeTests(unittest.TestCase):
    def bridge(self,mode="ok"):
        calls=[]
        def run(args,**kwargs):
            calls.append((args,kwargs))
            if args[-1]=="--help":return SimpleNamespace(returncode=0,stdout="--thread --message")
            if mode=="timeout":raise subprocess.TimeoutExpired(args,10)
            if mode=="mismatch":return SimpleNamespace(returncode=0,stdout="Queued message queue-123 for thread wrong-thread.")
            return SimpleNamespace(returncode=0,stdout=f"Queued message queue-123 for thread {CHAT}.\n")
        with patch("companion.shutil.which",return_value="/fake/codex"):
            bridge=Bridge(Path(os.environ.get("CODEX_HOME",Path.home()/".codex")),runner=run)
        return bridge,calls

    def test_queue_targets_existing_id_without_resuming_or_overrides(self):
        bridge,calls=self.bridge();result=bridge.send(CHAT,"hello; $(do-not-execute)")
        self.assertEqual(result["state"],"queued")
        self.assertEqual(calls[-1][0],["/fake/codex","queue","--thread",CHAT,"--message","hello; $(do-not-execute)"])
        self.assertNotIn("shell",calls[-1][1])

    def test_timeout_is_unknown_instead_of_reported_as_queued(self):
        bridge,_=self.bridge("timeout")
        with self.assertRaises(ControlError) as raised:bridge.send(CHAT,"hello")
        self.assertTrue(raised.exception.uncertain)

    def test_acknowledgement_must_match_target_chat(self):
        bridge,_=self.bridge("mismatch")
        with self.assertRaises(ControlError) as raised:bridge.send(CHAT,"hello")
        self.assertTrue(raised.exception.uncertain)

    def test_alternate_history_cannot_queue_into_real_user_home(self):
        bridge,_=self.bridge();bridge.home=Path("/different/history")
        self.assertFalse(bridge.availability()["available"])

    def test_read_only_mode_blocks_commands(self):
        bridge,calls=self.bridge();bridge.enabled=False
        with self.assertRaises(ControlError):bridge.send(CHAT,"hello")
        self.assertEqual(len(calls),1)  # Only the read-only help probe ran.


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();root=Path(cls.temp.name)
        cls.server=CompanionServer(("127.0.0.1",0),make_history(root),Bridge(root,enabled=False),CommandJournal(root/"journal.sqlite"))
        cls.worker=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.worker.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.worker.join();cls.temp.cleanup()

    def request(self,path="/api/chats",method="GET",headers=None,body=None):
        connection=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=3)
        connection.request(method,path,body=body,headers=headers or {})
        response=connection.getresponse();result=(response.status,dict(response.getheaders()),response.read())
        connection.close();return result

    def test_local_api_and_content_security_headers(self):
        code,headers,body=self.request()
        self.assertEqual(code,200);self.assertEqual(json.loads(body)["chats"][0]["id"],CHAT)
        self.assertEqual(headers["Cache-Control"],"no-store")
        self.assertIn("frame-ancestors 'none'",headers["Content-Security-Policy"])
        self.assertNotIn("Access-Control-Allow-Origin",headers)

    def test_dns_rebinding_and_cross_origin_are_rejected(self):
        for headers in [{"Host":"attacker.example"},{"Origin":"https://attacker.example"},{"Sec-Fetch-Site":"cross-site"}]:
            self.assertEqual(self.request(headers=headers)[0],403)

    def test_command_requires_csrf_and_cannot_fake_delivery(self):
        payload=json.dumps({"thread_id":CHAT,"message":"hello","command_id":"1234567890123456"})
        self.assertEqual(self.request("/api/command","POST",body=payload)[0],403)
        code,_,body=self.request("/api/command","POST",headers={"X-Companion-CSRF":self.server.csrf},body=payload)
        self.assertEqual(code,409);self.assertEqual(json.loads(body)["state"],"rejected")

    def test_private_files_and_path_traversal_not_served(self):
        for path in ["/slack-app.json","/../companion.py","/.state/commands.sqlite"]:
            self.assertEqual(self.request(path)[0],404)

    def test_http_command_replay_only_invokes_cli_once(self):
        payload=json.dumps({"thread_id":CHAT,"message":"hello","command_id":"http-command-1234567890"})
        headers={"X-Companion-CSRF":self.server.csrf}
        with patch.object(self.server.bridge,"send",return_value={"state":"queued","queue_item_id":"queued-1"}) as send:
            first=self.request("/api/command","POST",headers=headers,body=payload)
            second=self.request("/api/command","POST",headers=headers,body=payload)
        self.assertEqual(first[0],200);self.assertEqual(second[0],200)
        self.assertEqual(json.loads(first[2])["state"],"queued")
        self.assertEqual(send.call_count,1)

    def test_malformed_command_is_rejected_without_process_call(self):
        with patch.object(self.server.bridge,"send") as send:
            result=self.request("/api/command","POST",headers={"X-Companion-CSRF":self.server.csrf},body='["invalid"]')
        self.assertEqual(result[0],400);send.assert_not_called()

    def test_page_and_scripts_are_served_without_inline_execution(self):
        self.assertEqual(self.request("/")[0],200)
        self.assertEqual(self.request("/app.js")[0],200)
        self.assertEqual(self.request("/markdown.js")[0],200)
        self.assertEqual(self.request("/refresh.js")[0],200)
        self.assertNotIn("innerHTML",(Path(__file__).parent/"web/app.js").read_text())

    def test_history_cursor_is_validated_at_http_boundary(self):
        for cursor in ["-1","0","nope","9223372036854775808","1%20OR%201=1"]:
            self.assertEqual(self.request(f"/api/chat?id={CHAT}&before={cursor}")[0],400)
        self.assertEqual(self.request(f"/api/chat?id={CHAT}&before=4")[0],200)

    def test_catalog_contains_empty_saved_projects(self):
        code,_,body=self.request()
        self.assertEqual(code,200)
        self.assertIn("Empty project",[p["name"] for p in json.loads(body)["projects"]])

    def test_archived_history_is_readable_but_cannot_queue_input(self):
        payload=json.dumps({"thread_id":CHAT,"message":"hello","command_id":"archived-command-1234"})
        thread=self.server.history.read_thread(CHAT);thread["archived"]=True
        with patch.object(self.server.history,"read_thread",return_value=thread),patch.object(self.server.bridge,"send") as send:
            self.assertEqual(self.request(f"/api/chat?id={CHAT}")[0],200)
            self.assertEqual(self.request("/api/command","POST",headers={"X-Companion-CSRF":self.server.csrf},body=payload)[0],409)
        send.assert_not_called()

    def test_read_receipt_requires_csrf_and_exact_displayed_revision(self):
        thread=self.server.history.read_thread(CHAT)
        payload={'thread_id':CHAT,'revision':thread['message_revision'],'operation':'read'}
        self.assertEqual(self.request('/api/read-state','POST',body=json.dumps(payload))[0],403)
        headers={'X-Companion-CSRF':self.server.csrf}
        with patch.object(self.server.inbox,'mark') as mark:
            stale={**payload,'revision':'older-revision'}
            self.assertEqual(self.request('/api/read-state','POST',headers=headers,body=json.dumps(stale))[0],409)
            mark.assert_not_called()
            self.assertEqual(self.request('/api/read-state','POST',headers=headers,body=json.dumps(payload))[0],200)
            mark.assert_called_once_with(CHAT,thread['message_revision'],False)

    def test_conditional_catalog_skips_history_and_read_changes_invalidate(self):
        status,_,body=self.request('/api/chats');revision=json.loads(body)['revision']
        if not revision:revision=json.loads(self.request('/api/chats')[2])['revision']
        self.assertTrue(revision)
        with patch.object(self.server.history,'list_threads') as scan:
            status,headers,body=self.request('/api/chats',headers={'If-None-Match':'"'+revision+'"'})
            self.assertEqual(status,304);self.assertEqual(body,b'');scan.assert_not_called()
        self.server.inbox.mark(CHAT,'new-revision')
        self.assertEqual(self.request('/api/chats',headers={'If-None-Match':'"'+revision+'"'})[0],200)

    def test_html_and_status_have_consistent_build_and_versioned_assets(self):
        from companion import web_build
        build=web_build();html=self.request('/')[2].decode()
        self.assertNotIn('__BUILD__',html);self.assertIn('/app.js?v='+build,html)
        for path in re.findall(r'(?:src|href)="(/[^"?]+)\?v='+build+'"',html):
            status,headers,body=self.request(path+'?v='+build)
            self.assertEqual(status,200,path);self.assertTrue(body,path);self.assertEqual(headers['Cache-Control'],'no-store')
            self.assertIn('text/css' if path.endswith('.css') else 'text/javascript',headers['Content-Type'])
        self.assertEqual(json.loads(self.request('/api/status')[2])['build'],build)

    def test_connect_chat_opens_only_an_existing_chat_with_csrf_and_no_inference(self):
        opener=SimpleNamespace(opener=lambda _:None)
        with patch.object(self.server,'new_chat',opener),patch.object(self.server.bridge,'availability',return_value={'available':True}),patch.object(opener,'opener') as open_chat:
            self.assertEqual(self.request('/api/chat/connect','POST',body=json.dumps({'thread_id':CHAT}))[0],403)
            open_chat.assert_not_called()
            self.assertEqual(self.request('/api/chat/connect','POST',headers={'X-Companion-CSRF':self.server.csrf},body=json.dumps({'thread_id':'missing-chat'}))[0],400)
            open_chat.assert_not_called()
            self.assertEqual(self.request('/api/chat/connect','POST',headers={'X-Companion-CSRF':self.server.csrf},body=json.dumps({'thread_id':CHAT}))[0],200)
            open_chat.assert_called_once_with(CHAT)

    def test_obsolete_ui_cannot_submit_or_fall_back_to_queue(self):
        payload={'thread_id':CHAT,'message':'Not sent','command_id':'obsolete-ui-test-1234','mode':'queue'}
        with patch.object(self.server.bridge,'send') as send:
            status,_,body=self.request('/api/command','POST',headers={'X-Companion-CSRF':self.server.csrf,'X-Companion-UI':'old-version'},body=json.dumps(payload))
            self.assertEqual(status,409);self.assertTrue(json.loads(body)['update_required']);send.assert_not_called()

    def test_steer_uses_live_turn_even_when_saved_history_is_completed(self):
        payload={'thread_id':CHAT,'message':'correction','command_id':'live-new-turn-12345678','mode':'steer','expected_turn_id':'live-turn'}
        runtime=SimpleNamespace(execution_state=lambda _: {'status':'working','turn_id':'live-turn'},steer=lambda *_:{'state':'steered'})
        with patch.object(self.server,'runtime',runtime),patch.object(self.server.bridge,'availability',return_value={'available':True}),patch.object(runtime,'steer',wraps=runtime.steer) as steer:
            self.assertEqual(self.request('/api/command','POST',headers={'X-Companion-CSRF':self.server.csrf},body=json.dumps(payload))[0],200)
            steer.assert_called_once_with(CHAT,'correction','live-turn')

    def test_unavailable_steer_never_falls_back_to_queue(self):
        payload={'thread_id':CHAT,'message':'correction','command_id':'no-steer-request-123456','mode':'steer','expected_turn_id':'turn-1'}
        with patch.object(self.server.bridge,'send') as send:
            self.assertEqual(self.request('/api/command','POST',headers={'X-Companion-CSRF':self.server.csrf},body=json.dumps(payload))[0],409)
        send.assert_not_called()

    def test_idle_send_is_direct_deduplicated_and_never_queued(self):
        payload={'thread_id':CHAT,'message':'Next task','command_id':'direct-send-request-1234','mode':'send'}
        runtime=SimpleNamespace(send_now=lambda *_:None)
        headers={'X-Companion-CSRF':self.server.csrf}
        with patch.object(self.server.bridge,'send') as queue:
            self.assertEqual(self.request('/api/command','POST',headers=headers,body=json.dumps(payload))[0],409)
            with patch.object(self.server,'runtime',runtime),patch.object(self.server.bridge,'availability',return_value={'available':True}),patch.object(runtime,'send_now',return_value={'state':'sent','turn_id':'new-turn'}) as send:
                self.assertEqual(self.request('/api/command','POST',headers=headers,body=json.dumps(payload))[0],200)
                self.assertEqual(self.request('/api/command','POST',headers=headers,body=json.dumps(payload))[0],200)
                send.assert_called_once_with(CHAT,'Next task')
            queue.assert_not_called()

    def test_idle_send_rejects_a_chat_that_started_working(self):
        payload={'thread_id':CHAT,'message':'Next task','command_id':'racing-send-request-1234','mode':'send'}
        thread=self.server.history.read_thread(CHAT);thread['last_recorded_turn']={'turn_id':'turn-1','status':'inProgress'}
        runtime=SimpleNamespace(send_now=lambda *_:None)
        with patch.object(self.server,'runtime',runtime),patch.object(self.server.bridge,'availability',return_value={'available':True}),patch.object(self.server.history,'read_thread',return_value=thread),patch.object(runtime,'send_now') as send:
            self.assertEqual(self.request('/api/command','POST',headers={'X-Companion-CSRF':self.server.csrf},body=json.dumps(payload))[0],409)
            send.assert_not_called()

    def test_queued_steer_requires_csrf_exact_revision_and_deduplicates(self):
        payload={'thread_id':CHAT,'queue_id':'queue-message-1','revision':'a'*64,'expected_turn_id':'turn-1'}
        thread=self.server.history.read_thread(CHAT);thread['last_recorded_turn']={'turn_id':'turn-1','status':'inProgress'}
        item={'id':'queue-message-1','revision':'a'*64,'client_id':'client-1','text':'Original queued instruction'}
        runtime=SimpleNamespace(promote_queued=lambda *_:None)
        headers={'X-Companion-CSRF':self.server.csrf}
        with patch.object(self.server,'runtime',runtime),patch.object(self.server.bridge,'availability',return_value={'available':True}),patch.object(self.server.history,'read_thread',return_value=thread),patch.object(self.server.history,'queue_item',return_value=item),patch.object(runtime,'promote_queued',return_value={'state':'steered','turn_id':'turn-1'}) as promote:
            self.assertEqual(self.request('/api/queue/steer','POST',body=json.dumps(payload))[0],403)
            self.assertEqual(self.request('/api/queue/steer','POST',headers=headers,body=json.dumps({**payload,'revision':'b'*64}))[0],409)
            promote.assert_not_called()
            self.assertEqual(self.request('/api/queue/steer','POST',headers=headers,body=json.dumps(payload))[0],200)
            self.assertEqual(self.request('/api/queue/steer','POST',headers=headers,body=json.dumps(payload))[0],200)
            promote.assert_called_once_with(CHAT,item,'turn-1')

    def test_steer_checks_active_turn_and_deduplicates(self):
        runtime=SimpleNamespace(steer=lambda *_:None)
        payload={'thread_id':CHAT,'message':'correction','command_id':'steer-request-123456789','mode':'steer','expected_turn_id':'other-turn'}
        thread=self.server.history.read_thread(CHAT);thread['last_recorded_turn']={'turn_id':'turn-1','status':'inProgress'}
        with patch.object(self.server,'runtime',runtime),patch.object(self.server.bridge,'availability',return_value={'available':True}),patch.object(self.server.history,'read_thread',return_value=thread),patch.object(runtime,'steer',return_value={'state':'steered'}) as steer:
            headers={'X-Companion-CSRF':self.server.csrf}
            self.assertEqual(self.request('/api/command','POST',headers=headers,body=json.dumps(payload))[0],409)
            steer.assert_not_called();payload['expected_turn_id']='turn-1'
            self.assertEqual(self.request('/api/command','POST',headers=headers,body=json.dumps(payload))[0],200)
            self.assertEqual(self.request('/api/command','POST',headers=headers,body=json.dumps(payload))[0],200)
            steer.assert_called_once_with(CHAT,'correction','turn-1')

    def test_model_requires_live_connection_catalog_and_supported_effort(self):
        account=SimpleNamespace(snapshot=lambda:{'models':[{'id':'model-a','efforts':['high']}]})
        runtime=SimpleNamespace(set_model=lambda *_:None)
        payload={'thread_id':CHAT,'command_id':'model-request-123456789','model':'model-a','effort':'high'}
        headers={'X-Companion-CSRF':self.server.csrf}
        self.assertEqual(self.request('/api/model','POST',headers=headers,body=json.dumps(payload))[0],409)
        with patch.object(self.server,'runtime',runtime),patch.object(self.server,'account',account),patch.object(self.server.bridge,'availability',return_value={'available':True}),patch.object(runtime,'set_model',return_value={'state':'updated','model':'model-a'}) as update:
            invalid={**payload,'effort':'invented'}
            self.assertEqual(self.request('/api/model','POST',headers=headers,body=json.dumps(invalid))[0],400);update.assert_not_called()
            self.assertEqual(self.request('/api/model','POST',headers=headers,body=json.dumps(payload))[0],200)
            self.assertEqual(self.request('/api/model','POST',headers=headers,body=json.dumps(payload))[0],200)
            update.assert_called_once_with(CHAT,'model-a','high')


if __name__=="__main__":unittest.main()
