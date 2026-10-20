#!/usr/bin/env python3
"""Private, local process interface used by the native Mac control app."""

import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time
from urllib.request import urlopen

from access import AccessStore, RemoteSettings
from remote_access import public_status
import service
from system_status import tunnel_snapshot
from power import read_power_state,read_guard
from computer_use import computer_use_snapshot, update_locked_use


ROOT = Path(__file__).resolve().parent


def local_json(path):
    try:
        with urlopen("http://127.0.0.1:8765"+path,timeout=2) as response:
            return json.load(response)
    except (OSError,ValueError):
        return None


def component(identifier,title,state,detail,icon,action=None,action_title=None):
    return {"id":identifier,"title":title,"state":state,"detail":detail,"icon":icon,"action":action,"action_title":action_title}


def gateway_health(port):
    if type(port) is not int or not 1<=port<=65535:return False
    try:
        with urlopen(f'http://127.0.0.1:{port}/healthz',timeout=1) as response:value=json.load(response)
        return value.get('authentication_required') is True and value.get('gateway_version')==1
    except (OSError,ValueError):return False


def history_readable():
    from contextlib import closing
    home=Path(os.environ.get('CODEX_HOME',Path.home()/'.codex'))
    try:
        for filename,table in [('state_5.sqlite','threads'),('thread_history_1.sqlite','thread_items')]:
            with closing(sqlite3.connect((home/filename).resolve().as_uri()+'?mode=ro',uri=True)) as db:db.execute('SELECT 1 FROM '+table+' LIMIT 0')
        return True
    except (OSError,sqlite3.Error):return False


def read_snapshot(root=ROOT):
    loaded = service.launchctl("print",service.TARGET,check=False)
    running = bool(loaded.returncode == 0 and re.search(r"^\s*state = running\s*$",loaded.stdout,re.M))
    server = local_json("/api/status")
    gateway = local_json("/api/remote/status")
    origin = RemoteSettings(root/".state"/"remote.json").origin()
    tunnel=tunnel_snapshot(root/'.state',configured=service.CONFIG.remote_enabled)
    phone_ready = False
    if tunnel.get('phase')!='unknown':phone_ready=tunnel['public_ready']
    elif gateway and origin:
        try:
            phone_ready = public_status(origin) == 401
        except (OSError,ValueError,RuntimeError,subprocess.SubprocessError):
            pass
    desktop = service.desktop_app_running()
    power = read_power_state()
    plugged_in = power["source"] == "adapter"
    server_ready = bool(server and server.get("phone_setup_available") and server.get("mode") == "local_preview")
    queue_ready = bool(server_ready and server.get("control",{}).get("available"))
    gateway_ready=bool(gateway and gateway_health(gateway.get('gateway_port')))
    network=service.network_state()
    history_ready=history_readable()
    supervisor = re.search(r"^\s*pid = (\d+)\s*$",loaded.stdout,re.M)
    guard = read_guard(supervisor[1]) if running and supervisor else {"running":False,"battery_covered":False}
    awake = guard["battery_covered"]
    power["idle_guard_active"]=awake
    awake_detail=("Idle sleep is blocked on battery and charger power." if awake else "Restart the guard to cover battery and charger power." if guard["running"] else "Starts with the background service.")
    lid_enabled=power["closed_lid_enabled"]
    background_action = "restart" if loaded.returncode == 0 else "start"
    components = [
        component("background","Automatic recovery","ready" if running else "stopped","Starts at login and repairs stopped services." if running else "Start the system to enable recovery.","arrow.triangle.2.circlepath",background_action,"Restart" if running else "Start"),
        component("server","Desktop service","ready" if server_ready else "stopped","Your conversation dashboard is available." if server_ready else "The dashboard is not responding.","desktopcomputer","restart_web" if running else "start","Restart service" if running else "Start system"),
        component("phone","Public phone access","ready" if phone_ready else "warning" if running else "stopped","Protected HTTPS is reachable; unpaired chats are blocked." if phone_ready else tunnel['detail'],"iphone.radiowaves.left.and.right","restart_tunnel","Reconnect tunnel"),
        component("desktop","ChatGPT app","ready" if desktop else "stopped","The desktop app is running." if desktop else "Open ChatGPT to process chat instructions.","bubble.left.and.bubble.right","open_desktop","Open app"),
        component("queue","Instruction delivery","ready" if queue_ready else "warning","Ready to queue follow-ups for existing chats." if queue_ready else "Start the desktop service and check ChatGPT.","text.bubble","open_dashboard","Open conversations"),
        component("awake","Keep Mac awake","ready" if awake else "warning" if guard["running"] else "stopped",awake_detail,"battery.100","restart","Restart guard"),
        component("lid","Closed-lid mode","ready" if lid_enabled is True else "warning","System sleep is disabled, including on battery. Physical lid-close verification is still required." if lid_enabled is True else "Enable this with your Mac administrator password in Unattended mode." if lid_enabled is False else "The system sleep setting could not be read.","laptopcomputer","open_power","Manage power"),
        component('gateway','Protected phone gateway','ready' if gateway_ready else 'stopped','Authentication gateway is responding on this Mac.' if gateway_ready else 'The separate authentication gateway is not responding.','lock.shield','restart_web' if running else 'start','Restart service' if running else 'Start system'),
        component('tunnel','Cloudflare tunnel',tunnel['state'],tunnel['detail']+((' '+str(tunnel['connections'])+' edge connections.') if tunnel.get('connections') else ''),'network','restart_tunnel','Reconnect tunnel'),
        component('cloudflare','Cloudflare edge','ready' if not tunnel['stale'] and tunnel.get('public_http_status',0)>=100 else 'warning','Cloudflare is answering HTTPS. The connection back to this Mac is checked separately.' if not tunnel['stale'] and tunnel.get('public_http_status',0)>=100 else 'No fresh HTTPS response from Cloudflare. Check the Mac’s network connection.','cloud'),
        component('network','Mac network','ready' if network and network[0] else 'warning','A network interface is available. Cloudflare reachability is checked separately.' if network and network[0] else 'Waiting for macOS to join a saved network. Reconnection runs automatically.','wifi','open_network','Wi-Fi settings'),
        component('history','Conversation history','ready' if history_ready else 'warning','Local chat and message stores are readable.' if history_ready else 'Open ChatGPT and check that local history is available.','clock.arrow.circlepath','open_desktop','Open app'),
    ]
    access_error = None
    try:
        store = AccessStore(root/".state"/"access.sqlite")
        devices,history = store.devices(include_history=True),store.pairing_history()
    except (sqlite3.Error,OSError):
        devices,history = [],[]
        access_error = "Access records could not be read. Restart the system and try again."
    components.append(component('access','Pairing & browser access','warning' if access_error else 'ready',access_error or 'Private pairing and browser-access records are available.','key','open_phone','Manage access'))
    required={'background','server','phone','desktop','queue','awake','gateway','tunnel','network','history','access'}
    issues=[item for item in components if item['id'] in required and item['state'] not in ('ready','disabled')]
    summary={'state':'ready' if not issues else 'warning','title':'All components are ready' if not issues else str(len(issues))+' components need attention','detail':'Phone access is protected and the Mac is ready.' if not issues else 'Local and phone connectivity are checked independently. Open a component for recovery.'}
    return {"components":components,"devices":devices,"pairing_history":history,"access_error":access_error,
            'summary':summary,'tunnel':tunnel,
            "phone_url":origin or ('https://'+service.HOSTNAME if service.HOSTNAME else ''),"dashboard_url":"http://127.0.0.1:8765",
            "can_pair":phone_ready and server_ready and not access_error,"running":running,"plugged_in":plugged_in,"power":power,"computer_use":computer_use_snapshot(root/'.state'/'computer-use-check.json'),"checked_at":time.time()}


