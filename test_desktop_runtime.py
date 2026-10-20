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
    def setUp(self):
        self.calls=[];self.deleted=True
        self.item={'id':'queue-1','text':'Exact original','client_id':'client-1'}
        self.queued={'id':'queue-1','input':[{'type':'text','text':'Exact original','text_elements':[]}],'clientUserMessageId':'client-1'}
        outer=self
        class Client:
            def __init__(self,command,timeout):outer.command=command
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def call(self,method,params):
                outer.calls.append((method,params))
                if method=='thread/queue/list':return {'data':[outer.queued]}
                if method=='thread/queue/delete':return {'deleted':outer.deleted}
                raise AssertionError('Only queue operations are allowed')
        self.client=Client

    def claim(self):return claim_queued_message('chat-1',self.item,self.client,'/test/codex')

    def test_claim_only_uses_queue_apis_and_verifies_original_identity(self):
        self.assertEqual(self.claim()['message']['context']['prompt'],'Exact original')
        self.assertEqual(self.command,['/test/codex','app-server','--stdio'])
        self.assertEqual([method for method,_ in self.calls],['thread/queue/list','thread/queue/delete'])
        self.assertEqual(self.calls[-1][1],{'threadId':'chat-1','queuedSubmissionId':'queue-1'})

    def test_edited_or_different_submission_is_never_deleted(self):
        for key,value in [('clientUserMessageId','other-client'),('input',[{'type':'text','text':'Edited'}]),('input',[{'type':'image','url':'file:///private'}])]:
            original=self.queued.copy();self.queued[key]=value;self.calls=[]
            with self.assertRaises(RuntimeUnavailable):self.claim()
            self.assertEqual(len(self.calls),1);self.queued=original

    def test_missing_or_already_claimed_message_stops_promotion(self):
        self.queued['id']='other-queue'
        with self.assertRaises(RuntimeUnavailable):self.claim()
        self.queued['id']='queue-1';self.deleted=False
        with self.assertRaises(RuntimeUnavailable):self.claim()

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

    def test_queue_claim_preserves_approved_photo_paths(self):
        self.item['photos']=['/private/approved-photo.jpg']
        self.queued['input'].append({'type':'localImage','path':'/private/approved-photo.jpg'})
        self.assertEqual(self.claim()['message']['context']['prompt'],'Exact original')
        self.calls=[];self.queued['input'][-1]['path']='/private/another-photo.jpg'
        with self.assertRaises(RuntimeUnavailable):self.claim()
        self.assertEqual(len(self.calls),1)

    def test_native_queue_api_url_field_matches_storage_image_url_without_fetching(self):
        url='data:image/jpeg;base64,EXACT_OWNED_PHOTO'
        self.item.update(photos=['/private/photo.jpg'],image_urls=[url],source_photos=[('image',url)])
        self.queued['input'].append({'type':'image','url':url,'detail':None})
        self.assertEqual(self.claim()['message']['context']['prompt'],'Exact original')
        self.calls=[];self.queued['input'][-1]['url']='https://other.example/photo'
        with self.assertRaises(QueueNotClaimed):self.claim()
        self.assertEqual(len(self.calls),1)


class DesktopRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.home=Path(self.temp.name)
        (self.home/'ipc').mkdir(mode=0o700);self.path=self.home/'ipc/ipc.sock'
        self.server=socket.socket(socket.AF_UNIX);self.server.bind(str(self.path));self.path.chmod(0o600);self.server.listen();self.server.settimeout(.1)
        self.requests=[];self.error=None;self.wrong_turn=False;self.stop=False
        self.queued=True;self.fail_after_claim=False
        self.model='test-model';self.effort='high';self.live_override=None
        self.thread={'id':'chat-1','archived':False,'last_recorded_turn':{'turn_id':'turn-1','status':'inProgress'},'details':{'folder':'/demo/project'}}
        self.worker=threading.Thread(target=self.serve,daemon=True);self.worker.start()
        self.runtime=DesktopRuntime(self.home,lambda _:self.thread)

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
                                state={'id':'chat-1','threadRuntimeStatus':{'type':'active' if turn['status']=='inProgress' else 'idle'},'turns':[{'turnId':turn['turn_id'],'status':turn['status']}] }
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

    def test_steer_is_direct_owner_routed_and_checks_acknowledgement(self):
        result=self.runtime.steer('chat-1','Keep the fix small','turn-1')
        self.assertEqual(result['state'],'steered');request=self.requests[-1]
        self.assertEqual(request['method'],'thread-follower-steer-turn');self.assertEqual(request['version'],1)
        self.assertEqual(request['targetClientId'],'desktop-owner');self.assertEqual(request['params']['conversationId'],'chat-1')
        self.assertEqual(request['params']['input'],[{'type':'text','text':'Keep the fix small','text_elements':[]}])
        self.assertEqual(request['params']['attachments'],[])
        self.assertNotIn('approvalPolicy',json.dumps(request));self.assertNotIn('sandbox',json.dumps(request))
        self.wrong_turn=True
        with self.assertRaises(RuntimeUnavailable):self.runtime.steer('chat-1','Another correction','turn-1')

    def test_native_steer_preserves_photo_inputs(self):
        self.runtime.steer('chat-1','Inspect this photo','turn-1',photos=['/private/companion/photo.jpg'])
        self.assertEqual(self.requests[-1]['params']['input'][-1],{'type':'localImage','path':'/private/companion/photo.jpg'})

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

    def promotion_runtime(self):
        def read_queue(*_):
            if not self.queued:raise KeyError('gone')
            return {'id':'queued-1'}
        self.runtime.queue_reader=read_queue;self.runtime.receipt_reader=lambda *_:False
        self.runtime.promotions=QueuePromotions(self.home/'promotions.sqlite')
        def claim(thread,item):
            self.queued=False
            return {'message':{'context':{'prompt':item['text']}}}
        self.runtime.queue_claimer=claim
        return {'id':'queued-1','text':'Queued task','client_id':'original-client-1','revision':'a'*64}

    def test_queue_promotion_claims_original_before_steering_once(self):
        item=self.promotion_runtime();result=self.runtime.promote_queued('chat-1',item,'turn-1')
        self.assertEqual(result['state'],'steered');self.assertFalse(self.queued)
        self.assertEqual(self.requests[-1]['method'],'thread-follower-steer-turn')
        self.assertEqual(self.requests[-1]['params']['clientUserMessageId'],'original-client-1')
        self.assertEqual(self.runtime.queue_recovery('chat-1',str),[])
        self.assertNotIn(b'Queued task',(self.home/'promotions.sqlite').read_bytes())

    def test_promotion_owner_failure_is_retryable_and_keeps_original_queue(self):
        item=self.promotion_runtime();self.error='no-client-found'
        with self.assertRaises(QueueNotClaimed):self.runtime.promote_queued('chat-1',item,'turn-1')
        self.assertTrue(self.queued)
        self.assertNotIn('thread-follower-steer-turn',[r['method'] for r in self.requests])
        self.error=None
        self.assertEqual(self.runtime.promote_queued('chat-1',item,'turn-1')['state'],'steered')

    def test_uncertain_promotion_preserves_text_without_automatic_replay(self):
        item=self.promotion_runtime();self.fail_after_claim=True
        with self.assertRaises(RuntimeUnavailable):self.runtime.promote_queued('chat-1',item,'turn-1')
        self.assertFalse(self.queued);pending=self.runtime.queue_recovery('chat-1',str)
        self.assertEqual(pending[0]['text'],'Queued task')
        count=len(self.requests);self.runtime.queue_recovery('chat-1',str);self.assertEqual(len(self.requests),count)
        self.runtime.receipt_reader=lambda *_:True;self.assertEqual(self.runtime.queue_recovery('chat-1',str),[])

    def test_known_preclaim_failure_keeps_original_queued_and_safe_retry_retains_recovery(self):
        item=self.promotion_runtime();claim=self.runtime.queue_claimer
        def unavailable(*_):raise QueueNotClaimed('Not claimed')
        self.runtime.queue_claimer=unavailable
        with self.assertRaises(QueueNotClaimed):self.runtime.promote_queued('chat-1',item,'turn-1')
        self.assertTrue(self.queued);self.assertEqual(self.runtime.queue_recovery('chat-1',str),[])
        self.runtime.queue_claimer=claim;self.fail_after_claim=True
        with self.assertRaises(RuntimeUnavailable):self.runtime.promote_queued('chat-1',item,'turn-1')
        self.assertEqual(self.runtime.queue_recovery('chat-1',str)[0]['text'],'Queued task')


if __name__=='__main__':unittest.main()
