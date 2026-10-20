#!/usr/bin/env python3
"""Manage this companion's macOS LaunchAgent and supervised process group."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from urllib.request import urlopen

from power import AWAKE_COMMAND
from configuration import load
from system_status import publish,read_record,record_event


ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".state"
LABEL = "me.sagaryadav.desktop-companion"
CONFIG = load(ROOT)
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
        "ProgramArguments": [str(python), "-u", str(root / "service.py"), "run"],
        "WorkingDirectory": str(root),
        "EnvironmentVariables": {
            "HOME": str(home),
            "PATH": str(Path(codex).parent) + ":/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONUNBUFFERED": "1",
        },
        "KeepAlive": True,
        "ThrottleInterval": 15,
        "ExitTimeOut": 20,
        "AbandonProcessGroup": False,
        "Umask": 0o077,
        "StandardOutPath": str(root / ".state" / "service.log"),
        "StandardErrorPath": str(root / ".state" / "service-error.log"),
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


def ensure_desktop_app():
    if desktop_app_running():
        return
    app = CONFIG.app_path()
    if not app.is_dir():
        return
    result = subprocess.run(["/usr/bin/open","-g","-j",str(app)],capture_output=True,timeout=10)
    print("Opened the desktop app." if result.returncode == 0 else "The desktop app could not be opened automatically.",flush=True)


def start_tunnel():
    return subprocess.Popen([sys.executable,'-u',str(ROOT/'remote_access.py'),'start','--hostname',HOSTNAME,'--tunnel-id',TUNNEL_ID],cwd=ROOT)


def start_server():
    return subprocess.Popen([sys.executable,'-u',str(ROOT/'companion.py')],cwd=ROOT)


def recover_server(children,retry_at,now,restart=False):
    process=children.get('server')
    if restart:
        if process and process.poll() is None:stop_children({'server':process})
        children.pop('server',None);process=None;retry_at=now
    if process and process.poll() is not None:
        process.wait();children.pop('server');process=None;retry_at=now+3
        print('Desktop service stopped. Recovering it without interrupting the tunnel or awake guard.',flush=True)
    if not process and now>=retry_at:
        children['server']=start_server();write_runtime(children)
    return retry_at


def recover_tunnel(children,retry_at,now,reconnect=False,reason='network_changed'):
    process=children.get('tunnel')
    if reconnect and reason=='network_changed' and process and process.poll() is None:
        # Interface metadata changes do not prove a broken path. cloudflared owns
        # reconnection for a live connector; never discard its established edges.
        record_event(STATE,'network_change_preserved',supervisor_pid=os.getpid(),controller_pid=process.pid,reason=reason)
        return retry_at
    if reconnect:
        record_event(STATE,'reconnect_requested',supervisor_pid=os.getpid(),reason=reason)
        if process and process.poll() is None:stop_children({'tunnel':process})
        children.pop('tunnel',None);process=None;retry_at=now+3
        previous=read_record(STATE)
        publish(STATE,'retrying',retry_at=time.time()+3,reason=reason,**{key:previous[key] for key in ('last_ready_at','transport') if key in previous})
        write_runtime(children)
    if process and process.poll() is not None:
        record_event(STATE,'controller_exited',supervisor_pid=os.getpid(),controller_pid=process.pid,exit_code=process.returncode)
        process.wait();children.pop('tunnel');write_runtime(children)
        previous=read_record(STATE)
        publish(STATE,'retrying',retry_at=time.time()+30,reason=previous.get('reason','connector_exited'),**{key:previous[key] for key in ('last_ready_at','transport','public_http_status','connections') if key in previous})
        print('Phone tunnel disconnected. Retrying in 30 seconds; local Companion stays available.',flush=True)
        return now+30
    if not process and now>=retry_at:
        children['tunnel']=start_tunnel();write_runtime(children)
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
            children["awake"] = subprocess.Popen(AWAKE_COMMAND)
            if CONFIG.remote_enabled:
                children["tunnel"] = start_tunnel()
            write_runtime(children)
            record_event(STATE,'supervisor_started',supervisor_pid=os.getpid())
            print("Supervised companion and battery/adapter idle-sleep prevention started. Phone access: "+('configured' if CONFIG.remote_enabled else 'local only'),flush=True)
            next_app_check = 0
            tunnel_retry_at=0
            server_retry_at=0
            network=NetworkRecovery();next_network_check=0
            while not stopping.wait(1):
                now=time.monotonic();reconnect=False
                server_retry_at=recover_server(children,server_retry_at,now,restart=consume_web_restart())
                if now>=next_network_check:
                    reconnect=network.observe(network_state(),now);next_network_check=now+5
                if CONFIG.remote_enabled:
                    requested=consume_tunnel_restart()
                    tunnel_retry_at=recover_tunnel(children,tunnel_retry_at,now,reconnect=requested or reconnect,reason='manual_restart' if requested else 'network_changed')
                for name,process in children.items():
                    if name in ('tunnel','server'):continue
                    if process.poll() is not None:
                        raise RuntimeError(f"The {name} process exited; restarting the companion group.")
                if time.monotonic() >= next_app_check:
                    try:
                        ensure_desktop_app()
                    except (OSError,subprocess.TimeoutExpired):
                        print("The desktop app launch check is temporarily unavailable.",flush=True)
                    next_app_check = time.monotonic()+15
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


def install():
    STATE.mkdir(mode=0o700,exist_ok=True)
    PLIST.parent.mkdir(parents=True,exist_ok=True)
    if PLIST.exists():
        previous = plistlib.loads(PLIST.read_bytes())
        if str(ROOT / "service.py") not in previous.get("ProgramArguments",[]):
            raise RuntimeError("An unrelated LaunchAgent already uses this label; leaving it untouched.")
    for filename in ("service.log","service-error.log"):
        path = STATE / filename
        path.touch(exist_ok=True)
        path.chmod(0o600)
    if launchctl("print",TARGET,check=False).returncode == 0:
        launchctl("bootout",TARGET)
    PLIST.write_bytes(plistlib.dumps(definition()))
    PLIST.chmod(0o600)
    launchctl("enable",TARGET)
    launchctl("bootstrap",f"gui/{os.getuid()}",str(PLIST))
    print("Installed. Starts at user login and restarts automatically after an exit.")


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
    action = parser.parse_args().action
    if action == "run":
        supervise()
    elif action == "install":
        install()
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
        raise SystemExit(1)
