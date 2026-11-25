import hashlib
import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from companion import CompanionServer, CommandJournal, ControlError
from questions import answer_envelope, pending_questions, replies, live_question_ids
from test_companion import CHAT, make_history
from runtime import RuntimeUnavailable


class QuestionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.history=make_history(self.root)
        with sqlite3.connect(self.root/'thread_history_1.sqlite') as db:db.execute("UPDATE thread_turns SET status='inProgress'")
        self.add('call-question','agentMessage',{'type':'agentMessage','delivery':'async','questions':[{'title':'Where should it work?','options':['Anywhere','At home']},{'title':'Any details?','options':None}]},5)

    def tearDown(self):self.temp.cleanup()

    def add(self,identity,kind,value,ordinal):
        with sqlite3.connect(self.root/'thread_history_1.sqlite') as db:
            db.execute('INSERT INTO thread_items VALUES (?,?,?,?,?,?,?,?)',(CHAT,identity,kind,json.dumps(value),1000+ordinal,None,None,ordinal))

    def questions(self):return self.history.read_thread(CHAT)['questions']

    def test_exact_native_ids_options_and_no_inferred_questions(self):
        questions=self.questions()
        self.assertEqual(len(questions),2);self.assertEqual(questions[0]['id'],'["request_user_input_async","call-question",0]')
        self.assertEqual(questions[0]['options'],['Anywhere','At home']);self.assertEqual(questions[1]['options'],[])
        self.assertEqual(self.history.list_threads()[0]['question_count'],2)
        self.assertEqual(self.history.read_thread(CHAT,before=4)['questions'],questions)
        self.add('ordinary','agentMessage',{'text':'Should I do this?','questions':[{'title':'Wrong kind'}]},6)
        self.assertEqual(len(self.questions()),2)

    def test_accepted_reply_resolves_only_named_question_and_later_user_reply(self):
        question=self.questions()[0]
        envelope=answer_envelope(question,'Anywhere')
        self.add('pending-steer','steeringUserMessage',{'type':'steeringUserMessage','status':'pending','input':[{'type':'text','text':envelope}]},6)
        self.assertEqual(len(self.questions()),2)
        self.add('accepted-steer','steeringUserMessage',{'type':'steeringUserMessage','status':'accepted','input':[{'type':'text','text':envelope}]},7)
        self.assertEqual(len(self.questions()),1)
        self.add('receipt','userMessage',{'content':[{'type':'text','text':answer_envelope(self.questions()[0],'Please keep it simple')} ]},8)
        self.assertEqual(self.questions(),[])

    def test_completed_interrupted_and_new_turns_clear_old_question_counts_and_panels(self):
        for status in ['completed','interrupted','failed']:
            with sqlite3.connect(self.root/'thread_history_1.sqlite') as db:db.execute('UPDATE thread_turns SET status=?',(status,))
            self.assertEqual(self.questions(),[]);self.assertEqual(self.history.list_threads()[0]['question_count'],0)
        with sqlite3.connect(self.root/'thread_history_1.sqlite') as db:
            db.execute("INSERT INTO thread_turns VALUES (?, 'new-turn', 'inProgress', 2, NULL, 20)",(CHAT,))
        self.assertEqual(self.questions(),[])
        self.add('new-question','agentMessage',{'delivery':'async','questions':[{'title':'A current question?','options':['Yes']}]},21)
        self.assertEqual(len(self.questions()),1);self.assertEqual(self.questions()[0]['title'],'A current question?')

    def test_reply_in_another_chat_does_not_resolve_this_question(self):
        with sqlite3.connect(self.root/'thread_history_1.sqlite') as db:
            db.execute('INSERT INTO thread_items VALUES (?,?,?,?,?,?,?,?)',('another','reply','userMessage',json.dumps({'content':[{'type':'text','text':answer_envelope(self.questions()[0],'Yes')}]}),1006,None,None,6))
        self.assertEqual(len(self.questions()),2)

    def test_malformed_rows_and_runtime_requests_are_not_async_questions(self):
        rows=[{'item_id':'x','item_type':'agentMessage','created_at_ms':1,'item_json':json.dumps(value)} for value in [None,[],{'delivery':'async','questions':'bad'},{'delivery':'async','questions':[None,{'title':4},{'title':'x','options':[{}]}]}]]
        self.assertEqual(pending_questions(rows,str),[])
        self.assertEqual(replies('<send_user_message_question_reply>bad</send_user_message_question_reply>'),[])
        self.assertEqual(replies(answer_envelope(self.questions()[0],'')),[])

    def test_source_databases_are_unchanged_and_credentials_hidden(self):
        self.add('secret-question','agentMessage',{'delivery':'async','questions':[{'title':'api_key=github_pat_'+'x'*30,'options':['sk-'+'a'*30]}]},6)
        path=self.root/'thread_history_1.sqlite';before=hashlib.sha256(path.read_bytes()).hexdigest()
        encoded=json.dumps(self.questions());self.history.list_threads()
        self.assertNotIn('github_pat_',encoded);self.assertNotIn('sk-aaaa',encoded)
        self.assertEqual(before,hashlib.sha256(path.read_bytes()).hexdigest())

    def test_live_owner_question_lifecycle_keeps_active_questions_but_clears_terminal_turns(self):
        item={'id':'call','type':'agentMessage','delivery':'async','questions':[{'title':'One?'},{'title':'Two?'}]}
        turn={'turnId':'turn','status':'inProgress','items':[item]}
        ids=live_question_ids(turn);self.assertEqual(len(ids),2)
        response=answer_envelope({'id':ids[0],'title':'One?'},'Answered')
        reply={'type':'steeringUserMessage','status':'pending','input':[{'type':'text','text':response}]}
        turn['items'].append(reply);self.assertEqual(live_question_ids(turn),ids)
        reply['status']='accepted';self.assertEqual(live_question_ids(turn),[ids[1]])
        turn['items'].append({'type':'agentMessage','text':'I am continuing.'});self.assertEqual(live_question_ids(turn),[ids[1]])
        turn['status']='completed';self.assertEqual(live_question_ids(turn),[])


