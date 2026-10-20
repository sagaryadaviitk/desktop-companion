"""Companion-owned read receipts; the desktop databases remain read-only."""

from pathlib import Path
from contextlib import closing
import sqlite3
import threading


class Inbox:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY,seen TEXT,forced INTEGER NOT NULL DEFAULT 0)")
            db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value TEXT)")
        self.path.chmod(0o600)

    def decorate(self, chats):
        with self.lock, closing(sqlite3.connect(self.path)) as db, db:
            first = db.execute("SELECT value FROM metadata WHERE key='initialized'").fetchone() is None
            for chat in chats:
                revision = chat.get("message_revision")
                native = chat.get("desktop_unread")
                row = db.execute("SELECT seen,forced FROM receipts WHERE id=?", (chat["id"],)).fetchone()
                if row is None:
                    seen = revision if first and native is not True else None
                    db.execute("INSERT INTO receipts VALUES (?,?,0)", (chat["id"], seen))
                    row = (seen, 0)
                if native is False and not row[1] and row[0]!=revision:
                    db.execute("UPDATE receipts SET seen=? WHERE id=?", (revision, chat["id"]))
                    row = (revision, 0)
                chat["unread"] = bool(row[1] or revision and revision != row[0])
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('initialized','1')")
        return chats

    def mark(self, thread_id, revision, unread=False):
        with self.lock, closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO receipts VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET seen=excluded.seen,forced=excluded.forced WHERE receipts.seen IS NOT excluded.seen OR receipts.forced<>excluded.forced",
                       (thread_id, revision, int(unread)))

    def mark_current(self, thread_id, revision, read_revision, unread=False):
        # Serialize validation + persistence across paired browsers. A slower
        # older read must not overwrite a newer receipt after validating early.
        with self.lock:
            if read_revision()!=revision:return False
            self.mark(thread_id,revision,unread)
            return True
