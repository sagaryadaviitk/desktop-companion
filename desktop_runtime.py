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
    return {'state':'queued','queue_item_id':queued_id,'message':'Message and photos queued for this desktop chat. Use Steer now to deliver them to its active turn.'}


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


def change_queued_message(thread_id,item,message=None,*,executable=None,client_factory=RpcClient):
    """Update in place or delete using the native queue API, without starting work."""
    executable=executable or shutil.which('codex')
    if not executable:raise QueueNotClaimed('The queue service is unavailable.')
    with client_factory([executable,'app-server','--stdio'],timeout=8) as client:
        queued=verified_queued(client,thread_id,item)
        if message is None:
            result=client.call('thread/queue/delete',{'threadId':thread_id,'queuedSubmissionId':item['id']})
            if result.get('deleted') is not True:raise RuntimeUnavailable('The message may already have left the queue. Refresh before trying again.')
            return {'state':'deleted','message':'Queued message deleted.'}
        inputs=[{'type':'text','text':message,'text_elements':[]}]+[part for part in queued['input'] if part['type']!='text']
        updated=client.call('thread/queue/update',{'threadId':thread_id,'queuedSubmissionId':item['id'],'input':inputs}).get('queuedSubmission',{})
        if updated.get('id')!=item['id'] or updated.get('clientUserMessageId')!=item['client_id'] or not queue_input_matches(updated.get('input'),message,item.get('photos',[])):
            raise RuntimeUnavailable('The queue update acknowledgement could not be verified. Refresh before trying again.')
        return {'state':'updated','message':'Queued message updated. Attached photos were kept.' if item.get('photos') else 'Queued message updated.'}


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
            if deleted.get('deleted') is not True:raise RuntimeUnavailable('The desktop already claimed this queued message. Check the conversation.')
            return {'message':{'context':{'prompt':item['text']}}}
    except (OSError,ValueError,RuntimeUnavailable) as error:
        if not delete_requested:raise QueueNotClaimed(str(error) if isinstance(error,RuntimeUnavailable) else 'The queue service is unavailable. The message was not sent.') from error
        raise


