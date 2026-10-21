#!/usr/bin/env python3
"""Check, configure, set up, or export Desktop Companion on a Mac."""

import argparse
from contextlib import closing
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile

from configuration import load,save,ROOT,FILENAME

MODULES=('access.py','approvals.py','build_app.py','companion.py','computer_use.py','configuration.py',
         'control.py','desktop_runtime.py','inbox.py','manage.py','media.py','new_chat.py','photos.py',
         'power.py','promotions.py','questions.py','remote_access.py','runtime.py','service.py','system_status.py')


def native_build_tools():
    detail='Install Apple command-line tools and finish their setup before building the Raycast app.'
    xcrun=shutil.which('xcrun')
    if not xcrun:return False,detail
    try:
        compiler=subprocess.run([xcrun,'swiftc','--version'],capture_output=True,text=True,timeout=10)
        sdk=subprocess.run([xcrun,'--sdk','macosx','--show-sdk-path'],capture_output=True,text=True,timeout=10)
        sdk_path=sdk.stdout.strip()
        ready=compiler.returncode==0 and bool(compiler.stdout.strip()) and sdk.returncode==0 and bool(sdk_path) and Path(sdk_path).is_absolute() and Path(sdk_path).is_dir()
    except (OSError,subprocess.TimeoutExpired):
        return False,detail
    return bool(ready),'Swift compiler and macOS SDK are available.' if ready else detail


def doctor(root=ROOT,*,check_native=True):
    config=load(root);home=Path(os.environ.get('CODEX_HOME',Path.home()/'.codex'));cli=config.codex_path()
    native_ready,native_detail=native_build_tools() if check_native else (True,'Skipped for --without-app setup.')
    checks=[('macOS',platform.system()=='Darwin','This integration requires macOS.'),
            ('Python',sys.version_info>=(3,11),'Python 3.11 or newer.'),
            ('Desktop app',config.app_path().is_dir(),'Install and sign in to the desktop app on this Mac.'),
            ('Codex CLI',bool(cli),'The desktop’s bundled CLI or a codex command on PATH.'),
            ('Native build tools',native_ready,native_detail)]
    try:
        for filename,tables in [('state_5.sqlite',{'threads','projects','project_roots'}),('thread_history_1.sqlite',{'thread_items','thread_turns'})]:
            with closing(sqlite3.connect((home/filename).resolve().as_uri()+'?mode=ro',uri=True)) as db:
                available={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not tables<=available:raise ValueError('Unsupported local history schema.')
        checks.append(('Local history',True,'Required local history tables are readable.'))
    except (OSError,sqlite3.Error,ValueError):
        checks.append(('Local history',False,'Open the desktop app and create or open a local chat first. App updates can change its internal schema.'))
    if config.remote_enabled:
        credential=Path.home()/'.cloudflared'/(config.tunnel_id+'.json')
        checks.extend([('Cloudflare client',bool(shutil.which('cloudflared')),'Required for configured phone access.'),
                       ('Tunnel credential',credential.is_file(),'Use this Mac’s own Cloudflare tunnel credential; contents are never read by this check.')])
    else:checks.append(('Phone access',True,'Local-only mode. Configure a distinct hostname/tunnel later if needed.'))
    return [{'name':name,'ok':ok,'detail':detail} for name,ok,detail in checks]


def bundle(output,root=ROOT):
    root=Path(root).resolve();output=Path(output).resolve()
    files=[root/name for name in MODULES]+[root/name for name in ('.gitignore','config.example.json','README.md','SETUP.md')]
    for name in ('.gitattributes','AGENTS.md','CONTRIBUTING.md','.github/workflows/checks.yml'):
        path=root/name
        if path.exists() or path.is_symlink():files.append(path)
    for directory,patterns in [('web',('*.js','*.css','*.html')),('native',('*.swift',)),
                               ('cloudflare_fallback',('worker.mjs.template','offline.html','build.py','README.md'))]:
        for pattern in patterns:files.extend((root/directory).glob(pattern))
    files.extend(root.glob('test_*.py'));files.extend(root.glob('test_*.cjs'))
    for file in files:
        if not file.is_file() or file.is_symlink() or root not in file.resolve().parents:
            raise ValueError('A required source file is missing or is a symlink. Export stopped.')
    output.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    descriptor,temporary=tempfile.mkstemp(prefix='companion-export-',suffix='.zip',dir=output.parent);os.close(descriptor)
    path=Path(temporary)
    try:
        with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(set(files)):
                archive.write(file,Path('desktop_companion')/file.relative_to(root))
        path.replace(output);output.chmod(0o600)
    finally:path.unlink(missing_ok=True)
    return len(set(files))


def main():
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('doctor')
    configure=commands.add_parser('configure');configure.add_argument('--hostname');configure.add_argument('--tunnel-id');configure.add_argument('--desktop-app');configure.add_argument('--local',action='store_true')
    setup=commands.add_parser('setup');setup.add_argument('--without-app',action='store_true',help='Skip building the native control app during preparation')
    setup.add_argument('--install-service',action='store_true',help='Explicitly install login startup after reviewing machine security requirements')
    export=commands.add_parser('bundle');export.add_argument('--output',type=Path,default=ROOT/'.state/exports/Desktop-Companion-source.zip')
    args=parser.parse_args()
    if args.command=='bundle':
        count=bundle(args.output);print(f'Exported {count} source files: {args.output.resolve()}\nMachine configuration, sessions, photos, logs, and tunnel credentials are excluded.');return
    if args.command=='configure':
        if args.local and (args.hostname or args.tunnel_id):parser.error('--local cannot be combined with remote settings')
        old=load();value={'hostname':old.hostname,'tunnel_id':old.tunnel_id,'desktop_app':old.desktop_app}
        if args.local:value.update(hostname=None,tunnel_id=None)
        if args.hostname is not None:value['hostname']=args.hostname
        if args.tunnel_id is not None:value['tunnel_id']=args.tunnel_id
        if args.desktop_app is not None:value['desktop_app']=args.desktop_app
        save(value);print('Configuration saved. Restart Companion to apply it. Rebuild the control app if its desktop app path changed.');return
    checks=doctor(check_native=not (args.command=='setup' and args.without_app))
    for check in checks:print(('OK  ' if check['ok'] else 'NEEDS ACTION  ')+check['name']+' — '+check['detail'])
    required=[check for check in checks if not (args.command=='setup' and args.without_app and check['name']=='Native build tools')]
    if any(not check['ok'] for check in required):raise SystemExit(1)
    if args.command=='setup':
        if not (ROOT/FILENAME).exists():save({'hostname':None,'tunnel_id':None,'desktop_app':None})
        if not args.without_app:subprocess.run([sys.executable,str(ROOT/'build_app.py')],check=True)
        if args.install_service:
            subprocess.run([sys.executable,str(ROOT/'service.py'),'install','--background-install-approved'],check=True)
            print('Setup complete. Login startup explicitly installed. Open Desktop Companion from Raycast, or http://127.0.0.1:8765.')
        else:
            print('Preparation complete. No background service was installed. Review SETUP.md before explicitly enabling login startup. Power and Locked Use settings require separate native actions.')


if __name__=='__main__':
    try:main()
    except (ValueError,OSError,subprocess.SubprocessError) as error:
        print('Setup stopped: '+str(error),file=sys.stderr);raise SystemExit(1)
