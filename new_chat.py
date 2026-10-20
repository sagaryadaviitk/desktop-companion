"""Create only a new, empty chat, inheriting the desktop's configured defaults.

Creation targets come from saved local projects, never a caller-supplied path.
No existing chat is resumed and no inference turn is started by this adapter.
"""

import hashlib
import re
import shutil
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

from runtime import RpcClient, RuntimeUnavailable


CHAT_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
PERSONAL_WORKSPACE=Path.home()/'Library/Application Support/Desktop Companion/Chats'


def prompt_title(message,has_photos=False):
    if not isinstance(message,str):raise ValueError('Write a question to start this chat.')
    title=re.sub(r'\s+',' ',re.sub(r'^[#>*\-\s]+','',message)).strip()
    if not title:return 'Photo question' if has_photos else 'New chat'
    if len(title)>72:title=title[:73].rsplit(' ',1)[0] or title[:72]
    return title[:72].rstrip(' .,;:!?') or 'New chat'


class NewChat:
    def __init__(self, home, workspace, client_factory=RpcClient, executable=None, opener=None):
        self.home = Path(home).resolve()
        self.workspace = Path(workspace).resolve()
        self.client_factory = client_factory
        self.executable = executable or shutil.which('codex')
        self.opener = opener or self.open_desktop

    def _targets(self):
        with closing(sqlite3.connect((self.home/'state_5.sqlite').as_uri()+'?mode=ro', uri=True)) as db:
            rows = db.execute('SELECT p.id,p.name,r.path FROM projects p JOIN project_roots r ON p.id=r.project_id ORDER BY p.position,r.position').fetchall()
        result = [{'id':'personal', 'name':'Personal chat', 'project_key':'personal', 'folder':'Desktop Companion',
                   'path':self.workspace, 'project_id':None, 'available':True}]
        for project_id, name, source in rows:
            path = Path(source).expanduser().resolve()
            key = hashlib.sha256((project_id+'\0'+str(path)).encode()).hexdigest()[:32]
            result.append({'id':key, 'name':name or 'Untitled project', 'project_key':'project:'+project_id,
                           'folder':path.name, 'path':path, 'project_id':project_id, 'available':path.is_dir()})
        return result

    def targets(self):
        return [{key:value for key,value in target.items() if key not in ('path','project_id')} for target in self._targets()]

    @staticmethod
    def open_desktop(thread_id):
        # A supported desktop deep link. No UI scripting, prompt injection, or shell.
        subprocess.run(['/usr/bin/open', 'codex://threads/'+thread_id], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=True)

    def metadata(self,thread_id):
        with closing(sqlite3.connect((self.home/'state_5.sqlite').as_uri()+'?mode=ro',uri=True)) as db:
            row=db.execute('SELECT project_id,name,cwd FROM threads WHERE id=?',(thread_id,)).fetchone()
        if row is None:raise RuntimeUnavailable('The created chat has not appeared in desktop history. Check Recent before retrying.')
        return {'project_id':row[0],'name':row[1],'cwd':row[2]}

    def verify_target(self,thread_id,target_id):
        target=next((target for target in self._targets() if target['id']==target_id),None)
        if target is None:return False
        metadata=self.metadata(thread_id)
        return metadata['project_id']==target['project_id'] and Path(metadata['cwd']).resolve()==target['path']

    def rename(self,thread_id,title):
        if not isinstance(title,str) or not title.strip() or len(title.strip())>120 or any(ord(c)<32 for c in title):raise ValueError('Use a chat name of 1–120 characters on one line.')
        title=title.strip()
        if not self.executable:raise RuntimeUnavailable('The local naming service is unavailable.')
        self.metadata(thread_id)
        with self.client_factory([self.executable,'app-server','--stdio'],timeout=10) as client:
            client.call('thread/name/set',{'threadId':thread_id,'name':title})
        if self.metadata(thread_id)['name']!=title:raise RuntimeUnavailable('The rename could not be verified. Refresh the chat before trying again.')
        return {'state':'updated','title':title,'message':'Chat renamed.'}

    def create(self, target_id, title=''):
        target = next((target for target in self._targets() if target['id']==target_id), None)
        if target is None or not target['available']:
            raise ValueError('Choose an available project or Personal chat.')
        if not isinstance(title,str) or len(title)>120 or any(ord(c)<32 for c in title):
            raise ValueError('Use a title of up to 120 characters.')
        if not self.executable:
            raise RuntimeUnavailable('The local chat creation service is unavailable.')
        if target_id=='personal':
            self.workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
        params = {'cwd':str(target['path']), 'projectId':target['project_id'], 'historyMode':'paginated', 'ephemeral':False}
        named = not title.strip()
        with self.client_factory([self.executable,'app-server','--stdio'], timeout=10) as client:
            thread = client.call('thread/start', params).get('thread',{})
            thread_id = thread.get('id')
            if not isinstance(thread_id,str) or not CHAT_ID.fullmatch(thread_id):
                raise RuntimeUnavailable('Chat creation could not be confirmed. Check Recent before creating another.')
            # Verify the durable native assignment rather than inferring it from cwd.
            verified=False
            try:
                metadata=self.metadata(thread_id)
                if metadata['project_id']!=target['project_id']:
                    client.call('thread/metadata/update',{'threadId':thread_id,'projectId':target['project_id'] or ''})
                    metadata=self.metadata(thread_id)
                verified=metadata['project_id']==target['project_id'] and Path(metadata['cwd']).resolve()==target['path']
            except (OSError,ValueError,TypeError,sqlite3.Error,RuntimeUnavailable):pass
            if title.strip():
                try:
                    client.call('thread/name/set', {'threadId':thread_id,'name':title.strip()})
                    named = True
                except (OSError,RuntimeUnavailable):pass  # Creation already succeeded; never create a duplicate.
        opened = False
        try:
            self.opener(thread_id)
            opened = True
        except (OSError,subprocess.SubprocessError):pass
        return {'state':'created', 'thread_id':thread_id, 'desktop_opened':opened, 'named':named,'project_verified':verified,'project_id':target['project_id'],'target_name':target['name'],
                'message':'Chat created. Write your first message below.' if opened else
                'Chat created. Desktop loading is pending; open this chat on your Mac to connect live controls.'}
