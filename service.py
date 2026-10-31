#!/usr/bin/env python3
"""Manage this companion's macOS LaunchAgent and supervised process group."""

import argparse
import fcntl
import errno
import hashlib
import json
import os
from pathlib import Path
import plistlib
import random
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from urllib.request import urlopen

# A missing/quarantined dependency needs operator repair, not a login restart loop.
try:
    from power import AWAKE_COMMAND, read_battery_state, should_keep_awake
    from configuration import load
    from system_status import publish,read_record,record_event
except (ImportError,OSError,SyntaxError):
    if __name__ == '__main__' and sys.argv[1:] == ['run']:
        print('Companion dependencies are unavailable. Automatic startup stopped; review security status and repair the approved installation.',file=sys.stderr,flush=True)
        raise SystemExit(0)
    raise


ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".state"
LABEL = "me.sagaryadav.desktop-companion"
try:
    CONFIG = load(ROOT)
except (OSError,ValueError):
    if __name__ == '__main__' and sys.argv[1:] == ['run']:
        print('Companion configuration is unavailable. Automatic startup stopped; review the installation before restarting.',file=sys.stderr,flush=True)
        raise SystemExit(0)
    raise
HOSTNAME = CONFIG.hostname
TUNNEL_ID = CONFIG.tunnel_id
PLIST = Path.home() / "Library" / "LaunchAgents" / (LABEL + ".plist")
TARGET = f"gui/{os.getuid()}/{LABEL}"


def definition(root=ROOT, python=sys.executable, home=None, codex=None):
    home = Path(home or Path.home())
    codex = codex or CONFIG.codex_path()
    if not codex:
        raise RuntimeError("The installed codex command is unavailable.")
    return {
        "Label": LABEL,
        # Positional shell arguments preserve spaces without interpreting paths as code.
        # Exit zero only for a missing runtime so launchd does not retry that condition.
        "ProgramArguments": ["/bin/sh", "-c",
            'if [ ! -r "$1" ] || [ ! -x "$2" ]; then echo "Companion runtime unavailable; approved repair required. Automatic startup stopped." >&2; exit 0; fi; exec "$2" -u "$1" run',
            "desktop-companion-service", str(root / "service.py"), str(python)],
        "WorkingDirectory": str(home),
        "EnvironmentVariables": {
            "HOME": str(home),
            "PATH": str(Path(codex).parent) + ":/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONUNBUFFERED": "1",
        },
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 60,
        "ExitTimeOut": 20,
        "AbandonProcessGroup": False,
        "Umask": 0o077,
        "StandardOutPath": str(home / "Library" / "Logs" / "Desktop Companion" / "service.log"),
        "StandardErrorPath": str(home / "Library" / "Logs" / "Desktop Companion" / "service-error.log"),
    }


def stop_children(children):
    # Interrupt the tunnel controller first so its finally block stops cloudflared.
    for name in ("tunnel", "server", "awake"):
        process = children.get(name)
        if process and process.poll() is None:
            try:
                process.send_signal(signal.SIGTERM if name == "awake" else signal.SIGINT)
            except ProcessLookupError:
                pass
    for process in children.values():
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def write_runtime(children):
    value = {"supervisor": os.getpid(), "started_at": int(time.time()),
             "children": {name: process.pid for name,process in children.items()}}
    temporary = STATE / "service-runtime.tmp"
    temporary.write_text(json.dumps(value))
    temporary.chmod(0o600)
    temporary.replace(STATE / "service-runtime.json")


def desktop_app_running():
    # The app can change its short process name to Codex; its executable path is stable.
    result = subprocess.run(["/bin/ps","-axo","comm="],capture_output=True,text=True,timeout=3)
    app=CONFIG.app_path()
    try:executable=plistlib.loads((app/'Contents/Info.plist').read_bytes()).get('CFBundleExecutable',app.stem)
    except (OSError,ValueError):executable=app.stem
    return result.returncode == 0 and str(app/'Contents/MacOS'/executable) in {line.strip() for line in (result.stdout or "").splitlines()}


def start_tunnel():
    return subprocess.Popen([sys.executable,'-u',str(ROOT/'remote_access.py'),'start','--hostname',HOSTNAME,'--tunnel-id',TUNNEL_ID],cwd=ROOT)


def start_server():
    return subprocess.Popen([sys.executable,'-u',str(ROOT/'companion.py')],cwd=ROOT)


