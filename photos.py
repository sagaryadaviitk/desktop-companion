"""Authenticated photo drafts, kept outside the web root and Codex databases."""

import base64
import binascii
import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
import struct
import subprocess
import threading
import time
import uuid


class PhotoError(ValueError):
    pass


def image_size(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n') and len(data)>=24 and data[12:16]==b'IHDR':
        return struct.unpack('>II',data[16:24])
    if data.startswith(b'\xff\xd8'):
        offset=2
        while offset+4<=len(data):
            if data[offset]!=255:break
            marker=data[offset+1];offset+=2
            if marker==255:offset-=1;continue
            if marker in (0xd8,0xd9) or 0xd0<=marker<=0xd7:continue
            length=int.from_bytes(data[offset:offset+2],'big')
            if length<2 or offset+length>len(data):break
            if marker in (0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf) and length>=7:
                height,width=struct.unpack('>HH',data[offset+3:offset+7]);return width,height
            offset+=length
    raise PhotoError('Choose a JPEG or PNG photo that your browser can open.')


class PhotoStore:
    MAX_BYTES=4*1024*1024
    MAX_PHOTOS=3

    def __init__(self,root,runner=subprocess.run):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True,mode=0o700);self.root.chmod(0o700)
        self.db=self.root/'photos.sqlite';self.lock=threading.RLock();self.runner=runner
        with closing(sqlite3.connect(self.db)) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS photos (id TEXT PRIMARY KEY,thread_id TEXT,owner TEXT,name TEXT,created REAL,submitted INTEGER DEFAULT 0)')
            if 'sha256' not in [c[1] for c in db.execute('PRAGMA table_info(photos)')]:db.execute('ALTER TABLE photos ADD COLUMN sha256 TEXT')
            db.execute('CREATE INDEX IF NOT EXISTS photo_digest ON photos(thread_id,sha256)')
        self.db.chmod(0o600)

    def upload(self,thread,owner,data_url,name='Photo'):
        if not isinstance(data_url,str) or len(data_url)>self.MAX_BYTES*4//3+100:
            raise PhotoError('This photo is too large. Choose a smaller photo.')
        match=re.fullmatch(r'data:image/(?:jpeg|png);base64,([A-Za-z0-9+/=]+)',data_url)
        if not match:raise PhotoError('Choose a JPEG or PNG photo.')
        try:data=base64.b64decode(match[1],validate=True)
        except (ValueError,binascii.Error) as error:raise PhotoError('This photo could not be read. Choose it again.') from error
        width,height=image_size(data)
        if not 0<width<=4096 or not 0<height<=4096 or width*height>8_000_000:
            raise PhotoError('This photo is too large. Resize it before trying again.')
        with self.lock:
            with closing(sqlite3.connect(self.db)) as db:
                pending=db.execute('SELECT COUNT(*) FROM photos WHERE owner=? AND submitted=0',(owner,)).fetchone()[0]
            if pending>=20:raise PhotoError('Too many unsent photos. Remove photos from your other drafts first.')
            if sum(p.stat().st_size for p in self.root.glob('*.jpg'))>128*1024*1024:
                raise PhotoError('Photo storage is full. Use the desktop app for this attachment.')
            identity=uuid.uuid4().hex;source=self.root/(identity+'.input');target=self.root/(identity+'.jpg')
            try:
                with open(source,'xb') as handle:os.chmod(source,0o600);handle.write(data)
                try:result=self.runner(['/usr/bin/sips','-s','format','jpeg','-s','formatOptions','85',str(source),'--out',str(target)],capture_output=True,timeout=10)
                except subprocess.TimeoutExpired as error:raise PhotoError('Photo processing timed out. Try a smaller photo.') from error
                except OSError as error:raise PhotoError('The Mac photo decoder is unavailable. Try again after reconnecting.') from error
                if result.returncode!=0 or not target.is_file():raise PhotoError('This photo could not be decoded. Choose another photo.')
                target.chmod(0o600);canonical=target.read_bytes();image_size(canonical)
                if len(canonical)>self.MAX_BYTES:raise PhotoError('This photo is too large after processing. Choose a smaller photo.')
                name=Path(str(name or 'Photo')).name
                name=re.sub(r'[\x00-\x1f\x7f]','',name)[:80] or 'Photo'
                with closing(sqlite3.connect(self.db)) as db,db:
                    db.execute('INSERT INTO photos (id,thread_id,owner,name,created,submitted,sha256) VALUES (?,?,?,?,?,0,?)',(identity,thread,owner,name,time.time(),hashlib.sha256(canonical).hexdigest()))
            except Exception:
                target.unlink(missing_ok=True);raise
            finally:source.unlink(missing_ok=True)
            return self.public(identity,name)

    @staticmethod
    def public(identity,name):return {'id':identity,'name':name,'url':'/api/photos/'+identity}

    def resolve(self,identifiers,thread,owner):
        if not isinstance(identifiers,list) or len(identifiers)>self.MAX_PHOTOS or len(set(identifiers))!=len(identifiers):
            raise PhotoError('Attach up to three photos per message.')
        paths=[]
        with self.lock,closing(sqlite3.connect(self.db)) as db:
            for identity in identifiers:
                if not isinstance(identity,str) or not re.fullmatch(r'[a-f0-9]{32}',identity):raise PhotoError('Invalid photo.')
                row=db.execute('SELECT id FROM photos WHERE id=? AND thread_id=? AND (owner=? OR submitted=1)',(identity,thread,owner)).fetchone()
                path=self.root/(identity+'.jpg')
                if not row or path.is_symlink() or not path.is_file():raise PhotoError('A photo is no longer available. Remove it and choose it again.')
                paths.append(str(path))
            if sum(Path(path).stat().st_size for path in paths)>self.MAX_BYTES:raise PhotoError('These photos are too large together. Remove one photo and try again.')
        return paths

    def submit(self,identifiers):
        with self.lock,closing(sqlite3.connect(self.db)) as db,db:
            db.executemany('UPDATE photos SET submitted=1 WHERE id=?',[(i,) for i in identifiers])

    def bind_draft(self,identifiers,draft,thread,owner):
        """Move only this browser's unsent draft photos; retries keep the same binding."""
        with self.lock,closing(sqlite3.connect(self.db)) as db,db:
            for identity in identifiers:
                row=db.execute('SELECT thread_id,owner,submitted FROM photos WHERE id=?',(identity,)).fetchone()
                if not row or row[1]!=owner or row[0] not in (draft,thread) or row[0]==draft and row[2]:raise PhotoError('A photo does not belong to this new-chat draft.')
            for identity in identifiers:db.execute('UPDATE photos SET thread_id=? WHERE id=? AND owner=?',(thread,identity,owner))
        return self.resolve(identifiers,thread,owner)

    def read(self,identity,owner):
        if not re.fullmatch(r'[a-f0-9]{32}',identity):raise KeyError(identity)
        with self.lock,closing(sqlite3.connect(self.db)) as db:
            row=db.execute('SELECT owner,submitted FROM photos WHERE id=?',(identity,)).fetchone()
            path=self.root/(identity+'.jpg')
            if not row or not row[1] and row[0]!=owner or path.is_symlink():raise KeyError(identity)
            return path.read_bytes()

    def remove(self,identity,thread,owner):
        if not isinstance(identity,str) or not re.fullmatch(r'[a-f0-9]{32}',identity):raise PhotoError('Invalid photo.')
        with self.lock,closing(sqlite3.connect(self.db)) as db,db:
            row=db.execute('SELECT id FROM photos WHERE id=? AND thread_id=? AND owner=? AND submitted=0',(identity,thread,owner)).fetchone()
            if row:
                (self.root/(row[0]+'.jpg')).unlink(missing_ok=True)
                db.execute('DELETE FROM photos WHERE id=?',(identity,))

    def for_path(self,path):
        path=Path(path)
        if path.parent!=self.root or path.suffix!='.jpg' or not re.fullmatch(r'[a-f0-9]{32}',path.stem):return None
        with self.lock,closing(sqlite3.connect(self.db)) as db:
            row=db.execute('SELECT id,name FROM photos WHERE id=? AND submitted=1',(path.stem,)).fetchone()
            return self.public(*row) if row else None

    def for_input(self,part,thread):
        """Queue storage turns localImage into data URLs. Match only our submitted photos."""
        if part.get('type')=='localImage' and isinstance(part.get('path'),str):
            photo=self.for_path(part['path'])
            if photo:
                with closing(sqlite3.connect(self.db)) as db:
                    if db.execute('SELECT 1 FROM photos WHERE id=? AND thread_id=?',(photo['id'],thread)).fetchone():return photo
            return None
        url=part.get('image_url')
        if part.get('type')!='image' or not isinstance(url,str) or not url.startswith('data:image/jpeg;base64,') or len(url)>self.MAX_BYTES*4//3+100:return None
        try:digest=hashlib.sha256(base64.b64decode(url.split(',',1)[1],validate=True)).hexdigest()
        except (ValueError,binascii.Error):return None
        with self.lock,closing(sqlite3.connect(self.db)) as db,db:
            # Backfill only our bounded private upload files from before this schema update.
            for row in db.execute('SELECT id FROM photos WHERE thread_id=? AND sha256 IS NULL',(thread,)).fetchall():
                path=self.root/(row[0]+'.jpg')
                if path.is_file() and not path.is_symlink():db.execute('UPDATE photos SET sha256=? WHERE id=?',(hashlib.sha256(path.read_bytes()).hexdigest(),row[0]))
            row=db.execute('SELECT id,name FROM photos WHERE thread_id=? AND sha256=? AND submitted=1 ORDER BY created DESC LIMIT 1',(thread,digest)).fetchone()
            return self.public(*row) if row else None
