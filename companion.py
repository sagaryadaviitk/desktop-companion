#!/usr/bin/env python3
"""Desktop chat bridge and isolated opt-in local assistant; native history stays read-only."""

import argparse
from contextlib import closing, contextmanager
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import subprocess
import threading
import time
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import CookieError, SimpleCookie
from urllib.parse import parse_qs, urlsplit

from access import AccessStore, COOKIE_NAME, PairingError, RemoteSettings, SESSION_SECONDS
from power import cached_power_state
from inbox import Inbox
from runtime import AccountMonitor, SharedRuntime, RuntimeUnavailable
from questions import pending_questions, answer_envelope, answer_identity, queued_question_answers, replies as question_replies
from desktop_runtime import DesktopRuntime,queue_photo_message,QueueNotClaimed,change_queued_message
from approvals import ApprovalChanged, digest as approval_digest
from computer_use import computer_use_snapshot
from read_cache import ReadCache
from promotions import QueuePromotions
from photos import PhotoStore, PhotoError
from new_chat import NewChat,PERSONAL_WORKSPACE,prompt_title
from configuration import load as load_configuration
from system_status import read_record
from media import MediaStore, byte_range
from local_assistant import LocalAssistant, LocalError
from local_settings import LocalSettings


ROOT = Path(__file__).resolve().parent
STATIC_FILES={'/':('index.html','text/html'),'/style.css':('style.css','text/css'),'/theme.css':('theme.css','text/css'),
              '/local':('local.html','text/html'),'/local/':('local.html','text/html'),'/local.css':('local.css','text/css'),'/local.js':('local.js','text/javascript'),
              '/media':('media.html','text/html'),'/media/':('media.html','text/html'),'/media.css':('media.css','text/css'),
              **{f'/{name}.js':(f'{name}.js','text/javascript') for name in ('app','markdown','refresh','attachments','updates','new_chat','appearance','status','queue','conversation','shell','approvals','media','local_entry')}}
def web_build():
    digest=hashlib.sha256()
    for path in sorted((ROOT/'web').glob('*')):
        if path.suffix in ('.js','.css','.html'):digest.update(path.name.encode()+path.read_bytes())
    return digest.hexdigest()[:16]


THREAD_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.S), "[private key hidden]"),
    (re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|xox[baprs]-[A-Za-z0-9-]{8,}|xapp-[A-Za-z0-9-]{8,}|gh[pousr]_[A-Za-z0-9]{15,}|github_pat_[A-Za-z0-9_]{15,})\b"), "[credential hidden]"),
    (re.compile(r"(?i)(authorization\s*[:=]\s*[\"']?bearer\s+)[^\s\"'<>]+"), r"\1[hidden]"),
    (re.compile(r"(?i)([\"']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|client[_-]?secret|signing[_-]?secret)[\"']?\s*[:=]\s*[\"']?)[^\s\"',}<>]{8,}"), r"\1[hidden]"),
]


def redact(text):
    text = str(text or "")
    text = re.sub(r"<oai-mem-citation>.*?</oai-mem-citation>", "", text, flags=re.S)
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def visible_user_text(text):
    stripped = text.strip()
    prefix = '<in-app-browser-context source="ambient-ui-state">'
    if stripped.startswith(prefix):
        _, separator, remainder = stripped.partition("</in-app-browser-context>")
        if separator and remainder.lstrip().startswith("## My request:"):
            return remainder.lstrip().removeprefix("## My request:").lstrip()
    if stripped.startswith("<send_user_message_question_reply>") and stripped.endswith("</send_user_message_question_reply>"):
        try:
            value = json.loads(stripped.removeprefix("<send_user_message_question_reply>").removesuffix("</send_user_message_question_reply>"))
            if isinstance(value,list) and all(isinstance(item,dict) and "question" in item and "answer" in item for item in value):
                return "\n\n".join(str(item["question"])+"\n\n"+str(item["answer"]) for item in value)
        except (ValueError,TypeError):
            pass
    if stripped.startswith("<environment_context>") and stripped.endswith("</environment_context>"):
        return ""
    return text


def is_scheduled_input(value):
    """Hide scheduler triggers, preserving human examples and assistant results.

    Rollout messages carry user.heartbeat provenance. Current history projections
    omit it, so legacy rows need the exact envelope and no human client receipt.
    """
    metadata=value.get('internal_chat_message_metadata_passthrough')
    kinds=metadata.get('content_item_kinds') if isinstance(metadata,dict) else None
    if isinstance(kinds,list) and kinds:
        return 'user.heartbeat' in kinds
    if value.get('clientId'):
        return False
    content=value.get('content',[])
    if not isinstance(content,list) or not content or any(not isinstance(part,dict) or part.get('type')!='text' or not isinstance(part.get('text'),str) for part in content):
        return False
    text='\n'.join(part['text'] for part in content).strip()
    return bool(re.fullmatch(
        r'<heartbeat>\s*<automation_id>[A-Za-z0-9][A-Za-z0-9_-]*</automation_id>\s*'
        r'<current_time_iso>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})</current_time_iso>\s*'
        r'<instructions>\s*\S[\s\S]*</instructions>\s*</heartbeat>', text))


def initialize_remote_origin(settings,state,config,now=None):
    """A web-only restart must not revoke a still-running approved tunnel.

    A cold start, changed configuration, stale record, or absent controller still
    clears the origin and requires the tunnel's normal authentication preflight.
    """
    retained=None;now=time.time() if now is None else now
    try:
        runtime_path=Path(state)/'service-runtime.json'
        if runtime_path.is_symlink() or runtime_path.stat().st_size>4096:raise ValueError('Invalid runtime record')
        runtime=json.loads(runtime_path.read_text());record=read_record(state);pid=record.get('controller_pid')
        if config.remote_enabled and settings.origin()=='https://'+config.hostname and type(pid) is int and pid>1 and runtime.get('children',{}).get('tunnel')==pid and 0<=now-record.get('checked_at',0)<=90 and record.get('connections',0)>0:
            os.kill(pid,0)
            retained=settings.origin()
    except (OSError,ValueError,TypeError,AttributeError):pass
    settings.set_origin(retained)
    return retained is not None


@contextmanager
def readonly_db(path):
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        yield connection
    finally:
        connection.close()