class RecoveryBudget:
    """Bound rapid local failures; network retries inside cloudflared remain separate."""
    def __init__(self,initial_delay=3,max_delay=60,max_failures=5,random_value=None):
        self.initial_delay=initial_delay;self.max_delay=max_delay;self.max_failures=max_failures
        self.random_value=random_value or random.random
        self.reset()

    def reset(self):
        self.failures=0;self.started_at=None;self.blocked=False

    def started(self,now):
        self.started_at=now

    def failed(self,now,exit_code=None):
        if self.started_at is not None and now-self.started_at>=60:
            self.failures=0
        self.started_at=None;self.failures+=1
        self.blocked=exit_code in (2,78) or self.max_failures is not None and self.failures>=self.max_failures
        cap=min(self.max_delay,self.initial_delay*2**min(self.failures-1,16))
        return float('inf') if self.blocked else now+cap*(.5+.5*self.random_value())


def recovery_notice(name,budget,retry_at,now):
    if budget.blocked:
        print(f'{name} recovery paused. Review runtime/security status and repair the cause before an explicit restart. Other components remain running.',flush=True)
    else:
        print(f'{name} stopped. Retrying in {int(retry_at-now)} seconds; other components remain running.',flush=True)


def recover_process(children,name,starter,retry_at,now,budget,restart=False):
    process=children.get(name)
    if restart:
        if process and process.poll() is None:stop_children({name:process})
        elif process:process.wait()
        children.pop(name,None);process=None;retry_at=now;budget.reset()
        write_runtime(children)
    if process:
        exit_code=process.poll()
        if exit_code is not None:
            process.wait();children.pop(name);process=None
            retry_at=budget.failed(now,exit_code)
            write_runtime(children);recovery_notice(name,budget,retry_at,now)
    if not process and not budget.blocked and now>=retry_at:
        try:
            children[name]=starter()
        except OSError as error:
            repair_code=78 if error.errno in (errno.ENOENT,errno.ENOTDIR,errno.EACCES,errno.ENOEXEC) else None
            retry_at=budget.failed(now,repair_code)
            recovery_notice(name,budget,retry_at,now)
        else:
            budget.started(now)
        write_runtime(children)
    return retry_at


def recover_server(children,retry_at,now,restart=False,budget=None):
    return recover_process(children,'server',start_server,retry_at,now,
                           budget if budget is not None else RecoveryBudget(),restart)


def start_awake():
    return subprocess.Popen(AWAKE_COMMAND)


def recover_awake(children,retry_at,now,budget,power_state):
    if not should_keep_awake(power_state):
        process = children.pop('awake', None)
        if process:
            if process.poll() is None: stop_children({'awake': process})
            else: process.wait()
            write_runtime(children)
        budget.reset()
        return 0
    return recover_process(children,'awake',start_awake,retry_at,now,budget)


def recover_tunnel(children,retry_at,now,reconnect=False,reason='network_changed',budget=None):
    budget=budget if budget is not None else RecoveryBudget(initial_delay=30,max_delay=300,max_failures=None)
    process=children.get('tunnel')
    if reconnect and reason=='network_changed' and process and process.poll() is None:
        record_event(STATE,'network_change_preserved',supervisor_pid=os.getpid(),controller_pid=process.pid,reason=reason)
        return retry_at
    if reconnect and reason=='manual_restart':budget.reset()
    if reconnect and not budget.blocked:
        record_event(STATE,'reconnect_requested',supervisor_pid=os.getpid(),reason=reason)
        if process and process.poll() is None:stop_children({'tunnel':process})
        elif process:process.wait()
        children.pop('tunnel',None);process=None;retry_at=now+3
        previous=read_record(STATE)
        publish(STATE,'retrying',retry_at=time.time()+3,reason=reason,**{key:previous[key] for key in ('last_ready_at','transport') if key in previous})
        write_runtime(children)
    exit_code=process.poll() if process else None
    if process and exit_code is not None:
        record_event(STATE,'controller_exited',supervisor_pid=os.getpid(),controller_pid=process.pid,exit_code=exit_code)
        process.wait();children.pop('tunnel');process=None;write_runtime(children)
        retry_at=budget.failed(now,exit_code)
        previous=read_record(STATE)
        publish(STATE,'failed' if budget.blocked else 'retrying',
                **({} if budget.blocked else {'retry_at':time.time()+retry_at-now}),
                reason='controller_repair_required' if budget.blocked else previous.get('reason','connector_exited'),
                **{key:previous[key] for key in ('last_ready_at','transport','public_http_status','connections') if key in previous})
        recovery_notice('tunnel',budget,retry_at,now)
    if not process and not budget.blocked and now>=retry_at:
        try:
            children['tunnel']=start_tunnel()
        except OSError as error:
            repair_code=78 if error.errno in (errno.ENOENT,errno.ENOTDIR,errno.EACCES,errno.ENOEXEC) else None
            retry_at=budget.failed(now,repair_code)
            publish(STATE,'failed' if budget.blocked else 'retrying',
                    **({} if budget.blocked else {'retry_at':time.time()+retry_at-now}),
                    reason='controller_repair_required' if budget.blocked else 'startup_failed')
            recovery_notice('tunnel',budget,retry_at,now)
        else:budget.started(now)
        write_runtime(children)
    return retry_at


