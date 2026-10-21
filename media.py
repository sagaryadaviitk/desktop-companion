"""Explicit, private media snapshots; never a general-purpose filesystem server."""

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time


ITEM_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,79}')
ASSET_NAME = re.compile(r'[a-f0-9]{64}\.(?:mp4|webm|png|jpg|vtt)')
TYPES = {'.mp4': 'video/mp4', '.webm': 'video/webm', '.png': 'image/png',
         '.jpg': 'image/jpeg', '.vtt': 'text/vtt; charset=utf-8'}


def byte_range(value, size):
    """Return one inclusive byte interval; reject malformed/multipart ranges."""
    match = re.fullmatch(r'bytes=(\d{0,20})-(\d{0,20})', value or '')
    if not match or not any(match.groups()) or size <= 0:
        raise ValueError('Invalid range')
    first, last = match.groups()
    if not first:
        count = int(last)
        if count <= 0: raise ValueError('Invalid suffix')
        return max(0, size - count), size - 1
    first = int(first)
    last = min(int(last), size - 1) if last else size - 1
    if first >= size or first > last: raise ValueError('Unsatisfiable range')
    return first, last


class MediaStore:
    def __init__(self, root):
        self.root = Path(root)

    def _records(self):
        path = self.root / 'index.json'
        if not path.exists(): return {}
        if self.root.is_symlink() or path.is_symlink() or path.stat().st_size > 1_000_000:
            raise ValueError('Media index unavailable')
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or data.get('schema') != 1 or not isinstance(data.get('items'), dict):
            raise ValueError('Media index unavailable')
        return data['items']

    @staticmethod
    def _asset(record, role):
        assets = record.get('assets')
        asset = assets.get(role) if isinstance(assets, dict) else None
        if not isinstance(asset, dict) or not ASSET_NAME.fullmatch(str(asset.get('name', ''))):
            raise FileNotFoundError('Media unavailable')
        if asset.get('mime') != TYPES.get(Path(asset['name']).suffix) or type(asset.get('bytes')) is not int:
            raise FileNotFoundError('Media unavailable')
        return asset

    def catalog(self):
        items = []
        for identifier, record in self._records().items():
            if not ITEM_ID.fullmatch(identifier) or not isinstance(record, dict): continue
            try: video = self._asset(record, 'video')
            except FileNotFoundError: continue
            item = {key: record.get(key) for key in ('title', 'description', 'version', 'duration_seconds')}
            item.update(id=identifier, mime=video['mime'], bytes=video['bytes'])
            for role in ('video', 'poster', 'captions'):
                try: asset = self._asset(record, role)
                except FileNotFoundError: continue
                item[role + '_url'] = f"/api/media/{identifier}/{role}?v={asset['name'].split('.')[0]}"
            item['download_url'] = item['video_url'].replace('/video?', '/download?')
            items.append(item)
        return sorted(items, key=lambda item: item['id'])

    @contextmanager
    def open_asset(self, identifier, role, version=None):
        if not ITEM_ID.fullmatch(identifier) or role not in ('video', 'poster', 'captions', 'download'):
            raise FileNotFoundError('Media unavailable')
        record = self._records().get(identifier)
        if not isinstance(record, dict): raise FileNotFoundError('Media unavailable')
        asset = self._asset(record, 'video' if role == 'download' else role)
        digest = asset['name'].split('.')[0]
        if version is not None and version != digest: raise FileNotFoundError('Media changed; reload the page')
        directory = self.root / identifier
        path = directory / asset['name']
        if self.root.is_symlink() or directory.is_symlink() or path.is_symlink():
            raise FileNotFoundError('Media unavailable')
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size != asset['bytes'] or info.st_mtime_ns != asset['mtime_ns']:
                raise FileNotFoundError('Media snapshot changed')
            yield stream, asset, info

    def publish(self, source, identifier, title, description='', version='', duration_seconds=None, poster=None, captions=None):
        if not ITEM_ID.fullmatch(identifier): raise ValueError('Use a short lowercase media ID')
        if not 1 <= len(title.strip()) <= 160 or len(description) > 2000 or len(version) > 80:
            raise ValueError('Invalid media description')
        if duration_seconds is not None and not 0 < duration_seconds <= 86400:
            raise ValueError('Invalid duration')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.is_symlink(): raise ValueError('Media root must not be a symlink')
        lock = self.root / 'publish.lock'
        if lock.is_symlink(): raise ValueError('Invalid media lock')
        with lock.open('a') as guard:
            os.chmod(lock, 0o600)
            fcntl.flock(guard, fcntl.LOCK_EX)
            records = self._records()
            directory = self.root / identifier
            if directory.is_symlink(): raise ValueError('Invalid media directory')
            directory.mkdir(mode=0o700, exist_ok=True)
            assets = {}
            for role, source_path in [('video', source), ('poster', poster), ('captions', captions)]:
                if source_path is None: continue
                source_path = Path(source_path)
                suffix = source_path.suffix.lower()
                if suffix == '.jpeg': suffix = '.jpg'
                allowed = {'video': ('.mp4', '.webm'), 'poster': ('.png', '.jpg'), 'captions': ('.vtt',)}[role]
                if suffix not in allowed or not source_path.is_file() or source_path.is_symlink():
                    raise ValueError('Unsupported media asset')
                if role != 'video' and source_path.stat().st_size > 20_000_000:
                    raise ValueError('Supporting asset is too large')
                if role == 'captions' and not source_path.read_text().lstrip('\ufeff').startswith('WEBVTT'):
                    raise ValueError('Captions must be WebVTT')
                fd, temporary = tempfile.mkstemp(prefix='.publishing-', dir=directory)
                try:
                    digest = hashlib.sha256()
                    with source_path.open('rb') as original, os.fdopen(fd, 'wb') as copied:
                        while chunk := original.read(256 * 1024):
                            copied.write(chunk); digest.update(chunk)
                    target = directory / (digest.hexdigest() + suffix)
                    if target.exists() or target.is_symlink():
                        if target.is_symlink() or not target.is_file() or target.stat().st_size != Path(temporary).stat().st_size:
                            raise ValueError('Existing media snapshot is invalid')
                        existing_digest = hashlib.sha256()
                        with target.open('rb') as existing:
                            while chunk := existing.read(256 * 1024): existing_digest.update(chunk)
                        if existing_digest.hexdigest() != digest.hexdigest():
                            raise ValueError('Existing media snapshot is invalid')
                    else:
                        os.replace(temporary, target)
                    info = target.stat()
                    assets[role] = {'name': target.name, 'mime': TYPES[suffix], 'bytes': info.st_size, 'mtime_ns': info.st_mtime_ns}
                finally:
                    if os.path.exists(temporary): os.unlink(temporary)
            records[identifier] = {'title': title.strip(), 'description': description, 'version': version,
                                   'duration_seconds': duration_seconds, 'published_at': int(time.time()), 'assets': assets}
            fd, temporary = tempfile.mkstemp(prefix='.index-', dir=self.root)
            try:
                with os.fdopen(fd, 'w') as stream:
                    json.dump({'schema': 1, 'items': records}, stream, indent=2); stream.write('\n')
                os.replace(temporary, self.root / 'index.json')
            finally:
                if os.path.exists(temporary): os.unlink(temporary)
        return next(item for item in self.catalog() if item['id'] == identifier)


def main():
    parser = argparse.ArgumentParser(description='Make an explicit private media snapshot available to paired browsers.')
    parser.add_argument('source', type=Path)
    parser.add_argument('--id', required=True)
    parser.add_argument('--title', required=True)
    parser.add_argument('--description', default='')
    parser.add_argument('--version', default='')
    parser.add_argument('--duration', type=float)
    parser.add_argument('--poster', type=Path)
    parser.add_argument('--captions', type=Path)
    args = parser.parse_args()
    item = MediaStore(Path(__file__).resolve().parent / '.state' / 'media').publish(
        args.source, args.id, args.title, args.description, args.version, args.duration, args.poster, args.captions)
    print(json.dumps({'id': item['id'], 'review_path': '/media?item=' + item['id'], 'bytes': item['bytes']}))


if __name__ == '__main__': main()
