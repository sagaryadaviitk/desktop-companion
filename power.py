"""Read a small, non-secret power snapshot using macOS system utilities."""

import plistlib
import re
import subprocess
import threading
import time
from xml.parsers.expat import ExpatError


AWAKE_COMMAND = ["/usr/bin/caffeinate", "-is"]
_cache_lock = threading.Lock()
_cached = None
_cached_at = 0.0


def read_power_state(runner=None):
    runner = runner or subprocess.run
    state = {"source":"unknown","battery_percent":None,"charging":None,"lid_closed":None,
             "closed_lid_enabled":None,"lock_immediate":None,
             "display_sleep_battery_minutes":None,"display_sleep_adapter_minutes":None,
             "checked_at":time.time()}
    try:
        result=runner(["/usr/bin/pmset","-g","batt"],capture_output=True,text=True,timeout=3)
        if result.returncode == 0 and isinstance(result.stdout,str):
            if "'AC Power'" in result.stdout:state["source"]="adapter"
            elif "'Battery Power'" in result.stdout:state["source"]="battery"
            percent=re.search(r"\b([0-9]{1,3})%;",result.stdout)
            if percent and 0 <= int(percent[1]) <= 100:state["battery_percent"]=int(percent[1])
            charge=re.search(r"\b[0-9]{1,3}%;\s*([^;\r\n]+)",result.stdout)
            if charge:
                status=charge[1].strip().lower()
                if status in ('charging','finishing charge'):state['charging']=True
                elif status in ('discharging','charged','not charging','ac attached'):state['charging']=False
    except (OSError,subprocess.SubprocessError):
        pass
    try:
        result=runner(["/usr/sbin/ioreg","-a","-r","-c","IOPMrootDomain","-d","1"],capture_output=True,timeout=3)
        if result.returncode == 0:
            rows=plistlib.loads(result.stdout)
            if isinstance(rows,list) and rows and isinstance(rows[0],dict):
                for source,target in [("AppleClamshellState","lid_closed"),("SleepDisabled","closed_lid_enabled")]:
                    value=rows[0].get(source)
                    if type(value) is bool:state[target]=value
    except (OSError,ValueError,TypeError,ExpatError,subprocess.SubprocessError):
        pass
    try:
        result=runner(["/usr/sbin/sysadminctl","-screenLock","status"],capture_output=True,text=True,timeout=3)
        output=(result.stdout or "")+"\n"+(result.stderr or "")
        if result.returncode == 0:
            match=re.search(r"screenLock delay is ([^\r\n]+)",output)
            if match:state["lock_immediate"]=match[1].strip()=="immediate"
    except (OSError,subprocess.SubprocessError):
        pass
    try:
        result=runner(["/usr/bin/pmset","-g","custom"],capture_output=True,text=True,timeout=3)
        if result.returncode == 0:
            section=None
            for line in (result.stdout or "").splitlines():
                if line.strip()=="Battery Power:":section="display_sleep_battery_minutes"
                elif line.strip()=="AC Power:":section="display_sleep_adapter_minutes"
                elif line.strip().endswith(":"):section=None
                match=re.fullmatch(r"\s*displaysleep\s+([0-9]+)\s*",line)
                if section and match:state[section]=int(match[1])
    except (OSError,subprocess.SubprocessError):
        pass
    return state


def cached_power_state():
    global _cached,_cached_at
    with _cache_lock:
        now=time.monotonic()
        if _cached is None or now-_cached_at >= 5:
            _cached=read_power_state();_cached_at=time.monotonic()
        return dict(_cached)


def read_guard(supervisor,runner=None):
    runner=runner or subprocess.run
    value={"running":False,"battery_covered":False}
    try:
        child=runner(["/usr/bin/pgrep","-P",str(supervisor),"-x","caffeinate"],capture_output=True,text=True,timeout=3)
        if child.returncode != 0:return value
        for pid in (child.stdout or "").split():
            if not pid.isdigit():continue
            result=runner(["/bin/ps","-p",pid,"-o","args="],capture_output=True,text=True,timeout=3)
            if result.returncode != 0:continue
            args=(result.stdout or "").split()
            if args and args[0]=="/usr/bin/caffeinate":
                value["running"]=True
                if args==AWAKE_COMMAND:value["battery_covered"]=True
    except (OSError,subprocess.SubprocessError):
        pass
    return value
