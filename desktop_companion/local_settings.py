"""Machine-private configuration and read-only skill discovery for local chats."""

import argparse
import hashlib
import json
import os
from pathlib import Path
from desktop_companion import ROOT
import re
import tempfile
from urllib.parse import urlsplit


DEFAULT_ENDPOINT = 'http://127.0.0.1:52415/v1'
DEFAULT_MODEL = 'mlx-community/Qwen3.5-2B-MLX-8bit'
ID = re.compile(r'^[A-Za-z0-9_-]{1,80}$')


def private_json(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.is_symlink():raise ValueError('Private settings must not use a symlink folder.')
    descriptor, temporary = tempfile.mkstemp(prefix='local-settings-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class LocalSettings:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / '.state' / 'local-settings.json'

    def load(self):
        if not self.path.exists():
            return {'enabled': False, 'endpoint': DEFAULT_ENDPOINT, 'model': DEFAULT_MODEL,
                    'models': [DEFAULT_MODEL], 'tool_models': [DEFAULT_MODEL], 'skill_roots': [], 'workspaces': [], 'api_profiles': {},
                    'state_dir': str(self.root / '.state' / 'local-assistant')}
        if self.path.is_symlink() or self.path.stat().st_size > 128 * 1024:
            raise ValueError('Invalid local settings file.')
        if self.path.stat().st_mode & 0o077:
            raise ValueError('Local settings must be private to this user (chmod 600).')
        return self.validate(json.loads(self.path.read_text()))

    def validate(self, value):
        try:return self._validate(value)
        except (TypeError, AttributeError, KeyError) as error:
            raise ValueError('Local settings contain invalid field types.') from error

    def _validate(self, value):
        value = json.loads(json.dumps(value))
        if not isinstance(value, dict):
            raise ValueError('Local settings must be an object.')
        endpoint = value.get('endpoint', DEFAULT_ENDPOINT)
        if not isinstance(endpoint, str):raise ValueError('The local endpoint must be text.')
        parsed = urlsplit(endpoint)
        if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1')
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path.rstrip('/') != '/v1'):
            raise ValueError('The local model endpoint must be a loopback /v1 server.')
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError('The local model endpoint port is invalid.')
        if type(value.get('enabled', False)) is not bool:
            raise ValueError('enabled must be true or false.')
        models = value.get('models', [value.get('model', DEFAULT_MODEL)])
        if not isinstance(models, list) or not 1 <= len(models) <= 20 or any(
                not isinstance(item, str) or not 1 <= len(item) <= 200 or any(ord(c) < 32 for c in item)
                for item in models):
            raise ValueError('Configure between one and twenty local model IDs.')
        value.update(endpoint=endpoint.rstrip('/'), models=list(dict.fromkeys(models)),
                     model=value.get('model', models[0]), enabled=value.get('enabled', False))
        if value['model'] not in models:
            raise ValueError('The default model must be in models.')
        tool_models = value.setdefault('tool_models', [model for model in models if model == DEFAULT_MODEL])
        if not isinstance(tool_models, list) or any(not isinstance(model, str) or model not in models for model in tool_models):
            raise ValueError('tool_models must list configured models verified with tools.')
        roots = value.setdefault('skill_roots', [])
        workspaces = value.setdefault('workspaces', [])
        profiles = value.setdefault('api_profiles', {})
        if not isinstance(roots, list) or len(roots) > 20 or any(not isinstance(p, str) or not Path(p).is_absolute() for p in roots):
            raise ValueError('Skill roots must be absolute folders on this Mac.')
        if not isinstance(workspaces, list) or len(workspaces) > 20:
            raise ValueError('Invalid local workspaces.')
        seen = set()
        for workspace in workspaces:
            if (not isinstance(workspace, dict) or not ID.fullmatch(workspace.get('id', ''))
                    or not isinstance(workspace.get('path'), str) or not Path(workspace['path']).is_absolute()
                    or workspace['id'] in seen):
                raise ValueError('Workspace IDs must be unique and paths absolute.')
            seen.add(workspace['id'])
        if not isinstance(profiles, dict) or len(profiles) > 30 or any(not ID.fullmatch(k) or not isinstance(v, dict) for k, v in profiles.items()):
            raise ValueError('Invalid API profiles.')
        value['state_dir'] = str(self.root / '.state' / 'local-assistant')
        return value

    def save(self, value):
        self.validate(value)
        private_json(self.path, value)
        return self.load()


class SkillRegistry:
    """Only explicitly configured roots are scanned; skill files are never edited."""
    MAX_BYTES = 32 * 1024

    def __init__(self, roots):
        self.skills = {}
        for raw in roots:
            root = Path(raw).expanduser().resolve()
            if not root.is_dir():
                continue
            candidates = [root / 'SKILL.md'] if (root / 'SKILL.md').is_file() else sorted(root.glob('*/SKILL.md'))
            for candidate in candidates[:200]:
                resolved = candidate.resolve()
                if not resolved.is_relative_to(root) or not resolved.is_file() or resolved.stat().st_size > self.MAX_BYTES:
                    continue
                try:
                    source = resolved.read_text(encoding='utf-8')
                except (OSError, UnicodeError):
                    continue
                # The minimal metadata reader deliberately executes no YAML tags.
                name = candidate.parent.name
                description = ''
                if source.startswith('---\n'):
                    front = source.split('---', 2)[1]
                    for line in front.splitlines():
                        key, separator, text = line.partition(':')
                        if separator and key.strip() in ('name', 'description'):
                            text = text.strip().strip('\"\'')
                            if key.strip() == 'name': name = text[:120]
                            else: description = text[:500]
                identity = hashlib.sha256(str(resolved).encode()).hexdigest()[:24]
                self.skills[identity] = {'id': identity, 'name': name, 'description': description,
                                         'path': str(resolved), 'root': str(resolved.parent),
                                         'sha256': hashlib.sha256(source.encode()).hexdigest(), 'source': source}

    def public(self):
        return [{key: value[key] for key in ('id', 'name', 'description')} for value in self.skills.values()]

    def select(self, identities):
        if (not isinstance(identities, list) or any(not isinstance(item, str) for item in identities)
                or len(identities) > 3 or len(set(identities)) != len(identities)):
            raise ValueError('Choose up to three distinct skills.')
        try:
            return [self.skills[identity] for identity in identities]
        except (KeyError, TypeError) as error:
            raise ValueError('A selected skill is no longer available on this Mac.') from error

    def read_reference(self, identity, path):
        if identity not in self.skills or not isinstance(path, str) or len(path) > 500:
            raise ValueError('Unknown skill reference.')
        root = Path(self.skills[identity]['root'])
        target = (root / path).resolve()
        if (Path(path).is_absolute() or not target.is_relative_to(root) or target.suffix.lower() not in ('.md', '.txt', '.json')
                or not target.is_file() or target.stat().st_size > self.MAX_BYTES):
            raise ValueError('Choose a bounded text reference inside the selected skill.')
        return {'ok': True, 'content': target.read_text(encoding='utf-8')}


def main():
    parser = argparse.ArgumentParser(description='Configure independent local chats on this Mac.')
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--enable', action='store_true')
    parser.add_argument('--disable', action='store_true')
    parser.add_argument('--endpoint', default=None)
    parser.add_argument('--model')
    parser.add_argument('--skill-root', action='append', default=[])
    parser.add_argument('--workspace', action='append', default=[], metavar='ID=/absolute/path')
    args = parser.parse_args()
    settings = LocalSettings(args.root)
    value = settings.load()
    if args.enable and args.disable: parser.error('Choose enable or disable.')
    changed = args.enable or args.disable or args.endpoint or args.model or args.skill_root or args.workspace
    if args.enable: value['enabled'] = True
    if args.disable: value['enabled'] = False
    if args.endpoint: value['endpoint'] = args.endpoint
    if args.model:
        value['model'] = args.model
        value['models'] = list(dict.fromkeys([args.model] + value.get('models', [])))
    if args.skill_root: value['skill_roots'] = list(dict.fromkeys(value.get('skill_roots', []) + [str(Path(p).expanduser().resolve()) for p in args.skill_root]))
    for item in args.workspace:
        identity, separator, path = item.partition('=')
        if not separator: parser.error('Use --workspace ID=/absolute/path')
        value['workspaces'] = [w for w in value.get('workspaces', []) if w['id'] != identity] + [{'id': identity, 'name': identity, 'path': str(Path(path).expanduser().resolve())}]
    value.pop('state_dir', None)
    if changed: value = settings.save(value)
    print('Local chats: ' + ('enabled' if value['enabled'] else 'disabled'))
    print('Private settings: ' + str(settings.path))
    print('Model: ' + value.get('model', DEFAULT_MODEL))
    print('Skills: ' + str(len(SkillRegistry(value['skill_roots']).public())))
    print('API profiles: ' + str(len(value['api_profiles'])))
    if changed: print('Restart only the Companion web service after configuration changes.')


if __name__ == '__main__':
    try: main()
    except (ValueError, OSError) as error: raise SystemExit(str(error))
