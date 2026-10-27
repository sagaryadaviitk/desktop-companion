"""Opt-in, bounded page controls over a verified local desktop CDP endpoint.

The phone never receives a debugger URL, chooses a protocol method, evaluates
JavaScript, reads cookies, or accesses files. Screenshots and typed text are not
persisted. Existing Companion pairing and CSRF protection wrap every route.
"""
import argparse
import base64
from collections import OrderedDict
from contextlib import contextmanager
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import plistlib
import re
import secrets
import socket
import stat
import struct
import subprocess
import threading
import tempfile
import time
from urllib.parse import urlsplit

from configuration import load

IDENTITY = re.compile(r'[A-Za-z0-9_-]{1,150}')
MAX_FRAME = 8 * 1024 * 1024


class BrowserError(Exception):
    def __init__(self, message, status=409, uncertain=False):
        super().__init__(message)
        self.status, self.uncertain = status, uncertain


class WebSocket:
    """One bounded CDP connection. No proxy, redirect, or caller-supplied host."""
    def __init__(self, port, path, timeout=3):
        self.timeout = timeout
        self.deadline = time.monotonic()+4
        self.sock = socket.create_connection(('127.0.0.1', port), timeout=timeout)
        self.buffer = bytearray()
        self.sequence = 0
        try:
            key = base64.b64encode(os.urandom(16)).decode()
            self.sock.sendall((f'GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n'
                               f'Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n'
                               'Sec-WebSocket-Version: 13\r\n\r\n').encode())
            deadline = min(time.monotonic()+timeout,self.deadline)
            header = bytearray()
            while not header.endswith(b'\r\n\r\n'):
                if len(header) >= 8192: raise BrowserError('The desktop browser handshake is invalid.', 503)
                header.extend(self.read(1, deadline))
            lines = header.decode('ascii').split('\r\n')
            expected = base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
            # Header values are case-sensitive (notably Sec-WebSocket-Accept).
            fields = {line.split(':',1)[0].lower(): line.split(':',1)[1].strip() for line in lines[1:] if ':' in line}
            if (lines[0].split()[1:2] != ['101'] or fields.get('sec-websocket-accept') != expected
                    or fields.get('upgrade','').lower() != 'websocket'
                    or 'upgrade' not in {v.strip().lower() for v in fields.get('connection','').split(',')}):
                raise BrowserError('The desktop browser handshake could not be verified.', 503)
        except BaseException:
            self.sock.close()
            raise

    def read(self, size, deadline):
        while len(self.buffer) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise TimeoutError('Browser deadline')
            self.sock.settimeout(remaining)
            part = self.sock.recv(min(65536, size-len(self.buffer)))
            if not part: raise OSError('Browser connection closed')
            self.buffer.extend(part)
        result = bytes(self.buffer[:size]); del self.buffer[:size]
        return result

    def send(self, payload, opcode=1):
        mask = os.urandom(4); size = len(payload)
        length = bytes([0x80 | size]) if size < 126 else b'\xfe'+struct.pack('!H',size) if size < 65536 else b'\xff'+struct.pack('!Q',size)
        self.sock.sendall(bytes([0x80 | opcode])+length+mask+bytes(b ^ mask[i % 4] for i,b in enumerate(payload)))

    def message(self, deadline):
        parts = bytearray(); started = False
        while True:
            first, second = self.read(2, deadline)
            opcode = first & 15; size = second & 127
            if first & 0x70 or second & 128: raise BrowserError('Unsupported desktop browser frame.', 503)
            if size == 126: size = struct.unpack('!H',self.read(2,deadline))[0]
            elif size == 127: size = struct.unpack('!Q',self.read(8,deadline))[0]
            if size > MAX_FRAME or len(parts)+size > MAX_FRAME: raise BrowserError('Desktop browser response is too large.', 503)
            if opcode >= 8 and (not first & 128 or size > 125): raise BrowserError('Invalid desktop browser control frame.', 503)
            payload = self.read(size, deadline)
            if opcode == 8: raise OSError('Desktop browser closed')
            if opcode == 9: self.send(payload,10); continue
            if opcode == 10: continue
            if opcode == 1 and not started: started = True
            elif opcode != 0 or not started: raise BrowserError('Unexpected desktop browser frame.',503)
            parts.extend(payload)
            if first & 128: return json.loads(parts.decode('utf-8'))

    def call(self, method, params=None):
        remaining=self.deadline-time.monotonic()
        if remaining<=0:raise TimeoutError("Browser operation deadline")
        self.sock.settimeout(min(self.timeout,remaining))
        self.sequence += 1; identity = self.sequence
        self.send(json.dumps({'id':identity,'method':method,'params':params or {}}).encode())
        deadline = min(time.monotonic()+self.timeout,self.deadline)
        while True:
            value = self.message(deadline)
            if not isinstance(value,dict): raise BrowserError('Invalid desktop browser response.',503)
            if value.get('id') != identity: continue
            if 'error' in value: raise BrowserError('The page could not complete this browser operation.')
            if not isinstance(value.get('result'),dict): raise BrowserError('Invalid desktop browser result.',503)
            return value['result']

    def __enter__(self): return self
    def __exit__(self,*_): self.sock.close()


