"""Bounded local app-server queries. No token files or inference requests."""

import copy
import json
import os
from pathlib import Path
import selectors
import shutil
import stat
import subprocess
import threading
import time


class RuntimeUnavailable(Exception):
    pass


class RpcClient:
    def __init__(self, command, timeout=15):
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, bufsize=0)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.timeout = timeout
        self.buffer = b""
        self.serial = 0
        try:
            self.call("initialize", {"clientInfo": {"name": "desktop-companion", "version": "1.1"},
                                      "capabilities": {"experimentalApi": True}})
            self.notify("initialized", {})
        except Exception:
            self.close()
            raise

    def notify(self, method, params):
        try:
            self.process.stdin.write((json.dumps({"method": method, "params": params}) + "\n").encode())
            self.process.stdin.flush()
        except (OSError, ValueError) as error:
            raise RuntimeUnavailable("The local runtime disconnected.") from error

    def call(self, method, params):
        self.serial += 1
        identity = self.serial
        request = {"id": identity, "method": method, "params": params}
        try:
            self.process.stdin.write((json.dumps(request) + "\n").encode())
            self.process.stdin.flush()
        except (OSError, ValueError) as error:
            raise RuntimeUnavailable("The local runtime disconnected.") from error
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                try:
                    response = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if response.get("id") != identity:
                    continue
                if "error" in response:
                    # Raw runtime errors can contain paths or request details.
                    raise RuntimeUnavailable("The desktop runtime did not accept this operation. Refresh and try again.")
                return response.get("result", {})
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self.selector.select(remaining):
                break
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeUnavailable("The local runtime is unavailable.")
            self.buffer += chunk
            if len(self.buffer) > 8 * 1024 * 1024:
                raise RuntimeUnavailable("The local runtime response exceeded the safe limit.")
        raise RuntimeUnavailable("The local runtime timed out. Refresh before retrying.")

    def close(self):
        self.selector.close()
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        for stream in (self.process.stdin, self.process.stdout):
            if stream:
                stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def usage_snapshot(response, checked_at):
    """Select actual week-long windows; never assume primary means weekly."""
    buckets = response.get("rateLimitsByLimitId")
    if not isinstance(buckets, dict) or not buckets:
        legacy = response.get("rateLimits")
        buckets = {legacy.get("limitId") or "codex": legacy} if isinstance(legacy, dict) else {}
    weekly = []
    for key, bucket in buckets.items():
        if not isinstance(bucket, dict):
            continue
        for slot in ("primary", "secondary"):
            window = bucket.get(slot)
            if not isinstance(window, dict) or window.get("windowDurationMins") != 10080:
                continue
            used = window.get("usedPercent")
            if isinstance(used, bool) or not isinstance(used, (int, float)) or not 0 <= used <= 100:
                continue
            reset = window.get("resetsAt")
            if isinstance(reset, bool) or not isinstance(reset, (int, float)):
                reset = None
            weekly.append({"id": str(key)[:100], "label": str(bucket.get("limitName") or key)[:100],
                           "used_percent": used, "remaining_percent": round(100 - used, 1),
                           "resets_at": reset})
    return {"available": bool(weekly), "weekly": weekly, "checked_at": checked_at, "stale": False, "loading": False,
            "reason": "" if weekly else "This account did not report a weekly allowance."}


def model_catalog(response):
    models = []
    for entry in response.get("data", []):
        if not isinstance(entry, dict) or entry.get("hidden"):
            continue
        slug = entry.get("model") or entry.get("id")
        if not isinstance(slug, str) or not slug or len(slug) > 100:
            continue
        efforts = [value["reasoningEffort"] for value in entry.get("supportedReasoningEfforts", [])
                   if isinstance(value, dict) and isinstance(value.get("reasoningEffort"), str)]
        models.append({"id": slug, "name": str(entry.get("displayName") or slug)[:100],
                       "efforts": efforts, "default_effort": entry.get("defaultReasoningEffort")})
    return models


