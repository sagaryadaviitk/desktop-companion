"""Read-only workspace tools and explicitly configured, journaled API requests.

This module does not run commands, follow redirects, retry requests, or expose
credential configuration to a model. API call IDs are reserved durably before
opening a network connection; an unfinished call is never replayed.
"""
import hashlib
from contextlib import closing
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import ssl
import stat
import threading
import time
from urllib.parse import quote, quote_plus, unquote, urlencode, urlsplit
import uuid


_METHODS = frozenset(('GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'))
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_ENV_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
_PROTOCOL_HEADERS = frozenset(('host', 'content-length', 'transfer-encoding',
                             'connection', 'proxy-authorization', 'upgrade',
                             'expect', 'te', 'trailer'))
_SENSITIVE_HEADERS = frozenset(('authorization', 'cookie', 'x-api-key', 'api-key'))
_RESPONSE_HEADERS = frozenset(('content-type', 'content-length', 'content-encoding',
                             'date', 'etag', 'last-modified', 'retry-after',
                             'x-request-id', 'x-correlation-id'))
_PRIVATE_NAMES = frozenset(('.git', '.state', '.ssh', '.aws', '.kube', '.gnupg',
                            'config.local.json', 'credentials', 'credentials.json',
                            'id_rsa', 'id_ed25519', '.netrc', '.npmrc', '.pypirc'))


class _ToolError(ValueError):
    pass


def _bounded(config, key, default, minimum, maximum):
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _ToolError('Invalid local tool limit: ' + key)
    return max(minimum, min(value, maximum))


def _has_control(value):
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _safe_path(path, *, allow_root=False):
    """Reject ambiguous URL paths before any client/server normalization."""
    if not isinstance(path, str) or len(path) > 4096 or _has_control(path) or '\\' in path:
        raise _ToolError('API path must be a bounded relative path.')
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or '?' in path or '#' in path:
        raise _ToolError('Use a relative API path and the separate query object.')
    if path.startswith('/') and not allow_root:
        raise _ToolError('API path must be relative to the configured base prefix, without a leading slash.')
    decoded = path
    for _ in range(12):
        if _has_control(decoded) or '\\' in decoded:
            raise _ToolError('API path contains an unsafe escape.')
        if any(part.split(';', 1)[0] in ('.', '..') for part in decoded.split('/')):
            raise _ToolError('API paths cannot contain dot segments.')
        next_value = unquote(decoded, errors='strict')
        if next_value == decoded:
            break
        # Encoded separators are interpreted differently by servers and proxies.
        if next_value.count('/') != decoded.count('/') or '?' in next_value or '#' in next_value:
            raise _ToolError('API path contains an encoded separator.')
        decoded = next_value
    else:
        raise _ToolError('API path contains too many nested escapes.')
    return quote(path, safe="/%:@!$&'()*+,;=-._~")


class _CallJournal:
    """A separate private API-call store; stores no request bodies or headers."""

    def __init__(self, state_dir):
        self.directory = Path(state_dir)
        self.path = self.directory / 'local-api-calls.sqlite3'
        self.lock = threading.RLock()

    def _connect(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.directory.is_symlink() or self.path.is_symlink():
            raise _ToolError('The private API journal must not be a symlink.')
        os.chmod(self.directory, 0o700)
        # Pre-create with restrictive permissions before sqlite opens the file.
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            conn.execute('PRAGMA journal_mode=DELETE')
            conn.execute('PRAGMA synchronous=FULL')
            conn.execute('CREATE TABLE IF NOT EXISTS api_calls ('
                         'call_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, '
                         'state TEXT NOT NULL, result_json TEXT, '
                         'created REAL NOT NULL, updated REAL NOT NULL)')
            conn.commit()
        except BaseException:
            conn.close()
            raise
        return conn

    def reserve(self, call_id, request_hash):
        with self.lock, closing(self._connect()) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT request_hash, state, result_json FROM api_calls WHERE call_id=?',
                               (call_id,)).fetchone()
            if row is not None:
                return {'hash': row[0], 'state': row[1],
                        'result': json.loads(row[2]) if row[2] else None}
            now = time.time()
            conn.execute('INSERT INTO api_calls VALUES (?, ?, ?, NULL, ?, ?)',
                         (call_id, request_hash, 'in_flight', now, now))
            # The context commits before reserve returns, hence before network I/O.
            return None

    def finish(self, call_id, state, result):
        with self.lock, closing(self._connect()) as conn, conn:
            conn.execute('UPDATE api_calls SET state=?, result_json=?, updated=? '
                         'WHERE call_id=? AND state=?',
                         (state, json.dumps(result, ensure_ascii=True), time.time(), call_id, 'in_flight'))


