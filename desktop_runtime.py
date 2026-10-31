"""Version-specific adapter for the running desktop's local coordination protocol.

No daemon creation, chat resume, UI scripting, credentials, or database writes.
The phone never chooses an IPC method, socket, owner, or permission configuration.
"""

import json
from collections import OrderedDict
import threading
import base64
import hashlib
import os
from pathlib import Path
import socket
import stat
import struct
import shutil
import re
import time
import tempfile
import subprocess
import uuid

from runtime import RuntimeUnavailable, SharedRuntime, RpcClient
from approvals import approval_views, pending_requests, response_for, ApprovalChanged
from questions import live_question_ids


def photo_queue_supported(executable, runner=None):
    """Inspect the installed RPC schema without opening a chat or queueing input."""
    runner = runner or subprocess.run
    try:
        with tempfile.TemporaryDirectory(prefix='companion-schema-') as directory:
            result = runner([executable, 'app-server', 'generate-json-schema', '--experimental', '--out', directory],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
            if result.returncode != 0:return False
            root = Path(directory)
            paths = [root/'ClientRequest.json', root/'v2/ThreadQueueAddParams.json']
            if any(path.is_symlink() or not path.is_file() or path.stat().st_size > 8*1024*1024 for path in paths):return False
            methods, params = (json.loads(path.read_text()) for path in paths)
            exposed = any(entry.get('properties',{}).get('method',{}).get('enum') == ['thread/queue/add']
                          for entry in methods.get('oneOf',[]))
            reference = params['properties']['input']['items']['$ref']
            if reference != '#/definitions/UserInput':return False
            choices = params['definitions']['UserInput'].get('oneOf',[])
            images = any(entry.get('properties',{}).get('type',{}).get('enum') == ['localImage']
                         and 'path' in entry.get('required',[]) for entry in choices)
            return exposed and images
    except (OSError,ValueError,KeyError,TypeError,AttributeError,subprocess.SubprocessError):
        return False


def queue_input_matches(inputs,message,photos):
    """The queue normalizes localImage to inline image; compare bytes, never fetch."""
    if not isinstance(inputs,list) or any(not isinstance(part,dict) or part.get('type') not in ('text','localImage','image') or part.get('type')=='text' and not isinstance(part.get('text'),str) for part in inputs):return False
    if any(part.get('text_elements') for part in inputs if part['type']=='text'):return False
    if '\n'.join(part.get('text','') for part in inputs if part['type']=='text')!=message:return False
    images=[part for part in inputs if part['type']!='text']
    if len(images)!=len(photos):return False
    for part,path in zip(images,photos):
        if part['type']=='localImage':
            if part.get('path')!=path:return False
            continue
        url=part.get('url',part.get('image_url'))
        if not isinstance(url,str) or not url.startswith('data:image/jpeg;base64,') or len(url)>5_592_500:return False
        try:
            if hashlib.sha256(base64.b64decode(url.split(',',1)[1],validate=True)).digest()!=hashlib.sha256(Path(path).read_bytes()).digest():return False
        except (ValueError,OSError):return False
    return True


def queue_photo_message(thread_id,message,photos,executable=None,client_factory=RpcClient):
    """The CLI queue subcommand rejects images; the queue API accepts localImage input."""
    executable=executable or shutil.which('codex')
    if not executable:raise RuntimeUnavailable('The photo queue service is unavailable.')
    identity=str(uuid.uuid4());inputs=[{'type':'text','text':message,'text_elements':[]}]+[{'type':'localImage','path':path} for path in photos]
    with client_factory([executable,'app-server','--stdio'],timeout=10) as client:
        response=client.call('thread/queue/add',{'threadId':thread_id,'clientUserMessageId':identity,'input':inputs})
        result=response.get('queuedSubmission') if isinstance(response,dict) else None
    if not isinstance(result,dict):raise RuntimeUnavailable('Photo queue acknowledgement could not be verified. Check the queue before resending.')
    queued_id=result.get('id');accepted=result.get('input',[])
    if not isinstance(queued_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',queued_id) or result.get('clientUserMessageId')!=identity:
        raise RuntimeUnavailable('Photo queue acknowledgement could not be verified. Check the queue before resending.')
    if not queue_input_matches(accepted,message,photos):
        raise RuntimeUnavailable('Photo queue content acknowledgement changed. Check the queue before resending.')
    return {'state':'queued','queue_item_id':queued_id,'message':'Message and photos queued for this desktop chat. They will wait until its active turn finishes.'}


class QueueNotClaimed(RuntimeUnavailable):
    """No removal or steering request was made; an explicit retry is safe."""


def verified_queued(client,thread_id,item):
    page=client.call('thread/queue/list',{'threadId':thread_id,'limit':100})
    queued=next((q for q in page.get('data',[]) if q.get('id')==item['id']),None)
    if not queued:raise QueueNotClaimed('This message is no longer queued. Refresh the chat.')
    inputs=queued.get('input',[])
    def image_url(part):return part.get('url',part.get('image_url'))
    if any(not isinstance(part,dict) or not (part.get('type')=='text' and isinstance(part.get('text'),str) and not part.get('text_elements') or part.get('type')=='localImage' and part.get('path') in item.get('photos',[]) or part.get('type')=='image' and image_url(part) in item.get('image_urls',[])) for part in inputs):
        raise QueueNotClaimed('This queued message needs the desktop.')
    text='\n'.join(part['text'] for part in inputs if part['type']=='text')
    source_photos=[(part['type'],part.get('path') if part['type']=='localImage' else image_url(part)) for part in inputs if part['type'] in ('localImage','image')]
    expected_photos=item.get('source_photos',[('localImage',path) for path in item.get('photos',[])])
    if text!=item['text'] or source_photos!=expected_photos or queued.get('clientUserMessageId')!=item['client_id']:
        raise QueueNotClaimed('This queued message was edited. Refresh before changing it.')
    return queued


def change_queued_message(thread_id,item,message=None,*,executable=None,client_factory=RpcClient,recovery=None):
    """Update in place or delete using the native queue API, without starting work."""
    executable=executable or shutil.which('codex')
    if not executable:raise QueueNotClaimed('The queue service is unavailable.')
    with client_factory([executable,'app-server','--stdio'],timeout=8) as client:
        queued=verified_queued(client,thread_id,item)
        backup=recovery.capture_change(thread_id,item,'delete' if message is None else 'edit') if recovery else None
        try:
            # The native API is optimistic, just like the Mac's editor: it has no
            # transaction spanning our read and write. Do not call this a CAS.
            if message is None:
                result=client.call('thread/queue/delete',{'threadId':thread_id,'queuedSubmissionId':item['id']})
                if not isinstance(result,dict) or result.get('deleted') is not True:raise RuntimeUnavailable('The message may already have left the queue. Refresh before trying again.')
                state='deleted';notice='Queued message deleted.'
            else:
                inputs=[{'type':'text','text':message,'text_elements':[]}]+[part for part in queued['input'] if part['type']!='text']
                response=client.call('thread/queue/update',{'threadId':thread_id,'queuedSubmissionId':item['id'],'input':inputs})
                updated=response.get('queuedSubmission') if isinstance(response,dict) else None
                if not isinstance(updated,dict) or updated.get('id')!=item['id'] or updated.get('clientUserMessageId')!=item['client_id'] or not queue_input_matches(updated.get('input'),message,item.get('photos',[])):
                    raise RuntimeUnavailable('The queue update acknowledgement could not be verified. Refresh before trying again.')
                state='updated';notice='Queued message updated. Attached photos were kept.' if item.get('photos') else 'Queued message updated.'
            if backup:recovery.stage(backup,state)
            return {'state':state,'message':notice+(' The previous version is saved below.' if backup else '')}
        except (OSError,ValueError,RuntimeUnavailable):
            if backup:recovery.stage(backup,'change_unknown')
            raise



def claim_queued_message(thread_id,item,client_factory=RpcClient,executable=None):
    """Use only queue APIs in this short-lived process; never load or run a thread."""
    executable=executable or shutil.which('codex')
    if not executable:raise QueueNotClaimed('The queue service is unavailable.')
    delete_requested=False
    try:
        with client_factory([executable,'app-server','--stdio'],timeout=8) as client:
            verified_queued(client,thread_id,item)
            delete_requested=True
            deleted=client.call('thread/queue/delete',{'threadId':thread_id,'queuedSubmissionId':item['id']})
            if not isinstance(deleted,dict) or deleted.get('deleted') is not True:raise RuntimeUnavailable('The desktop already claimed this queued message. Check the conversation.')
            return {'message':{'context':{'prompt':item['text']}}}
    except (OSError,ValueError,RuntimeUnavailable) as error:
        if not delete_requested:raise QueueNotClaimed(str(error) if isinstance(error,RuntimeUnavailable) else 'The queue service is unavailable. The message was not sent.') from error
        raise


class DesktopSnapshotTooLarge(RuntimeUnavailable):
    """The owner is reachable, but its full activity snapshot exceeds our bound."""


class DesktopClient:
    VERSIONS = {'initialize': 0, 'thread-owner-discovery': 1,
                'thread-follower-read-model-settings': 1,
                'thread-follower-start-turn': 2,
                'thread-follower-steer-turn':1,
                'thread-follower-command-approval-decision':1,
                'thread-follower-file-approval-decision':1,
                'thread-follower-permissions-request-approval-response':1,
                'thread-follower-submit-mcp-server-elicitation-response':1,
                'thread-follower-submit-user-input':1,
                'thread-follower-update-thread-settings':2}

    def __init__(self, path, timeout=5):
        self.timeout = timeout
        self.socket = socket.socket(socket.AF_UNIX)
        self.socket.settimeout(timeout)
        self.client_id = 'initializing-client'
        try:
            self.socket.connect(str(path))
            response = self.call('initialize', {'clientType': 'desktop-companion'})
            self.client_id = response.get('result', {}).get('clientId')
            if not isinstance(self.client_id, str):
                raise RuntimeUnavailable('Desktop coordination initialization failed.')
        except Exception:
            self.socket.close()
            raise

    def _send(self, value):
        data = json.dumps(value).encode()
        self.socket.sendall(struct.pack('<I', len(data)) + data)

    def _read(self, size, deadline):
        data = bytearray()
        while len(data) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeUnavailable('The desktop did not acknowledge this request in time.')
            self.socket.settimeout(remaining)
            part = self.socket.recv(size - len(data))
            if not part:
                raise RuntimeUnavailable('The desktop connection closed. Check the chat before retrying.')
            data.extend(part)
        return data

    def call(self, method, params, target=None):
        if method not in self.VERSIONS:
            raise RuntimeUnavailable('Unsupported desktop operation.')
        identity = str(uuid.uuid4())
        request = {'type': 'request', 'requestId': identity, 'sourceClientId': self.client_id,
                   'method': method, 'version': self.VERSIONS[method], 'params': params,
                   'timeoutMs': int(self.timeout * 1000)-200}
        if target:
            request['targetClientId'] = target
        self._send(request)
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            size = struct.unpack('<I', self._read(4, deadline))[0]
            if not 0 < size <= 8 * 1024 * 1024:
                raise RuntimeUnavailable('The desktop protocol changed or returned an oversized frame.')
            value = json.loads(self._read(size, deadline))
            if not isinstance(value, dict):
                raise RuntimeUnavailable('Unexpected desktop protocol response.')
            if value.get('type') == 'client-discovery-request':
                # This observer never claims a chat or handles another app's requests.
                self._send({'type': 'client-discovery-response', 'requestId': value['requestId'],
                            'response': {'canHandle': False}})
            if value.get('type') != 'response' or value.get('requestId') != identity:
                continue  # Never expose unrelated broadcasts, tools, or conversation payloads.
            if value.get('resultType') != 'success' or value.get('method') != method:
                raise RuntimeUnavailable('The desktop owner did not accept this operation. Refresh the chat before retrying.')
            return value
        raise RuntimeUnavailable('Desktop acknowledgement timed out. Check the chat before retrying.')

    def close(self):
        self.socket.close()

    def conversation_state(self, thread_id, owner):
        """Observe one owner snapshot, then unsubscribe; never acquire ownership."""
        def follow(enabled):
            self._send({'type':'broadcast','method':'thread-stream-following-changed','version':1,
                        'sourceClientId':self.client_id,'targetClientIds':[owner],
                        'params':{'hostId':'local','conversationId':thread_id,'following':enabled}})
        follow(True)
        try:
            deadline=time.monotonic()+self.timeout
            while time.monotonic()<deadline:
                size=struct.unpack('<I',self._read(4,deadline))[0]
                if size<=0:raise RuntimeUnavailable('Unexpected desktop snapshot frame.')
                if size>32*1024*1024:raise DesktopSnapshotTooLarge('Desktop state exceeded the supported size.')
                value=json.loads(self._read(size,deadline))
                if not isinstance(value,dict):raise RuntimeUnavailable('Unexpected desktop snapshot frame.')
                params=value.get('params',{});change=params.get('change',{})
                if value.get('type')!='broadcast' or value.get('sourceClientId')!=owner or value.get('method')!='thread-stream-state-changed' or value.get('version')!=11 or params.get('hostId')!='local' or params.get('conversationId')!=thread_id or change.get('type')!='snapshot':continue
                state=change.get('conversationState',{})
                if state.get('id')!=thread_id:raise RuntimeUnavailable('Desktop snapshot identity changed.')
                return state
            raise RuntimeUnavailable('Live desktop state timed out.')
        finally:
            try:follow(False)
            except OSError:pass

    def execution_state(self,thread_id,owner,include_approvals=False,redact=str):
        state=self.conversation_state(thread_id,owner)
        runtime=state.get('threadRuntimeStatus',{}).get('type')
        if runtime not in ('active','idle','notLoaded','systemError'):raise RuntimeUnavailable('Desktop execution status is unavailable.')
        turns=state.get('turns',[])
        history=state.get('turnHistory') or {}
        if history.get('kind')=='canonical':turns=list(history.get('history',{}).get('entitiesByKey',{}).values())
        active=[t for t in turns if isinstance(t,dict) and t.get('status')=='inProgress' and t.get('turnId')]
        turn=max(active,key=lambda t:t.get('turnStartedAtMs') or 0,default={}).get('turnId')
        latest=max(enumerate(turns),key=lambda pair:(pair[1].get('turnStartedAtMs') or 0,pair[0]),default=(0,{}))[1]
        question_ids=live_question_ids(latest)
        result={'status':'working' if runtime=='active' else 'idle' if runtime=='idle' else 'unknown',
                'turn_id':turn if runtime=='active' else None,'checked_at':int(time.time()*1000),
                'approval_count':len([r for r in pending_requests(state) if r.get('params',{}).get('threadId')==thread_id]),'question_count':len(question_ids)}
        if include_approvals:result['approvals']=approval_views(state,owner,redact);result['async_question_ids']=question_ids
        return result

    def __enter__(self):return self
    def __exit__(self, *_):self.close()


class DesktopRuntime:
    REASON = 'Live desktop controls are disconnected. Queue can still save a follow-up for later.'

    def __init__(self, home, thread_reader, client_factory=DesktopClient, shared=None,queue_reader=None,receipt_reader=None,promotions=None,queue_claimer=None,approval_redactor=str,observation_clock=time.monotonic):
        self.home = Path(home).resolve()
        self.socket = self.home / 'ipc' / 'ipc.sock'
        self.thread_reader = thread_reader
        self.client_factory = client_factory
        self.shared = shared or SharedRuntime(home)
        self.queue_reader=queue_reader;self.receipt_reader=receipt_reader;self.promotions=promotions
        self.queue_claimer=queue_claimer
        self.approval_redactor=approval_redactor
        self.observation_clock=observation_clock
        self.large_snapshots=OrderedDict()
        self.observation_lock=threading.Lock()

    def present(self):
        try:
            directory, endpoint = self.socket.parent.stat(), self.socket.lstat()
            return (directory.st_uid == endpoint.st_uid == os.getuid()
                    and stat.S_ISDIR(directory.st_mode) and not directory.st_mode & 0o022
                    and stat.S_ISSOCK(endpoint.st_mode) and not endpoint.st_mode & 0o077)
        except OSError:
            return False

    def client(self,timeout=5):
        if not self.present():raise RuntimeUnavailable(self.REASON)
        return self.client_factory(self.socket,timeout=timeout)

    @staticmethod
    def owner(client, thread_id):
        response = client.call('thread-owner-discovery', {'hostId': 'local', 'conversationId': thread_id})
        owner = response.get('handledByClientId')
        if not isinstance(owner, str) or not owner:
            raise RuntimeUnavailable('Open this chat on your Mac to connect live controls. Queue remains available.')
        settings = client.call('thread-follower-read-model-settings', {'conversationId': thread_id}, target=owner).get('result', {}).get('settings')
        if not isinstance(settings, dict) or settings.get('resumeState') != 'resumed':
            raise RuntimeUnavailable('This chat is not loaded in the desktop. Open it on your Mac first.')
        return owner, settings

    def observe_execution(self,client,thread_id,owner,include_approvals=False):
        # A large history must not be re-serialized by the desktop on every
        # phone poll. This cooldown applies only to reads, never action preflight.
        key=(thread_id,owner)
        with self.observation_lock:
            if self.large_snapshots.get(key,0)>self.observation_clock():
                raise DesktopSnapshotTooLarge('Desktop state exceeded the supported size.')
            self.large_snapshots.pop(key,None)
        try:
            return client.execution_state(thread_id,owner,include_approvals=include_approvals,redact=self.approval_redactor)
        except DesktopSnapshotTooLarge:
            with self.observation_lock:
                self.large_snapshots[key]=self.observation_clock()+30
                self.large_snapshots.move_to_end(key)
                while len(self.large_snapshots)>64:self.large_snapshots.popitem(last=False)
            raise

    def capabilities(self, thread_id):
        value = {'steer': False, 'send_now': False, 'model_selection': False, 'connected':False, 'reason': self.REASON}
        try:
            with self.client(timeout=2) as client:
                owner, settings = self.owner(client, thread_id)
                # Owner discovery + resumed model settings establish the chat
                # connection independently of its potentially enormous history.
                value.update(connected=True,model_selection=True,model=settings.get('model'),effort=settings.get('reasoningEffort'),
                             model_reason='Connected. Model and reasoning changes apply to subsequent turns in this chat.',reason='Connected to the desktop chat.')
                try:
                    execution=self.observe_execution(client,thread_id,owner,include_approvals=True)
                except (OSError,ValueError,KeyError,TypeError,AttributeError,RuntimeUnavailable) as error:
                    reason=('The live activity snapshot is too large to check here.' if isinstance(error,DesktopSnapshotTooLarge)
                            else 'Live activity is temporarily unavailable.')
                    return {**value,'execution':{'status':'unknown','turn_id':None,'checked_at':int(time.time()*1000)},
                            'reason':'Connected to desktop. '+reason+' You can queue a follow-up; Send and Steer need a fresh activity check.',
                            'steer_reason':reason}
            active = execution['status']=='working' and bool(execution['turn_id'])
            return {**value, 'steer': active, 'steer_reason':'', 'send_now': execution['status']=='idle',
                    'queue_steer':bool(active and self.promotions and self.queue_reader), 'execution':execution,
                    'reason':'Connected to the desktop chat.'}
        except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeUnavailable):
            return value

    def execution_state(self,thread_id):
        try:
            with self.client(timeout=1) as client:
                owner,_=self.owner(client,thread_id)
                return self.observe_execution(client,thread_id,owner)
        except (OSError,ValueError,KeyError,TypeError,AttributeError,RuntimeUnavailable):
            return {'status':'unknown','turn_id':None,'checked_at':int(time.time()*1000)}

    def respond_approval(self,thread_id,identity,revision,choice,answers):
        """Fresh exact request, one response, then verify removal. Never queue/replay."""
        with self.client() as client:
            owner,_=self.owner(client,thread_id)
            if self.thread_reader(thread_id).get('archived'):raise ApprovalChanged('Restore this chat on your Mac before responding.')
            state=client.conversation_state(thread_id,owner)
            method,params=response_for(state,owner,identity,revision,choice,answers,self.approval_redactor)
            result=client.call(method,params,target=owner)
            if result.get('handledByClientId')!=owner or result.get('result',{}).get('ok') is not True:
                raise RuntimeUnavailable('Approval delivery is unconfirmed. Check the request on your Mac; it will not be resent automatically.')
            # IPC acknowledgement alone can be a no-op for a request already resolved.
            # Removal is confirmation of desktop handling, not proof the action succeeded.
            after=client.conversation_state(thread_id,owner)
            if any(r.get('id')==params['requestId'] and type(r.get('id')) is type(params['requestId']) for r in pending_requests(after)):
                raise RuntimeUnavailable('The desktop has not confirmed this response. Check the prompt on your Mac before trying again.')
            return {'state':'answered','message':'Response accepted by your desktop. The requested action may still be running.'}

    @staticmethod
    def restore(thread, message, identity):
        return {'id': identity, 'cwd': thread.get('details', {}).get('folder', ''),
                'context': {'prompt': message, 'turnTrigger': 'send_user_message', 'addedFiles': [],
                            'fileAttachments': [], 'imageAttachments': [], 'ideContext': None}}

    def steer(self, thread_id, message, expected_turn_id,photos=None):
        with self.client() as client:
            owner,_=self.owner(client,thread_id)
            thread=self.thread_reader(thread_id);execution=client.execution_state(thread_id,owner)
            if thread.get('archived') or execution['status']!='working' or execution['turn_id']!=expected_turn_id:
                raise QueueNotClaimed('The active turn changed. Refresh before steering; no message was submitted.')
            if self.shared.present() and self.shared.capabilities(thread_id).get('steer') is True:
                return self.shared.steer(thread_id,message,expected_turn_id,photos=photos,client_id=str(uuid.uuid4()))
            return self._steer(client,owner,thread_id,thread,message,photos or [],str(uuid.uuid4()),expected_turn_id)

    def _steer(self,client,owner,thread_id,thread,message,photos,identity,expected_turn_id,queue_id=None):
        restore=self.restore(thread,message,identity)
        if queue_id:restore['serverQueuedMessageId']=queue_id
        response=client.call('thread-follower-steer-turn',{
            'conversationId':thread_id,'input':[{'type':'text','text':message,'text_elements':[]}]+[{'type':'localImage','path':p} for p in photos],
            'clientUserMessageId':identity,'restoreMessage':restore,'attachments':[],'serviceTier':None},target=owner)
        body=response.get('result');result=body.get('result') if isinstance(body,dict) else None
        actual=result.get('turnId') if isinstance(result,dict) else None
        if response.get('handledByClientId')!=owner or not isinstance(actual,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',actual):
            raise RuntimeUnavailable('Steer delivery is unconfirmed. Check the conversation before retrying; it will not be resent automatically.')
        # Like the Mac composer, the owner may resolve a rollover to the newer
        # active turn in THIS chat. Its successful acknowledgement is delivery,
        # not a reason to retry and create a duplicate in the older turn.
        changed=actual!=expected_turn_id
        return {'state':'steered','turn_id':actual,'client_id':identity,'turn_changed':changed,
                'message':'Accepted by your Mac after the active turn advanced. It was not added to the queue.' if changed else 'Accepted by the active desktop turn. It was not added to the queue.'}

    def send_now(self, thread_id, message,photos=None):
        with self.client() as client:
            owner, _ = self.owner(client, thread_id)
            thread = self.thread_reader(thread_id)
            if thread.get('archived') or client.execution_state(thread_id,owner)['status']!='idle':
                raise RuntimeUnavailable('This chat is now working. Refresh and choose Queue or Steer.')
            identity = str(uuid.uuid4())
            response = client.call('thread-follower-start-turn', {
                'conversationId': thread_id, 'turnStart': {
                    'request': {'threadId': thread_id, 'input': [{'type': 'text', 'text': message, 'text_elements': []}]+[{'type':'localImage','path':path} for path in photos or []],
                                'clientUserMessageId': identity, 'turnTrigger': 'send_user_message'},
                    'context': {'inheritThreadSettings': True,
                                'restoreMessage': self.restore(thread, message, identity)}}}, target=owner)
            turn = response.get('result', {}).get('result', {}).get('turn', {})
            if not isinstance(turn.get('id'), str) or turn.get('status') != 'inProgress':
                raise RuntimeUnavailable('Desktop start acknowledgement could not be verified. Check the chat before retrying.')
            return {'state': 'sent', 'turn_id': turn['id'], 'client_id': identity, 'message': 'Sent to the desktop. A new turn has started.'}

    def set_model(self, thread_id, model, effort=None):
        with self.client() as client:
            owner,_=self.owner(client,thread_id)
            if self.thread_reader(thread_id).get('archived'):raise RuntimeUnavailable('Restore this chat before changing its model.')
            settings={'model':model}
            if effort:settings['effort']=effort
            # Omitting activeTurnId selects the native next-turn settings path.
            # The phone cannot provide permissions or other thread settings.
            result=client.call('thread-follower-update-thread-settings',{'conversationId':thread_id,'threadSettings':settings},target=owner)
            if result.get('result',{}).get('applied') is not True:raise RuntimeUnavailable('The desktop did not apply this model change. Refresh before trying again.')
            _,actual=self.owner(client,thread_id)
            if actual.get('model')!=model or effort and actual.get('reasoningEffort')!=effort:
                raise RuntimeUnavailable('The model change could not be verified. Refresh before trying again.')
            return {'state':'updated','model':model,'effort':actual.get('reasoningEffort'),'message':'Model saved for subsequent turns in this chat.'}

    def queue_recovery(self,thread_id,redact):
        return self.promotions.pending(thread_id,self.receipt_reader,redact) if self.promotions and self.receipt_reader else []

    def promote_queued(self,thread_id,item,expected_turn_id):
        if not self.promotions or not self.queue_reader:raise RuntimeUnavailable('Queue promotion is unavailable.')
        try:client=self.client()
        except (OSError,ValueError,RuntimeUnavailable) as error:raise QueueNotClaimed('Desktop connection is unavailable. The message is still queued; reconnect and retry.') from error
        with client:
            try:
                owner,_=self.owner(client,thread_id)
                execution=client.execution_state(thread_id,owner)
                if execution['status']!='working' or execution['turn_id']!=expected_turn_id:
                    raise RuntimeUnavailable('The active turn changed. The message is still queued.')
            except (OSError,ValueError,RuntimeUnavailable) as error:raise QueueNotClaimed(str(error)) from error
            if self.receipt_reader and self.receipt_reader(thread_id,item['client_id']):raise RuntimeUnavailable('This message is already recorded in the conversation. It was not sent again.')
            try:
                current=self.queue_reader(thread_id,item['id'])
                if any(current.get(key)!=item.get(key) for key in ('revision','client_id','text','source_photos')):
                    raise QueueNotClaimed('This queued message changed. Refresh before steering; it is still queued.')
            except KeyError:raise QueueNotClaimed('This message already left the queue. Check the conversation.')
            self.promotions.prepare(thread_id,item)
            try:
                removed=self.queue_claimer(thread_id,item) if self.queue_claimer else claim_queued_message(thread_id,item)
                if not isinstance(removed,dict):raise RuntimeUnavailable('The desktop could not claim this queued message. Check the conversation before retrying.')
                self.promotions.stage(item['id'],'claimed')
                try:self.queue_reader(thread_id,item['id'])
                except KeyError:pass
                else:raise RuntimeUnavailable('The message is still in the desktop queue. It was not sent again.')
                prompt=removed.get('message',{}).get('context',{}).get('prompt')
                source=removed.get('serverSubmission',{})
                if not isinstance(prompt,str) or prompt!=item['text'] or source.get('clientUserMessageId',item['client_id'])!=item['client_id'] or source.get('input') is not None and not queue_input_matches(source['input'],item['text'],item.get('photos',[])):
                    self.promotions.stage(item['id'],'unknown',prompt if isinstance(prompt,str) else None)
                    raise RuntimeUnavailable('The removed message changed or could not be verified. Its text is saved for review below; no Steer was sent.')
                # Claim first; never send a copy while the original remains eligible for execution.
                thread=self.thread_reader(thread_id);execution=client.execution_state(thread_id,owner)
                if execution['status']!='working' or execution['turn_id']!=expected_turn_id:
                    raise RuntimeUnavailable('The turn ended during promotion. The message is saved for review below; it was not resent.')
                result=self._steer(client,owner,thread_id,thread,item['text'],item.get('photos',[]),item['client_id'],expected_turn_id,item['id'])
                self.promotions.stage(item['id'],'sent')
                result['message']='Moved this queued message into the active turn. It is no longer waiting in the queue.' if not result['turn_changed'] else 'Moved this queued message into the newer active turn in this chat. It is no longer waiting in the queue.'
                return result
            except QueueNotClaimed:
                self.promotions.stage(item['id'],'not_claimed')
                raise
            except (OSError,ValueError,RuntimeUnavailable):
                self.promotions.stage(item['id'],'unknown')
                raise RuntimeUnavailable('Queue promotion is unconfirmed. The original text is saved for review; it will never be resent automatically.')
