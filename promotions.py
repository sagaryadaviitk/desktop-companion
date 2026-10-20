"""Private recovery for moving a queued message into a live turn.

The original is claimed through the desktop before sending, so it cannot also run
as the next queued turn. A process crash retains its text for review, never replay.
"""

from contextlib import closing
import json
import re
from pathlib import Path
import sqlite3
import threading
import time
import uuid


class QueuePromotions:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700);self.lock=threading.Lock()
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS promotions (id TEXT PRIMARY KEY,thread_id TEXT,client_id TEXT,text TEXT,stage TEXT,created REAL)')
            if 'photos' not in [column[1] for column in db.execute('PRAGMA table_info(promotions)')]:db.execute("ALTER TABLE promotions ADD COLUMN photos TEXT NOT NULL DEFAULT '[]'")
            if 'kind' not in [column[1] for column in db.execute('PRAGMA table_info(promotions)')]:db.execute("ALTER TABLE promotions ADD COLUMN kind TEXT NOT NULL DEFAULT 'promotion'")
        self.path.chmod(0o600)

    def prepare(self,thread,item):
        with self.lock,closing(sqlite3.connect(self.path)) as db,db:
            photos=[Path(path).stem for path in item.get('photos',[]) if re.fullmatch(r'[a-f0-9]{32}',Path(path).stem)]
            db.execute("INSERT INTO promotions (id,thread_id,client_id,text,stage,created,photos) VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET text=excluded.text,photos=excluded.photos,stage='prepared',created=excluded.created WHERE promotions.stage='not_claimed'",(item['id'],thread,item['client_id'],item['text'],'prepared',time.time(),json.dumps(photos)))

    def stage(self,identity,stage,text=None):
        with self.lock,closing(sqlite3.connect(self.path)) as db,db:
            db.execute('PRAGMA secure_delete=ON')
            db.execute('UPDATE promotions SET stage=? WHERE id=?',(stage,identity))
            if stage in ('sent','not_claimed'):db.execute("UPDATE promotions SET text='',photos='[]' WHERE id=?",(identity,))
            elif text is not None:db.execute('UPDATE promotions SET text=? WHERE id=?',(text,identity))

    def capture_change(self,thread,item,kind):
        """Keep the last observed version before an explicit edit/delete, never replay it."""
        if kind not in ('edit','delete'):raise ValueError('Unsupported queue change')
        identity=uuid.uuid4().hex
        photos=[Path(path).stem for path in item.get('photos',[]) if re.fullmatch(r'[a-f0-9]{32}',Path(path).stem)]
        with self.lock,closing(sqlite3.connect(self.path)) as db,db:
            db.execute('PRAGMA secure_delete=ON')
            db.execute("DELETE FROM promotions WHERE kind IN ('edit','delete') AND stage IN ('updated','deleted') AND created<?",(time.time()-7*86400,))
            db.execute('INSERT INTO promotions(id,thread_id,client_id,text,stage,created,photos,kind) VALUES(?,?,?,?,?,?,?,?)',
                       (identity,thread,item['client_id'],item['text'],'change_pending',time.time(),json.dumps(photos),kind))
        return identity

    def pending(self,thread,has_receipt,redact):
        with self.lock,closing(sqlite3.connect(self.path)) as db:
            rows=db.execute("SELECT id,client_id,text,created,photos,kind,stage FROM promotions WHERE thread_id=? AND stage NOT IN ('sent','dismissed','not_claimed') ORDER BY created DESC LIMIT 20",(thread,)).fetchall()
        result=[]
        for identity,client,text,created,photos,kind,stage in rows:
            if kind=='promotion' and has_receipt(thread,client):self.stage(identity,'sent');continue
            confirmed=stage in ('updated','deleted')
            title='Previous queued version' if kind=='edit' and confirmed else 'Deleted queued message' if kind=='delete' and confirmed else 'Delivery needs review'
            message='The last observed text and photos are saved here for seven days. Copying to a draft does not send them. Avoid editing the same entry on both devices at once.' if confirmed else 'Queue change needs review. Check the queue and conversation before reusing this text; it is never resent automatically.'
            result.append({'id':identity,'text':redact(text),'timestamp':int(created*1000),'state':'review',
                           'title':title,'kind':kind,'confirmed':confirmed,
                           'photos':[{'id':p,'name':'Saved photo','url':'/api/photos/'+p} for p in json.loads(photos) if isinstance(p,str) and re.fullmatch(r'[a-f0-9]{32}',p)],
                           'message':message})
        return result

    def dismiss(self,thread,identity):
        with self.lock,closing(sqlite3.connect(self.path)) as db,db:
            db.execute('PRAGMA secure_delete=ON')
            db.execute("UPDATE promotions SET stage='dismissed',text='',photos='[]' WHERE id=? AND thread_id=? AND stage!='sent'",(identity,thread))