class BrowserRemote:
    def __init__(self, root, *, verifier=None, fetcher=None, client_factory=WebSocket, clock=time.monotonic):
        self.root = Path(root); self.path = self.root/'.state'/'browser-settings.json'
        self.verifier = verifier or self.verify_endpoint
        self.fetcher = fetcher or self.fetch_targets
        self.client_factory, self.clock = client_factory, clock
        self.frames = OrderedDict(); self.selections = OrderedDict(); self.gate = threading.Lock()

    def settings(self):
        if not self.path.exists(): return {'enabled':False}
        info = self.path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 4096):
            raise BrowserError('Browser sharing settings need repair on your Mac.',503)
        value = json.loads(self.path.read_text())
        if (not isinstance(value,dict) or set(value) != {'enabled','port'} or type(value['enabled']) is not bool
                or type(value['port']) is not int or not 1024 <= value['port'] <= 65535 or value['port'] in (8765,8766)):
            raise BrowserError('Browser sharing settings need repair on your Mac.',503)
        return value

    def verify_endpoint(self, port):
        """Require an existing loopback listener owned by this desktop app/user."""
        result = subprocess.run(['/usr/sbin/lsof','-nP',f'-iTCP:{port}','-sTCP:LISTEN','-Fpun'],capture_output=True,text=True,timeout=2)
        records = []; record = None
        for line in result.stdout.splitlines():
            if line.startswith('p'):
                record = {'pid':line[1:],'addresses':[]}; records.append(record)
            elif record is not None and line.startswith('u'): record['uid'] = line[1:]
            elif record is not None and line.startswith('n'): record['addresses'].append(line[1:])
        if (result.returncode or len(records) != 1 or records[0].get('uid') != str(os.getuid())
                or not records[0]['addresses'] or any(a not in (f'127.0.0.1:{port}',f'[::1]:{port}') for a in records[0]['addresses'])):
            raise BrowserError('The local desktop browser connection is unavailable. Finish browser setup on your Mac.',503)
        app = load(self.root).app_path()
        executable = plistlib.loads((app/'Contents/Info.plist').read_bytes())['CFBundleExecutable']
        process = subprocess.run(['/bin/ps','-p',records[0]['pid'],'-o','uid=,comm='],capture_output=True,text=True,timeout=2)
        values = process.stdout.strip().split(None,1)
        if (process.returncode or len(values) != 2 or values[0] != str(os.getuid())
                or Path(values[1]).resolve() != (app/'Contents/MacOS'/executable).resolve()):
            raise BrowserError('This connection does not belong to the configured desktop app.',503)

    def fetch_targets(self, port):
        connection = http.client.HTTPConnection('127.0.0.1',port,timeout=2)
        try:
            connection.request('GET','/json/list'); response = connection.getresponse()
            data = response.read(1024*1024+1)
            if response.status != 200 or len(data)>1024*1024: raise BrowserError('Desktop browser pages are unavailable.',503)
            value = json.loads(data)
            if not isinstance(value,list) or len(value)>256: raise BrowserError('Invalid desktop browser page list.',503)
            return value
        finally: connection.close()

    def allowed_url(self, url):
        if not isinstance(url,str) or len(url)>8192 or any(ord(c)<32 for c in url): return False
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username is not None or parsed.password is not None: return False
            reserved={8765,8766,self.settings().get('port')} - {None}
            if parsed.port in reserved: return False
            # Companion's own controls and pairing must not become remote targets.
            from access import RemoteSettings
            origin = RemoteSettings(self.root/'.state/remote.json').origin()
            return not origin or parsed.hostname.lower() != urlsplit(origin).hostname.lower()
        except ValueError: return False

    def targets(self):
        config = self.settings()
        if not config['enabled']: return None, []
        port = config['port']; self.verifier(port)
        targets = []
        for item in self.fetcher(port):
            if not isinstance(item,dict) or item.get('type') != 'page' or not IDENTITY.fullmatch(str(item.get('id',''))) or not self.allowed_url(item.get('url')): continue
            if not isinstance(item.get('webSocketDebuggerUrl'),str): continue
            try:
                address = urlsplit(item['webSocketDebuggerUrl'])
                address_port = address.port
            except (ValueError,TypeError): continue
            if (address.scheme != 'ws' or address.hostname != '127.0.0.1' or address_port != port or address.username is not None
                    or address.password is not None or address.query or address.fragment
                    or address.path != '/devtools/page/'+item['id']): continue
            targets.append({'id':item['id'],'title':str(item.get('title') or 'Untitled page')[:300], 'url':item['url'],'path':address.path})
        return port,targets

    @staticmethod
    def context(owner, chat_id):
        if not isinstance(owner,str) or not owner or not isinstance(chat_id,str) or chat_id and not IDENTITY.fullmatch(chat_id):
            raise BrowserError('Choose an available chat before opening its browser.',400)
        return owner,chat_id

    def _catalog(self, port, targets, owner, chat_id):
        selection = self.selections.get(self.context(owner,chat_id)) if owner is not None else None
        if selection and not any(t['id']==selection['id'] for t in targets):
            # A closed target never silently rebinds, even if its ID reappears.
            selection['closed'] = True
        return {'available':port is not None,'tabs':[{k:t[k] for k in ('id','title','url')} for t in targets],
                'selection':{'id':selection['id'],'source':'manual','available':not selection['closed'],'revision':selection['revision']} if selection else None,
                'reason':'' if port else 'Finish browser sharing setup on your Mac, then refresh.','checked_at':int(time.time()*1000)}

    def catalog(self, owner=None, chat_id=''):
        with self.exclusive():
            return self._catalog(*self.targets(),owner,chat_id)

    def select(self, identity, owner, chat_id=''):
        context = self.context(owner,chat_id)
        with self.exclusive():
            port,targets = self.targets()
            if not isinstance(identity,str) or not IDENTITY.fullmatch(identity) or not any(t['id']==identity for t in targets):
                raise BrowserError('This page is no longer available. Refresh the page list.',404)
            self.selections[context] = {'id':identity,'revision':secrets.token_urlsafe(24),'closed':False}
            self.selections.move_to_end(context)
            while len(self.selections)>128:self.selections.popitem(last=False)
            return self._catalog(port,targets,owner,chat_id)

    def selected(self, identity, owner, chat_id, revision):
        selection = self.selections.get(self.context(owner,chat_id))
        if (not selection or selection['closed'] or selection['id']!=identity
                or not isinstance(revision,str) or selection['revision']!=revision):
            raise BrowserError('Choose a page for this chat, then refresh before controlling it.')
        return selection

    def target(self, identity, selection=None):
        if not isinstance(identity,str) or not IDENTITY.fullmatch(identity): raise BrowserError('Choose an available page.',400)
        port, targets = self.targets()
        target = next((t for t in targets if t['id']==identity),None)
        if target is None:
            if selection is not None:selection['closed']=True
            raise BrowserError('This page is no longer available. Refresh the page list.',404)
        return port,target

    @contextmanager
    def exclusive(self):
        if not self.gate.acquire(timeout=.1): raise BrowserError('The browser is busy. Refresh in a moment.',429)
        try: yield
        finally: self.gate.release()

    @staticmethod
    def witness(client):
        frame = client.call('Page.getFrameTree').get('frameTree',{}).get('frame',{})
        view = client.call('Page.getLayoutMetrics').get('cssVisualViewport',{})
        keys = ('clientWidth','clientHeight','pageX','pageY')
        values = [view.get(k) for k in keys]
        if (not all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in values)
                or not all(1<=v<=16384 for v in values[:2]) or not all(isinstance(frame.get(k),str) and frame[k] for k in ('id','loaderId','url'))):
            raise BrowserError('The current page view could not be verified. Refresh it.')
        return {'document':tuple(frame[k] for k in ('id','loaderId','url')),'viewport':tuple(values)}

    @staticmethod
    def capture(client):
        data = client.call('Page.captureScreenshot',{'format':'jpeg','quality':65,'fromSurface':True,'captureBeyondViewport':False}).get('data')
        try: image = base64.b64decode(data,validate=True)
        except (ValueError,TypeError): raise BrowserError('The page image could not be read.',503)
        if not image.startswith(b'\xff\xd8') or len(image)>5*1024*1024: raise BrowserError('The page image exceeds the supported size.',503)
        return data

    def snapshot(self, identity, owner, *, chat_id='', selection_revision=None):
        with self.exclusive():
            selection=self.selected(identity,owner,chat_id,selection_revision)
            port,target = self.target(identity,selection)
            with self.client_factory(port,target['path']) as client:
                before = self.witness(client)
                if before['document'][2] != target['url']: raise BrowserError('The page navigated. Refresh it before continuing.')
                data = self.capture(client)
                after = self.witness(client)
            if before != after: raise BrowserError('The page moved while refreshing. Refresh again.')
            token = secrets.token_urlsafe(24)
            self.frames[token] = {'owner':owner,'chat_id':chat_id,'selection_revision':selection_revision,'id':identity,
                                  'view':after,'created':self.clock(),'used':False}
            while len(self.frames)>128: self.frames.popitem(last=False)
            return {'frame':token,'id':identity,'title':target['title'],'url':target['url'],'image':data,
                    'chat_id':chat_id,'selection_revision':selection_revision,
                    'image_mime':'image/jpeg','width':after['viewport'][0],'height':after['viewport'][1],'checked_at':int(time.time()*1000)}

    @staticmethod
    def number(value, low, high):
        if type(value) not in (float,int) or not math.isfinite(value) or not low<=value<=high: raise BrowserError('Invalid page control coordinates.',400)
        return value

    def action(self, payload, owner):
        if not isinstance(payload,dict) or set(payload)-{'id','frame','action','x','y','dx','dy','text','key','url','chat_id','selection_revision'}: raise BrowserError('Unsupported page control.',400)
        action = payload.get('action')
        if action not in ('click','scroll','text','key','navigate','back','forward','reload'): raise BrowserError('Unsupported page control.',400)
        if action=='click' and not {'x','y'}<=set(payload): raise BrowserError('Tap a location in the page.',400)
        if action in ('click','scroll'):
            x,y = (self.number(payload.get(k,.5),0,1) for k in ('x','y'))
        if action=='scroll': dx,dy = (self.number(payload.get(k,0),-1500,1500) for k in ('dx','dy'))
        if action=='text' and (not isinstance(payload.get('text'),str) or not 1<=len(payload['text'])<=4000): raise BrowserError('Enter up to 4,000 characters.',400)
        keys = {'Enter':('Enter',13),'Tab':('Tab',9),'Backspace':('Backspace',8),'Delete':('Delete',46),'Escape':('Escape',27),
                'ArrowLeft':('ArrowLeft',37),'ArrowUp':('ArrowUp',38),'ArrowRight':('ArrowRight',39),'ArrowDown':('ArrowDown',40),'SelectAll':('a',65)}
        if action=='key' and (not isinstance(payload.get('key'),str) or payload['key'] not in keys): raise BrowserError('Unsupported page key.',400)
        if action=='navigate' and not self.allowed_url(payload.get('url')): raise BrowserError('Use a regular HTTP or HTTPS page address.',400)
        with self.exclusive():
            selection=self.selected(payload.get('id'),owner,payload.get('chat_id'),payload.get('selection_revision'))
            receipt = self.frames.get(payload.get('frame')) if isinstance(payload.get('frame'),str) else None
            if (not receipt or receipt['owner']!=owner or receipt['id']!=payload.get('id')
                    or receipt['chat_id']!=payload.get('chat_id') or receipt['selection_revision']!=payload.get('selection_revision')
                    or self.clock()-receipt['created']>8):
                raise BrowserError('Refresh this page before controlling it.')
            if receipt['used']: raise BrowserError('This control was already attempted. Refresh before another action.',uncertain=True)
            port,target = self.target(receipt['id'],selection)
            with self.client_factory(port,target['path']) as client:
                current = self.witness(client)
                if current != receipt['view'] or current['document'][2] != target['url']:
                    raise BrowserError('The page changed on your Mac. Refresh before controlling it.')
                # Consume before the first input, including its acknowledgement.
                # No browser input is ever replayed after a timeout/disconnect.
                receipt['used'] = True
                try:
                    if action=='click':
                        point={'x':min(current['viewport'][0]-1,x*current['viewport'][0]),'y':min(current['viewport'][1]-1,y*current['viewport'][1]),'button':'left','clickCount':1}
                        client.call('Input.dispatchMouseEvent',{'type':'mousePressed',**point})
                        client.call('Input.dispatchMouseEvent',{'type':'mouseReleased',**point})
                    elif action=='scroll': client.call('Input.dispatchMouseEvent',{'type':'mouseWheel','x':x*current['viewport'][0],'y':y*current['viewport'][1],'deltaX':dx,'deltaY':dy})
                    elif action=='text': client.call('Input.insertText',{'text':payload['text']})
                    elif action=='key':
                        key,code=keys[payload['key']]; params={'key':key,'windowsVirtualKeyCode':code,'modifiers':4 if payload['key']=='SelectAll' else 0}
                        if key=='Enter': params['text']='\r'
                        client.call('Input.dispatchKeyEvent',{'type':'keyDown',**params})
                        client.call('Input.dispatchKeyEvent',{'type':'keyUp',**{k:v for k,v in params.items() if k!='text'}})
                    elif action=='navigate': client.call('Page.navigate',{'url':payload['url']})
                    elif action=='reload': client.call('Page.reload',{'ignoreCache':False})
                    else:
                        history=client.call('Page.getNavigationHistory'); index=history.get('currentIndex',-1)+(-1 if action=='back' else 1)
                        entries=history.get('entries',[])
                        if not isinstance(entries,list) or not 0<=index<len(entries) or not self.allowed_url(entries[index].get('url')): raise BrowserError('No available page in that direction.')
                        client.call('Page.navigateToHistoryEntry',{'entryId':entries[index]['id']})
                except (OSError,ValueError,TypeError,KeyError,BrowserError) as error:
                    raise BrowserError('The page action may have run. Refresh and check the page before trying again.',uncertain=True) from error
            return {'state':'applied','message':'Page action acknowledged. Refreshing the view.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['enable','disable','status']); parser.add_argument('--port',type=int,default=9223)
    parser.add_argument('--browser-control-approved',action='store_true')
    args=parser.parse_args(); remote=BrowserRemote(Path(__file__).resolve().parent)
    if args.command=='status': print(json.dumps(remote.catalog())); return
    if args.command=='enable' and not args.browser_control_approved: parser.error('Enabling browser control requires explicit --browser-control-approved after reviewing BROWSER.md.')
    if not 1024<=args.port<=65535 or args.port in (8765,8766): parser.error('Choose a dedicated loopback browser port.')
    if args.command=='enable': remote.verifier(args.port)
    remote.path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if remote.path.is_symlink(): raise BrowserError('Linked browser settings are not supported.')
    descriptor,name=tempfile.mkstemp(prefix='browser-settings-',dir=remote.path.parent)
    temporary=Path(name)
    try:
        with os.fdopen(descriptor,'w') as output: json.dump({'enabled':args.command=='enable','port':args.port},output)
        temporary.chmod(0o600); temporary.replace(remote.path)
    finally: temporary.unlink(missing_ok=True)
    print('Browser sharing enabled for paired browsers.' if args.command=='enable' else 'Browser sharing disabled.')


if __name__=='__main__':
    try: main()
    except (BrowserError,OSError,ValueError,subprocess.SubprocessError) as error: raise SystemExit(str(error))