def network_state(runner=subprocess.run):
    """A private change detector, never an SSID/IP inventory or Wi-Fi setting."""
    try:
        result=runner(['/usr/sbin/scutil','--nwi'],capture_output=True,text=True,timeout=2)
        if result.returncode or not isinstance(result.stdout,str) or len(result.stdout)>65536:return None
        online=bool(re.search(r'^Network interfaces:\s*\S+',result.stdout,re.M))
        return online,hashlib.sha256(result.stdout.encode()).hexdigest()
    except (OSError,subprocess.SubprocessError):return None


class NetworkRecovery:
    def __init__(self):self.previous=None;self.pending_at=None;self.last_reconnect=-float('inf')
    def observe(self,state,now):
        if state is None:return False
        if self.previous is None:self.previous=state;return False
        if state!=self.previous:
            self.previous=state;self.pending_at=now+3 if state[0] else None
        if self.pending_at is not None and now>=self.pending_at and now-self.last_reconnect>=30:
            self.pending_at=None;self.last_reconnect=now;return True
        return False


def request_tunnel_restart():
    STATE.mkdir(mode=0o700,parents=True,exist_ok=True)
    path=STATE/'restart-tunnel.request'
    if path.is_symlink():raise RuntimeError('The tunnel restart request is unavailable.')
    with path.open('w') as stream:stream.write(str(time.time()))
    path.chmod(0o600)


def consume_tunnel_restart():
    path=STATE/'restart-tunnel.request'
    try:
        if path.is_symlink() or not path.is_file():return False
        path.unlink();return True
    except OSError:return False


def request_web_restart():
    STATE.mkdir(mode=0o700,parents=True,exist_ok=True)
    path=STATE/'restart-web.request'
    if path.is_symlink():raise RuntimeError('The web restart request is unavailable.')
    with path.open('w') as stream:stream.write(str(time.time()))
    path.chmod(0o600)


def consume_web_restart():
    path=STATE/'restart-web.request'
    try:
        if path.is_symlink() or not path.is_file():return False
        path.unlink();return True
    except OSError:return False


def supervise():
    os.umask(0o077)
    STATE.mkdir(mode=0o700,exist_ok=True)
    stopping = threading.Event()
    for number in (signal.SIGTERM,signal.SIGINT):
        signal.signal(number,lambda *_:stopping.set())
    children = {}
    with (STATE / "service.lock").open("a+") as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another companion supervisor is already running.")
        try:
            children["server"] = start_server()
            deadline = time.monotonic()+25
            while not stopping.is_set():
                if children["server"].poll() is not None:
                    raise RuntimeError("The companion server exited during startup.")
                try:
                    with urlopen("http://127.0.0.1:8765/api/remote/status",timeout=2) as response:
                        health = json.load(response)
                    if health.get("gateway_port") == 8766 and health.get("authentication_required") is True:
                        break
                except (OSError,ValueError):
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError("The local authenticated gateway did not become ready.")
                stopping.wait(0.5)
            if stopping.is_set():
                return
            now=time.monotonic()
            server_budget=RecoveryBudget();server_budget.started(now)
            awake_budget=RecoveryBudget()
            tunnel_budget=RecoveryBudget(initial_delay=30,max_delay=300,max_failures=None)
            power_state=read_battery_state()
            next_power_check=now+5
            awake_retry_at=recover_awake(children,0,now,awake_budget,power_state)
            tunnel_retry_at=0
            if CONFIG.remote_enabled:
                tunnel_retry_at=recover_tunnel(children,0,now,budget=tunnel_budget)
            write_runtime(children)
            record_event(STATE,'supervisor_started',supervisor_pid=os.getpid())
            print("Companion supervision started. Check component status for readiness. Phone access: "+('configured' if CONFIG.remote_enabled else 'local only'),flush=True)
            server_retry_at=0
            network=NetworkRecovery();next_network_check=0
            while not stopping.wait(1):
                now=time.monotonic();reconnect=False
                server_retry_at=recover_server(children,server_retry_at,now,restart=consume_web_restart(),budget=server_budget)
                if now>=next_power_check:
                    power_state=read_battery_state();next_power_check=now+5
                awake_retry_at=recover_awake(children,awake_retry_at,now,awake_budget,power_state)
                if now>=next_network_check:
                    reconnect=network.observe(network_state(),now);next_network_check=now+5
                if CONFIG.remote_enabled:
                    requested=consume_tunnel_restart()
                    tunnel_retry_at=recover_tunnel(children,tunnel_retry_at,now,reconnect=requested or reconnect,reason='manual_restart' if requested else 'network_changed',budget=tunnel_budget)
        finally:
            stop_children(children)
            runtime = STATE / "service-runtime.json"
            if runtime.exists():
                runtime.unlink()