class AnswerHttpTests(QuestionTests):
    def setUp(self):
        super().setUp();self.calls=[];self.fail_delivery=False;self.available=True
        outer=self
        class FakeBridge:
            def availability(self):return {'available':outer.available,'reason':'Read only'}
            def describe(self,_):return self.availability()
            def send(self,thread,message):
                outer.calls.append((thread,message))
                if outer.fail_delivery:raise ControlError('No acknowledgement',uncertain=True)
                return {'state':'queued','queue_item_id':'fake-queue'}
        self.server=CompanionServer(('127.0.0.1',0),self.history,FakeBridge(),CommandJournal(self.root/'journal.sqlite'))
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True);self.worker.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.worker.join();super().tearDown()

    def post(self,payload,csrf=True):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        connection.request('POST','/api/questions/answer',json.dumps(payload),{'X-Companion-CSRF':self.server.csrf} if csrf else {})
        response=connection.getresponse();result=response.status,json.loads(response.read());connection.close();return result

    def payload(self):
        question=self.questions()[0]
        return {'thread_id':CHAT,'question_id':question['id'],'revision':question['revision'],'answer':'Mobile data please'}

    def test_csrf_exact_question_and_revision_required(self):
        payload=self.payload()
        self.assertEqual(self.post(payload,False)[0],403)
        self.assertEqual(self.post({**payload,'question_id':'invented'})[0],409)
        self.assertEqual(self.post({**payload,'revision':'0'*64})[0],409)
        self.assertEqual(self.calls,[])

    def test_reply_targets_question_and_is_deduplicated_across_retries_and_devices(self):
        payload=self.payload();self.assertEqual(self.post(payload)[0],200);self.assertEqual(self.post(payload)[0],200)
        self.assertEqual(self.post({**payload,'answer':'Different reply'})[0],409)
        self.assertEqual(len(self.calls),1);thread,envelope=self.calls[0];self.assertEqual(thread,CHAT)
        self.assertEqual(replies(envelope),[{'questionItemId':payload['question_id'],'question':'Where should it work?','answer':'Mobile data please'}])
        # A service restart retains deduplication and does not persist the plaintext answer.
        self.server.journal=CommandJournal(self.root/'journal.sqlite');self.assertEqual(self.post(payload)[0],200)
        self.assertEqual(len(self.calls),1);self.assertNotIn(b'Mobile data please',(self.root/'journal.sqlite').read_bytes())

    def test_already_answered_question_cannot_receive_new_reply(self):
        payload=self.payload();self.add('desktop-answer','userMessage',{'content':[{'type':'text','text':answer_envelope(self.questions()[0],'Already answered on Mac')}]},6)
        self.assertEqual(self.post(payload)[0],409);self.assertEqual(self.calls,[])

    def test_live_desktop_progress_clears_saved_question_and_rejects_late_reply(self):
        payload=self.payload()
        class Live:
            def capabilities(self,_):return {'connected':True,'execution':{'async_question_ids':[],'question_count':0}}
        self.server.runtime=Live()
        self.assertEqual(self.post(payload)[0],409);self.assertEqual(self.calls,[])
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        c.request('GET','/api/chat?id='+CHAT);r=c.getresponse();value=json.loads(r.read());c.close()
        self.assertEqual(value['questions'],[])

    def test_lost_ack_is_never_resent(self):
        self.fail_delivery=True;payload=self.payload();self.assertEqual(self.post(payload)[1]['state'],'unknown');self.assertEqual(self.post(payload)[1]['state'],'unknown');self.assertEqual(len(self.calls),1)

    def test_read_only_and_archived_chats_cannot_answer(self):
        payload=self.payload();self.available=False;self.assertEqual(self.post(payload)[0],409);self.available=True
        with sqlite3.connect(self.root/'state_5.sqlite') as db:db.execute('UPDATE threads SET archived=1')
        self.assertEqual(self.post(payload)[0],409);self.assertEqual(self.calls,[])

    def test_invalid_answer_never_calls_bridge(self):
        for answer in ['',None,{},'x'*4001]:self.assertEqual(self.post({**self.payload(),'answer':answer})[0],400)
        self.assertEqual(self.calls,[])

    def test_changed_question_and_existing_desktop_queue_reject_new_replies(self):
        payload=self.payload()
        with sqlite3.connect(self.root/'thread_history_1.sqlite') as db:
            value={'delivery':'async','questions':[{'title':'Updated question?','options':['A','B']}]}
            db.execute("UPDATE thread_items SET item_json=? WHERE item_id='call-question'",(json.dumps(value),))
        self.assertEqual(self.post(payload)[0],409)
        question=self.questions()[0]
        with sqlite3.connect(self.root/'queue_1.sqlite') as db:
            db.execute('CREATE TABLE queued_items(id TEXT,thread_id TEXT,payload_json TEXT,created_at_ms INTEGER,queue_order INTEGER)')
            db.execute('INSERT INTO queued_items VALUES (?,?,?,?,?)',('desktop-reply',CHAT,json.dumps({'UserInput':{'content':[{'type':'text','text':answer_envelope(question,'A')}]}}),2000,1))
        self.assertEqual(self.questions()[0]['delivery'],'queued')
        self.assertEqual(self.questions()[0]['queued_answer'],'A')
        self.assertEqual(self.get('/api/chats')[2]['chats'][0]['question_count'], 0)
        self.assertEqual(self.post(self.payload())[0],409);self.assertEqual(self.calls,[])

    def test_queue_disappearance_alone_does_not_mark_a_reply_received(self):
        payload=self.payload();self.assertEqual(self.post(payload)[0],200)
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        connection.request('GET','/api/chat?id='+CHAT);response=connection.getresponse();value=json.loads(response.read());connection.close()
        self.assertEqual(value['questions'][0]['delivery'],'queued');self.assertEqual(len(value['questions']),2)

    def live_runtime(self,state='steered',fail=False):
        outer=self
        class Live:
            def capabilities(self,_):return {'steer':state=='steered','send_now':state=='sent'}
            def steer(self,thread,envelope,turn):
                outer.live_calls.append((thread,envelope,turn))
                if fail:raise RuntimeUnavailable('Desktop acknowledgement lost')
                return {'state':'steered','turn_id':turn}
            def send_now(self,thread,envelope):
                outer.live_calls.append((thread,envelope))
                return {'state':'sent','turn_id':'next-turn'}
        self.live_calls=[];self.server.runtime=Live()

    def get(self, path, etag=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        connection.request('GET', path, headers={'If-None-Match': etag} if etag else {})
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, response.getheader('ETag'), json.loads(data) if data else None

    def test_acknowledged_reply_clears_exact_card_and_catalog_across_restart(self):
        for state, index in [('steered', 0), ('sent', 1)]:
            self.live_runtime(state)
            question = self.questions()[index]
            _, chat_tag, _ = self.get('/api/chat?id=' + CHAT)
            _, list_tag, _ = self.get('/api/chats')
            payload = {**self.payload(), 'question_id': question['id'], 'revision': question['revision']}
            self.assertEqual(self.post(payload)[1]['state'], state)
            self.server.journal = CommandJournal(self.root / 'journal.sqlite')
            status, _, chat = self.get('/api/chat?id=' + CHAT, chat_tag)
            self.assertEqual(status, 200)
            self.assertEqual([q['id'] for q in chat['questions']], [q['id'] for q in self.questions()[index+1:]])
            status, _, catalog = self.get('/api/chats', list_tag)
            self.assertEqual(status, 200)
            self.assertEqual(catalog['chats'][0]['question_count'], 1-index)
            self.assertEqual(self.post(payload)[1]['state'], state)
            self.assertEqual(len(self.live_calls), 1)

    def test_updated_question_revision_is_actionable_after_old_receipt(self):
        self.live_runtime()
        self.assertEqual(self.post(self.payload())[1]['state'], 'steered')
        with sqlite3.connect(self.root/'thread_history_1.sqlite') as db:
            updated = {'delivery':'async','questions':[{'title':'Revised question?'}]}
            db.execute("UPDATE thread_items SET item_json=? WHERE item_id='call-question'", (json.dumps(updated),))
        self.assertEqual(self.get('/api/chat?id='+CHAT)[2]['questions'][0]['delivery'], 'unanswered')
        self.assertEqual(self.get('/api/chats')[2]['chats'][0]['question_count'], 1)

    def test_queued_reply_promotion_retires_only_matching_question_after_ack(self):
        first, second = self.questions()
        self.assertEqual(self.post(self.payload())[1]['state'], 'queued')
        self.server.journal.execute('promote-other', {}, lambda: {'state':'steered'},
                                    reconcile_queue_id='other-queue')
        self.server.journal.execute('promote-uncertain', {}, lambda: {'state':'unknown'},
                                    reconcile_queue_id='fake-queue')
        questions = self.get('/api/chat?id='+CHAT)[2]['questions']
        self.assertEqual([(q['id'], q['delivery']) for q in questions],
                         [(first['id'], 'queued'), (second['id'], 'unanswered')])
        self.assertEqual(self.get('/api/chats')[2]['chats'][0]['question_count'], 1)
        self.server.journal.execute('confirmed-promotion', {}, lambda: {'state':'steered'},
                                    reconcile_queue_id='fake-queue')
        self.assertEqual(self.get('/api/chat?id='+CHAT)[2]['questions'], [second])

    def test_active_question_answer_is_steered_once_without_queueing(self):
        self.live_runtime();payload=self.payload()
        self.assertEqual(self.post(payload)[1]['state'],'steered');self.assertEqual(self.post(payload)[1]['state'],'steered')
        self.assertEqual(self.calls,[]);self.assertEqual(len(self.live_calls),1)
        self.assertEqual(self.live_calls[0][2],'turn-1')
        self.assertEqual(replies(self.live_calls[0][1])[0]['answer'],payload['answer'])

    def test_idle_question_answer_starts_a_turn_without_queueing(self):
        self.live_runtime('sent');self.assertEqual(self.post(self.payload())[1]['state'],'sent')
        self.assertEqual(self.calls,[]);self.assertEqual(len(self.live_calls),1)

    def test_uncertain_direct_answer_is_not_fallen_back_to_queue_or_replayed(self):
        self.live_runtime(fail=True);payload=self.payload()
        self.assertEqual(self.post(payload)[1]['state'],'unknown');self.assertEqual(self.post(payload)[1]['state'],'unknown')
        self.assertEqual(self.calls,[]);self.assertEqual(len(self.live_calls),1)


if __name__=='__main__':unittest.main()
