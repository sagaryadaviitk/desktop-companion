"""Small, allowlisted health records shared by the tunnel and control surfaces.

No credentials, process arguments, network names, IP addresses, or raw logs leave
this module. A running process alone never establishes public phone access.
"""

import json
import fcntl
import os
from pathlib import Path
import re
import tempfile
import threading
import time


PHASES={'starting','verifying','ready','degraded','failed','retrying','stopped'}
REASONS={
    'edge_connection_closed':'The connection to Cloudflare was closed. Check whether the current network allows Cloudflare Tunnel.',
    'edge_handshake_timeout':'Cloudflare’s tunnel handshake timed out. The current network may block its connection.',
    'edge_tls_inspection':'Zscaler TLS inspection is present, and fresh tunnel connections are failing. Check the approved Cloudflare Tunnel policy on port 7844 with IT. Recovery will keep retrying.',
    'public_unreachable':'The public HTTPS check did not complete. Recovery will retry automatically.',
    'public_error':'Cloudflare cannot currently reach the protected gateway.',
    'authentication_bypass':'The public authentication check failed. Phone access was stopped to protect your chats.',
    'connector_exited':'The Cloudflare connector stopped. Automatic recovery is restarting it.',
    'network_changed':'The Mac’s network changed. Reconnecting the phone tunnel.',
    'manual_restart':'Reconnecting only the phone tunnel. Chats and the local dashboard keep running.',
    'startup_failed':'The protected phone connection could not start. Check the local gateway and Cloudflare configuration.',
    'controller_repair_required':'Tunnel recovery paused after a permanent or repeated local failure. Review runtime files and security status before an explicit reconnect.',
}

EVENTS={'supervisor_started','connector_started','connector_exited','public_ready','public_degraded',
        'network_change_preserved','reconnect_requested','controller_exited','watchdog_recovery','authentication_bypass'}