class DesktopClient:
    VERSIONS = {'initialize': 0, 'thread-owner-discovery': 1,
                'thread-follower-read-model-settings': 1,
                'thread-follower-steer-turn': 1, 'thread-follower-start-turn': 2,
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

    def execution_state(self, thread_id, owner):
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
                params=value.get('params',{});change=params.get('change',{})
                if value.get('type')!='broadcast' or value.get('sourceClientId')!=owner or value.get('method')!='thread-stream-state-changed' or value.get('version')!=11 or params.get('hostId')!='local' or params.get('conversationId')!=thread_id or change.get('type')!='snapshot':continue
                state=change.get('conversationState',{})
                if state.get('id')!=thread_id:raise RuntimeUnavailable('Desktop snapshot identity changed.')
                runtime=state.get('threadRuntimeStatus',{}).get('type')
                if runtime not in ('active','idle','notLoaded','systemError'):raise RuntimeUnavailable('Desktop execution status is unavailable.')
                turns=state.get('turns',[])
                history=state.get('turnHistory') or {}
                if history.get('kind')=='canonical':turns=list(history.get('history',{}).get('entitiesByKey',{}).values())
                active=[t for t in turns if isinstance(t,dict) and t.get('status')=='inProgress' and t.get('turnId')]
                turn=max(active,key=lambda t:t.get('turnStartedAtMs') or 0,default={}).get('turnId')
                return {'status':'working' if runtime=='active' else 'idle' if runtime=='idle' else 'unknown',
                        'turn_id':turn if runtime=='active' else None,'checked_at':int(time.time()*1000)}
            raise RuntimeUnavailable('Live desktop state timed out.')
        finally:
            try:follow(False)
            except OSError:pass

    def __enter__(self):return self
    def __exit__(self, *_):self.close()


class DesktopRuntime:
    REASON = 'Live desktop controls are disconnected. Queue can still save a follow-up for later.'

    def __init__(self, home, thread_reader, client_factory=DesktopClient, shared=None,queue_reader=None,receipt_reader=None,promotions=None,queue_claimer=claim_queued_message):
        self.home = Path(home).resolve()
        self.socket = self.home / 'ipc' / 'ipc.sock'
        self.thread_reader = thread_reader
        self.client_factory = client_factory
        self.shared = shared or SharedRuntime(home)
        self.queue_reader=queue_reader;self.receipt_reader=receipt_reader;self.promotions=promotions
        self.queue_claimer=queue_claimer

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
                execution=client.execution_state(thread_id,owner)
            active = execution['status']=='working' and bool(execution['turn_id'])
            return {**value, 'steer': active, 'send_now': execution['status']=='idle', 'queue_steer':active and self.promotions is not None, 'model_selection':True,'connected': True,'execution':execution,
                    'reason': 'Connected to the desktop chat.', 'model': settings.get('model'),
                    'effort': settings.get('reasoningEffort'),
                    'model_reason': 'Connected. Model and reasoning changes apply to subsequent turns in this chat.'}
        except (OSError, ValueError, KeyError, RuntimeUnavailable):
            return value

    def execution_state(self,thread_id):
        try:
            with self.client(timeout=1) as client:
                owner,_=self.owner(client,thread_id)
                return client.execution_state(thread_id,owner)
        except (OSError,ValueError,KeyError,RuntimeUnavailable):
            return {'status':'unknown','turn_id':None,'checked_at':int(time.time()*1000)}

    @staticmethod
    def restore(thread, message, identity):
        return {'id': identity, 'cwd': thread.get('details', {}).get('folder', ''),
                'context': {'prompt': message, 'turnTrigger': 'send_user_message', 'addedFiles': [],
                            'fileAttachments': [], 'imageAttachments': [], 'ideContext': None}}

    def steer(self, thread_id, message, expected_turn_id,photos=None):
        with self.client() as client:
            owner, _ = self.owner(client, thread_id)
            thread = self.thread_reader(thread_id)
            execution=client.execution_state(thread_id,owner)
            if thread.get('archived') or execution['status']!='working' or execution['turn_id']!=expected_turn_id:
                raise RuntimeUnavailable('The active turn changed. Refresh before steering.')
            identity = str(uuid.uuid4())
            response = client.call('thread-follower-steer-turn', {
                'conversationId': thread_id, 'input': [{'type': 'text', 'text': message, 'text_elements': []}]+[{'type':'localImage','path':path} for path in photos or []],
                'clientUserMessageId': identity, 'restoreMessage': self.restore(thread, message, identity),
                'attachments': [], 'serviceTier': None,
            }, target=owner)
            result = response.get('result', {}).get('result', {})
            if result.get('turnId') != expected_turn_id:
                raise RuntimeUnavailable('The desktop turn changed during submission. Check the conversation before retrying.')
            return {'state': 'steered', 'turn_id': result['turnId'], 'client_id': identity,
                    'message': 'Accepted by the active desktop turn. This was not added to the follow-up queue.'}

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
            self.promotions.prepare(thread_id,item)
            try:
                removed=self.queue_claimer(thread_id,item)
                if not isinstance(removed,dict):raise RuntimeUnavailable('The desktop could not claim this queued message. Check the conversation before retrying.')
                self.promotions.stage(item['id'],'claimed')
                try:self.queue_reader(thread_id,item['id'])
                except KeyError:pass
                else:raise RuntimeUnavailable('The message is still in the desktop queue. It was not sent again.')
                prompt=removed.get('message',{}).get('context',{}).get('prompt')
                if isinstance(prompt,str) and prompt!=item['text']:
                    self.promotions.stage(item['id'],'unknown',prompt)
                    raise RuntimeUnavailable('The queued message changed while being claimed. Its text is saved for review below.')
                # Claim first; never send a copy while the original remains eligible for execution.
                thread=self.thread_reader(thread_id);execution=client.execution_state(thread_id,owner)
                if execution['status']!='working' or execution['turn_id']!=expected_turn_id:
                    raise RuntimeUnavailable('The turn ended during promotion. The message is saved for review below; it was not resent.')
                restore=self.restore(thread,item['text'],item['client_id']);restore['serverQueuedMessageId']=item['id']
                response=client.call('thread-follower-steer-turn',{'conversationId':thread_id,'input':[{'type':'text','text':item['text'],'text_elements':[]}]+[{'type':'localImage','path':path} for path in item.get('photos',[])],
                    'clientUserMessageId':item['client_id'],'restoreMessage':restore,'attachments':[],'serviceTier':None},target=owner)
                result=response.get('result',{}).get('result',{})
                if result.get('turnId')!=expected_turn_id:raise RuntimeUnavailable('Queue promotion acknowledgement is unconfirmed. Check the saved message below before retrying.')
                self.promotions.stage(item['id'],'sent')
                return {'state':'steered','turn_id':result['turnId'],'client_id':item['client_id'],'message':'Moved this queued message into the active turn. It is no longer waiting in the queue.'}
            except QueueNotClaimed:
                self.promotions.stage(item['id'],'not_claimed')
                raise
            except (OSError,ValueError,RuntimeUnavailable):
                self.promotions.stage(item['id'],'unknown')
                raise RuntimeUnavailable('Queue promotion is unconfirmed. The original text is saved for review; it will never be resent automatically.')