class LocalTools:
    """Tools for a local chat agent; all configuration is private to this Mac.

    Limits may be lowered/raised within hard bounds with timeout_seconds (1..60),
    max_request_bytes (1..1 MiB), max_response_bytes (1..1 MiB),
    max_file_bytes (1..1 MiB), and max_list_entries (1..500).
    """

    def __init__(self, config):
        if not isinstance(config, dict):
            raise ValueError('Local tool configuration must be an object.')
        self.config = config
        self.profiles = config.get('api_profiles') or {}
        self.workspaces = config.get('workspaces') or []
        self._secrets = set()
        self._secret_lock = threading.RLock()
        self._secret_files = set()
        state_dir = config.get('state_dir') or Path(__file__).resolve().parent / '.state' / 'local'
        self.journal = _CallJournal(state_dir)
        self._refresh_secrets()

    def _remember_secret(self, value, header=''):
        if not value:
            return
        values = {value}
        if header.lower() == 'cookie':
            for part in value.split(';'):
                if '=' in part:
                    cookie_value = part.split('=', 1)[1].strip().strip('"')
                    if cookie_value:
                        values.add(cookie_value)
        if header.lower() == 'authorization' and ' ' in value:
            values.add(value.split(' ', 1)[1])
        with self._secret_lock:
            for item in values:
                if item:
                    self._secrets.update((item, quote(item, safe=''), quote_plus(item),
                                          json.dumps(item, ensure_ascii=True)[1:-1],
                                          json.dumps(item, ensure_ascii=False)[1:-1]))

    def _secret_value(self, header, source):
        if not isinstance(source, dict) or bool(source.get('env')) == bool(source.get('file')):
            raise _ToolError('Each secret header requires exactly one environment variable or private file.')
        prefix = source.get('prefix', '')
        if not isinstance(prefix, str) or _has_control(prefix):
            raise _ToolError('Secret header prefix is invalid.')
        if source.get('env'):
            name = source['env']
            if not isinstance(name, str) or not _ENV_NAME.fullmatch(name):
                raise _ToolError('Secret environment variable name is invalid.')
            value = os.environ.get(name)
            if value is None or not value:
                raise _ToolError('A configured secret environment variable is unavailable to this process. '
                                 'Background services may not inherit shell variables; configure a private token file instead.')
        else:
            path = source['file']
            if not isinstance(path, str) or not Path(path).is_absolute():
                raise _ToolError('Secret file paths must be absolute.')
            self._secret_files.add(str(Path(path).resolve()))
            fd = None
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_size > 16384 or info.st_mode & 0o077:
                    raise _ToolError('A secret file must be a small regular file private to its owner (mode 600).')
                value = os.read(fd, 16385).decode('utf-8').strip()
                if not value:
                    raise _ToolError('A configured secret file is empty.')
            except (OSError, UnicodeError) as exc:
                raise _ToolError('A configured private secret file is unavailable or invalid.') from exc
            finally:
                if fd is not None:
                    os.close(fd)
        self._remember_secret(value, header)
        self._remember_secret(prefix + value, header)
        if len(value) > 16384 or _has_control(value):
            raise _ToolError('A configured secret header is invalid or too large.')
        return prefix + value

    def _refresh_secrets(self):
        if not isinstance(self.profiles, dict):
            return
        for profile in self.profiles.values():
            if not isinstance(profile, dict):
                continue
            headers = profile.get('headers') or {}
            if isinstance(headers, dict):
                for name, value in headers.items():
                    if isinstance(name, str) and isinstance(value, str) and name.lower() in _SENSITIVE_HEADERS:
                        self._remember_secret(value, name)
            sources = profile.get('secret_headers') or {}
            if isinstance(sources, dict):
                for name, source in sources.items():
                    try:
                        if isinstance(source, dict) and isinstance(source.get('file'), str):
                            self._secret_files.add(str(Path(source['file']).resolve()))
                        if isinstance(name, str):
                            self._secret_value(name, source)
                    except (ValueError, OSError, UnicodeError, RuntimeError):
                        # Missing auth blocks only that profile's requests.
                        pass

    def redact(self, text):
        self._refresh_secrets()
        if not isinstance(text, str):
            text = str(text)
        with self._secret_lock:
            for secret in sorted(self._secrets, key=len, reverse=True):
                text = text.replace(secret, '[REDACTED]')
        return text

    def _clean(self, value):
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, dict):
            return {self.redact(str(key)): self._clean(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._clean(item) for item in value]
        return value

    def public_profiles(self):
        if not isinstance(self.profiles, dict):
            return []
        profiles = []
        for key, profile in self.profiles.items():
            if not isinstance(key, str) or not isinstance(profile, dict):
                continue
            item = {'id': key, 'name': profile.get('name', key)}
            try:
                _, _, _, _, methods = self._profile(key)
                item.update(base_url=profile['base_url'], methods=methods)
            except (ValueError, OSError):
                # A misconfigured URL can contain inline credentials. Never
                # publish it, including while reporting configuration errors.
                item.update(methods=[], configuration_error=True)
            profiles.append(item)
        return self._clean(profiles)

    def definitions(self):
        profiles = [item['id'] for item in self.public_profiles()]
        workspaces = [item['id'] for item in self.workspaces
                      if isinstance(item, dict) and isinstance(item.get('id'), str)] if isinstance(self.workspaces, list) else []
        def tool(name, description, properties, required):
            return {'type': 'function', 'function': {'name': name, 'description': description,
                    'parameters': {'type': 'object', 'properties': properties,
                                   'required': required, 'additionalProperties': False}}}
        return self._clean([
            tool('api_request', 'Call one explicitly configured API profile once. No redirects or retries. '
                 'Use only a relative path inside its base prefix. Auth is injected privately by the Mac. '
                 'Configured profile IDs: ' + json.dumps(profiles) + '.', {
                    'profile': {'type': 'string'}, 'method': {'type': 'string', 'enum': sorted(_METHODS)},
                    'path': {'type': 'string', 'description': 'Relative path, e.g. users/42; no leading slash, URL or query string.'},
                    'query': {'type': 'object', 'description': 'Query parameters as primitive values or arrays of primitives.'},
                    'json_body': {'type': 'object'}, 'body': {'type': 'string'}}, ['profile', 'method', 'path']),
            tool('list_files', 'List one directory inside an explicitly configured read-only workspace. '
                 'Configured workspace IDs: ' + json.dumps(workspaces) + '.', {
                    'workspace': {'type': 'string'}, 'path': {'type': 'string', 'description': 'Relative directory, default workspace root.'}}, ['workspace']),
            tool('read_file', 'Read a bounded UTF-8 text file inside a configured read-only workspace. '
                 'No shell, scripts, or skills are executed; private files and symlinks are refused.', {
                    'workspace': {'type': 'string'}, 'path': {'type': 'string'}}, ['workspace', 'path'])])

    def execute(self, name, args, call_id=None):
        try:
            if not isinstance(args, dict):
                raise _ToolError('Tool arguments must be an object.')
            allowed = {'api_request': {'profile', 'method', 'path', 'query', 'json_body', 'body'},
                       'list_files': {'workspace', 'path'}, 'read_file': {'workspace', 'path'}}
            if name not in allowed or set(args) - allowed[name]:
                raise _ToolError('Unknown tool or unsupported tool arguments.')
            if name == 'api_request':
                result = self._api_request(args, call_id)
            elif name == 'list_files':
                result = self._list_files(args.get('workspace'), args.get('path', ''))
            else:
                result = self._read_file(args.get('workspace'), args.get('path'))
            return self._clean(result)
        except (ValueError, OSError, UnicodeError, TypeError, RuntimeError, sqlite3.Error) as exc:
            # Configuration/file failures do not echo file contents, headers or URLs.
            message = str(exc) if isinstance(exc, _ToolError) else 'The local tool could not complete this request.'
            return self._clean({'ok': False, 'error': message, 'state': 'not_sent'})

    def _profile(self, profile_id):
        if not isinstance(self.profiles, dict) or not self.profiles:
            raise _ToolError('No API profiles are configured. Add an allowed base URL, methods and per-Mac auth in private local configuration.')
        if not isinstance(profile_id, str) or not isinstance(self.profiles.get(profile_id), dict):
            raise _ToolError('Unknown API profile; choose a configured profile ID.')
        profile = self.profiles[profile_id]
        base = profile.get('base_url')
        if not isinstance(base, str) or _has_control(base) or '\\' in base or len(base) > 4096:
            raise _ToolError('The API profile base URL is invalid.')
        url = urlsplit(base)
        if url.scheme not in ('http', 'https') or not url.hostname or url.username is not None or url.password is not None or url.query or url.fragment:
            raise _ToolError('API base URLs require HTTP(S), a host, and no credentials, query or fragment.')
        host = url.hostname
        try:
            port = url.port
        except ValueError as exc:
            raise _ToolError('The API profile port is invalid.') from exc
        try:
            loopback = host.lower() == 'localhost' or ipaddress.ip_address(host).is_loopback
        except ValueError:
            if ':' in host:
                raise _ToolError('The API profile host is invalid.')
            loopback = False
        if url.scheme == 'http' and not loopback:
            raise _ToolError('Unencrypted HTTP is allowed only on loopback. Configure office APIs with HTTPS and a trusted CA.')
        prefix = _safe_path(url.path, allow_root=True).rstrip('/')
        methods = profile.get('methods', ['GET', 'HEAD'])
        if not isinstance(methods, list) or not methods or any(not isinstance(method, str) or method not in _METHODS for method in methods):
            raise _ToolError('API profile methods must explicitly list supported uppercase HTTP methods.')
        return profile, url, port, prefix, methods

    def _request_parts(self, args):
        profile, url, port, prefix, methods = self._profile(args.get('profile'))
        method = args.get('method')
        if not isinstance(method, str) or method not in methods:
            raise _ToolError('This HTTP method is not enabled for the selected profile.')
        path = _safe_path(args.get('path', ''))
        target = prefix + '/' + path
        query = args.get('query', {})
        if not isinstance(query, dict) or len(query) > 100:
            raise _ToolError('Query parameters must be a bounded object.')
        pairs = []
        def scalar(value):
            if value is None:
                return ''
            if isinstance(value, bool):
                return str(value).lower()
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if isinstance(value, float) and not math.isfinite(value):
                    raise _ToolError('Query numbers must be finite.')
                return str(value)
            if isinstance(value, str):
                return value
            raise _ToolError('Query values must be primitives or arrays of primitives.')
        for key, value in query.items():
            if not isinstance(key, str) or len(key) > 256:
                raise _ToolError('Query parameter names are invalid.')
            for item in value if isinstance(value, list) else [value]:
                pairs.append((key, scalar(item)))
                if len(pairs) > 100:
                    raise _ToolError('Too many query parameters.')
        if pairs:
            target += '?' + urlencode(pairs)
        if len(target.encode('utf-8')) > 8192:
            raise _ToolError('The request URL is too large.')
        if 'json_body' in args and 'body' in args:
            raise _ToolError('Supply either json_body or body, not both.')
        payload = None
        headers = {'Accept': 'application/json', 'User-Agent': 'DesktopCompanionLocal/1'}
        if 'json_body' in args:
            if not isinstance(args['json_body'], dict):
                raise _ToolError('json_body must be an object.')
            try:
                payload = json.dumps(args['json_body'], ensure_ascii=False, allow_nan=False).encode('utf-8')
            except (ValueError, TypeError, RecursionError) as exc:
                raise _ToolError('json_body must contain finite JSON values.') from exc
            headers['Content-Type'] = 'application/json'
        elif 'body' in args:
            if not isinstance(args['body'], str):
                raise _ToolError('body must be text.')
            payload = args['body'].encode('utf-8')
            headers['Content-Type'] = 'text/plain; charset=utf-8'
        max_request = int(_bounded(self.config, 'max_request_bytes', 65536, 1, 1048576))
        if payload is not None and len(payload) > max_request:
            raise _ToolError('The request body exceeds the configured size limit.')
        fixed = profile.get('headers') or {}
        secrets = profile.get('secret_headers') or {}
        if not isinstance(fixed, dict) or not isinstance(secrets, dict):
            raise _ToolError('API headers must be configured as objects.')
        seen = set()
        for name, value in list(fixed.items()) + list(secrets.items()):
            if not isinstance(name, str) or not _HEADER_NAME.fullmatch(name) or name.lower() in _PROTOCOL_HEADERS or name.lower() in seen:
                raise _ToolError('An API header name is invalid, duplicated or reserved.')
            seen.add(name.lower())
            if name in secrets:
                value = self._secret_value(name, value)
            elif name.lower() in _SENSITIVE_HEADERS:
                raise _ToolError('Credential headers must use secret_headers with an environment variable or private file.')
            if not isinstance(value, str) or _has_control(value) or len(value) > 16384:
                raise _ToolError('An API header value is invalid or too large.')
            # HTTP/1.1 field values use ISO-8859-1 in the standard client.
            try:
                value.encode('latin-1')
            except UnicodeError as exc:
                raise _ToolError('API header values must be valid HTTP header text.') from exc
            headers = {key: val for key, val in headers.items() if key.lower() != name.lower()}
            headers[name] = value
        if sum(len(key) + len(value) for key, value in headers.items()) > 32768:
            raise _ToolError('The API request headers are too large.')
        context = None
        if url.scheme == 'https':
            ca_file = profile.get('ca_file')
            if ca_file is not None and (not isinstance(ca_file, str) or not Path(ca_file).is_absolute()):
                raise _ToolError('A custom CA file must have an absolute path.')
            try:
                context = ssl.create_default_context(cafile=ca_file)
            except (OSError, ssl.SSLError) as exc:
                raise _ToolError('The configured TLS trust file is unavailable or invalid.') from exc
        timeout = float(_bounded(self.config, 'timeout_seconds', 20, 1, 60))
        max_response = int(_bounded(self.config, 'max_response_bytes', 131072, 1, 1048576))
        return url, port, method, target, payload, headers, context, timeout, max_response

    def _api_request(self, args, call_id):
        url, port, method, target, payload, headers, context, timeout, max_response = self._request_parts(args)
        call_id = uuid.uuid4().hex if call_id is None else call_id
        if not isinstance(call_id, str) or not 1 <= len(call_id) <= 256 or _has_control(call_id):
            raise _ToolError('API call identity is invalid.')
        fingerprint = hashlib.sha256(json.dumps([args.get('profile'), method, target,
                                               hashlib.sha256(payload or b'').hexdigest()],
                                              separators=(',', ':')).encode()).hexdigest()
        previous = self.journal.reserve(call_id, fingerprint)
        if previous is not None:
            if previous['hash'] != fingerprint:
                return {'ok': False, 'call_id': call_id, 'state': previous['state'], 'duplicate': True,
                        'error': 'This call ID was already used for a different request. Nothing was sent.'}
            if previous['result'] is not None:
                return {**previous['result'], 'duplicate': True}
            return {'ok': False, 'call_id': call_id, 'state': previous['state'], 'duplicate': True,
                    'error': 'This API call is already in flight or its outcome is uncertain. It will not be replayed; inspect the API before a new manual request.'}
        connection = None
        response = None
        result = None
        state = 'uncertain'
        try:
            deadline = time.monotonic() + timeout
            if context is None:
                connection = http.client.HTTPConnection(url.hostname, port, timeout=timeout)
            else:
                connection = http.client.HTTPSConnection(url.hostname, port, timeout=timeout, context=context)
            connection.request(method, target, body=payload, headers=headers)
            self._remaining_timeout(connection, deadline)
            response = connection.getresponse()
            response_headers = response.getheaders()
            if sum(len(key) + len(value) for key, value in response_headers) > 32768:
                result = {'ok': False, 'call_id': call_id, 'state': 'completed', 'status': response.status,
                          'error': 'API response headers exceed the size limit.'}
            else:
                safe_headers = {key.lower(): value for key, value in response_headers
                                if key.lower() in _RESPONSE_HEADERS}
                chunks = []
                received = 0
                while received <= max_response:
                    self._remaining_timeout(connection, deadline, response)
                    chunk = response.read1(min(8192, max_response + 1 - received))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    received += len(chunk)
                body = b''.join(chunks)
                truncated = len(body) > max_response
                result = {'ok': 200 <= response.status < 300, 'call_id': call_id, 'state': 'completed',
                          'status': response.status, 'headers': safe_headers,
                          # Do not expose a partial credential cut at the cap.
                          'body': '' if truncated else body.decode('utf-8', errors='replace'),
                          'truncated': truncated}
                if truncated:
                    result['note'] = 'Response body omitted because it exceeds the configured size limit. Request a smaller response.'
                if 300 <= response.status < 400:
                    result.update(ok=False, redirect_blocked=True,
                                  error='API redirect blocked. Configure the final trusted base URL explicitly; no redirect was followed.')
                elif response.status >= 400:
                    result['error'] = 'API returned HTTP ' + str(response.status) + '.'
            state = 'completed'
        except (OSError, http.client.HTTPException, TimeoutError, ssl.SSLError) as exc:
            result = {'ok': False, 'call_id': call_id, 'state': 'uncertain',
                      'error': 'The API connection failed (' + type(exc).__name__ + '). '
                               'Its outcome is uncertain and it will not be retried. Inspect the API before sending a new request.'}
        finally:
            # HTTP/1.0 can detach the socket from HTTPConnection while the
            # response file still owns it, especially for a capped body.
            if response is not None:
                response.close()
            if connection is not None:
                connection.close()
        result = self._clean(result)
        # A failed final journal write leaves the durable in_flight reservation.
        # The outer caller may report a local failure, but a retry cannot send.
        try:
            self.journal.finish(call_id, state, result)
        except (sqlite3.Error, OSError):
            return {**result, 'ok': False, 'state': 'uncertain',
                    'error': 'The API result could not be recorded durably. This call will not be replayed; inspect the API before a new request.'}
        return result

    @staticmethod
    def _remaining_timeout(connection, deadline, response=None):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Local API deadline reached.')
        transport = connection.sock
        if transport is None and response is not None:
            # HTTP/1.0 may detach the connection socket while its response
            # file remains open. Bound that socket's read as well.
            transport = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
        if transport is not None:
            transport.settimeout(remaining)

    def _workspace(self, workspace_id):
        if not isinstance(self.workspaces, list):
            raise _ToolError('Read-only workspaces are not configured correctly.')
        for workspace in self.workspaces:
            if isinstance(workspace, dict) and workspace.get('id') == workspace_id and isinstance(workspace_id, str):
                path = workspace.get('path')
                if not isinstance(path, str) or not Path(path).is_absolute():
                    raise _ToolError('Workspace roots must be configured as absolute paths.')
                root = Path(path).resolve(strict=True)
                if not root.is_dir():
                    raise _ToolError('The configured workspace is unavailable.')
                return root
        raise _ToolError('Unknown read-only workspace. Configure a workspace on this Mac first.')

    @staticmethod
    def _private_part(part):
        lowered = part.lower()
        return lowered in _PRIVATE_NAMES or lowered == '.env' or lowered.startswith('.env.') or lowered.endswith(('.pem', '.key', '.p12', '.pfx'))

    def _open_workspace_path(self, workspace_id, path, directory):
        root = self._workspace(workspace_id)
        if not isinstance(path, str) or len(path) > 4096 or _has_control(path) or '\\' in path or path.startswith('/'):
            raise _ToolError('Workspace paths must be bounded relative paths.')
        parts = [part for part in path.split('/') if part and part != '.']
        if '..' in parts or any(self._private_part(part) for part in parts):
            raise _ToolError('Private files and parent-directory paths are not available to local tools.')
        candidate = root.joinpath(*parts)
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(root) or str(resolved) in self._secret_files:
            raise _ToolError('This path is outside the workspace or is a private credential file.')
        if not directory and not parts:
            raise _ToolError('Choose a relative text-file path.')
        fd = None
        try:
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            for index, part in enumerate(parts):
                last_file = index == len(parts) - 1 and not directory
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                if not last_file:
                    flags |= os.O_DIRECTORY
                next_fd = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            return fd, '/'.join(parts)
        except OSError as exc:
            if fd is not None:
                os.close(fd)
            raise _ToolError('This workspace path is unavailable; symlinks and non-regular files are refused.') from exc

    def _list_files(self, workspace_id, path):
        limit = int(_bounded(self.config, 'max_list_entries', 200, 1, 500))
        root = self._workspace(workspace_id)
        fd, relative = self._open_workspace_path(workspace_id, path, True)
        entries = []
        truncated = False
        try:
            with os.scandir(fd) as listing:
                for entry in listing:
                    if self._private_part(entry.name):
                        continue
                    if str(root.joinpath(relative, entry.name)) in self._secret_files:
                        continue
                    if len(entries) >= limit:
                        truncated = True
                        break
                    info = entry.stat(follow_symlinks=False)
                    kind = 'directory' if stat.S_ISDIR(info.st_mode) else 'file' if stat.S_ISREG(info.st_mode) else 'unavailable'
                    entries.append({'name': entry.name, 'type': kind,
                                    **({'size': info.st_size} if kind == 'file' else {})})
        finally:
            os.close(fd)
        return {'ok': True, 'workspace': workspace_id, 'path': relative,
                'entries': sorted(entries, key=lambda item: item['name'].lower()), 'truncated': truncated}

    def _read_file(self, workspace_id, path):
        fd, relative = self._open_workspace_path(workspace_id, path, False)
        try:
            info = os.fstat(fd)
            limit = int(_bounded(self.config, 'max_file_bytes', 131072, 1, 1048576))
            if not stat.S_ISREG(info.st_mode):
                raise _ToolError('Only regular UTF-8 text files may be read.')
            if info.st_size > limit:
                raise _ToolError('This file exceeds the configured read limit.')
            chunks = []
            received = 0
            while received <= limit:
                chunk = os.read(fd, min(8192, limit + 1 - received))
                if not chunk:
                    break
                chunks.append(chunk)
                received += len(chunk)
            raw = b''.join(chunks)
            if len(raw) > limit:
                raise _ToolError('This file exceeds the configured read limit.')
            if b'\0' in raw:
                raise _ToolError('Only regular UTF-8 text files may be read.')
            try:
                content = raw.decode('utf-8')
            except UnicodeError as exc:
                raise _ToolError('Only regular UTF-8 text files may be read.') from exc
            return {'ok': True, 'workspace': workspace_id, 'path': relative, 'content': content, 'size': len(raw)}
        finally:
            os.close(fd)
