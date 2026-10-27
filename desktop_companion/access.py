"""Pairing and revocable browser sessions for the separate phone gateway."""

from collections import deque
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import tempfile
import threading
import time
from urllib.parse import urlsplit


COOKIE_NAME = "__Host-companion"
SESSION_SECONDS = 7 * 24 * 60 * 60


class PairingError(Exception):
    def __init__(self, limited=False):
        self.limited = limited
        super().__init__("Too many pairing attempts. Wait five minutes." if limited else "Pairing code is invalid, expired or already used.")


class AccessStore:
    def __init__(self, path, clock=time.time):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.clock = clock
        self.lock = threading.Lock()
        self.failures = deque()
        with self.db() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("CREATE TABLE IF NOT EXISTS pairing (code_hash TEXT PRIMARY KEY, expires REAL NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, csrf TEXT NOT NULL, name TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL)")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(sessions)")}
            for name in ("last_seen", "revoked_at"):
                if name not in columns:
                    connection.execute(f"ALTER TABLE sessions ADD COLUMN {name} REAL")
            if "event_id" not in {row[1] for row in connection.execute("PRAGMA table_info(pairing)")}:
                connection.execute("ALTER TABLE pairing ADD COLUMN event_id TEXT")
            connection.execute("CREATE TABLE IF NOT EXISTS pairing_events (id TEXT PRIMARY KEY, created REAL NOT NULL, expires REAL NOT NULL, status TEXT NOT NULL)")
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def digest(value):
        return hashlib.sha256(value.encode()).hexdigest()

    def create_pairing(self):
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        raw = "".join(secrets.choice(alphabet) for _ in range(12))
        now = self.clock()
        expires = now + 300
        event_id = secrets.token_hex(12)
        with self.lock, self.db() as connection:
            connection.execute("UPDATE pairing_events SET status=CASE WHEN expires<=? THEN 'expired' ELSE 'replaced' END WHERE id IN (SELECT event_id FROM pairing)",(now,))
            connection.execute("DELETE FROM pairing")
            connection.execute("INSERT INTO pairing_events VALUES (?,?,?,?)", (event_id,now,expires,"pending"))
            connection.execute("INSERT INTO pairing (code_hash,expires,event_id) VALUES (?,?,?)", (self.digest(raw),expires,event_id))
        return {"code": "-".join(raw[i:i + 4] for i in range(0, 12, 4)), "expires_at": int(expires * 1000), "id": event_id}

    def exchange(self, code, name):
        if not isinstance(code, str) or len(code) > 100:
            code = ""
        normalized = re.sub(r"[\s-]", "", code.upper())
        now = self.clock()
        with self.lock, self.db() as connection:
            while self.failures and self.failures[0] <= now - 300:
                self.failures.popleft()
            if len(self.failures) >= 15:
                raise PairingError(limited=True)
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT expires,event_id FROM pairing WHERE code_hash=?", (self.digest(normalized),)).fetchone()
            if not row or row["expires"] <= now:
                self.failures.append(now)
                raise PairingError()
            connection.execute("DELETE FROM pairing WHERE code_hash=?", (self.digest(normalized),))
            connection.execute("UPDATE pairing_events SET status='used' WHERE id=?",(row["event_id"],))
            token = secrets.token_urlsafe(32)
            session = {"id": secrets.token_hex(12), "csrf": secrets.token_urlsafe(32), "expires": now + SESSION_SECONDS}
            clean_name = re.sub(r"[\x00-\x1f\x7f]", "", str(name or "Phone browser"))[:80]
            connection.execute("INSERT INTO sessions (id,token_hash,csrf,name,created,expires,last_seen,revoked_at) VALUES (?,?,?,?,?,?,?,?)", (session["id"],self.digest(token),session["csrf"],clean_name,now,session["expires"],now,None))
        return token, session

    def authenticate(self, token):
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{40,60}", token):
            return None
        with self.db() as connection:
            now = self.clock()
            row = connection.execute("SELECT id,csrf,name,expires,last_seen FROM sessions WHERE token_hash=? AND expires>? AND revoked_at IS NULL", (self.digest(token),now)).fetchone()
            if row and (row["last_seen"] is None or row["last_seen"] < now-60):
                connection.execute("UPDATE sessions SET last_seen=? WHERE id=?",(now,row["id"]))
        return dict(row) if row else None

    def devices(self, include_history=False):
        with self.db() as connection:
            query = "SELECT id,name,created,expires,last_seen,revoked_at FROM sessions"
            rows = connection.execute(query + (" ORDER BY created DESC LIMIT 100" if include_history else " WHERE expires>? AND revoked_at IS NULL ORDER BY created DESC"),() if include_history else (self.clock(),)).fetchall()
        values = []
        for row in rows:
            value = dict(row)
            value["status"] = "revoked" if row["revoked_at"] is not None else "expired" if row["expires"] <= self.clock() else "active"
            values.append(value)
        return values

    def revoke(self, device_id):
        with self.db() as connection:
            connection.execute("UPDATE sessions SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (self.clock(),device_id))

    def cancel_pairing(self, event_id=None):
        with self.db() as connection:
            suffix = " WHERE event_id=?" if event_id else ""
            arguments = (event_id,) if event_id else ()
            connection.execute("UPDATE pairing_events SET status=CASE WHEN expires<=? THEN 'expired' ELSE 'cancelled' END WHERE id IN (SELECT event_id FROM pairing"+suffix+")",(self.clock(),)+arguments)
            connection.execute("DELETE FROM pairing"+suffix,arguments)

    def pairing_history(self):
        with self.db() as connection:
            rows = connection.execute("SELECT id,created,expires,status FROM pairing_events ORDER BY created DESC,rowid DESC LIMIT 40").fetchall()
        values = []
        for row in rows:
            value = dict(row)
            if value["status"] == "pending" and value["expires"] <= self.clock():
                value["status"] = "expired"
            values.append(value)
        return values


class RemoteSettings:
    def __init__(self, path):
        self.path = Path(path)

    @staticmethod
    def valid_origin(value):
        if not isinstance(value, str):
            return False
        parts = urlsplit(value)
        return bool(parts.scheme == "https" and not parts.username and not parts.password and not parts.port and not parts.path and not parts.query and not parts.fragment and re.fullmatch(r"[a-z0-9]+(?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9]+(?:[a-z0-9-]*[a-z0-9])?)+", parts.netloc))

    def origin(self):
        try:
            value = json.loads(self.path.read_text()).get("public_origin")
            return value if self.valid_origin(value) else None
        except (OSError, ValueError, TypeError, AttributeError):
            return None

    def set_origin(self, value):
        if value is not None and not self.valid_origin(value):
            raise ValueError("A valid HTTPS origin is required.")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, prefix="remote-", delete=False) as handle:
            os.chmod(handle.name, 0o600)
            json.dump({"public_origin": value}, handle)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = handle.name
        os.replace(temporary, self.path)