class History:
    """Version-specific read-only adapter; internal schema changes fail visibly."""

    def __init__(self, home, photos=None,personal_workspaces=None):
        self.home = Path(home).resolve()
        self.state = self.home / "state_5.sqlite"
        self.history = self.home / "thread_history_1.sqlite"
        self.photos = photos
        self.personal_workspaces=[Path(p).resolve() for p in (personal_workspaces if personal_workspaces is not None else [PERSONAL_WORKSPACE,ROOT/'.state/chat-workspace'])]

    def catalog_revision(self,inbox):
        # SQLite commits touch either the DB or its WAL/journal. Include receipts
        # so another paired browser's Seen action invalidates the catalog too.
        values=[]
        for path in (self.state,self.history,self.home/'queue_1.sqlite',inbox.path):
            for candidate in (path,Path(str(path)+'-wal'),Path(str(path)+'-journal')):
                try:
                    info=candidate.stat();values.append((info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns))
                except FileNotFoundError:values.append(None)
        return hashlib.sha256(json.dumps(values).encode()).hexdigest()

    def active_candidates(self):
        with readonly_db(self.history) as connection:
            return [row[0] for row in connection.execute("SELECT t.thread_id FROM thread_turns t JOIN (SELECT thread_id,MAX(rollout_ordinal) latest FROM thread_turns GROUP BY thread_id) l ON l.thread_id=t.thread_id AND l.latest=t.rollout_ordinal WHERE t.status='inProgress' ORDER BY t.started_at DESC LIMIT 12")]

    @staticmethod
    def _project_roots(connection):
        return sorted([(row["project_id"],Path(row["path"]).expanduser()) for row in connection.execute("SELECT project_id,path FROM project_roots")],key=lambda item:len(item[1].parts),reverse=True)

    def _public_thread(self, row, roots=()):
        project_id = row["project_id"]
        personal=not project_id and row['cwd'] and any(Path(row['cwd']).resolve()==root or root in Path(row['cwd']).resolve().parents for root in self.personal_workspaces)
        project_keys=["project:"+project_id] if project_id else []
        if not project_id and row["cwd"] and not personal:
            cwd=Path(row["cwd"]).expanduser()
            matched_depth=0
            for identifier,path in roots:
                if cwd==path or path in cwd.parents:
                    if matched_depth and len(path.parts)<matched_depth:break
                    matched_depth=len(path.parts)
                    project_keys.append("project:"+identifier)
            project_keys=list(dict.fromkeys(project_keys))
        workspace_key = "folder:"+hashlib.sha256((row["cwd"] or "").encode()).hexdigest()[:24]
        project_keys=project_keys or (['personal'] if personal else [workspace_key])
        return {
            "id": row["id"],
            "title": redact(row["name"] or row["title"] or "Untitled chat")[:300],
            "workspace": 'Personal' if personal else Path(row["cwd"]).name if row["cwd"] else "",
            "updated_at": row["updated_at_ms"] or row["updated_at"] * 1000,
            "pinned": bool(row["is_pinned"]),
            "archived": bool(row["archived"]),
            "subagent": bool(row["agent_path"] and row["agent_path"] not in ("", "/root")),
            "project_key": project_keys[0],"project_keys":project_keys,
        }

    def list_threads(self, include_archived=False, include_children=False):
        with readonly_db(self.state) as connection:
            roots=self._project_roots(connection)
            rows = connection.execute(
                "SELECT id,name,title,cwd,updated_at,updated_at_ms,recency_at_ms,is_pinned,archived,agent_path,project_id,model "
                "FROM threads WHERE 1=1 "
                + ("AND archived=0 " if not include_archived else "")
                + ("AND (agent_path IS NULL OR agent_path='' OR agent_path='/root') " if not include_children else "")
                + "ORDER BY COALESCE(recency_at_ms,updated_at_ms,updated_at*1000) DESC,id DESC"
            ).fetchall()
            receipts={}
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='thread_read_receipts'").fetchone():
                published='last_published_turn' if any(column['name']=='last_published_turn' for column in connection.execute('PRAGMA table_info(thread_read_receipts)')) else 'NULL AS last_published_turn'
                receipts={row['thread_id']:(row['first_unread_turn'] is not None,row['last_published_turn']) for row in connection.execute('SELECT thread_id,first_unread_turn,'+published+' FROM thread_read_receipts')}
        with readonly_db(self.history) as connection:
            statuses = {row["thread_id"]: {"turn_id":row['turn_id'],"status":row["status"],"started_at":row["started_at"],"completed_at":row["completed_at"]} for row in connection.execute(
                "SELECT t.thread_id,t.turn_id,t.status,t.started_at,t.completed_at FROM thread_turns t "
                "JOIN (SELECT thread_id,MAX(rollout_ordinal) AS latest FROM thread_turns GROUP BY thread_id) l "
                "ON l.thread_id=t.thread_id AND l.latest=t.rollout_ordinal")}
            summaries=self.message_summaries(connection,[row['id'] for row in rows])
            questions=self.questions_by_thread(connection,[row['id'] for row in rows])
            activity={row['thread_id']:row['latest'] for row in connection.execute(
                "SELECT thread_id,MAX(MAX(COALESCE(created_at_ms,0),COALESCE(started_at_ms,0),COALESCE(completed_at_ms,0))) AS latest "
                "FROM thread_items WHERE item_type IN ('userMessage','agentMessage','steeringUserMessage','commandExecution','mcpToolCall','fileChange','webSearch','plan','collabAgentToolCall','subAgentActivity') GROUP BY thread_id")}
        queued={}
        if (self.home/'queue_1.sqlite').exists():
            with readonly_db(self.home/'queue_1.sqlite') as connection:
                if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='queued_items'").fetchone():
                    updated='updated_at_ms' if any(c['name']=='updated_at_ms' for c in connection.execute('PRAGMA table_info(queued_items)')) else 'created_at_ms'
                    queued={r['thread_id']:r['latest'] for r in connection.execute('SELECT thread_id,MAX(MAX(COALESCE(created_at_ms,0),COALESCE('+updated+',0))) AS latest FROM queued_items GROUP BY thread_id')}
        result=[]
        for row in rows:
            value=self._public_thread(row,roots)
            value["last_recorded_turn"]=statuses.get(row["id"])
            value.update(summaries[row['id']]);receipt=receipts.get(row['id'])
            # A desktop read receipt for an older published turn cannot clear a new reply.
            value['desktop_unread']=True if receipt and receipt[0] else False if receipt and receipt[1] and receipt[1]==(value['last_recorded_turn'] or {}).get('turn_id') else None
            value['model']=redact(row['model'] or '')
            value['question_count']=len(questions[row['id']])
            turn=value['last_recorded_turn'] or {}
            value['last_activity_at']=max(row['recency_at_ms'] or 0,value['updated_at'],activity.get(row['id']) or 0,queued.get(row['id']) or 0,(turn.get('started_at') or 0)*1000,(turn.get('completed_at') or 0)*1000)
            result.append(value)
        return sorted(result,key=lambda chat:(chat['last_activity_at'],chat['id']),reverse=True)

    @staticmethod
    def public_text(kind, value):
        if not isinstance(value,dict):return ''
        if kind=='userMessage':
            if is_scheduled_input(value):return ''
            text='\n'.join(part.get('text','') for part in value.get('content',[]) if isinstance(part,dict) and part.get('type')=='text' and isinstance(part.get('text'),str))
            text=visible_user_text(text)
        else:text=value.get('text','')
        if not isinstance(text,str) or text.strip().startswith(('<external_codex_apps_open_page>','# AGENTS.md instructions')):return ''
        return redact(text)

    def message_summaries(self, connection, identifiers):
        result={}
        for identifier in identifiers:
            summary={'preview':'','preview_role':None,'message_revision':None,'last_message_at':None}
            # Match the existing paginated reader's indexed thread/ordinal access path.
            for kind,where in [('latest',"item_type IN ('agentMessage','userMessage')"),('assistant',"item_type='agentMessage'")]:
                # Skip hidden triggers rather than blanking the last real preview.
                with closing(connection.execute('SELECT item_id,item_type,item_json,rollout_ordinal,created_at_ms FROM thread_items WHERE thread_id=? AND '+where+' ORDER BY rollout_ordinal DESC',(identifier,))) as cursor:
                    for row in cursor:
                        try:text=self.public_text(row['item_type'],json.loads(row['item_json']))
                        except (ValueError,TypeError):continue
                        if not text.strip():continue
                        if kind=='latest':
                            preview=re.sub(r'(?m)^\s{0,3}(?:#{1,6}|>|[-*+])\s+','',text)
                            preview=re.sub(r'!?\[([^\]]+)\]\([^\n)]*\)',r'\1',preview)
                            preview=re.sub(r'[`*_~]','',preview)
                            summary.update(preview=re.sub(r'\s+',' ',preview).strip()[:220],preview_role='assistant' if row['item_type']=='agentMessage' else 'user',last_message_at=row['created_at_ms'])
                        else:
                            summary['message_revision']=hashlib.sha256(json.dumps([row['item_id'],row['rollout_ordinal'],text]).encode()).hexdigest()
                        break
            result[identifier]=summary
        return result

    def list_projects(self, chats):
        projects={}
        with readonly_db(self.state) as connection:
            for row in connection.execute("SELECT id,name FROM projects ORDER BY position,created_at_ms"):
                key="project:"+row["id"]
                projects[key]={"id":key,"name":redact(row["name"] or "Untitled project"),"kind":"project","chat_count":0,"active_count":0,"archived_count":0}
        for chat in chats:
            for key in chat["project_keys"]:
                if key not in projects:
                    projects[key]={"id":key,"name":chat["workspace"] or "Without a project","kind":"folder","chat_count":0,"active_count":0,"archived_count":0}
                project=projects[key]
                project["chat_count"]+=1
                project["archived_count" if chat["archived"] else "active_count"]+=1
        return list(projects.values())

    @staticmethod
    def questions_by_thread(connection, identifiers):
        result={}
        for identifier in identifiers:
            turn=connection.execute('SELECT status,rollout_ordinal FROM thread_turns WHERE thread_id=? ORDER BY rollout_ordinal DESC LIMIT 1',(identifier,)).fetchone()
            if not turn or turn['status']!='inProgress':result[identifier]=[];continue
            rows=connection.execute("SELECT item_id,item_type,item_json,created_at_ms FROM thread_items "
                                    "WHERE thread_id=? AND rollout_ordinal>=? AND (item_type='agentMessage' AND item_json LIKE '%\"questions\"%' "
                                    "OR item_type IN ('userMessage','steeringUserMessage') AND item_json LIKE '%send_user_message_question_reply%') "
                                    "ORDER BY rollout_ordinal",(identifier,turn['rollout_ordinal'])).fetchall()
            result[identifier]=pending_questions(rows,redact)
        return result

    def read_thread(self, thread_id, before=None):
        if not THREAD_ID.fullmatch(thread_id):
            raise KeyError(thread_id)
        if before is not None and (type(before) is not int or not 1 <= before <= 9223372036854775807):
            raise ValueError("Invalid history cursor.")
        with readonly_db(self.state) as connection:
            roots=self._project_roots(connection)
            effort='reasoning_effort' if any(column['name']=='reasoning_effort' for column in connection.execute('PRAGMA table_info(threads)')) else 'NULL AS reasoning_effort'
            row = connection.execute(
                "SELECT id,name,title,cwd,updated_at,updated_at_ms,is_pinned,archived,agent_path,project_id,model,git_branch,"+effort+" FROM threads "
                "WHERE id=?", (thread_id,)
            ).fetchone()
        if row is None:
            raise KeyError(thread_id)
        thread = self._public_thread(row,roots)
        with readonly_db(self.history) as connection:
            connection.execute('BEGIN')  # The displayed messages and read revision share one snapshot.
            turn = connection.execute(
                "SELECT turn_id,status,started_at,completed_at FROM thread_turns "
                "WHERE thread_id=? ORDER BY rollout_ordinal DESC LIMIT 1", (thread_id,)
            ).fetchone()
            # Only public conversation messages. Never expose reasoning, tools or raw logs.
            rows = connection.execute(
                "SELECT item_id,item_type,item_json,created_at_ms,started_at_ms,completed_at_ms,rollout_ordinal "
                "FROM thread_items WHERE thread_id=? AND item_type IN ('userMessage','agentMessage') "
                + ("AND rollout_ordinal<? " if before is not None else "") +
                "ORDER BY rollout_ordinal DESC LIMIT 121", (thread_id,before) if before is not None else (thread_id,)
            ).fetchall()
            activity_rows = connection.execute(
                "SELECT item_id,item_type,item_json,created_at_ms,started_at_ms,completed_at_ms,rollout_ordinal "
                "FROM thread_items WHERE thread_id=? AND item_type IN ('commandExecution','mcpToolCall','fileChange','webSearch','plan','collabAgentToolCall','subAgentActivity') "
                "ORDER BY rollout_ordinal DESC LIMIT 100",(thread_id,)
            ).fetchall()
            summary=self.message_summaries(connection,[thread_id])[thread_id]
            questions=self.questions_by_thread(connection,[thread_id])[thread_id]
        has_older = len(rows) > 120
        rows = rows[:120]
        messages = []
        for item in reversed(rows):
            try:
                value = json.loads(item["item_json"])
            except (ValueError, TypeError):
                continue
            if not isinstance(value,dict):continue
            if item["item_type"] == "userMessage":
                if is_scheduled_input(value):continue
                content = value.get("content", [])
                photos=[photo for part in content if isinstance(part,dict) and self.photos for photo in [self.photos.for_input(part,thread_id)] if photo]
                text = "\n".join(part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text")
                text = visible_user_text(text)
                role = "user"
            else:
                text = value.get("text", "")
                role = "assistant"
                photos=[]
            if not text.strip() and not photos:
                continue
            # Do not present injected app context as a person's instruction.
            if text.strip().startswith(("<external_codex_apps_open_page>", "# AGENTS.md instructions")):
                continue
            messages.append({
                "id": item["item_id"], "role": role, "phase": value.get("phase", ""),
                "text": redact(text), "ordinal": item["rollout_ordinal"],
                "photos":photos,
                "client_id":value.get("clientId") if role=="user" and isinstance(value.get("clientId"),str) else None,
                "timestamp": item["created_at_ms"] or item["started_at_ms"] or item["completed_at_ms"],
            })
        thread["messages"] = messages
        thread.update(summary)
        thread["next_before"] = rows[-1]["rollout_ordinal"] if has_older and rows else None
        thread["last_recorded_turn"] = dict(turn) if turn else None
        thread["pending_instructions"] = self.pending_instructions(thread_id)
        queued=queued_question_answers(thread['pending_instructions'])
        for question in questions:
            if question['id'] in queued:
                question['delivery']='queued';question['queued_answer']=queued[question['id']]
                item=next(item for item in thread['pending_instructions'] if question['id'] in queued_question_answers([item]))
                question['queued_message']={key:item[key] for key in ('id','revision','can_steer')}
        thread['questions']=questions
        thread["snapshot_at"] = int(time.time() * 1000)
        thread["details"]={"model":redact(row["model"] or "Unknown"),"reasoning_effort":redact(row['reasoning_effort'] or ''),"branch":redact(row["git_branch"] or ""),"folder":redact(row["cwd"] or ""),"history_source":"Local desktop history"}
        thread["activity"],thread["plans"],thread["changes"]=self.public_activity(activity_rows)
        return thread

    def local_fork_snapshot(self, thread_id):
        """Read completed public text for an independent local copy.

        Each item belongs to its nearest preceding native turn ordinal. Turn
        state and selected items are read in one transaction, so an active or
        failed turn cannot enter the copy through a separate status check.
        No photo resolver, activity reader, native control, or provider is used.
        """
        if not isinstance(thread_id, str) or not THREAD_ID.fullmatch(thread_id):
            raise KeyError(thread_id)
        with readonly_db(self.state) as connection:
            row = connection.execute('SELECT name,title FROM threads WHERE id=?', (thread_id,)).fetchone()
        if row is None:
            raise KeyError(thread_id)
        title = redact(row['name'] or row['title'] or 'Untitled chat')[:300]
        with readonly_db(self.history) as connection:
            connection.execute('BEGIN')
            turn = connection.execute('SELECT turn_id,status,started_at,completed_at,rollout_ordinal FROM thread_turns '
                                      'WHERE thread_id=? ORDER BY rollout_ordinal DESC LIMIT 1', (thread_id,)).fetchone()
            omitted_incomplete = connection.execute('SELECT 1 FROM thread_turns WHERE thread_id=? '
                                                     "AND (status IS NULL OR status<>'completed') LIMIT 1", (thread_id,)).fetchone() is not None
            rows = connection.execute('''
                SELECT i.item_type,i.item_json,i.rollout_ordinal
                FROM thread_items i
                WHERE i.thread_id=? AND i.item_type IN ('userMessage','agentMessage')
                  AND (SELECT t.status FROM thread_turns t
                       WHERE t.thread_id=i.thread_id AND t.rollout_ordinal<=i.rollout_ordinal
                       ORDER BY t.rollout_ordinal DESC LIMIT 1)='completed'
                ORDER BY i.rollout_ordinal DESC LIMIT 121
            ''', (thread_id,)).fetchall()
        older = len(rows) > 120
        rows = rows[:120]
        messages = []
        for item in reversed(rows):
            try:
                value = json.loads(item['item_json'])
            except (ValueError, TypeError):
                continue
            if not isinstance(value, dict):
                continue
            if item['item_type'] == 'userMessage':
                if is_scheduled_input(value):
                    continue
                parts = value.get('content', [])
                if not isinstance(parts, list):
                    continue
                text = '\n'.join(part['text'] for part in parts if isinstance(part, dict)
                                 and part.get('type') == 'text' and isinstance(part.get('text'), str))
                text = visible_user_text(text)
                role = 'user'
            else:
                text = value.get('text')
                role = 'assistant'
            if not isinstance(text, str) or not text.strip():
                continue
            if text.lstrip().startswith(('<external_codex_apps_open_page>', '# AGENTS.md instructions')):
                continue
            messages.append({'role': role, 'phase': value.get('phase', ''),
                             'text': redact(text), 'ordinal': item['rollout_ordinal'], 'status': 'completed'})
        return {'id': thread_id, 'title': title, 'messages': messages,
                'last_recorded_turn': dict(turn) if turn else None,
                'fork_completed_only': True,
                'fork_omitted_incomplete': omitted_incomplete,
                'next_before': rows[-1]['rollout_ordinal'] if older and rows else None}

    @staticmethod
    def public_activity(rows):
        """Allowlist recorded activity metadata; never return commands, arguments, logs or reasoning."""
        labels={"commandExecution":"Command","mcpToolCall":"Connected tool","fileChange":"File edit","webSearch":"Web search","plan":"Plan","collabAgentToolCall":"Agent collaboration","subAgentActivity":"Subagent activity"}
        activity=[];plans=[];changes=[];seen_paths=set();patch_budget=300000
        for row in rows:
            try:value=json.loads(row["item_json"])
            except (ValueError,TypeError):continue
            if not isinstance(value,dict):continue
            kind=row["item_type"]
            status=value.get("status","")
            if status not in ("completed","failed","inProgress","declined","interrupted","pending"):status="recorded"
            activity.append({"id":row["item_id"],"kind":kind,"label":labels[kind],"status":status,"timestamp":row["completed_at_ms"] or row["started_at_ms"] or row["created_at_ms"]})
            if kind=="plan" and isinstance(value.get("text"),str):
                plans.append({"text":redact(value["text"]),"timestamp":row["created_at_ms"]})
            if kind=="fileChange" and isinstance(value.get("changes"),list):
                for change in value["changes"]:
                    if not isinstance(change,dict) or not isinstance(change.get("path"),str):continue
                    path=change["path"]
                    if path in seen_paths:continue
                    seen_paths.add(path)
                    diff=redact(change.get("diff") if isinstance(change.get("diff"),str) else "")
                    limit=min(24000,patch_budget);patch_budget-=min(len(diff),limit)
                    changes.append({"path":redact(path),"diff":diff[:limit],"truncated":len(diff)>limit,"status":status})
        return activity,plans,changes

    def pending_instructions(self, thread_id):
        path = self.home / "queue_1.sqlite"
        if not path.exists():
            return []
        with readonly_db(path) as connection:
            rows = connection.execute(
                "SELECT id,payload_json,created_at_ms FROM queued_items "
                "WHERE thread_id=? ORDER BY queue_order LIMIT 20", (thread_id,)
            ).fetchall()
        pending = []
        for row in rows:
            try:
                queued_input=json.loads(row["payload_json"])["UserInput"]
                content = queued_input["content"]
                text = "\n".join(item.get("text", "") for item in content if item.get("type") == "text")
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
            client=queued_input.get('client_id')
            text,photos,supported=self.queued_content(content,thread_id)
            plain=isinstance(client,str) and bool(THREAD_ID.fullmatch(client)) and supported and bool(text.strip() or photos) and len(text)<=8000
            answers=question_replies(text)
            pending.append({"id": row["id"], "text": redact(text)[:8000], "timestamp": row["created_at_ms"],
                            'photos':photos,'attachment_count':sum(1 for part in content if isinstance(part,dict) and part.get('type') in ('image','localImage')),'revision':hashlib.sha256(row['payload_json'].encode()).hexdigest(),'can_steer':plain,
                            'can_edit':plain and len(answers)<=1 and redact(text)==text,'can_delete':plain,'controls_reason':'' if plain else 'This message includes content that needs the Mac app.',
                            'editable_text':redact(answers[0]['answer'] if len(answers)==1 else text)[:8000],
                            'answer':bool(answers)})
        return pending

    def queued_content(self,content,thread):
        text=[];photos=[];supported=True
        if not isinstance(content,list):return '',[],False
        for part in content:
            if isinstance(part,dict) and part.get('type')=='text' and isinstance(part.get('text'),str):
                text.append(part['text']);supported=supported and not part.get('text_elements');continue
            photo=self.photos.for_input(part,thread) if self.photos and isinstance(part,dict) else None
            if photo:photos.append(photo)
            else:supported=False
        return '\n'.join(text),photos,supported

    def queue_item(self,thread_id,identity):
        with readonly_db(self.home/'queue_1.sqlite') as connection:
            row=connection.execute('SELECT id,payload_json FROM queued_items WHERE thread_id=? AND id=?',(thread_id,identity)).fetchone()
        if row is None:raise KeyError(identity)
        value=json.loads(row['payload_json'])['UserInput'];content=value['content']
        text,photo_refs,supported=self.queued_content(content,thread_id)
        if not content or not supported:
            raise ValueError('This queued message includes attachments that need the desktop.')
        client=value.get('client_id');photos=[str(self.photos.root/(photo['id']+'.jpg')) for photo in photo_refs]
        if len(photos)>PhotoStore.MAX_PHOTOS:raise ValueError('Too many photos.')
        if not isinstance(client,str) or not THREAD_ID.fullmatch(client) or len(text.strip())>8000 or not (text.strip() or photos):raise ValueError('Unsupported queued message.')
        return {'id':row['id'],'text':text,'photos':photos,'image_urls':[part['image_url'] for part in content if part.get('type')=='image'],'source_photos':[(part['type'],part.get('path') if part['type']=='localImage' else part.get('image_url')) for part in content if part['type'] in ('localImage','image')],'client_id':client,'revision':hashlib.sha256(row['payload_json'].encode()).hexdigest()}

    def has_client_receipt(self,thread_id,client_id):
        with readonly_db(self.history) as connection:
            rows=connection.execute("SELECT item_type,item_json FROM thread_items WHERE thread_id=? AND item_type IN ('userMessage','steeringUserMessage') AND item_json LIKE ?",(thread_id,'%'+client_id+'%')).fetchall()
        for kind,raw in rows:
            value=json.loads(raw)
            if kind=='userMessage' and value.get('clientId')==client_id or kind=='steeringUserMessage' and value.get('clientUserMessageId')==client_id and value.get('status')=='accepted':return True
        return False


class ControlError(Exception):
    def __init__(self, message, uncertain=False,retryable=False):
        super().__init__(message)
        self.uncertain = uncertain
        self.retryable=retryable


class Bridge:
    """Use the installed CLI's supported persistent input queue, without resuming."""

    def __init__(self, home, enabled=True, runner=subprocess.run):
        self.home = Path(home).resolve()
        self.active_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).resolve()
        self.enabled = enabled
        self.executable = shutil.which("codex")
        self.runner = runner
        self.queue_supported = False
        self.photos_supported = False
        if self.executable:
            try:
                result = runner([self.executable, "queue", "--help"], capture_output=True, text=True, timeout=3)
                self.queue_supported = result.returncode == 0 and "--thread" in result.stdout and "--message" in result.stdout
                self.photos_supported = self.queue_supported and '--image' in result.stdout
            except (OSError, subprocess.TimeoutExpired):
                pass

    def availability(self):
        if not self.enabled:
            return {"available": False, "reason": "Instructions are disabled in read-only mode."}
        if self.home != self.active_home:
            return {"available": False, "reason": "Instructions are disabled when viewing an alternate history directory."}
        if not self.executable or not self.queue_supported:
            return {"available": False, "reason": "This Codex CLI does not expose the supported message queue."}
        return {"available": True, "mode": "queue", "reason": "Instructions enter the existing chat's queue. They do not interrupt its current turn."}

    def describe(self, thread_id):
        return self.availability()

    def send(self, thread_id, message, photos=None):
        availability = self.availability()
        if not availability["available"]:
            raise ControlError(availability["reason"])
        if photos and not self.photos_supported:raise ControlError('This desktop CLI does not support photo attachments.')
        if photos:
            try:return queue_photo_message(thread_id,message,photos,self.executable)
            except RuntimeUnavailable as error:raise ControlError(str(error),uncertain=True) from error
        command=[self.executable,"queue","--thread",thread_id,"--message",message]
        try:
            result = self.runner(
                command,
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10,
            )
        except subprocess.TimeoutExpired as error:
            raise ControlError("Queue acknowledgement timed out. Check pending instructions before resending.", uncertain=True) from error
        except OSError as error:
            raise ControlError("The queue command could not be started.") from error
        match = re.search(r"Queued message ([A-Za-z0-9_-]+) for thread ([A-Za-z0-9_-]+)\.", result.stdout)
        if result.returncode != 0 or not match or match[2] != thread_id:
            raise ControlError("Queue acknowledgement could not be verified. Check the desktop before resending.", uncertain=True)
        return {"state": "queued", "queue_item_id": match[1], "message": "Queued for the existing desktop chat. Execution has not been confirmed."}