def record_event(state,event,**values):
    """Bounded local lifecycle history. Never accepts free-form log/network data."""
    if event not in EVENTS:return
    state=Path(state);entry={'at':time.time(),'event':event}
    for key in ('supervisor_pid','controller_pid','connector_pid','status','exit_code','connections'):
        value=values.get(key)
        if type(value) is int and -255<=value<10**9:entry[key]=value
    if values.get('reason') in REASONS:entry['reason']=values['reason']
    try:
        state.mkdir(mode=0o700,parents=True,exist_ok=True)
        lock_path=state/'tunnel-events.lock';path=state/'tunnel-events.jsonl';previous=state/'tunnel-events.previous.jsonl'
        if any(p.is_symlink() for p in (lock_path,path,previous)):return
        descriptor=os.open(lock_path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        os.fchmod(descriptor,0o600)
        with os.fdopen(descriptor,'a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            if path.exists() and path.stat().st_size>=256*1024:path.replace(previous)
            descriptor=os.open(path,os.O_CREAT|os.O_APPEND|os.O_WRONLY|os.O_NOFOLLOW,0o600)
            os.fchmod(descriptor,0o600)
            with os.fdopen(descriptor,'a') as stream:stream.write(json.dumps(entry,separators=(',',':'))+'\n')
    except OSError:pass  # Diagnostic failure must never stop a working connection.


def read_record(state):
    path=Path(state)/'tunnel-status.json'
    try:
        if path.is_symlink() or path.stat().st_size>4096:return {}
        value=json.loads(path.read_text())
        if not isinstance(value,dict):return {}
        result={}
        if value.get('phase') in PHASES:result['phase']=value['phase']
        if value.get('reason') in REASONS:result['reason']=value['reason']
        if value.get('transport') in ('http2','quic','auto'):result['transport']=value['transport']
        for key in ('checked_at','started_at','last_ready_at','retry_at'):
            if type(value.get(key)) in (int,float) and 0<value[key]<10**12:result[key]=value[key]
        for key in ('controller_pid','connector_pid','connections','public_http_status'):
            if type(value.get(key)) is int and 0<=value[key]<10**9:result[key]=value[key]
        return result
    except (OSError,ValueError):return {}


def publish(state,phase,**values):
    if phase not in PHASES:raise ValueError('Unknown tunnel phase.')
    state=Path(state);state.mkdir(mode=0o700,parents=True,exist_ok=True)
    record={'phase':phase,'checked_at':time.time(),**values}
    descriptor,name=tempfile.mkstemp(prefix='tunnel-status-',suffix='.tmp',dir=state)
    temporary=Path(name)
    try:
        with os.fdopen(descriptor,'w') as stream:json.dump(record,stream)
        temporary.replace(state/'tunnel-status.json')
    finally:temporary.unlink(missing_ok=True)


class TunnelReporter:
    def __init__(self,state,transport='http2'):
        self.state=Path(state);self.lock=threading.RLock();self.connected=set()
        previous=read_record(state)
        self.value={'controller_pid':os.getpid(),'started_at':time.time(),'transport':transport,'connections':0}
        if previous.get('last_ready_at'):self.value['last_ready_at']=previous['last_ready_at']
        self.update('starting')

    def update(self,phase=None,**values):
        with self.lock:
            before=self.value.get('phase')
            self.value.update(values)
            if phase:self.value['phase']=phase
            if phase=='ready':self.value['last_ready_at']=time.time();self.value.pop('reason',None)
            try:publish(self.state,self.value['phase'],**{k:v for k,v in self.value.items() if k!='phase'})
            except OSError:pass  # A diagnostic write must not take down phone access.
            if phase in ('ready','degraded') and phase!=before:
                record_event(self.state,'public_ready' if phase=='ready' else 'public_degraded',
                             controller_pid=os.getpid(),status=self.value.get('public_http_status'),connections=len(self.connected))

    def log(self,line):
        with self.lock:
            index=re.search(r'\bconnIndex=(\d+)\b',line)
            if 'Registered tunnel connection' in line and index:self.connected.add(index[1])
            elif index and any(text in line for text in ('Lost connection with the edge','Serve tunnel error','Unregistered tunnel connection')):self.connected.discard(index[1])
            reason='edge_connection_closed' if 'connection reset by peer' in line or 'connection with edge closed' in line else 'edge_handshake_timeout' if 'handshake did not complete' in line else None
            if index or reason:self.update(connections=len(self.connected),**({'reason':reason} if reason else {}))

    def checked(self,status):
        if status==200:self.update('failed',public_http_status=200,reason='authentication_bypass')
        elif status==401:self.update('ready',public_http_status=401)
        else:self.update('degraded',public_http_status=status or 0,reason=self.value.get('reason') if self.value.get('reason','').startswith('edge_') else 'public_error' if status else 'public_unreachable')


def tunnel_snapshot(state,configured=True,now=None):
    now=time.time() if now is None else now
    if not configured:return {'state':'disabled','phase':'not_configured','detail':'Local-only setup. Phone access has not been configured.','public_ready':False,'stale':False}
    record=read_record(state);phase=record.get('phase','unknown');age=now-record.get('checked_at',0)
    retrying=phase=='retrying' and 0<=record.get('retry_at',0)-now<=180
    stale=not record or age<0 or age>90 and not retrying
    ready=not stale and phase=='ready' and record.get('public_http_status')==401
    detail=REASONS.get(record.get('reason'),'Waiting for a verified Cloudflare connection.')
    if ready:detail='Protected public HTTPS is reachable. Unpaired chat access is blocked.'
    elif stale:detail='No fresh tunnel health check. The connector may be stopped or recovering.'
    elif phase=='starting':detail='Starting the Cloudflare connector.'
    elif phase=='verifying':detail='Checking the public HTTPS connection and authentication gate.'
    remaining=max(0,int(record.get('retry_at',0)-now))
    if retrying:detail+=' Retrying in '+str(remaining)+' seconds.'
    return {**{key:record[key] for key in ('checked_at','last_ready_at','connections','transport','public_http_status') if key in record},
            'state':'ready' if ready else 'warning','phase':phase,'detail':detail,'public_ready':ready,'stale':stale,'retry_in_seconds':remaining if retrying else None}