class AccountMonitor:
    """Refresh once a minute off the HTTP path; retain an explicitly stale snapshot."""
    def __init__(self, executable=None, clock=time.time, client_factory=RpcClient):
        self.executable = executable or shutil.which("codex")
        self.clock = clock
        self.client_factory = client_factory
        self.lock = threading.Lock()
        self.running = False
        self.next_refresh = 0
        self.usage = {"available": False, "weekly": [], "checked_at": None, "stale": False, "loading": True,
                      "reason": "Checking the local account…"}
        self.models = []

    def snapshot(self):
        with self.lock:
            if self.clock() >= self.next_refresh and not self.running:
                self.running = True
                self.next_refresh = self.clock() + 60
                threading.Thread(target=self._refresh, daemon=True, name="companion-account").start()
            usage = copy.deepcopy(self.usage)
            if usage["checked_at"] and self.clock() - usage["checked_at"] > 120:
                usage["stale"] = True
            return {"usage": usage, "models": copy.deepcopy(self.models)}

    def _refresh(self):
        try:
            if not self.executable:
                raise RuntimeUnavailable("The Codex CLI is unavailable.")
            # This independent process only reads account/catalog data. It never loads a chat.
            with self.client_factory([self.executable, "app-server", "--stdio"]) as client:
                usage = usage_snapshot(client.call("account/rateLimits/read", {}), int(self.clock()))
                with self.lock:
                    self.usage = usage
                models = model_catalog(client.call("model/list", {"limit": 100, "includeHidden": False}))
                with self.lock:
                    self.models = models
        except (OSError, RuntimeUnavailable, ValueError, TypeError):
            with self.lock:
                if self.usage["checked_at"]:
                    self.usage["stale"] = True
                else:
                    self.usage["loading"] = False
                    self.usage["reason"] = "Weekly usage is temporarily unavailable. It will retry automatically."
        finally:
            with self.lock:
                self.running = False


class SharedRuntime:
    """Only the existing shared daemon may control desktop work. Never start/resume a chat."""
    REASON = "The desktop shared-control connection is unavailable. Queue follow-ups still work."

    def __init__(self, home, executable=None, client_factory=RpcClient):
        self.home = Path(home).resolve()
        self.socket = self.home / "app-server-control" / "app-server-control.sock"
        self.executable = executable or shutil.which("codex")
        self.client_factory = client_factory

    def present(self):
        try:
            info = self.socket.stat()
            return bool(self.executable and info.st_uid == os.getuid() and stat.S_ISSOCK(info.st_mode))
        except OSError:
            return False

    def client(self):
        if not self.present():
            raise RuntimeUnavailable(self.REASON)
        return self.client_factory([self.executable, "app-server", "proxy", "--sock", str(self.socket)], timeout=5)

    @staticmethod
    def loaded(client, thread_id):
        if thread_id not in client.call("thread/loaded/list", {}).get("data", []):
            raise RuntimeUnavailable("This chat is not loaded in the shared desktop runtime. Open it on your Mac first.")
        thread = client.call("thread/read", {"threadId": thread_id, "includeTurns": False}).get("thread", {})
        if thread.get("canAcceptDirectInput") is not True:
            raise RuntimeUnavailable("This chat cannot accept direct input from the shared desktop runtime.")
        return thread

    def capabilities(self, thread_id):
        value = {"steer": False, "model_selection": False, "reason": self.REASON}
        if not self.present():
            return value
        try:
            with self.client() as client:
                thread = self.loaded(client, thread_id)
                active = thread.get("status", {}).get("type") == "active"
                return {"steer": active, "model_selection": True,
                        "reason": "Steer the current turn." if active else "Steering is available while this chat is running.",
                        "model": thread.get("model"), "effort": thread.get("reasoningEffort")}
        except (OSError, RuntimeUnavailable):
            return value

    def steer(self, thread_id, message, expected_turn_id):
        with self.client() as client:
            self.loaded(client, thread_id)
            result = client.call("turn/steer", {"threadId": thread_id, "expectedTurnId": expected_turn_id,
                                                "input": [{"type": "text", "text": message}]})
            if result.get("turnId") != expected_turn_id:
                raise RuntimeUnavailable("Steering acknowledgement could not be verified. Check the desktop before retrying.")
            return {"state": "steered", "message": "Sent to the active turn. Completion is not yet confirmed."}

    def set_model(self, thread_id, model, effort=None):
        with self.client() as client:
            self.loaded(client, thread_id)
            params = {"threadId": thread_id, "model": model}
            if effort:
                params["effort"] = effort
            client.call("thread/settings/update", params)
            thread = client.call("thread/read", {"threadId": thread_id, "includeTurns": False}).get("thread", {})
            if thread.get("model") != model or effort and thread.get("reasoningEffort") != effort:
                raise RuntimeUnavailable("The model change could not be verified. Refresh before trying again.")
            return {"state": "updated", "model": model, "effort": thread.get("reasoningEffort"),
                    "message": "Model saved for subsequent turns in this chat."}
