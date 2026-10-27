"""Verify local skill -> file read -> authenticated synthetic API -> actual answer.

Only loopback servers and disposable fake data are used. No existing chats,
settings, skill libraries, office APIs, credentials or model downloads change.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import tempfile
import threading
import time
import uuid

from desktop_companion.local_assistant import LocalAssistant
from desktop_companion.local_settings import LocalSettings, DEFAULT_ENDPOINT, DEFAULT_MODEL


def verify(endpoint=DEFAULT_ENDPOINT, model=DEFAULT_MODEL):
    nonce = 'probe-' + uuid.uuid4().hex[:12]
    incident = 'incident-' + uuid.uuid4().hex[:12]
    token = 'synthetic-test-key-' + uuid.uuid4().hex
    hits = []
    class Diagnostic(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            if self.path != '/v1/health' or self.headers.get('X-API-Key') != token:
                self.send_response(403); self.end_headers(); return
            hits.append(self.path)
            body = json.dumps({'healthy': True, 'probe_id': nonce, 'queued_jobs': 3}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers(); self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Diagnostic)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    root = Path(tempfile.mkdtemp(prefix='companion-local-smoke-')); root.chmod(0o700)
    assistant = None
    complete = False
    try:
        workspace = root / 'debug'; workspace.mkdir()
        (workspace / 'incident.txt').write_text('Incident ID: ' + incident + '\n')
        secret = root / 'private-test-key'; secret.write_text(token); secret.chmod(0o600)
        skill = root / 'skills' / 'office-proof'; skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('---\nname: office-proof\ndescription: Verify a synthetic incident using local tools.\n---\n'
            'Read incident.txt in workspace debug. Call profile diagnostic with GET and relative path health. '
            'Report the actual incident ID, probe_id and healthy flag. Do not guess the identifiers.\n')
        settings = LocalSettings(root)
        settings.save({'enabled': True, 'endpoint': endpoint, 'model': model, 'models': [model], 'tool_models': [model],
            'skill_roots': [str(skill.parent)], 'workspaces': [{'id': 'debug', 'name': 'Fixture', 'path': str(workspace)}],
            'api_profiles': {'diagnostic': {'base_url': f'http://127.0.0.1:{server.server_port}/v1/', 'methods': ['GET'],
                'secret_headers': {'X-API-Key': {'file': str(secret)}}}}})
        assistant = LocalAssistant(root)
        status = assistant.status()
        if not status['ready']: raise ValueError('Open Exo and load this model before running the smoke check.')
        chat = assistant.create({'request_id': 'smoke-create-' + uuid.uuid4().hex, 'model': model,
            'skills': [status['skills'][0]['id']], 'workspace': 'debug'})['chat']['id']
        assistant.send({'request_id': 'smoke-message-' + uuid.uuid4().hex, 'chat_id': chat,
            'message': 'Follow the selected office-proof skill now. Read the incident file, call the diagnostic API, '
                       'and tell me both exact identifiers and whether the API is healthy.'})
        start = time.monotonic()
        while time.monotonic() - start < 210:
            detail = assistant.detail(chat)
            if detail['chat']['status'] != 'working': break
            time.sleep(0.25)
        complete = detail['chat']['status'] != 'working'
        answer = '\n'.join(m['content'] for m in detail['messages'] if m['role'] == 'assistant')
        tool_names = [m['tool_name'] for m in detail['messages'] if m['role'] == 'tool']
        result = {'passed': detail['chat']['status'] == 'completed' and len(hits) == 1
                  and incident in answer and nonce in answer and token not in json.dumps(detail)
                  and 'read_file' in tool_names and 'api_request' in tool_names,
                  'status': detail['chat']['status'], 'seconds': round(time.monotonic() - start, 2),
                  'api_requests': len(hits), 'file_identifier_verified': incident in answer,
                  'api_identifier_verified': nonce in answer, 'credential_hidden': token not in json.dumps(detail),
                  'tools': tool_names, 'error': detail['error']}
        return result
    finally:
        if assistant: assistant.close()
        server.shutdown(); server.server_close()
        if complete or assistant is None: shutil.rmtree(root)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', default=DEFAULT_ENDPOINT)
    parser.add_argument('--model', default=DEFAULT_MODEL)
    arguments = parser.parse_args()
    try:
        result = verify(arguments.endpoint, arguments.model)
        print(json.dumps(result, indent=2))
        raise SystemExit(0 if result['passed'] else 1)
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
