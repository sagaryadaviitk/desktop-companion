"""Public conversation messages which have not reached the desktop history index."""
from collections import deque
from datetime import datetime
import json
import os
from pathlib import Path
import stat

from read_cache import ReadCache


def public_item(item, stamp, public_text):
    kind = item.get('type')
    if kind == 'steeringUserMessage':
        if item.get('status') != 'accepted':
            return None
        item = {**item, 'content': item.get('input') or [], 'clientId': item.get('clientUserMessageId')}
        kind = 'userMessage'
    identity = item.get('id')
    if item.get('type') == 'steeringUserMessage':
        identity = item.get('serverUserMessageId') or identity
    if kind not in ('userMessage', 'agentMessage') or not isinstance(identity, str) or not identity:
        return None
    text = public_text(kind, item)
    if not text.strip():
        return None
    return {'id': identity, 'role': 'user' if kind == 'userMessage' else 'assistant',
            'phase': item.get('phase') or '', 'text': text, 'timestamp': stamp,
            'photos': [], 'client_id': item.get('clientId') if kind == 'userMessage' and isinstance(item.get('clientId'), str) else None}


def merge_tail(messages, tail):
    """Keep SQLite ordinals authoritative. Match streamed items by stable identity."""
    if not tail:
        return
    indexed = [m for m in messages if not m.get('unindexed')]
    by_id = {m['id']: m for m in indexed}
    pending = {m['id']: m for m in messages if m.get('unindexed')}
    newest = max((m['timestamp'] or 0 for m in indexed), default=0)
    last_id = indexed[-1]['id'] if indexed else None
    after_last = False
    for item in tail:
        saved = by_id.get(item['id'])
        if saved is not None:
            if item['id'] == last_id:
                after_last = True
            if (item['role'] == saved['role'] == 'assistant' and item['text'].startswith(saved['text'])
                    and len(item['text']) > len(saved['text'])):
                saved['text'] = item['text']
            continue
        if not item['timestamp'] or (item['timestamp'] < newest and not after_last):
            continue
        previous = pending.get(item['id'])
        if previous and item['role'] == 'assistant' and previous['text'].startswith(item['text']):
            continue
        pending[item['id']] = item
    ordinal = max((m['ordinal'] for m in indexed), default=0)
    messages[:] = indexed
    for index, item in enumerate(sorted(pending.values(), key=lambda m: m['timestamp']), 1):
        messages.append({**item, 'ordinal': ordinal + index, 'unindexed': True})


def owner_tail(turns, public_text):
    candidates = deque(maxlen=120)
    for turn in sorted((t for t in turns if isinstance(t, dict)), key=lambda t: t.get('turnStartedAtMs') or 0)[-120:]:
        stamp = turn.get('turnStartedAtMs')
        for item in turn.get('items') or []:
            if not isinstance(item, dict):
                continue
            stamp = item.get('startedAtMs') or item.get('completedAtMs') or stamp
            if item.get('type') in ('agentMessage', 'userMessage', 'steeringUserMessage') and type(stamp) in (int, float):
                candidates.append((item, stamp))
    return [message for item, stamp in candidates if (message := public_item(item, stamp, public_text))]


class SessionTail:
    """Read only bounded, complete JSONL records from the selected session."""
    def __init__(self, home):
        self.home = home
        self.cache = ReadCache(capacity=4)

    def messages(self, location, thread_id, public_text):
        if not isinstance(location, str) or not location:
            return []
        try:
            path = Path(location).resolve()
            if not any(path.is_relative_to(self.home / folder) for folder in ('sessions', 'archived_sessions')):
                return []
            info = path.stat()
            if path.suffix != '.jsonl' or not stat.S_ISREG(info.st_mode):
                return []
            key = (thread_id, path, info.st_ino, info.st_size, info.st_mtime_ns)
            return self.cache.get(key, lambda: self._read(path, thread_id, public_text), ttl=30)
        except (OSError, RuntimeError, ValueError):
            return []

    @staticmethod
    def _read(path, thread_id, public_text):
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                return []
            header = json.loads(stream.readline(512 * 1024))
            if not isinstance(header, dict) or header.get('type') != 'session_meta' or not isinstance(header.get('payload'), dict) or header['payload'].get('id') != thread_id:
                return []
            start = max(stream.tell(), info.st_size - 1024 * 1024)
            if start > stream.tell():
                stream.seek(start - 1)
                if stream.read(1) != b'\n':
                    stream.readline(1024 * 1024)
            data = stream.read(max(0, info.st_size - stream.tell()))
        result = deque(maxlen=120)
        for line in data.split(b'\n')[:-1]:
            try:
                entry = json.loads(line)
                if (isinstance(entry, dict) and entry.get('type') == 'event_msg'
                        and isinstance(entry.get('payload'), dict) and entry['payload'].get('type') == 'thread_rolled_back'):
                    result.clear()
                    continue
                message = SessionTail._message(entry, public_text)
            except (ValueError, OverflowError, RecursionError):
                continue
            if message:
                result.append(message)
        return list(result)

    @staticmethod
    def _message(entry, public_text):
        if not isinstance(entry, dict) or entry.get('type') != 'response_item':
            return None
        item = entry.get('payload')
        if (not isinstance(item, dict) or item.get('type') != 'message' or item.get('recipient') not in (None, 'all')
                or item.get('channel') not in (None, 'commentary', 'final') or not isinstance(entry.get('timestamp'), str)):
            return None
        role = item.get('role')
        parts = item.get('content')
        if not isinstance(parts, list):
            return None
        if role == 'assistant' and item.get('phase') in ('commentary', 'final_answer'):
            kind, part_type = 'agentMessage', 'output_text'
        elif role == 'user' and isinstance(item.get('internal_chat_message_metadata_passthrough'), dict) and item['internal_chat_message_metadata_passthrough'].get('content_item_kinds') == ['user.text']:
            kind, part_type = 'userMessage', 'input_text'
        else:
            return None
        # Refuse mixed tool/context content instead of guessing what is public.
        if not parts or any(not isinstance(p, dict) or p.get('type') != part_type or not isinstance(p.get('text'), str) for p in parts):
            return None
        instant = datetime.fromisoformat(entry['timestamp'].replace('Z', '+00:00'))
        if instant.tzinfo is None:
            return None
        text = '\n'.join(p['text'] for p in parts)
        # Older id-less records lack a stable join to the native history projection.
        return public_item({'type': kind, 'id': item.get('id'), 'phase': item.get('phase'),
                            'content': [{'type': 'text', 'text': text}], 'text': text},
                           int(instant.timestamp()*1000), public_text)
