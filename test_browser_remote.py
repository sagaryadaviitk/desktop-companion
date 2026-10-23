import base64
import hashlib
import json
import os
from pathlib import Path
import plistlib
import socket
import struct
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from browser_remote import BrowserRemote, BrowserError, WebSocket


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.calls=[];self.now=[0];self.fail=None;self.scroll=0;self.loader='document-1'
        self.pages=[{'id':'page-1','type':'page','title':'Demo <script>','url':'http://localhost:8000/demo','webSocketDebuggerUrl':'ws://127.0.0.1:9223/devtools/page/page-1'}]
        outer=self
        class Client:
            def __init__(self,*args):pass
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def call(self,method,params=None):
                outer.calls.append((method,params))
                if method==outer.fail:raise OSError('synthetic lost acknowledgement')
                if method=='Page.getFrameTree':return {'frameTree':{'frame':{'id':'frame-1','loaderId':outer.loader,'url':outer.pages[0]['url']}}}
                if method=='Page.getLayoutMetrics':return {'cssVisualViewport':{'clientWidth':1000,'clientHeight':600,'pageX':0,'pageY':outer.scroll}}
                if method=='Page.captureScreenshot':return {'data':base64.b64encode(b'\xff\xd8fixture').decode()}
                if method=='Page.getNavigationHistory':return {'currentIndex':1,'entries':[{'id':1,'url':'https://example.test/'},{'id':2,'url':outer.pages[0]['url']}]}
                return {}
        self.browser=BrowserRemote(self.root,verifier=lambda port:None,fetcher=lambda port:self.pages,client_factory=Client,clock=lambda:self.now[0])
        self.browser.path.parent.mkdir();self.browser.path.write_text(json.dumps({'enabled':True,'port':9223}));self.browser.path.chmod(0o600)

    def frame(self,owner='phone'):return self.browser.snapshot('page-1',owner)

    def action(self,frame,**values):return self.browser.action({'id':'page-1','frame':frame['frame'],'action':'click','x':.25,'y':.5,**values},'phone')

    def test_clean_install_is_disabled_and_private_settings_are_required(self):
        self.browser.path.unlink();self.assertFalse(self.browser.catalog()['available'])
        self.browser.path.write_text('{"enabled":true,"port":9223}');self.browser.path.chmod(0o644)
        with self.assertRaises(BrowserError):self.browser.catalog()
        self.browser.path.chmod(0o600);self.browser.path.write_text('{"enabled":true,"port":8765}')
        with self.assertRaises(BrowserError):self.browser.catalog()

    def test_catalog_filters_internal_companion_file_and_foreign_debugger_targets(self):
        good=dict(self.pages[0]);cases=[]
        for url in ['file:///private/test.html','javascript:alert(1)','devtools://page','chrome://settings','http://127.0.0.1:8765/','http://localhost:9223/json/list','https://user:password@example.test/','https://example.test:8766/']:
            cases.append({**good,'url':url})
        for endpoint in ['ws://192.168.1.2:9223/devtools/page/page-1','ws://127.0.0.1:9224/devtools/page/page-1','ws://127.0.0.1:9223/devtools/browser/page-1','ws://127.0.0.1:bad/path','ws://user@127.0.0.1:9223/devtools/page/page-1','ws://127.0.0.1:9223/devtools/page/page-1?token=secret']:
            cases.append({**good,'webSocketDebuggerUrl':endpoint})
        self.pages.extend(cases)
        value=self.browser.catalog();self.assertEqual(len(value['tabs']),1)
        self.assertNotIn('webSocketDebuggerUrl',json.dumps(value));self.assertNotIn('path',value['tabs'][0])

    def test_snapshot_receipt_binds_session_document_scroll_and_viewport(self):
        frame=self.frame();self.assertEqual(frame['width'],1000);self.assertEqual(frame['image_mime'],'image/jpeg')
        with self.assertRaises(BrowserError):self.browser.action({'id':'page-1','frame':frame['frame'],'action':'reload'},'different-phone')
        self.loader='different-document'
        with self.assertRaisesRegex(BrowserError,'changed'):self.action(frame)
        self.assertFalse(any(m.startswith('Input.') for m,_ in self.calls))
        frame=self.frame();self.scroll=50
        with self.assertRaisesRegex(BrowserError,'changed'):self.action(frame)

    def test_stale_frames_and_closed_pages_cannot_be_controlled(self):
        frame=self.frame();self.now[0]=9
        with self.assertRaisesRegex(BrowserError,'Refresh'):self.action(frame)
        self.now[0]=0;self.pages.clear()
        with self.assertRaisesRegex(BrowserError,'no longer'):self.action(frame)

    def test_click_uses_css_coordinates_and_a_frame_is_never_replayed(self):
        frame=self.frame();self.assertEqual(self.action(frame)['state'],'applied')
        inputs=[(m,p) for m,p in self.calls if m.startswith('Input.')]
        self.assertEqual([p['type'] for _,p in inputs],['mousePressed','mouseReleased'])
        self.assertEqual((inputs[0][1]['x'],inputs[0][1]['y']),(250,300))
        with self.assertRaises(BrowserError) as caught:self.action(frame)
        self.assertTrue(caught.exception.uncertain)
        self.assertEqual(len([m for m,_ in self.calls if m.startswith('Input.')]),2)

    def test_lost_input_acknowledgement_is_consumed_before_dispatch_and_never_replayed(self):
        frame=self.frame();self.fail='Input.insertText'
        with self.assertRaises(BrowserError) as caught:self.action(frame,action='text',text='PRIVATE TEST TEXT')
        self.assertTrue(caught.exception.uncertain)
        self.fail=None
        with self.assertRaises(BrowserError):self.action(frame,action='text',text='PRIVATE TEST TEXT')
        self.assertEqual(sum(m=='Input.insertText' for m,_ in self.calls),1)
        self.assertNotIn('PRIVATE TEST TEXT',json.dumps(self.browser.frames))

    def test_only_allowlisted_inputs_are_dispatched(self):
        for payload in [dict(action='Runtime.evaluate',text='alert(1)'),dict(action='navigate',url='file:///private/x.html'),dict(action='key',key='Meta+L'),dict(action='key',key=[]),dict(action='click',x=float('nan')),dict(action='scroll',dy=999999),dict(action='text',text='x'*4001),dict(action='click',method='Browser.grantPermissions')]:
            frame=self.frame();before=len(self.calls)
            with self.assertRaises(BrowserError):self.action(frame,**payload)
            self.assertEqual(len(self.calls),before)
        for action,extra in [('scroll',{'dx':0,'dy':300}),('text',{'text':'Hello'}),('key',{'key':'Enter'}),('navigate',{'url':'https://example.test/next'}),('back',{}),('reload',{})]:
            self.assertEqual(self.action(self.frame(),action=action,**extra)['state'],'applied')

    def test_frame_storage_is_bounded_and_screenshots_are_not_retained(self):
        for _ in range(140):self.frame()
        self.assertEqual(len(self.browser.frames),128)
        self.assertNotIn('image',json.dumps(self.browser.frames))

    def test_busy_browser_has_a_retryable_read_error_without_dispatch(self):
        self.browser.gate.acquire()
        try:
            with self.assertRaises(BrowserError) as caught:self.frame()
            self.assertEqual(caught.exception.status,429);self.assertEqual(self.calls,[])
        finally:self.browser.gate.release()

    def test_wrong_listener_user_program_and_wildcard_are_rejected(self):
        app=self.root/'Demo App.app';(app/'Contents/MacOS').mkdir(parents=True)
        (app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable':'Demo'}))
        output=f'p42\nu{os.getuid()}\nn127.0.0.1:9223\n'
        config=SimpleNamespace(app_path=lambda:app)
        def complete(stdout):return subprocess.CompletedProcess([],0,stdout,'')
        with patch('browser_remote.load',return_value=config):
            with patch('browser_remote.subprocess.run',side_effect=[complete(output),complete(f'{os.getuid()} {app}/Contents/MacOS/Demo')]):self.browser.verify_endpoint(9223)
            for invalid in [output.replace('127.0.0.1','*'),output.replace('u'+str(os.getuid()),'u999999')]:
                with patch('browser_remote.subprocess.run',return_value=complete(invalid)):
                    with self.assertRaises(BrowserError):self.browser.verify_endpoint(9223)
            with patch('browser_remote.subprocess.run',side_effect=[complete(output),complete(f'{os.getuid()} /different/app')]):
                with self.assertRaises(BrowserError):self.browser.verify_endpoint(9223)


class WebSocketTests(unittest.TestCase):
    def server(self,reply):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(3)
        errors=[]
        def run():
            try:
                connection,_=listener.accept()
                with connection:
                    connection.settimeout(3);header=b''
                    while not header.endswith(b'\r\n\r\n'):header+=connection.recv(1)
                    key=next(line.split(b': ',1)[1] for line in header.split(b'\r\n') if line.startswith(b'Sec-WebSocket-Key:'))
                    accept=base64.b64encode(hashlib.sha1(key+b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest())
                    connection.sendall(b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: '+accept+b'\r\n\r\n')
                    first,second=self.exact(connection,2);self.assertEqual(first,129);self.assertTrue(second&128)
                    length=second&127
                    if length==126:length=struct.unpack('!H',self.exact(connection,2))[0]
                    mask=self.exact(connection,4);payload=self.exact(connection,length)
                    request=json.loads(bytes(b^mask[i%4] for i,b in enumerate(payload)))
                    reply(connection,request)
            except (OSError,AssertionError,ValueError) as error:errors.append(error)
            finally:listener.close()
        worker=threading.Thread(target=run,daemon=True);worker.start()
        self.addCleanup(lambda:worker.join(4));return listener.getsockname()[1],errors

    @staticmethod
    def exact(connection,size):
        data=b''
        while len(data)<size:
            value=connection.recv(size-len(data))
            if not value:raise OSError('Closed test socket')
            data+=value
        return data

    def test_fragmented_cdp_response_and_ping_are_accepted_without_resending(self):
        calls=[]
        def reply(connection,request):
            calls.append(request);data=json.dumps({'id':request['id'],'result':{'ok':True}}).encode();part=len(data)//2
            connection.sendall(b'\x89\x01x'+bytes([1,part])+data[:part]+bytes([128,len(data)-part])+data[part:])
            self.exact(connection,7) # masked pong
        port,errors=self.server(reply)
        with WebSocket(port,'/devtools/page/test') as client:self.assertTrue(client.call('Page.getFrameTree')['ok'])
        self.assertEqual(len(calls),1);self.assertEqual(errors,[])

    def test_announced_oversized_response_is_rejected_before_body_allocation(self):
        port,_=self.server(lambda c,r:c.sendall(b'\x81\x7f'+struct.pack('!Q',20*1024*1024)))
        with WebSocket(port,'/devtools/page/test') as client:
            with self.assertRaisesRegex(BrowserError,'too large'):client.call('Page.getFrameTree')


if __name__=='__main__':unittest.main()