class CommandJournal:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lock = threading.Lock()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL)")
        self.path.chmod(0o600)

    def lookup(self, identity, payload=None):
        with self.lock, closing(sqlite3.connect(self.path)) as connection:
            row=connection.execute('SELECT fingerprint,result FROM commands WHERE id=?',(identity,)).fetchone()
            if row and payload is not None and row[0]!=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest():
                raise ValueError('This command ID was already used for a different instruction.')
            return json.loads(row[1]) if row else None

    def forget_deleted_answer(self, queue_id):
        # Only a confirmed deletion of its exact queue entry permits a new reply.
        with self.lock, closing(sqlite3.connect(self.path)) as connection, connection:
            for identity,raw in connection.execute("SELECT id,result FROM commands WHERE id LIKE 'answer-%'").fetchall():
                result=json.loads(raw)
                if result.get('state')=='queued' and result.get('queue_item_id')==queue_id:
                    connection.execute('DELETE FROM commands WHERE id=?',(identity,))

    def execute(self, command_id, payload, action):
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        with self.lock, closing(sqlite3.connect(self.path)) as connection, connection:
            row = connection.execute("SELECT fingerprint,result FROM commands WHERE id=?", (command_id,)).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise ValueError("This command ID was already used for a different instruction.")
                previous=json.loads(row[1])
                if not (previous.get('state')=='rejected' and previous.get('retryable') is True):return previous
            # Persist intent before sending. A crash or lost acknowledgement must never auto-resend.
            pending = {"state": "unknown", "message": "Delivery is unconfirmed. Check the desktop before resending."}
            connection.execute("INSERT OR REPLACE INTO commands VALUES (?,?,?)", (command_id, fingerprint, json.dumps(pending)))
            connection.commit()
            try:
                result = action()
            except ControlError as error:
                result = {"state": "unknown" if error.uncertain else "rejected", "message": str(error)}
                if error.retryable and not error.uncertain:result['retryable']=True
            connection.execute("UPDATE commands SET result=? WHERE id=?", (json.dumps(result), command_id))
            connection.commit()
            return result


class CompanionServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32

    def __init__(self, address, history, bridge, journal, access=None, remote=False, settings=None, gateway_port=None, inbox=None, account=None, runtime=None,photos=None,new_chat=None,media=None,read_cache=None,local_assistant=None,local_error=None):
        if remote and (access is None or settings is None):
            raise ValueError("The remote gateway requires authentication and explicit origin settings.")
        self.history = history
        self.bridge = bridge
        self.journal = journal
        self.csrf = secrets.token_urlsafe(32)
        self.access = access
        self.remote = remote
        self.settings = settings
        self.gateway_port = gateway_port
        self.inbox = inbox or Inbox(journal.path.with_name('inbox.sqlite'))
        self.account = account
        self.runtime = runtime
        self.reads = read_cache if read_cache is not None else ReadCache()
        self.read_executor = ThreadPoolExecutor(max_workers=4,thread_name_prefix='companion-read')
        self.photos=photos
        self.new_chat=new_chat
        self.media=media or MediaStore(journal.path.parent/'media')
        self.local_assistant=local_assistant
        self.local_error=local_error or 'Local chats are unavailable in this read-only viewer.'
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(address, Handler)

    def server_close(self):
        super().server_close()
        self.read_executor.shutdown(wait=False,cancel_futures=True)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    def finish_creation(self,result,payload,owner):
        """Creation is checkpointed separately from first delivery; never create twice."""
        if result.get('state')!='created' or 'message' not in payload:return result
        result=dict(result);thread_id=result['thread_id'];photos=payload.get('photos',[])
        prior=self.server.journal.lookup('initial-'+payload['command_id'])
        if prior:return {**result,'delivery':prior}
        try:verified=self.server.new_chat.verify_target(thread_id,payload['target_id'])
        except (OSError,sqlite3.Error,RuntimeUnavailable):
            return {**result,'delivery':{'state':'pending','message':'Chat created. Waiting to verify its saved project; your question has not been sent.'}}
        if not verified:
            return {**result,'delivery':{'state':'blocked','message':'The selected project could not be verified. Open this chat and check its project before sending.'}}
        result['project_verified']=True
        try:paths=self.server.photos.bind_draft(photos,'draft:'+payload['draft_id'],thread_id,owner) if photos else []
        except (PhotoError,OSError,sqlite3.Error):
            return {**result,'delivery':{'state':'blocked','message':'Chat created, but a draft photo is no longer available. Open this chat and attach the photo again; your question has not been sent.'}}
        live=self.server.runtime.capabilities(thread_id) if self.server.runtime else {}
        if not live.get('send_now'):
            return {**result,'delivery':{'state':'pending','message':'Chat created. Waiting for its desktop connection; your question and photos are kept.'}}
        message=payload['message'].strip() or 'Photo attached.'
        canonical={'thread_id':thread_id,'message':message,'photos':photos}
        def start():
            try:
                if photos:self.server.photos.submit(photos)
                return self.server.runtime.send_now(thread_id,message,photos=paths) if paths else self.server.runtime.send_now(thread_id,message)
            except RuntimeUnavailable as error:raise ControlError(str(error),uncertain=True) from error
        delivery=self.server.journal.execute('initial-'+payload['command_id'],canonical,start)
        return {**result,'delivery':delivery}

    def execution_state(self,thread):
        runtime=self.server.runtime
        if runtime and hasattr(runtime,'execution_state'):return runtime.execution_state(thread['id'])
        turn=thread.get('last_recorded_turn') or {}
        return {'status':'working' if turn.get('status')=='inProgress' else 'idle','turn_id':turn.get('turn_id')}

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *_):
        pass  # Conversation IDs, prompts and browser secrets never enter request logs.

    def invalidate_reads(self):
        if getattr(self,"_invalidate_reads",False):
            self.server.reads.clear()
            self._invalidate_reads=False

    def reply(self, code, value, mime="application/json", extra_headers=None):
        # Fence observational reads before the caller can consume a write reply.
        self.invalidate_reads()
        data = json.dumps(value).encode() if mime == "application/json" else value
        self.send_response(code)
        self.send_header("Content-Type", mime + ("; charset=utf-8" if mime.startswith('text/') or mime=='application/json' else ''))
        if code != 304: self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if self.server.remote:
            self.send_header("Strict-Transport-Security", "max-age=86400")
        for key, value in extra_headers or []:
            self.send_header(key, value)
        self.end_headers()
        if self.command != 'HEAD': self.wfile.write(data)

    def stream_media(self, identifier, role, version=None):
        try:
            with self.server.media.open_asset(identifier, role, version) as (stream, asset, info):
                size = info.st_size
                etag = '"' + asset['name'].split('.')[0] + '"'
                modified = formatdate(info.st_mtime, usegmt=True)
                headers = [('Accept-Ranges', 'bytes'), ('ETag', etag), ('Last-Modified', modified)]
                if self.headers.get('If-None-Match') in (etag, '*'):
                    return self.reply(304, b'', asset['mime'], headers)
                first, last, code = 0, size - 1, 200
                requested = self.headers.get('Range') if self.command == 'GET' else None
                if requested and self.headers.get('If-Range', etag) in (etag, modified):
                    try: first, last = byte_range(requested, size)
                    except ValueError:
                        return self.reply(416, b'', asset['mime'], headers + [('Content-Range', f'bytes */{size}')])
                    code = 206
                    headers.append(('Content-Range', f'bytes {first}-{last}/{size}'))
                self.send_response(code)
                self.send_header('Content-Type', asset['mime'])
                self.send_header('Content-Length', str(max(0, last - first + 1)))
                self.send_header('Cache-Control', 'private, no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('X-Frame-Options', 'DENY')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'; sandbox")
                if self.server.remote: self.send_header('Strict-Transport-Security', 'max-age=86400')
                if role == 'download':
                    self.send_header('Content-Disposition', f'attachment; filename="{identifier}{Path(asset["name"]).suffix}"')
                for name, value in headers: self.send_header(name, value)
                self.end_headers()
                if self.command == 'HEAD': return
                stream.seek(first)
                remaining = last - first + 1
                while remaining > 0:
                    chunk = stream.read(min(256 * 1024, remaining))
                    if not chunk: break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (FileNotFoundError, ValueError, KeyError):
            return self.reply(404, {'error': 'This media is unavailable. Reload the media page.'})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass  # A seek or closed player can cancel its previous stream.

    def do_HEAD(self):
        path = urlsplit(self.path).path
        if path in ('/media', '/media/', '/api/media') or path.startswith('/api/media/'):
            return self.do_GET()
        return self.reply(405, {'error': 'HEAD is available for media only.'})

    def valid_origin(self):
        host = self.headers.get("Host", "")
        origin = self.server.settings.origin() if self.server.settings else None
        expected = urlsplit(origin).netloc if self.server.remote and origin else f"127.0.0.1:{self.server.server_port}"
        if self.server.remote and (not origin or self.headers.get("X-Forwarded-Proto") != "https"):
            return False
        if host != expected:
            return False
        expected_origin = origin if self.server.remote else "http://" + expected
        request_origin = self.headers.get("Origin")
        if request_origin and request_origin != expected_origin:
            return False
        if self.server.remote and self.command == "POST" and request_origin != expected_origin:
            return False
        if self.command in ("GET", "HEAD") and urlsplit(self.path).path in STATIC_FILES:
            if self.headers.get("Sec-Fetch-Mode") == "navigate" and self.headers.get("Sec-Fetch-Dest") == "document":
                return True
        return self.headers.get("Sec-Fetch-Site", "none") in ("same-origin", "none")

    def session(self):
        if not self.server.remote:
            return None
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            token = cookie[COOKIE_NAME].value if COOKIE_NAME in cookie else None
            return self.server.access.authenticate(token)
        except (CookieError, ValueError):
            return None

    def read_payload(self):
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= (PhotoStore.MAX_BYTES*4//3+1000 if self.path=='/api/photos' else 40000):
            raise ValueError("Invalid request size.")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("Expected an object.")
        return value

    def do_GET(self):
        if self.path == "/healthz" and self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}":
            return self.reply(200, {"ok": True, "authentication_required": self.server.remote, "gateway_version": 1})
        if not self.valid_origin():
            return self.reply(403, {"error": "This origin is not enabled."})
        parsed = urlsplit(self.path)
        try:
            static = STATIC_FILES
            if parsed.path in static:
                filename, mime = static[parsed.path]
                data=(ROOT/'web'/filename).read_bytes()
                if filename in ('index.html','media.html','local.html'):data=data.replace(b'__BUILD__',web_build().encode())
                return self.reply(200,data,mime)
            session = self.session()
            if parsed.path == "/api/auth/status":
                return self.reply(200, {"remote": self.server.remote, "authenticated": bool(session) if self.server.remote else True})
            if self.server.remote and not session:
                return self.reply(401, {"error": "Pair this browser before viewing conversations.", "auth_required": True})
            if parsed.path.startswith('/api/local/'):
                assistant=self.server.local_assistant
                if parsed.path=='/api/local/status':
                    return self.reply(200,assistant.status() if assistant else {'enabled':False,'ready':False,'models':[],'skills':[],'api_profiles':[],'workspaces':[],'busy':False,'error':self.server.local_error})
                if not assistant:
                    if parsed.path=='/api/local/chats':return self.reply(200,{'chats':[]})
                    return self.reply(409,{'error':self.server.local_error})
                if parsed.path=='/api/local/chats':return self.reply(200,assistant.chats())
                if parsed.path=='/api/local/fork-preview':
                    identities=parse_qs(parsed.query).get('source_chat_id',[])
                    if len(identities)!=1:raise LocalError('Choose one desktop chat to preview.')
                    return self.reply(200,assistant.fork_preview(self.server.history.local_fork_snapshot(identities[0])))
                if parsed.path=='/api/local/chat':
                    identities=parse_qs(parsed.query).get('id',[])
                    if len(identities)!=1:raise LocalError('Choose one local chat.')
                    return self.reply(200,assistant.detail(identities[0]))
                return self.reply(404,{'error':'Unknown local route.'})
            if parsed.path == '/api/media':
                return self.reply(200, {'items': self.server.media.catalog()})
            if parsed.path.startswith('/api/media/'):
                match = re.fullmatch(r'/api/media/([a-z0-9][a-z0-9-]{0,79})/(video|poster|captions|download)', parsed.path)
                if not match: return self.reply(404, {'error': 'Media unavailable.'})
                versions = parse_qs(parsed.query).get('v', [])
                if len(versions) > 1: return self.reply(404, {'error': 'Media unavailable.'})
                return self.stream_media(*match.groups(), versions[0] if versions else None)
            if parsed.path=='/api/photos/storage' and self.server.photos:
                return self.reply(200,self.server.photos.storage(session['id'] if session else 'local'))
            if parsed.path.startswith('/api/photos/') and self.server.photos:
                try:return self.reply(200,self.server.photos.read(parsed.path.removeprefix('/api/photos/'),session['id'] if session else 'local'),'image/jpeg')
                except (KeyError,FileNotFoundError):return self.reply(404,{'error':'Photo unavailable.'})
            if parsed.path == '/api/ready':
                return self.reply(200, {'mode':'remote' if self.server.remote else 'local_preview',
                                       'phone_setup_available':bool(self.server.access) and not self.server.remote,
                                       'control':self.server.bridge.availability()})
            if parsed.path == '/api/control/status' and not self.server.remote:
                from control import read_snapshot
                snapshot=self.server.reads.get('control',lambda:read_snapshot(ROOT),ttl=1)
                return self.reply(200, {'ok':True,'snapshot':snapshot})
            if parsed.path == "/api/status":
                origin = self.server.settings.origin() if self.server.settings else None
                account=self.server.account.snapshot() if self.server.account else {'usage':{'available':False,'weekly':[],'checked_at':None,'stale':False,'reason':'Account usage is unavailable in this viewer.'},'models':[]}
                execution={}
                if self.server.runtime and hasattr(self.server.runtime,'execution_state'):
                    candidates=self.server.history.active_candidates()
                    def read_execution(identifier):
                        return self.server.reads.get(('execution',identifier),lambda:self.server.runtime.execution_state(identifier),ttl=.75)
                    execution=dict(zip(candidates,self.server.read_executor.map(read_execution,candidates)))
                return self.reply(200, {"build":web_build(),"execution":execution,"mode": "remote" if self.server.remote else "local_preview", "remote_access": self.server.remote, "public_origin": origin, "phone_setup_available": bool(self.server.access) and not self.server.remote, "control": self.server.bridge.availability(), "csrf": session["csrf"] if session else self.server.csrf, "power":cached_power_state(), "computer_use":computer_use_snapshot(ROOT/'.state'/'computer-use-check.json'), **account, "snapshot_at": int(time.time() * 1000)})
            if parsed.path=='/api/components':
                from control import read_snapshot
                snapshot=self.server.reads.get('control',lambda:read_snapshot(ROOT),ttl=1)
                return self.reply(200,{key:snapshot[key] for key in ('components','summary','tunnel','checked_at')})
            if parsed.path == "/api/devices" and not self.server.remote and self.server.access:
                return self.reply(200, {"devices": self.server.access.devices()})
            if parsed.path == "/api/remote/status" and not self.server.remote:
                return self.reply(200, {"gateway_port": self.server.gateway_port, "gateway_version": 1, "authentication_required": True, "public_origin": self.server.settings.origin() if self.server.settings else None})
            if parsed.path == "/api/chats":
                before=self.server.history.catalog_revision(self.server.inbox)
                if self.headers.get('If-None-Match')=='"'+before+'"':
                    return self.reply(304,b'',"text/plain",extra_headers=[('ETag','"'+before+'"')])
                chats=self.server.history.list_threads(include_archived=True,include_children=True)
                chats=self.server.inbox.decorate(chats)
                projects=self.server.history.list_projects(chats)
                after=self.server.history.catalog_revision(self.server.inbox)
                # If a write raced the snapshot, don't tag it as the newer data.
                revision=after if before==after else None
                return self.reply(200, {"chats":chats,"projects":projects,"revision":revision,"scope":"Local desktop history; cloud-only chats and other hosts are not included."},extra_headers=[('ETag','"'+revision+'"')] if revision else [])
            if parsed.path == '/api/chat-targets':
                available=bool(self.server.new_chat and self.server.bridge.availability()['available'])
                targets=self.server.new_chat.targets() if available else []
                for target in targets:target['name']=redact(target['name'])
                return self.reply(200,{'available':available,'targets':targets})
            if parsed.path=='/api/chats/creation':
                identity=parse_qs(parsed.query).get('id',[''])[0]
                if not re.fullmatch(r'[A-Za-z0-9_-]{16,100}',identity):raise ValueError('Invalid creation request.')
                result=self.server.journal.lookup(identity)
                if not result or result.get('state')!='created':return self.reply(200,{'state':'unknown','message':'Check Recent for the previous chat before starting another.'})
                return self.reply(200,{**result,'delivery':self.server.journal.lookup('initial-'+identity)})
            if parsed.path == "/api/chat":
                query = parse_qs(parsed.query)
                thread_id = query.get("id", [""])[0]
                cursor = query.get("before",[None])[0]
                if cursor is not None and not re.fullmatch(r"[1-9][0-9]{0,18}",cursor):
                    raise ValueError("Invalid history cursor.")
                value = self.server.history.read_thread(thread_id,before=int(cursor) if cursor is not None else None)
                value['queue_recovery']=self.server.runtime.queue_recovery(thread_id,redact) if self.server.runtime and hasattr(self.server.runtime,'queue_recovery') else []
                for question in value['questions']:
                    attempt=self.server.journal.lookup(answer_identity(thread_id,question))
                    if attempt and question['delivery']!='queued':
                        question['delivery']=attempt['state']
                value["control"] = self.server.bridge.describe(thread_id)
                value['control']['live']=self.server.runtime.capabilities(thread_id) if self.server.runtime and value['control']['available'] and not value['archived'] else {'steer':False,'model_selection':False,'reason':SharedRuntime.REASON}
                active_questions=value['control']['live'].get('execution',{}).get('async_question_ids')
                if isinstance(active_questions,list):value['questions']=[q for q in value['questions'] if q['id'] in active_questions]
                for approval in value['control']['live'].get('execution',{}).get('approvals',[]):
                    attempt=self.server.journal.lookup('approval-'+approval_digest([thread_id,approval['id'],approval['revision']]))
                    if attempt:approval['response_state']=attempt['state'];approval['response_message']=attempt['message']
                if value["archived"]:
                    value["control"]={"available":False,"reason":"Archived conversation. Restore it in the desktop app before queueing a follow-up."}
                # Revalidate live controls on every read, but avoid re-sending an unchanged transcript.
                witness={**value};witness.pop('snapshot_at',None)
                control={**value['control']};live={**control.get('live',{})};execution={**live.get('execution',{})}
                checked=execution.pop('checked_at',0);live['execution']=execution;control['live']=live;witness['control']=control
                revision='W/"'+hashlib.sha256(json.dumps(witness,sort_keys=True).encode()).hexdigest()+'"'
                headers=[('ETag',revision),('X-Companion-Execution-Checked-At',str(checked))]
                if self.headers.get('If-None-Match')==revision:return self.reply(304,b'',"text/plain",extra_headers=headers)
                return self.reply(200, value,extra_headers=headers)
            return self.reply(404, {"error": "Not found."})
        except LocalError as error:
            return self.reply(400,{'error':str(error)})
        except KeyError:
            return self.reply(404, {"error": "Chat not found."})
        except (ValueError,TypeError):
            return self.reply(400, {"error": "Invalid conversation history request."})
        except (sqlite3.Error, OSError):
            return self.reply(503, {"error": "Local history is unavailable or its schema has changed. No data was modified."})

    def do_POST(self):
        if not self.valid_origin():
            return self.reply(403, {"error": "This origin is not enabled."})
        self._invalidate_reads=False
        try:
            if self.path == "/api/auth/login" and self.server.remote:
                payload = self.read_payload()
                try:
                    token, _ = self.server.access.exchange(payload.get("code"), payload.get("name"))
                except PairingError as error:
                    return self.reply(429 if error.limited else 401, {"error": str(error)}, extra_headers=[("Retry-After", "300")] if error.limited else [])
                cookie = f"{COOKIE_NAME}={token}; Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_SECONDS}"
                return self.reply(200, {"authenticated": True}, extra_headers=[("Set-Cookie", cookie)])
            session = self.session()
            if self.server.remote and not session:
                return self.reply(401, {"error": "Pair this browser before sending instructions.", "auth_required": True})
            csrf = session["csrf"] if session else self.server.csrf
            if not secrets.compare_digest(self.headers.get("X-Companion-CSRF", ""), csrf):
                return self.reply(403, {"error": "Refresh this page before making changes."})
            self._invalidate_reads=True
            client_build=self.headers.get('X-Companion-UI')
            if client_build and client_build!=web_build():
                return self.reply(409,{'error':'Companion was updated. Reload this tab before sending. Your instruction has not been submitted.','update_required':True})
            if self.path.startswith('/api/local/'):
                if not self.server.local_assistant:return self.reply(409,{'error':'Local chats are unavailable in this viewer.'})
                payload=self.read_payload()
                if self.path=='/api/local/chats':return self.reply(200,self.server.local_assistant.create(payload))
                if self.path=='/api/local/fork':
                    return self.reply(200,self.server.local_assistant.fork(payload,lambda:self.server.history.local_fork_snapshot(payload.get('source_chat_id'))))
                if self.path=='/api/local/message':return self.reply(202,self.server.local_assistant.send(payload))
                if self.path=='/api/local/archive':return self.reply(200,self.server.local_assistant.archive(payload))
                return self.reply(404,{'error':'Unknown local route.'})
            if self.path=='/api/chat/connect':
                payload=self.read_payload();thread=self.server.history.read_thread(payload['thread_id'])
                if thread['archived'] or not self.server.new_chat or not self.server.bridge.availability()['available']:
                    return self.reply(409,{'error':'Restore this chat or reconnect the desktop service first.'})
                try:self.server.new_chat.opener(thread['id'])
                except (OSError,subprocess.SubprocessError):return self.reply(503,{'error':'The desktop app could not open this chat. Your messages have not been sent.'})
                return self.reply(200,{'state':'opening','message':'Opening this chat in the desktop app. Live controls will reconnect as it loads.'})
            if self.path=='/api/photos/drafts/remove':
                if not self.server.photos or not self.server.bridge.availability()['available']:raise PhotoError('Photo changes are unavailable in this viewer.')
                payload=self.read_payload()
                self.server.photos.remove_draft(payload.get('photo_id'),session['id'] if session else 'local')
                return self.reply(200,{'removed':True})
            if self.path == '/api/chats/create':
                if not self.server.new_chat or not self.server.bridge.availability()['available']:
                    return self.reply(409,{'error':'New chat is unavailable in this viewer.'})
                payload=self.read_payload()
                if set(payload)-{'command_id','target_id','title','message','photos','draft_id'}:raise ValueError('Unexpected creation fields.')
                identity=payload.get('command_id');target_id=payload.get('target_id');title=payload.get('title','')
                if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,100}',identity) or not isinstance(target_id,str):
                    raise ValueError('Invalid creation request.')
                owner=session['id'] if session else 'local'
                canonical={'operation':'create_chat','target_id':target_id,'title':title}
                if 'message' in payload:
                    message=payload['message'];photos=payload.get('photos',[]);draft_id=payload.get('draft_id')
                    if not isinstance(message,str) or len(message)>8000 or not message.strip() and not photos or title or not isinstance(draft_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,100}',draft_id):raise ValueError('Write a question or attach a photo to start this chat.')
                    if not isinstance(photos,list) or len(photos)>PhotoStore.MAX_PHOTOS or any(not isinstance(i,str) or not re.fullmatch(r'[a-f0-9]{32}',i) for i in photos):raise ValueError('Invalid draft photos.')
                    if photos:
                        if not self.server.photos:raise ValueError('Photos are unavailable.')
                        previous=self.server.journal.lookup(identity)
                        if previous is None:self.server.photos.resolve(photos,'draft:'+draft_id,owner)
                    title=prompt_title(redact(message),bool(photos))
                    canonical.update(title=title,message=message,photos=photos,draft_id=draft_id,owner=owner)
                def create_chat():
                    try:return self.server.new_chat.create(target_id,title)
                    except ValueError as error:raise ControlError(str(error)) from error
                    except RuntimeUnavailable as error:raise ControlError(str(error),uncertain=True) from error
                result=self.server.journal.execute(identity,canonical,create_chat)
                result=self.finish_creation(result,payload,owner)
                return self.reply(200 if result['state']=='created' else 409,result)
            if self.path=='/api/chat/rename':
                payload=self.read_payload();thread=self.server.history.read_thread(payload['thread_id'])
                if set(payload)-{'command_id','thread_id','title'}:raise ValueError('Unexpected rename fields.')
                identity=payload.get('command_id')
                if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,100}',identity):raise ValueError('Invalid request ID.')
                if not self.server.new_chat or not self.server.bridge.availability()['available']:return self.reply(409,{'error':'Renaming is unavailable in this viewer.'})
                def rename():
                    try:return self.server.new_chat.rename(thread['id'],payload.get('title'))
                    except ValueError as error:raise ControlError(str(error)) from error
                    except RuntimeUnavailable as error:raise ControlError(str(error),uncertain=True) from error
                result=self.server.journal.execute(identity,{'operation':'rename',**payload},rename)
                return self.reply(200 if result['state']=='updated' else 409,result)
            if self.path in ('/api/photos','/api/photos/remove'):
                if not self.server.photos or not self.server.bridge.availability()['available']:return self.reply(409,{'error':'Photo attachments are unavailable in this viewer.'})
                payload=self.read_payload();owner=session['id'] if session else 'local'
                if 'draft_id' in payload:
                    identity=payload['draft_id']
                    if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,100}',identity) or 'thread_id' in payload:raise ValueError('Invalid photo draft.')
                    scope='draft:'+identity
                else:
                    thread=self.server.history.read_thread(payload['thread_id']);scope=thread['id']
                    if thread['archived']:return self.reply(409,{'error':'Restore this chat before adding a photo.'})
                if self.path.endswith('/remove'):
                    self.server.photos.remove(payload.get('photo_id'),scope,owner)
                    return self.reply(200,{'removed':True})
                return self.reply(200,self.server.photos.upload(scope,owner,payload.get('data_url'),payload.get('name')))
            if self.path == "/api/auth/logout" and self.server.remote:
                self.server.access.revoke(session["id"])
                return self.reply(200, {"authenticated": False}, extra_headers=[("Set-Cookie", f"{COOKIE_NAME}=; Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")])
            if self.path == "/api/pairing" and not self.server.remote and self.server.access:
                return self.reply(200, self.server.access.create_pairing())
            if self.path == "/api/pairing/cancel" and not self.server.remote and self.server.access:
                self.server.access.cancel_pairing()
                return self.reply(200, {"cancelled": True})
            if self.path == "/api/devices/revoke" and not self.server.remote and self.server.access:
                payload = self.read_payload()
                device_id = payload.get("device_id")
                if not isinstance(device_id, str) or not re.fullmatch(r"[a-f0-9]{24}", device_id):
                    raise ValueError("Invalid device.")
                self.server.access.revoke(device_id)
                return self.reply(200, {"revoked": True})
            if self.path == '/api/read-state':
                payload=self.read_payload()
                revision=payload.get('revision')
                if payload.get('operation') not in ('read','unread'):raise ValueError('Invalid read operation.')
                accepted=self.server.inbox.mark_current(payload['thread_id'],revision,lambda:self.server.history.read_thread(payload['thread_id'])['message_revision'],payload['operation']=='unread')
                if not accepted:
                    return self.reply(409,{'error':'This conversation has newer messages. Refresh before marking it read.'})
                return self.reply(200,{'updated':True})
            if self.path == '/api/approvals/respond':
                payload=self.read_payload()
                thread_id=payload.get('thread_id');identity=payload.get('approval_id');revision=payload.get('revision');choice=payload.get('choice');answers=payload.get('answers',{})
                if not isinstance(thread_id,str) or not THREAD_ID.fullmatch(thread_id) or any(not isinstance(v,str) or not re.fullmatch(r'[a-f0-9]{64}',v) for v in (identity,revision)) or choice not in ('allow','session','decline','cancel','reply') or not isinstance(answers,dict):raise ValueError('Invalid approval response.')
                if set(payload)-{'thread_id','approval_id','revision','choice','answers'}:raise ValueError('Unexpected approval fields.')
                if not self.server.bridge.availability()['available']:return self.reply(409,{'error':'Approval responses are disabled in this viewer.'})
                if not self.server.runtime or not hasattr(self.server.runtime,'respond_approval'):return self.reply(409,{'error':'Live approvals are disconnected. Open this chat on your Mac.'})
                canonical={'thread_id':thread_id,'approval_id':identity,'revision':revision,'choice':choice,'answers':answers}
                def respond():
                    try:return self.server.runtime.respond_approval(thread_id,identity,revision,choice,answers)
                    except ApprovalChanged as error:raise ControlError(str(error)) from error
                    except (RuntimeUnavailable,OSError,ValueError,KeyError) as error:raise ControlError('Approval delivery is unconfirmed. Check the prompt on your Mac; this response will not be resent automatically.',uncertain=True) from error
                try:result=self.server.journal.execute('approval-'+approval_digest([thread_id,identity,revision]),canonical,respond)
                except ValueError:return self.reply(409,{'error':'A response was already submitted for this request. Refresh and check its status.'})
                return self.reply(200 if result['state']=='answered' else 409,result)
            if self.path == '/api/questions/answer':
                payload=self.read_payload()
                thread_id=payload.get('thread_id');question_id=payload.get('question_id');revision=payload.get('revision');answer=payload.get('answer')
                if not isinstance(thread_id,str) or not THREAD_ID.fullmatch(thread_id) or not isinstance(question_id,str) or len(question_id)>500 or not isinstance(revision,str) or not re.fullmatch(r'[a-f0-9]{64}',revision):
                    raise ValueError('Invalid question.')
                if not isinstance(answer,str) or not 1<=len(answer.strip())<=4000:raise ValueError('Invalid answer.')
                canonical={'thread_id':thread_id,'question_id':question_id,'revision':revision,'answer':answer.strip()}
                identity=answer_identity(thread_id,{'id':question_id,'revision':revision})
                # The question identity deduplicates replies across devices and service restarts.
                if not self.server.bridge.availability()['available']:
                    return self.reply(409,{'error':self.server.bridge.availability()['reason']})
                def send_answer():
                    thread=self.server.history.read_thread(thread_id)
                    question=next((q for q in thread['questions'] if q['id']==question_id and q['revision']==revision),None)
                    if thread['archived'] or question is None:
                        raise ControlError('This question was answered, changed, or archived. Refresh before replying.')
                    if question['delivery']=='queued':
                        raise ControlError('A reply to this question is already in the desktop queue. Check the chat before replying again.')
                    envelope=answer_envelope(question,canonical['answer'])
                    live=self.server.runtime.capabilities(thread_id) if self.server.runtime else {}
                    active_questions=live.get('execution',{}).get('async_question_ids')
                    if isinstance(active_questions,list) and question_id not in active_questions:
                        raise ControlError('This question is no longer open on your Mac. Refresh the chat; no reply was sent.')
                    try:
                        if live.get('steer'):
                            result=self.server.runtime.steer(thread_id,envelope,(live.get('execution') or thread['last_recorded_turn'])['turn_id'])
                            result['message']='Your answer was accepted by the active desktop turn.'
                        elif live.get('send_now'):
                            result=self.server.runtime.send_now(thread_id,envelope)
                            result['message']='Your answer was sent and the next desktop turn started.'
                        else:
                            result=self.server.bridge.send(thread_id,envelope)
                            result['message']='Reply queued. Direct delivery to this active turn is unavailable; it may wait until the turn finishes.'
                    except QueueNotClaimed as error:raise ControlError(str(error),retryable=True) from error
                    except RuntimeUnavailable as error:raise ControlError(str(error),uncertain=True) from error
                    return result
                try:result=self.server.journal.execute(identity,canonical,send_answer)
                except ValueError:return self.reply(409,{'error':'A different reply was already submitted for this question. Check the chat before sending again.'})
                return self.reply(200 if result['state'] in ('queued','steered','sent') else 409,result)
            if self.path in ('/api/queue/edit','/api/queue/delete'):
                payload=self.read_payload();thread=self.server.history.read_thread(payload['thread_id']);identity=payload.get('queue_id');revision=payload.get('revision');request_id=payload.get('command_id')
                if not isinstance(identity,str) or not THREAD_ID.fullmatch(identity) or not isinstance(revision,str) or not re.fullmatch(r'[a-f0-9]{64}',revision) or not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,100}',request_id):raise ValueError('Invalid queue request.')
                if thread['archived'] or not self.server.bridge.availability()['available']:return self.reply(409,{'error':'Queue changes are unavailable for this chat.'})
                editing=self.path.endswith('/edit');message=payload.get('message')
                if editing and (not isinstance(message,str) or len(message.strip())>8000):raise ValueError('Keep the message under 8,000 characters.')
                def change_queue():
                    try:
                        item=self.server.history.queue_item(thread['id'],identity)
                        if item['revision']!=revision:raise ControlError('This queued message changed. Refresh before editing or deleting it.',retryable=True)
                        updated=message.strip() if editing else None
                        if editing:
                            if not updated and not item.get('photos'):raise ControlError('Add a message before saving.')
                            answers=question_replies(item['text'])
                            if len(answers)>1:raise ControlError('Edit this multi-question reply on your Mac.')
                            if answers:
                                if len(updated)>4000:raise ControlError('Keep your answer under 4,000 characters.')
                                updated=answer_envelope({'id':answers[0]['questionItemId'],'title':answers[0]['question']},updated)
                        return change_queued_message(thread['id'],item,updated,executable=self.server.bridge.executable,recovery=getattr(self.server.runtime,'promotions',None))
                    except KeyError:raise ControlError('This message is no longer queued. Refresh the chat.')
                    except QueueNotClaimed as error:raise ControlError(str(error),retryable=True) from error
                    except RuntimeUnavailable as error:raise ControlError(str(error),uncertain=True) from error
                result=self.server.journal.execute(request_id,{'operation':self.path,**payload},change_queue)
                if result['state']=='deleted':self.server.journal.forget_deleted_answer(identity)
                return self.reply(200 if result['state'] in ('updated','deleted') else 409,result)
            if self.path == '/api/queue/steer':
                payload=self.read_payload();thread=self.server.history.read_thread(payload['thread_id']);identity=payload.get('queue_id');revision=payload.get('revision');expected=payload.get('expected_turn_id')
                if not isinstance(identity,str) or not THREAD_ID.fullmatch(identity) or not isinstance(revision,str) or not re.fullmatch(r'[a-f0-9]{64}',revision):raise ValueError('Invalid queued message.')
                if not self.server.runtime or not hasattr(self.server.runtime,'promote_queued') or thread['archived'] or not self.server.bridge.availability()['available']:
                    return self.reply(409,{'error':'Live queue promotion is unavailable for this chat.'})
                turn=thread.get('last_recorded_turn') or {}
                if not isinstance(expected,str) or not THREAD_ID.fullmatch(expected):raise ValueError('Refresh to obtain the live active turn.')
                request_id='promote-'+hashlib.sha256((thread['id']+identity+revision).encode()).hexdigest()
                canonical={'thread_id':thread['id'],'queue_id':identity,'revision':revision}
                def promote():
                    try:
                        item=self.server.history.queue_item(thread['id'],identity)
                        if item['revision']!=revision:raise ControlError('This queued message was edited. Refresh before steering it.')
                        return self.server.runtime.promote_queued(thread['id'],item,expected)
                    except KeyError:raise ControlError('This message is no longer queued. Check the conversation.')
                    except QueueNotClaimed as error:raise ControlError(str(error),retryable=True) from error
                    except RuntimeUnavailable as error:raise ControlError(str(error),uncertain=True) from error
                result=self.server.journal.execute(request_id,canonical,promote)
                return self.reply(200 if result['state']=='steered' else 409,result)
            if self.path == '/api/queue/recovery/dismiss':
                payload=self.read_payload();thread=self.server.history.read_thread(payload['thread_id']);identity=payload.get('queue_id')
                if not isinstance(identity,str) or not THREAD_ID.fullmatch(identity):raise ValueError('Invalid recovery item.')
                if not self.server.runtime or not getattr(self.server.runtime,'promotions',None):return self.reply(409,{'error':'Recovery controls are unavailable.'})
                self.server.runtime.promotions.dismiss(thread['id'],identity)
                return self.reply(200,{'dismissed':True})
            if self.path == '/api/model':
                payload=self.read_payload();thread=self.server.history.read_thread(payload['thread_id'])
                if thread['archived'] or not self.server.runtime or not self.server.bridge.availability()['available']:
                    return self.reply(409,{'error':SharedRuntime.REASON})
                models=self.server.account.snapshot()['models'] if self.server.account else []
                choice=next((model for model in models if model['id']==payload.get('model')),None)
                effort=payload.get('effort')
                if not choice or effort is not None and effort not in choice['efforts']:raise ValueError('Unknown model or reasoning effort.')
                identity=payload.get('command_id')
                if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,100}',identity):raise ValueError('Invalid request ID.')
                def update_model():
                    try:return self.server.runtime.set_model(thread['id'],choice['id'],effort)
                    except RuntimeUnavailable as error:raise ControlError(str(error),uncertain=True) from error
                result=self.server.journal.execute(identity,payload,update_model)
                return self.reply(200 if result['state']=='updated' else 409,result)
            if self.path != "/api/command":
                return self.reply(404, {"error": "Not found."})
            payload = self.read_payload()
            thread_id = payload["thread_id"]
            message = payload["message"]
            command_id = payload["command_id"]
            if not isinstance(thread_id, str) or not THREAD_ID.fullmatch(thread_id):
                raise ValueError("Invalid chat ID.")
            if not isinstance(command_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,100}", command_id):
                raise ValueError("Invalid command ID.")
            photo_ids=payload.get('photos',[])
            if not isinstance(message, str) or len(message.strip())>8000 or not message.strip() and not photo_ids:
                raise ValueError("Add a message or a photo before sending.")
            if photo_ids and not self.server.photos:raise ValueError('Photo attachments are unavailable.')
            # Recover an exact durable acknowledgement before checking mutable turn/photo state.
            # A lost HTTP response must not turn a previously accepted send into a fresh send.
            previous=self.server.journal.lookup(command_id,payload)
            if previous and not (previous.get('state')=='rejected' and previous.get('retryable') is True):
                return self.reply(200 if previous['state'] in ('queued','steered','sent') else 409,previous)
            photo_paths=self.server.photos.resolve(photo_ids,thread_id,session['id'] if session else 'local') if photo_ids else []
            message=message.strip() or 'Photo attached.'
            thread=self.server.history.read_thread(thread_id)
            if thread["archived"]:
                return self.reply(409,{"state":"rejected","message":"Restore this archived chat in the desktop app before queueing instructions."})
            mode=payload.get('mode','queue')
            if mode not in ('queue','steer','send'):raise ValueError('Invalid instruction mode.')
            if mode=='steer':
                turn=thread.get('last_recorded_turn') or {}
                if not self.server.runtime or not self.server.bridge.availability()['available']:
                    return self.reply(409,{'error':SharedRuntime.REASON})
                expected=payload.get('expected_turn_id')
                if not isinstance(expected,str) or not THREAD_ID.fullmatch(expected):
                    return self.reply(409,{'error':'The active turn changed. Refresh before steering.'})
                execution=self.execution_state(thread)
                if execution['status']!='working' or execution['turn_id']!=expected:
                    return self.reply(409,{'error':'The desktop turn changed or ended. Refresh to use its current controls. Your draft has not been sent.'})
                def action():
                    try:return self.server.runtime.steer(thread_id,message,expected,photos=photo_paths) if photo_paths else self.server.runtime.steer(thread_id,message,expected)
                    except QueueNotClaimed as error:raise ControlError(str(error),retryable=True) from error
                    except RuntimeUnavailable as error:raise ControlError(str(error),uncertain=True) from error
            elif mode=='send':
                if not self.server.runtime or not self.server.bridge.availability()['available']:
                    return self.reply(409,{'error':DesktopRuntime.REASON})
                if self.execution_state(thread)['status']!='idle':return self.reply(409,{'error':'This chat is now working. Refresh and choose Queue or Steer.'})
                def action():
                    try:return self.server.runtime.send_now(thread_id,message,photos=photo_paths) if photo_paths else self.server.runtime.send_now(thread_id,message)
                    except RuntimeUnavailable as error:raise ControlError(str(error),uncertain=True) from error
            else:action=lambda:self.server.bridge.send(thread_id,message,photos=photo_paths) if photo_paths else self.server.bridge.send(thread_id,message)
            if photo_ids:
                original_action=action
                def action():
                    # Retain images even after an ambiguous submission; deleting them could break an accepted turn.
                    self.server.photos.submit(photo_ids)
                    return original_action()
            result = self.server.journal.execute(command_id, payload, action)
            return self.reply(200 if result["state"] in ("queued","steered","sent") else 409, result)
        except LocalError as error:
            return self.reply(400,{'error':str(error)})
        except PhotoError as error:
            return self.reply(400,{'error':str(error)})
        except KeyError:
            return self.reply(400, {"error": "Missing fields or unknown chat."})
        except (ValueError, TypeError, AttributeError):
            return self.reply(400, {"error": "Invalid command request."})
        except (sqlite3.Error, OSError):
            return self.reply(503, {"error": "The local command service is unavailable. Check the desktop before resending."})
        finally:
            self.invalidate_reads()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")))
    parser.add_argument("--read-only", action="store_true", help="Disable queueing instructions")
    parser.add_argument("--remote-port", type=int, default=8766, help="Separate authenticated gateway, still bound to loopback")
    args = parser.parse_args()
    photos=PhotoStore(ROOT/'.state'/'photos')
    history = History(args.codex_home,photos)
    bridge = Bridge(args.codex_home, not args.read_only)
    journal = CommandJournal(ROOT / ".state" / "commands.sqlite")
    access = AccessStore(ROOT / ".state" / "access.sqlite")
    access.cancel_pairing()
    settings = RemoteSettings(ROOT / ".state" / "remote.json")
    initialize_remote_origin(settings,ROOT/'.state',load_configuration())
    inbox=Inbox(ROOT/'.state'/'inbox.sqlite')
    account=AccountMonitor()
    runtime=DesktopRuntime(args.codex_home,history.read_thread,queue_reader=history.queue_item,receipt_reader=history.has_client_receipt,promotions=QueuePromotions(ROOT/'.state'/'promotions.sqlite'),approval_redactor=redact)
    new_chat=NewChat(args.codex_home,PERSONAL_WORKSPACE)
    account.snapshot()
    reads = ReadCache()
    local=None;local_error='Local chats are unavailable in this read-only viewer.'
    if not args.read_only and bridge.home==bridge.active_home:
        try:
            if LocalSettings(ROOT).load()['enabled']:local=LocalAssistant(ROOT,redact=redact)
            else:local_error='Local assistant is disabled. Follow LOCAL_ASSISTANT.md to enable it on this Mac.'
        except (ValueError,TypeError,AttributeError,OSError,sqlite3.Error):
            local_error='Local assistant settings or storage need repair on this Mac. Existing desktop chats remain available.'
    gateway = CompanionServer(("127.0.0.1", args.remote_port), history, bridge, journal, access=access, remote=True, settings=settings, inbox=inbox, account=account, runtime=runtime,photos=photos,new_chat=new_chat,read_cache=reads,local_assistant=local,local_error=local_error)
    server = CompanionServer(("127.0.0.1", args.port), history, bridge, journal, access=access, settings=settings, gateway_port=gateway.server_port, inbox=inbox, account=account, runtime=runtime,photos=photos,new_chat=new_chat,read_cache=reads,local_assistant=local,local_error=local_error)
    worker = threading.Thread(target=gateway.serve_forever, daemon=True)
    worker.start()
    print(f"Desktop Companion: http://127.0.0.1:{server.server_port}", flush=True)
    print("Phone gateway is locked on loopback. No tunnel or agent process is started.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if local:local.close()
        server.server_close()
        gateway.shutdown()
        gateway.server_close()


if __name__ == "__main__":
    main()
