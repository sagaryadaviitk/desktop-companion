#!/usr/bin/env python3
"""Check, configure, set up, or export Desktop Companion on a Mac."""

import argparse
from contextlib import closing
import os
from pathlib import Path
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile

from configuration import load,save,ROOT,FILENAME

MODULES=('access.py','approvals.py','build_app.py','companion.py','computer_use.py','configuration.py',
         'control.py','desktop_runtime.py','inbox.py','local_assistant.py','local_forks.py','local_settings.py','local_smoke.py','local_tools.py','manage.py','media.py','new_chat.py','photos.py',
         'power.py','promotions.py','questions.py','read_cache.py','remote_access.py','runtime.py','service.py','system_status.py')

# Columns read by History and NewChat. Keep runtime-supported fallbacks optional:
# threads.reasoning_effort, receipts.last_published_turn, queue.updated_at_ms.
HISTORY_SCHEMAS={
    'state_5.sqlite':{
        'threads':{'id','name','title','cwd','updated_at','updated_at_ms','recency_at_ms',
                   'is_pinned','archived','agent_path','project_id','model','git_branch'},
        'projects':{'id','name','position','created_at_ms'},
        'project_roots':{'project_id','path','position'},
    },
    'thread_history_1.sqlite':{
        'thread_items':{'thread_id','item_id','item_type','item_json','created_at_ms',
                        'started_at_ms','completed_at_ms','rollout_ordinal'},
        'thread_turns':{'thread_id','turn_id','status','started_at','completed_at','rollout_ordinal'},
    },
}
OPTIONAL_HISTORY_TABLES={'state_5.sqlite':{'thread_read_receipts':{'thread_id','first_unread_turn'}}}
QUEUE_SCHEMA={'queued_items':{'id','thread_id','payload_json','created_at_ms','queue_order'}}


def history_schema(home):
    """Inspect metadata only, without creating databases or reading chat rows."""
    home=Path(home)
    schemas=list(HISTORY_SCHEMAS.items())
    # No queue database is normal before the first queued message. Once present,
    # History.pending requires its table even though catalog reads tolerate absence.
    if (home/'queue_1.sqlite').exists():schemas.append(('queue_1.sqlite',QUEUE_SCHEMA))
    for filename,required in schemas:
        try:
            with closing(sqlite3.connect((home/filename).resolve().as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
                db.execute('PRAGMA query_only=ON')
                available={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                tables={**required,**{table:columns for table,columns in OPTIONAL_HISTORY_TABLES.get(filename,{}).items() if table in available}}
                for table,columns in tables.items():
                    if table not in available:return False,f'{filename}: missing required table {table}. Open a local desktop chat or check COMPATIBILITY.md.'
                    # Table names come exclusively from the fixed schemas above.
                    found={row[1] for row in db.execute(f'PRAGMA table_info({table})')}
                    missing=columns-found
                    if missing:return False,f'{filename}: {table} is missing required columns: {", ".join(sorted(missing))}. Check COMPATIBILITY.md.'
        except (OSError,sqlite3.Error,ValueError):
            return False,f'{filename}: metadata is unreadable or unavailable. Open a local desktop chat and check COMPATIBILITY.md.'
    return True,'Required history columns are readable; supported optional fields may be absent. Live desktop controls are not tested.'


def cli_queue_capabilities(executable):
    """Run only bounded help; never sign in, read tokens, or submit a command."""
    detail='Install a compatible desktop app with an executable bundled CLI, or a compatible codex on PATH. See COMPATIBILITY.md.'
    if not executable or not Path(executable).is_file() or not os.access(executable,os.X_OK):return False,detail
    try:
        result=subprocess.run([str(executable),'queue','--help'],capture_output=True,text=True,timeout=3)
    except (OSError,subprocess.SubprocessError,UnicodeError):
        return False,'The CLI message-queue help check failed or timed out. See COMPATIBILITY.md; no chat command was sent.'
    flags=set(re.findall(r'(?<![\w-])--(?:thread|message|image)(?![\w-])',result.stdout))
    if result.returncode or not {'--thread','--message'}<=flags:
        return False,'The CLI does not advertise the required queue --thread/--message flags. See COMPATIBILITY.md.'
    photo='Photo queue flag is available.' if '--image' in flags else 'Photo queue flag is unavailable; photo delivery needs separate verification.'
    return True,'Message-queue help is supported. '+photo+' Sign-in and live delivery are not tested.'


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
    native_ready,native_detail=native_build_tools() if check_native else (True,'Skipped by --without-app; preparation continues without the native control app.')
    version=platform.mac_ver()[0].split('.')[0]
    macos_ready=platform.system()=='Darwin' and version.isdigit() and int(version)>=13
    cli_ready,cli_detail=cli_queue_capabilities(cli)
    checks=[('macOS',macos_ready,'Requires macOS 13 or newer; the desktop app may have a higher requirement. See COMPATIBILITY.md.'),
            ('Python',sys.version_info>=(3,11),'Python 3.11 or newer.'),
            ('Desktop app',config.app_path().is_dir(),'Desktop app folder check only. Open and sign in to a compatible desktop app on this Mac.'),
            ('Codex CLI',cli_ready,cli_detail),
            ('Native build tools',native_ready,native_detail)]
    history_ready,history_detail=history_schema(home)
    checks.append(('Local history',history_ready,history_detail))
    if config.remote_enabled:
        credential=Path.home()/'.cloudflared'/(config.tunnel_id+'.json')
        checks.extend([('Cloudflare client',bool(shutil.which('cloudflared')),'Required for configured phone access.'),
                       ('Tunnel credential',credential.is_file(),'Use this Mac’s own Cloudflare tunnel credential; contents are never read by this check.')])
    else:checks.append(('Phone access',True,'Local-only mode. Configure a distinct hostname/tunnel later if needed.'))
    return [{'name':name,'ok':ok,'detail':detail} for name,ok,detail in checks]


def bundle(output,root=ROOT):
    root=Path(root).resolve();output=Path(output).resolve()
    files=[root/name for name in MODULES]+[root/name for name in ('.gitignore','config.example.json','README.md','SETUP.md','COMPATIBILITY.md','LOCAL_ASSISTANT.md','local-settings.example.json')]
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
    check=commands.add_parser('doctor')
    check.add_argument('--without-app',action='store_true',help='Skip native build-tool checks to prepare without the native control app')
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
    checks=doctor(check_native=not args.without_app)
    for check in checks:print(('OK  ' if check['ok'] else 'NEEDS ACTION  ')+check['name']+' — '+check['detail'])
    required=[check for check in checks if not (args.without_app and check['name']=='Native build tools')]
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
