from contextlib import closing
import base64
import hashlib
import http.client
import json
from pathlib import Path
import sqlite3
import struct
import subprocess
import tempfile
import threading
import unittest
import zlib
import time

from desktop_companion.companion import CompanionServer,CommandJournal
from desktop_companion.photos import PhotoStore,PhotoError,image_size
from test_companion import CHAT,make_history


def png(width=32,height=24):
    def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\x00'+b'\x48\x90\x70'*width)*height))+chunk(b'IEND',b'')


def photo_data():return 'data:image/png;base64,'+base64.b64encode(png()).decode()


class PhotoStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=PhotoStore(Path(self.temp.name)/'photos')
    def tearDown(self):self.temp.cleanup()

    def test_abandoned_week_old_drafts_do_not_permanently_exhaust_owner_quota(self):
        photo=self.store.upload('chat','phone',photo_data());data=self.store.read(photo['id'],'phone')
        old=time.time()-self.store.DRAFT_SECONDS-1
        with closing(sqlite3.connect(self.store.db)) as db, db:
            db.execute('DELETE FROM photos')
            for i in range(21):
                identity=f'{i:032x}';(self.store.root/(identity+'.jpg')).write_bytes(data)
                db.execute('INSERT INTO photos (id,thread_id,owner,name,created,submitted) VALUES (?,?,?,?,?,?)',(identity,'chat','phone','Old photo',old,1 if i==20 else 0))
        new=self.store.upload('chat','phone',photo_data())
        self.assertEqual(len(self.store.storage('phone')['drafts']),1)
        self.assertTrue((self.store.root/(f'{20:032x}'+'.jpg')).exists())
        self.assertTrue(self.store.read(new['id'],'phone'))

    def test_storage_recovers_recent_draft_references_but_never_deletes_submitted_or_other_owner(self):
        draft=self.store.upload('draft:temporary','phone',photo_data(),'Draft')
        protected=self.store.upload('chat','phone',photo_data(),'Unconfirmed')
        self.store.submit([protected['id']])
        other=self.store.upload('chat','other-phone',photo_data(),'Other browser')
        storage=self.store.storage('phone');self.assertEqual([p['id'] for p in storage['drafts']],[draft['id']]);self.assertGreater(storage['protected_bytes'],0)
        self.store.remove_draft(other['id'],'phone');self.assertTrue(self.store.read(other['id'],'other-phone'))
        self.store.remove_draft(protected['id'],'phone');self.assertTrue(self.store.read(protected['id'],'phone'))
        self.store.remove_draft(draft['id'],'phone');self.assertEqual(self.store.storage('phone')['drafts'],[])

    def test_submission_rechecks_draft_existence_after_concurrent_remove(self):
        photo=self.store.upload('chat','phone',photo_data())
        self.store.resolve([photo['id']],'chat','phone')
        self.store.remove_draft(photo['id'],'phone')
        with self.assertRaises(PhotoError):self.store.submit([photo['id']])

    def test_expiry_and_capacity_never_reclaim_submitted_or_uncertain_photos(self):
        photo=self.store.upload('chat','phone',photo_data());self.store.submit([photo['id']])
        with closing(sqlite3.connect(self.store.db)) as db, db:db.execute('UPDATE photos SET created=0')
        self.store.expire_drafts(now=time.time()+self.store.DRAFT_SECONDS)
        self.assertTrue(self.store.read(photo['id'],'phone'))
        self.store.STORAGE_BYTES=1
        with self.assertRaisesRegex(PhotoError,'protected'):self.store.upload('chat','phone',photo_data())
        self.assertTrue(self.store.read(photo['id'],'phone'))

    def test_real_decoder_creates_private_canonical_photo_and_binds_draft_to_owner_and_chat(self):
        photo=self.store.upload('chat-1','phone-1',photo_data(),'../../Camera.png')
        self.assertEqual(photo['name'],'Camera.png');path=Path(self.store.resolve([photo['id']],'chat-1','phone-1')[0])
        self.assertEqual(path.stat().st_mode&0o777,0o600);self.assertEqual(image_size(path.read_bytes()),(32,24))
        for thread,owner in [('chat-2','phone-1'),('chat-1','phone-2')]:
            with self.assertRaises(PhotoError):self.store.resolve([photo['id']],thread,owner)
        with self.assertRaises(KeyError):self.store.read(photo['id'],'phone-2')
        self.assertIsNone(self.store.for_path(path))
        self.store.submit([photo['id']]);self.assertEqual(self.store.for_path(path),photo)
        self.assertTrue(self.store.read(photo['id'],'another-paired-phone').startswith(b'\xff\xd8'))
        self.store.remove(photo['id'],'chat-1','phone-1');self.assertTrue(path.exists())

    def test_invalid_images_and_large_dimensions_never_reach_decoder(self):
        calls=[]
        self.store.runner=lambda *args,**kwargs:calls.append(args)
        giant=png(4097,1)
        for value in ['data:image/svg+xml;base64,PHN2Zz4=', 'data:image/png;base64,bm90YW5pbWFnZQ==','data:image/png;base64,'+base64.b64encode(giant).decode()]:
            with self.assertRaises(PhotoError):self.store.upload('chat','phone',value)
        self.assertEqual(calls,[])

    def test_remove_only_removes_the_owners_unsent_draft_and_cannot_read_arbitrary_files(self):
        photo=self.store.upload('chat','phone',photo_data());identity=photo['id']
        self.store.remove(identity,'other-chat','phone');self.assertTrue(self.store.read(identity,'phone'))
        self.store.remove(identity,'chat','other-phone');self.assertTrue(self.store.read(identity,'phone'))
        with self.assertRaises(PhotoError):self.store.resolve(['../../private'],'chat','phone')
        with self.assertRaises(PhotoError):self.store.resolve([identity]*4,'chat','phone')
        self.assertIsNone(self.store.for_path('/private/'+identity+'.jpg'))
        self.store.remove(identity,'chat','phone')
        with self.assertRaises(KeyError):self.store.read(identity,'phone')

    def test_only_submitted_same_chat_photos_match_native_queue_data_urls(self):
        photo=self.store.upload('chat','phone',photo_data());path=self.store.resolve([photo['id']],'chat','phone')[0]
        part={'type':'image','image_url':'data:image/jpeg;base64,'+base64.b64encode(Path(path).read_bytes()).decode()}
        self.assertIsNone(self.store.for_input(part,'chat'));self.store.submit([photo['id']])
        self.assertEqual(self.store.for_input(part,'chat'),photo)
        self.assertIsNone(self.store.for_input(part,'other-chat'))
        self.assertIsNone(self.store.for_input({'type':'image','image_url':'https://private.example/photo'},'chat'))

    def test_decoder_timeout_is_reported_without_retaining_a_partial_upload(self):
        def timeout(*args,**kwargs):raise subprocess.TimeoutExpired('sips',10)
        self.store.runner=timeout
        with self.assertRaisesRegex(PhotoError,'timed out'):self.store.upload('chat','phone',photo_data())
        self.assertEqual(list(self.store.root.glob('*.input')),[]);self.assertEqual(list(self.store.root.glob('*.jpg')),[])


class PhotoHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.history=make_history(self.root);self.photos=PhotoStore(self.root/'photos');self.history.photos=self.photos;self.calls=[]
        outer=self
        class Bridge:
            def availability(self):return {'available':True}
            def describe(self,_):return self.availability()
            def send(self,thread,message,photos=None):outer.calls.append((thread,message,photos));return {'state':'queued','queue_item_id':'fixture'}
        self.server=CompanionServer(('127.0.0.1',0),self.history,Bridge(),CommandJournal(self.root/'commands.sqlite'),photos=self.photos)
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True);self.worker.start()
    def tearDown(self):self.server.shutdown();self.server.server_close();self.worker.join();self.temp.cleanup()
    def request(self,path,payload=None,csrf=True):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        conn.request('POST' if payload is not None else 'GET',path,json.dumps(payload) if payload is not None else None,{'X-Companion-CSRF':self.server.csrf} if csrf else {})
        r=conn.getresponse();result=r.status,r.read();conn.close();return result

    def test_upload_and_command_use_owned_photo_ids_once_and_history_has_no_local_paths(self):
        payload={'thread_id':CHAT,'data_url':photo_data(),'name':'Test.png'}
        self.assertEqual(self.request('/api/photos',payload,False)[0],403)
        status,raw=self.request('/api/photos',payload);self.assertEqual(status,200);photo=json.loads(raw)
        command={'thread_id':CHAT,'command_id':'photo-request-12345','message':'','photos':[photo['id']]}
        self.assertEqual(self.request('/api/command',command)[0],200);self.assertEqual(self.request('/api/command',command)[0],200)
        self.assertEqual(len(self.calls),1);thread,text,paths=self.calls[0];self.assertEqual(text,'Photo attached.')
        with closing(sqlite3.connect(self.root/'thread_history_1.sqlite')) as db, db:
            db.execute('INSERT INTO thread_items VALUES (?,?,?,?,?,?,?,?)',(CHAT,'photo-message','userMessage',json.dumps({'content':[{'type':'localImage','path':paths[0]}]}),2000,None,None,5))
        message=self.history.read_thread(CHAT)['messages'][-1];self.assertEqual(message['photos'],[photo]);self.assertNotIn(str(self.root),json.dumps(message))
        self.assertEqual(self.request(photo['url'])[0],200)
        self.assertNotIn(b'Photo attached.',(self.root/'commands.sqlite').read_bytes())

    def test_unknown_photo_and_archived_chat_are_rejected_before_delivery(self):
        payload={'thread_id':CHAT,'command_id':'invalid-photo-12345','message':'Look','photos':['a'*32]}
        self.assertEqual(self.request('/api/command',payload)[0],400);self.assertEqual(self.calls,[])
        with closing(sqlite3.connect(self.root/'state_5.sqlite')) as db, db:db.execute('UPDATE threads SET archived=1')
        self.assertEqual(self.request('/api/photos',{'thread_id':CHAT,'data_url':photo_data()})[0],409)


if __name__=='__main__':unittest.main()
