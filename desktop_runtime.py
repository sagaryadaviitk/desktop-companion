"""Version-specific adapter for the running desktop's local coordination protocol.

No daemon creation, chat resume, UI scripting, credentials, or database writes.
The phone never chooses an IPC method, socket, owner, or permission configuration.
"""

import json
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
import uuid

from runtime import RuntimeUnavailable, SharedRuntime, RpcClient
from approvals import approval_views, pending_requests, response_for, ApprovalChanged
from questions import live_question_ids


def queue_input_matches(inputs,message,photos):
    """The queue normalizes localImage to inline image; compare bytes, never fetch."""
    if not isinstance(inputs,list) or any(not isinstance(part,dict) or part.get('type') not in ('text','localImage','image') for part in inputs):return False
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
        result=client.call('thread/queue/add',{'threadId':thread_id,'clientUserMessageId':identity,'input':inputs}).get('queuedSubmission',{})
    queued_id=result.get('id');accepted=result.get('input',[])
    if not isinstance(queued_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',queued_id) or result.get('clientUserMessageId')!=identity:
        raise RuntimeUnavailable('Photo queue acknowledgement could not be verified. Check the queue before resending.')
    if not queue_input_matches(accepted,message,photos):
        raise RuntimeUnavailable('Photo queue content acknowledgement changed. Check the queue before resending.')
    return {'state':'queued','queue_item_id':queued_id,'message':'Message and photos queued for this desktop chat. They will wait until its active turn finishes.'}


class QueueNotClaimed(RuntimeUnavailable):
    """No removal or steering request was made; an explicit retry is safe."""


QUEUE_GUARD_REASON='This desktop queue API cannot protect a concurrent edit. Manage queued messages on your Mac; the original has not been changed.'


def change_queued_message(thread_id,item,message=None,*,executable=None,client_factory=RpcClient):
    # Current queue/update and queue/delete accept only an ID, with no content
    # precondition. A preceding list or Companion lock cannot guard desktop edits.
    raise QueueNotClaimed(QUEUE_GUARD_REASON)


def claim_queued_message(thread_id,item,client_factory=RpcClient,executable=None):
    raise QueueNotClaimed(QUEUE_GUARD_REASON)


class DesktopClient:
    VERSIONS = {'initialize': 0, 'thread-owner-discovery': 1,
                'thread-follower-read-model-settings': 1,
                'thread-follower-start-turn': 2,
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
                if not 0<size<=32*1024*1024:raise RuntimeUnavailable('Desktop state exceeded the supported size.')
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

    def __init__(self, home, thread_reader, client_factory=DesktopClient, shared=None,queue_reader=None,receipt_reader=None,promotions=None,queue_claimer=claim_queued_message,approval_redactor=str):
        self.home = Path(home).resolve()
        self.socket = self.home / 'ipc' / 'ipc.sock'
        self.thread_reader = thread_reader
        self.client_factory = client_factory
        self.shared = shared or SharedRuntime(home)
        self.queue_reader=queue_reader;self.receipt_reader=receipt_reader;self.promotions=promotions
        self.queue_claimer=queue_claimer
        self.approval_redactor=approval_redactor

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

    def capabilities(self, thread_id):
        value = {'steer': False, 'send_now': False, 'model_selection': False, 'reason': self.REASON}
        try:
            with self.client(timeout=2) as client:
                owner, settings = self.owner(client, thread_id)
                execution=client.execution_state(thread_id,owner,include_approvals=True,redact=self.approval_redactor)
            active = execution['status']=='working' and bool(execution['turn_id'])
            atomic_steer=active and self.shared.capabilities(thread_id).get('steer') is True
            return {**value, 'steer': atomic_steer, 'steer_reason':'Steer requires a shared desktop channel that atomically checks the turn. Use Queue or the Mac app on this version.' if active and not atomic_steer else '', 'send_now': execution['status']=='idle', 'queue_steer':False, 'model_selection':True,'connected': True,'execution':execution,
                    'reason': 'Connected to the desktop chat.' if not active or atomic_steer else 'Queue is available. This desktop version needs the Mac app for safe turn-bound Steer.', 'model': settings.get('model'),
                    'effort': settings.get('reasoningEffort'),
                    'model_reason': 'Connected. Model and reasoning changes apply to subsequent turns in this chat.'}
        except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeUnavailable):
            return value

    def execution_state(self,thread_id):
        try:
            with self.client(timeout=1) as client:
                owner,_=self.owner(client,thread_id)
                return client.execution_state(thread_id,owner)
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
        # The follower IPC handler retries a turn mismatch into the new active
        # turn. Only turn/steer on an existing shared channel has the guard.
        if not self.shared.present():raise RuntimeUnavailable('Safe turn-bound steering is unavailable on this desktop version. Use Queue or Steer on your Mac.')
        with self.client() as client:
            owner,_=self.owner(client,thread_id)
            execution=client.execution_state(thread_id,owner)
            if self.thread_reader(thread_id).get('archived') or execution['status']!='working' or execution['turn_id']!=expected_turn_id:
                raise RuntimeUnavailable('The active turn changed. Refresh before steering.')
        return self.shared.steer(thread_id,message,expected_turn_id,photos=photos,client_id=str(uuid.uuid4()))

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
        raise QueueNotClaimed(QUEUE_GUARD_REASON)
