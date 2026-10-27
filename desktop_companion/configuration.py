"""Machine-specific, non-secret settings. A clean checkout starts locally only."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from desktop_companion.access import RemoteSettings

from desktop_companion import ROOT
FILENAME='config.local.json'


@dataclass(frozen=True)
class Configuration:
    hostname: str | None = None
    tunnel_id: str | None = None
    desktop_app: str | None = None

    @property
    def remote_enabled(self):return bool(self.hostname and self.tunnel_id)

    def app_path(self):
        if self.desktop_app:return Path(self.desktop_app).expanduser().resolve()
        candidates=[Path('/Applications/ChatGPT.app'),Path.home()/'Applications/ChatGPT.app',Path('/Applications/Codex.app'),Path.home()/'Applications/Codex.app']
        return next((path for path in candidates if path.is_dir()),candidates[0])

    def codex_path(self):
        bundled=self.app_path()/'Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex'
        return str(bundled) if bundled.is_file() else shutil.which('codex')


def validate(value):
    if not isinstance(value,dict) or set(value)-{'hostname','tunnel_id','desktop_app'}:
        raise ValueError('Configuration supports hostname, tunnel_id, and desktop_app only. Keep credentials outside this project.')
    host=value.get('hostname');tunnel=value.get('tunnel_id');app=value.get('desktop_app')
    if bool(host)!=bool(tunnel):raise ValueError('Configure both hostname and tunnel_id, or leave both empty for local-only mode.')
    if host is not None and (not isinstance(host,str) or not RemoteSettings.valid_origin('https://'+host) or '/' in host or ':' in host):
        raise ValueError('Use a hostname without a scheme, port, or path.')
    if tunnel is not None and (not isinstance(tunnel,str) or not re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}',tunnel)):
        raise ValueError('Use the Cloudflare tunnel UUID for this laptop.')
    if app is not None and (not isinstance(app,str) or not Path(app).expanduser().is_absolute() or Path(app).suffix!='.app'):
        raise ValueError('desktop_app must be an absolute .app path, or null for automatic detection.')
    return Configuration(host,tunnel,app)


def load(root=ROOT):
    path=Path(root)/FILENAME
    if not path.exists():return Configuration()
    if path.is_symlink() or path.stat().st_size>10000:raise ValueError('Use a regular, small config.local.json file.')
    return validate(json.loads(path.read_text()))


def save(value,root=ROOT):
    config=validate(value);path=Path(root)/FILENAME
    with tempfile.NamedTemporaryFile(mode='w',prefix='config-',suffix='.tmp',dir=root,delete=False) as stream:
        temporary=Path(stream.name)
        try:
            json.dump({'hostname':config.hostname,'tunnel_id':config.tunnel_id,'desktop_app':config.desktop_app},stream,indent=2)
            stream.write('\n');stream.flush();os.fsync(stream.fileno())
            temporary.chmod(0o600);temporary.replace(path)
        finally:temporary.unlink(missing_ok=True)
    return config
