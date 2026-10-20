from contextlib import closing
import json
from pathlib import Path
import socket
import struct
import tempfile
import threading
import unittest

from desktop_runtime import DesktopClient, DesktopRuntime,claim_queued_message,queue_photo_message,QueueNotClaimed
from runtime import RuntimeUnavailable
from promotions import QueuePromotions


class QueueClaimTests(unittest.TestCase):
    def test_claim_uses_only_queue_list_and_delete_with_explicit_identity_verification(self):
        calls=[];queued={'id':'queued','clientUserMessageId':'client','input':[{'type':'text','text':'Original'}]}
        class QueueClient:
            def __init__(self,*args,**kwargs):pass
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def call(self,method,params):
                calls.append((method,params))
                if method=='thread/queue/list':return {'data':[queued]}
                if method=='thread/queue/delete':return {'deleted':True}
                raise AssertionError('Only queue operations are allowed')
        item={'id':'queued','client_id':'client','text':'Original'}
        result=claim_queued_message('chat',item,QueueClient,'/fake/codex')
        self.assertEqual(result['message']['context']['prompt'],'Original')
        self.assertEqual([method for method,_ in calls],['thread/queue/list','thread/queue/delete'])
        queued['input'][0]['text']='Changed on Mac';calls.clear()
        with self.assertRaises(QueueNotClaimed):claim_queued_message('chat',item,QueueClient,'/fake/codex')
        self.assertEqual([method for method,_ in calls],['thread/queue/list'])

    def test_photo_queue_uses_only_add_and_verifies_exact_client_and_content(self):
        calls=[]
        class PhotoClient:
            def __init__(self,*_,**kwargs):pass
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def call(self,method,params):
                calls.append((method,params))
                return {'queuedSubmission':{'id':'queue-photo-1','input':params['input'],'clientUserMessageId':params['clientUserMessageId']}}
        result=queue_photo_message('chat-1','Inspect this',['/private/photo.jpg'],'/test/codex',PhotoClient)
        self.assertEqual(result['state'],'queued');self.assertEqual(len(calls),1);self.assertEqual(calls[0][0],'thread/queue/add')
        self.assertEqual(calls[0][1]['input'][-1],{'type':'localImage','path':'/private/photo.jpg'})




class DesktopRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.home=Path(self.temp.name)
        (self.home/'ipc').mkdir(mode=0o700);self.path=self.home/'ipc/ipc.sock'
        self.server=socket.socket(socket.AF_UNIX);self.server.bind(str(self.path));self.path.chmod(0o600);self.server.listen();self.server.settimeout(.1)
        self.requests=[];self.error=None;self.wrong_turn=False;self.stop=False
        self.queued=True;self.fail_after_claim=False
        self.model='test-model';self.effort='high';self.live_override=None
        self.approval_requests=[];self.keep_approval=False
        self.thread={'id':'chat-1','archived':False,'last_recorded_turn':{'turn_id':'turn-1','status':'inProgress'},'details':{'folder':'/demo/project'}}
        self.worker=threading.Thread(target=self.serve,daemon=True);self.worker.start()
        self.runtime=DesktopRuntime(self.home,lambda _:self.thread)
        self.atomic_calls=[]
        outer=self
        class Atomic:
            def present(self):return True
            def capabilities(self,_):return {'steer':True}
            def steer(self,*args,**kwargs):
                outer.atomic_calls.append((*args,kwargs))
                if outer.wrong_turn:raise RuntimeUnavailable('Expected turn changed')
                return {'state':'steered','client_id':kwargs['client_id'],'turn_id':args[2]}
        self.runtime.shared=Atomic()

    def tearDown(self):
        self.stop=True;self.server.close();self.worker.join(timeout=2);self.temp.cleanup()

    @staticmethod
    def exact(connection,size):
        value=b''
        while len(value)<size:
            chunk=connection.recv(size-len(value))
            if not chunk:raise EOFError()
            value+=chunk
        return value

    @staticmethod
    def send(connection,value):
        data=json.dumps(value).encode();frame=struct.pack('<I',len(data))+data
        connection.sendall(frame[:2]);connection.sendall(frame[2:])  # Header fragmentation is normal on streams.

    def serve(self):
        while not self.stop:
            try:connection,_=self.server.accept()
            except socket.timeout:continue
            except OSError:return
            with closing(connection):
                connection.settimeout(1)
                while not self.stop:
                    try:
                        size=struct.unpack('<I',self.exact(connection,4))[0];request=json.loads(self.exact(connection,size))
                        method=request['method']
                        if method=='thread-stream-following-changed':
                            if request['params']['following']:
                                turn=self.live_override or self.thread['last_recorded_turn']
                                state={'id':'chat-1','threadRuntimeStatus':{'type':'active' if turn['status']=='inProgress' else 'idle'},'turns':[{'turnId':turn['turn_id'],'status':turn['status']}], 'requests':self.approval_requests}
                                self.send(connection,{'type':'broadcast','method':'thread-stream-state-changed','version':11,'sourceClientId':'desktop-owner','params':{'hostId':'local','conversationId':'chat-1','change':{'type':'snapshot','conversationState':state}}})
                            continue
                        self.requests.append(request)
                        if self.error and method!='initialize':
                            self.send(connection,{'type':'response','requestId':request['requestId'],'resultType':'error','error':self.error});continue
                        if method=='initialize':result={'clientId':'companion-test-client'}
                        elif method=='thread-owner-discovery':result={'supportsUntrustedAppInput':True}
                        elif method=='thread-follower-read-model-settings':result={'settings':{'resumeState':'resumed','model':self.model,'reasoningEffort':self.effort}}
                        elif method=='thread-follower-update-thread-settings':
                            settings=request['params']['threadSettings'];self.model=settings['model'];self.effort=settings.get('effort',self.effort);result={'applied':True}
                        elif method=='thread-follower-steer-turn':
                            if self.fail_after_claim:
                                self.send(connection,{'type':'response','requestId':request['requestId'],'resultType':'error','error':'connection-lost'});continue
                            result={'result':{'turnId':'different-turn' if self.wrong_turn else 'turn-1'}}
                        elif method=='thread-follower-remove-queued-message':
                            self.queued=False;result={'removed':{'message':{'context':{'prompt':'Queued task'}}}}
                        elif method=='thread-follower-start-turn':result={'result':{'turn':{'id':'turn-2','status':'inProgress'}}}
                        elif method=='thread-follower-command-approval-decision':
                            result={'ok':True}
                            if not self.keep_approval:self.approval_requests=[]
                        else:raise ValueError('Unexpected mutation')
                        self.send(connection,{'type':'broadcast','method':'unrelated','params':{'text':'PRIVATE_TOOL_PAYLOAD'}})
                        self.send(connection,{'type':'response','requestId':request['requestId'],'resultType':'success','method':method,'handledByClientId':'desktop-owner','result':result})
                    except (OSError,EOFError):break

    def test_capabilities_read_the_actual_owner_without_loading_or_resuming(self):
        value=self.runtime.capabilities('chat-1')
        self.assertTrue(value['steer']);self.assertFalse(value['send_now']);self.assertTrue(value['model_selection'])
        self.assertEqual(value['model'],'test-model')
        self.assertEqual([r['method'] for r in self.requests],['initialize','thread-owner-discovery','thread-follower-read-model-settings'])
        self.assertNotIn('PRIVATE_TOOL_PAYLOAD',json.dumps(value))



    def test_model_changes_use_next_turn_native_settings_and_readback_without_permissions(self):
        result=self.runtime.set_model('chat-1','another-model','medium');self.assertEqual(result['model'],'another-model')
        request=next(r for r in self.requests if r['method']=='thread-follower-update-thread-settings')
        self.assertEqual(request['version'],2)
        self.assertEqual(request['params'],{'conversationId':'chat-1','threadSettings':{'model':'another-model','effort':'medium'}})
        self.assertEqual(self.thread['last_recorded_turn']['turn_id'],'turn-1')
        self.assertEqual(self.requests[-1]['method'],'thread-follower-read-model-settings')

    def test_changed_active_turn_is_rejected_before_submission(self):
        self.thread['last_recorded_turn']['turn_id']='new-turn'
        with self.assertRaises(RuntimeUnavailable):self.runtime.steer('chat-1','Correction','turn-1')
        self.assertNotIn('thread-follower-steer-turn',[r['method'] for r in self.requests])

    def test_idle_send_inherits_chat_settings_and_does_not_write_to_a_queue(self):
        self.thread['last_recorded_turn']['status']='completed'
        self.assertTrue(self.runtime.capabilities('chat-1')['send_now'])
        result=self.runtime.send_now('chat-1','Next request');self.assertEqual(result['state'],'sent')
        request=self.requests[-1];self.assertEqual(request['version'],2)
        operation=request['params']['turnStart'];self.assertEqual(operation['request']['threadId'],'chat-1')
        self.assertTrue(operation['context']['inheritThreadSettings'])
        self.assertEqual(result['client_id'],operation['request']['clientUserMessageId'])
        for forbidden in ('model','effort','approvalPolicy','sandboxPolicy','permissions'):
            self.assertNotIn(forbidden,operation['request'])

    def test_send_cannot_preempt_an_active_turn_or_archived_chat(self):
        with self.assertRaises(RuntimeUnavailable):self.runtime.send_now('chat-1','Next')
        self.thread['last_recorded_turn']['status']='completed';self.thread['archived']=True
        with self.assertRaises(RuntimeUnavailable):self.runtime.send_now('chat-1','Next')
        self.assertNotIn('thread-follower-start-turn',[r['method'] for r in self.requests])

    def test_owner_failure_is_not_reported_as_live_or_fallen_back_to_queue(self):
        self.error='no-client-found';value=self.runtime.capabilities('chat-1')
        self.assertFalse(value['steer']);self.assertFalse(value['send_now'])
        with self.assertRaises(RuntimeUnavailable):self.runtime.steer('chat-1','Correction','turn-1')

    def test_live_idle_overrides_stuck_recorded_in_progress(self):
        self.live_override={'turn_id':'turn-1','status':'completed'}
        value=self.runtime.capabilities('chat-1')
        self.assertFalse(value['steer']);self.assertTrue(value['send_now'])
        with self.assertRaises(RuntimeUnavailable):self.runtime.steer('chat-1','Not sent','turn-1')
        self.assertNotIn('thread-follower-steer-turn',[r['method'] for r in self.requests])

    def test_live_turn_overrides_older_history_without_steering_old_turn(self):
        self.thread['last_recorded_turn']={'turn_id':'obsolete','status':'completed'}
        self.live_override={'turn_id':'turn-1','status':'inProgress'}
        self.assertEqual(self.runtime.capabilities('chat-1')['execution']['turn_id'],'turn-1')
        self.assertEqual(self.runtime.steer('chat-1','Correction','turn-1')['state'],'steered')

    def test_socket_security_and_method_allowlist(self):
        self.path.chmod(0o666);self.assertFalse(self.runtime.present());self.path.chmod(0o600)
        self.path.parent.chmod(0o777);self.assertFalse(self.runtime.present());self.path.parent.chmod(0o700)
        with self.runtime.client() as client:
            with self.assertRaises(RuntimeUnavailable):client.call('thread/resume',{})
        self.assertEqual(self.requests[-1]['params']['clientType'],'desktop-companion')

    def test_live_approval_routes_exact_typed_request_to_owner_and_verifies_removal(self):
        self.approval_requests=[{'id':17,'method':'item/commandExecution/requestApproval','params':{'threadId':'chat-1','turnId':'turn-1','itemId':'command-1','command':'pwd'}}]
        view=self.runtime.capabilities('chat-1')['execution']['approvals'][0]
        self.assertEqual(self.runtime.execution_state('chat-1')['approval_count'],1)
        self.assertNotIn('approvals',self.runtime.execution_state('chat-1'))
        result=self.runtime.respond_approval('chat-1',view['id'],view['revision'],'allow',{})
        self.assertEqual(result['state'],'answered')
        operation=self.requests[-1];self.assertEqual(operation['targetClientId'],'desktop-owner')
        self.assertEqual(operation['params'],{'conversationId':'chat-1','requestId':17,'decision':'accept'})
        self.assertNotIn('start-turn',[r['method'] for r in self.requests]);self.assertEqual(self.approval_requests,[])

    def test_changed_request_is_not_answered_and_ack_without_resolution_is_uncertain(self):
        from approvals import ApprovalChanged
        self.approval_requests=[{'id':'request-1','method':'item/commandExecution/requestApproval','params':{'threadId':'chat-1','turnId':'turn-1','itemId':'command-1','command':'pwd'}}]
        view=self.runtime.capabilities('chat-1')['execution']['approvals'][0]
        self.approval_requests[0]['params']['command']='changed command'
        with self.assertRaises(ApprovalChanged):self.runtime.respond_approval('chat-1',view['id'],view['revision'],'allow',{})
        self.assertNotIn('thread-follower-command-approval-decision',[r['method'] for r in self.requests])
        view=self.runtime.capabilities('chat-1')['execution']['approvals'][0];self.keep_approval=True
        with self.assertRaises(RuntimeUnavailable):self.runtime.respond_approval('chat-1',view['id'],view['revision'],'decline',{})
        self.assertEqual(len([r for r in self.requests if r['method']=='thread-follower-command-approval-decision']),1)

    def promotion_runtime(self):
        item={'id':'queued-1','text':'Queued task','client_id':'original-client-1','revision':'a'*64}
        def read_queue(*_):
            if not self.queued:raise KeyError('gone')
            return dict(item)
        self.runtime.queue_reader=read_queue;self.runtime.receipt_reader=lambda *_:False
        self.runtime.promotions=QueuePromotions(self.home/'promotions.sqlite')
        def claim(thread,item):
            self.queued=False
            return {'message':{'context':{'prompt':item['text']}}}
        self.runtime.queue_claimer=claim
        return item





    def test_atomic_shared_steer_gets_exact_turn_identity_and_photos_without_follower_retry(self):
        result=self.runtime.steer('chat-1','Keep the fix small','turn-1',photos=['/private/photo.jpg'])
        self.assertEqual(result['state'],'steered')
        self.assertEqual(self.atomic_calls[-1][:3],('chat-1','Keep the fix small','turn-1'))
        self.assertEqual(self.atomic_calls[-1][3]['photos'],['/private/photo.jpg'])
        self.assertNotIn('thread-follower-steer-turn',[r['method'] for r in self.requests])
        self.assertEqual(result['client_id'],self.atomic_calls[-1][3]['client_id'])

    def use_follower(self):
        self.runtime.shared.present=lambda:False
        self.runtime.shared.capabilities=lambda _: {'steer':False}

    def test_follower_steer_is_available_without_a_shared_daemon_and_reports_native_rollover(self):
        self.use_follower();self.assertTrue(self.runtime.capabilities('chat-1')['steer'])
        self.wrong_turn=True
        result=self.runtime.steer('chat-1','Keep this fix small','turn-1',photos=['/private/photo.jpg'])
        self.assertEqual(result['state'],'steered');self.assertEqual(result['turn_id'],'different-turn');self.assertTrue(result['turn_changed'])
        requests=[r for r in self.requests if r['method']=='thread-follower-steer-turn']
        self.assertEqual(len(requests),1);request=requests[0]
        self.assertEqual(request['version'],1);self.assertEqual(request['targetClientId'],'desktop-owner')
        self.assertEqual(request['params']['clientUserMessageId'],result['client_id'])
        self.assertEqual(request['params']['input'][-1],{'type':'localImage','path':'/private/photo.jpg'})
        self.assertNotIn('approvalPolicy',request['params']);self.assertEqual(self.atomic_calls,[])

    def test_queue_promotion_removes_original_before_steering_and_reuses_client_identity(self):
        self.use_follower();item=self.promotion_runtime()
        self.assertTrue(self.runtime.capabilities('chat-1')['queue_steer'])
        result=self.runtime.promote_queued('chat-1',item,'turn-1')
        self.assertEqual(result['state'],'steered');self.assertFalse(self.queued)
        mutations=[r for r in self.requests if r['method'] in ('thread-follower-remove-queued-message','thread-follower-steer-turn')]
        self.assertEqual([r['method'] for r in mutations],['thread-follower-steer-turn'])
        self.assertEqual(result['client_id'],item['client_id']);self.assertEqual(self.runtime.queue_recovery('chat-1',str),[])

    def test_changed_queue_item_is_rejected_before_claim(self):
        item=self.promotion_runtime();self.runtime.queue_reader=lambda *_:{**item,'revision':'new'}
        with self.assertRaises(QueueNotClaimed):self.runtime.promote_queued('chat-1',item,'turn-1')
        self.assertTrue(self.queued);self.assertNotIn('thread-follower-steer-turn',[r['method'] for r in self.requests])

    def test_changed_removed_text_is_saved_without_sending_the_old_version(self):
        item=self.promotion_runtime()
        def changed(*_):
            self.queued=False;return {'message':{'context':{'prompt':'Actual edited text'}}}
        self.runtime.queue_claimer=changed
        with self.assertRaises(RuntimeUnavailable):self.runtime.promote_queued('chat-1',item,'turn-1')
        self.assertEqual(self.runtime.queue_recovery('chat-1',str)[0]['text'],'Actual edited text')
        self.assertNotIn('thread-follower-steer-turn',[r['method'] for r in self.requests])

    def test_turn_ending_after_removal_keeps_recovery_without_starting_a_new_turn(self):
        item=self.promotion_runtime()
        def ended(*_):
            self.queued=False;self.live_override={'turn_id':'turn-1','status':'completed'}
            return {'message':{'context':{'prompt':item['text']}}}
        self.runtime.queue_claimer=ended
        with self.assertRaises(RuntimeUnavailable):self.runtime.promote_queued('chat-1',item,'turn-1')
        self.assertEqual(self.runtime.queue_recovery('chat-1',str)[0]['text'],item['text'])
        self.assertNotIn('thread-follower-start-turn',[r['method'] for r in self.requests])
        self.assertNotIn('thread-follower-steer-turn',[r['method'] for r in self.requests])

    def test_unknown_steer_after_claim_is_saved_and_never_automatically_replayed(self):
        self.use_follower();item=self.promotion_runtime();self.fail_after_claim=True
        with self.assertRaises(RuntimeUnavailable):self.runtime.promote_queued('chat-1',item,'turn-1')
        self.assertFalse(self.queued);self.assertEqual(self.runtime.queue_recovery('chat-1',str)[0]['text'],item['text'])
        self.assertEqual(len([r for r in self.requests if r['method']=='thread-follower-steer-turn']),1)
        for body in [None,[],{'result':[]},{'result':{'turnId':''}}]:
            class Malformed:
                def call(self,*args,**kwargs):return {'handledByClientId':'owner','result':body}
            with self.assertRaises(RuntimeUnavailable):self.runtime._steer(Malformed(),'owner','chat-1',self.thread,'Text',[],'client','turn-1')


if __name__=='__main__':unittest.main()