def launchctl(*args,check=True):
    result = subprocess.run(["/bin/launchctl",*args],capture_output=True,text=True)
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or "launchctl did not complete the requested action.")
    return result


def install(*,background_install_approved=False):
    if not background_install_approved:
        raise RuntimeError('Background installation is explicit. Review SETUP.md and security requirements, then use install --background-install-approved on an approved machine.')
    if PLIST.exists():
        previous = plistlib.loads(PLIST.read_bytes())
        if str(ROOT / "service.py") not in previous.get("ProgramArguments",[]):
            raise RuntimeError("An unrelated LaunchAgent already uses this label; leaving it untouched.")
    # Validate before stopping an existing service or writing files.
    value=definition()
    STATE.mkdir(mode=0o700,exist_ok=True)
    PLIST.parent.mkdir(parents=True,exist_ok=True)
    log_directory=Path(value['StandardOutPath']).parent
    log_paths=[Path(value[key]) for key in ('StandardOutPath','StandardErrorPath')]
    if log_directory.is_symlink() or any(path.is_symlink() for path in log_paths):
        raise RuntimeError('The service log location is a symlink; installation stopped.')
    log_directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    log_directory.chmod(0o700)
    for path in log_paths:
        descriptor=os.open(path,os.O_CREAT|os.O_APPEND|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        try:os.fchmod(descriptor,0o600)
        finally:os.close(descriptor)
    if launchctl("print",TARGET,check=False).returncode == 0:
        launchctl("bootout",TARGET)
    PLIST.write_bytes(plistlib.dumps(value))
    PLIST.chmod(0o600)
    launchctl("enable",TARGET)
    launchctl("bootstrap",f"gui/{os.getuid()}",str(PLIST))
    print("Installed. Starts at login with isolated, bounded recovery. ChatGPT is opened only by the user.")


def status():
    result = launchctl("print",TARGET,check=False)
    if result.returncode:
        print("Background service is not loaded.")
        return
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith(("state =","pid =","runs =","last exit code =")):
            print(stripped)
    runtime = STATE / "service-runtime.json"
    if runtime.exists():
        print(runtime.read_text())
    try:
        with urlopen("http://127.0.0.1:8765/api/remote/status",timeout=3) as response:
            health = json.load(response)
        print("public_origin = " + str(health.get("public_origin")))
    except (OSError,ValueError):
        print("Local gateway is starting or unavailable.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=["install","run","restart","restart-web","status","stop","uninstall"])
    parser.add_argument("--background-install-approved",action="store_true",help="Explicitly opt in to login startup after reviewing machine security requirements")
    args = parser.parse_args()
    if args.background_install_approved and args.action != "install":parser.error("--background-install-approved is only valid with install")
    action = args.action
    if action == "run":
        supervise()
    elif action == "install":
        install(background_install_approved=args.background_install_approved)
    elif action == 'restart-web':
        if launchctl('print',TARGET,check=False).returncode:raise RuntimeError('Start the background service first.')
        request_web_restart()
        print('Web service restart requested. The tunnel and awake guard remain running.')
    elif action == "restart":
        if launchctl("print",TARGET,check=False).returncode:
            if not PLIST.exists():
                raise RuntimeError("Install the background service first.")
            launchctl("enable",TARGET)
            launchctl("bootstrap",f"gui/{os.getuid()}",str(PLIST))
        else:
            launchctl("kickstart","-k",TARGET)
        print("Companion group restart requested.")
    elif action in ("stop","uninstall"):
        if launchctl("print",TARGET,check=False).returncode == 0:
            launchctl("bootout",TARGET)
        if action == "uninstall" and PLIST.exists():
            PLIST.unlink()
        print("Background service stopped." if action == "stop" else "Background service uninstalled; app data retained.")
    else:
        status()


if __name__ == "__main__":
    try:
        main()
    except (OSError,ValueError,RuntimeError) as error:
        print(str(error),file=sys.stderr,flush=True)
        if sys.argv[1:] == ['run']:
            print('Automatic startup stopped. Repair the approved installation before restarting; no automatic reinstall is attempted.',file=sys.stderr,flush=True)
            raise SystemExit(0)
        raise SystemExit(1)