def perform(action,identifier=None,root=ROOT):
    if action == "status":
        return {"ok":True,"snapshot":read_snapshot(root)}
    if action == 'update_locked_use':
        return update_locked_use()
    if action=='restart_tunnel':
        if not service.CONFIG.remote_enabled:raise RuntimeError('Phone access has not been configured on this Mac.')
        if service.launchctl('print',service.TARGET,check=False).returncode:raise RuntimeError('Start the background service first.')
        service.request_tunnel_restart()
        return {'ok':True,'message':'Reconnecting the phone tunnel. Chats and the local dashboard keep running.'}
    if action=='restart_web':
        if service.launchctl('print',service.TARGET,check=False).returncode:raise RuntimeError('Start the background service first.')
        service.request_web_restart()
        return {'ok':True,'message':'Restarting the dashboard and gateway. The tunnel, desktop app, and awake guard keep running.'}
    if action in ("start","restart"):
        loaded = service.launchctl("print",service.TARGET,check=False)
        if loaded.returncode != 0:
            if service.PLIST.exists():
                service.launchctl("enable",service.TARGET)
                service.launchctl("bootstrap",f"gui/{os.getuid()}",str(service.PLIST))
            else:
                # Keep stdout a single JSON response for the native client.
                import contextlib
                import io
                with contextlib.redirect_stdout(io.StringIO()):
                    service.install()
        else:
            service.launchctl("kickstart","-k",service.TARGET)
        return {"ok":True,"message":"Starting your system. The connection will recover automatically."}
    if action == "stop":
        if service.launchctl("print",service.TARGET,check=False).returncode == 0:
            service.launchctl("bootout",service.TARGET)
        return {"ok":True,"message":"System stopped. It will start again at your next login."}
    store = AccessStore(root/".state"/"access.sqlite")
    if action == "pair":
        server = local_json("/api/remote/status")
        origin = RemoteSettings(root/".state"/"remote.json").origin()
        if not server or not origin or public_status(origin) != 401:
            raise RuntimeError("Start the system and wait for Phone connection to become ready, then create a code.")
        return {"ok":True,"pairing":store.create_pairing(),"message":"Enter this code on your phone. It works once and expires in five minutes."}
    if not identifier or not re.fullmatch(r"[a-f0-9]{24}",identifier):
        raise ValueError("Select a valid access record.")
    if action == "revoke":
        store.revoke(identifier)
        return {"ok":True,"message":"Browser access revoked. Pair again to reconnect it."}
    if action == "cancel_code":
        store.cancel_pairing(identifier)
        return {"ok":True,"message":"Pairing code cancelled."}
    raise ValueError("Unknown control action.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=["status","start","restart","restart_web","restart_tunnel","stop","pair","revoke","cancel_code","update_locked_use"])
    parser.add_argument("--id")
    args = parser.parse_args()
    try:
        value = perform(args.action,args.id)
    except RuntimeError as error:
        # This action raises only our fixed, user-facing installer diagnostics.
        value = {"ok":False,"message":str(error) if args.action == 'update_locked_use' else "The action could not complete. Check the component status and try again."}
    except (OSError,sqlite3.Error,ValueError,subprocess.SubprocessError):
        value = {"ok":False,"message":"The action could not complete. Check the component status and try again."}
    print(json.dumps(value))


if __name__ == "__main__":
    main()
