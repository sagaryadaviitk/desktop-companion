"""The desktop's asynchronous question messages, distinct from runtime approvals."""

import hashlib
import json


OPEN = '<send_user_message_question_reply>'
CLOSE = '</send_user_message_question_reply>'


def replies(text):
    if not isinstance(text, str):
        return []
    text = text.strip()
    if not text.startswith(OPEN) or not text.endswith(CLOSE):
        return []
    try:
        values = json.loads(text[len(OPEN):-len(CLOSE)])
    except ValueError:
        return []
    if isinstance(values, dict):
        values = [values]
    if not isinstance(values, list):
        return []
    return [value for value in values if isinstance(value, dict)
            and all(isinstance(value.get(key), str) for key in ('questionItemId', 'question', 'answer'))
            and value['answer'].strip()]


def reply_text(value):
    content = value.get('content') if value.get('type') != 'steeringUserMessage' else value.get('input')
    if not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict):
        return ''
    return content[0].get('text', '') if content[0].get('type') == 'text' else ''


def pending_questions(rows, redact):
    """Use persisted async metadata and accepted replies, never infer questions from prose.

    Rows are chronological and independent of the conversation's visible history page.
    A completed turn can still have an unanswered async question, as on the desktop.
    """
    pending = {}
    for row in rows:
        try:
            value = json.loads(row['item_json'])
        except (TypeError, ValueError):
            continue
        if not isinstance(value, dict):
            continue
        kind = row['item_type']
        if kind == 'agentMessage' and value.get('delivery') == 'async':
            questions = value.get('questions')
            if not isinstance(questions, list):
                continue
            for index, question in enumerate(questions):
                if not isinstance(question, dict) or not isinstance(question.get('title'), str):
                    continue
                options = question.get('options') or []
                if not isinstance(options, list) or any(not isinstance(option, str) for option in options):
                    continue
                identity = json.dumps(['request_user_input_async', row['item_id'], index], separators=(',', ':'))
                title = redact(question['title'])
                options = [redact(option) for option in options]
                revision = hashlib.sha256(json.dumps([identity, title, options]).encode()).hexdigest()
                pending[identity] = {'id': identity, 'message_id': row['item_id'], 'index': index,
                                     'title': title, 'options': options, 'revision': revision,
                                     'timestamp': row['created_at_ms'], 'delivery': 'unanswered'}
        elif kind == 'userMessage' or kind == 'steeringUserMessage' and value.get('status') == 'accepted':
            for reply in replies(reply_text(value)):
                pending.pop(reply['questionItemId'], None)
    return list(pending.values())


def answer_envelope(question, answer):
    return OPEN + '\n' + json.dumps([{'questionItemId': question['id'], 'question': question['title'],
                                    'answer': answer}], ensure_ascii=False) + '\n' + CLOSE


def answer_identity(thread_id, question):
    return 'answer-' + hashlib.sha256(json.dumps([thread_id, question['id'], question['revision']]).encode()).hexdigest()


def queued_question_answers(instructions):
    return {reply['questionItemId']: reply['answer'] for item in instructions for reply in replies(item['text'])}
