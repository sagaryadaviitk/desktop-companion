#!/usr/bin/env python3
"""Activate an HTTPS tunnel to the authenticated phone gateway only."""

import argparse
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from desktop_companion.access import RemoteSettings
from desktop_companion.system_status import TunnelReporter,record_event


from desktop_companion import ROOT


def inspected_tunnel_reason(runner=subprocess.run):
    """Inspect only the public edge certificate after a reset, without credentials.

    This bounded diagnostic neither alters cloudflared's TLS checks nor makes a
    data request. Only the known issuer name becomes an allowlisted status reason.
    """
    try:
        result=runner(['/usr/bin/openssl','s_client','-connect','region1.v2.argotunnel.com:7844','-servername','h2.cftunnel.com','-alpn','h2'],input='',capture_output=True,text=True,timeout=4)
        output=result.stdout[:20000]+'\n'+result.stderr[:20000]
        if any('zscaler' in line.lower() for line in output.splitlines() if line.lstrip().startswith('issuer=')):
            return 'edge_tls_inspection'
    except (OSError,subprocess.SubprocessError):
        pass
    return None


def get_json(url, headers=None, timeout=8):
    with urlopen(Request(url, headers=headers or {}), timeout=timeout) as response:
        return json.load(response)


def preflight(local_port, settings):
    status = get_json(f"http://127.0.0.1:{local_port}/api/remote/status")
    port = status.get("gateway_port")
    if status.get("gateway_version") != 1 or not isinstance(port, int) or port == local_port:
        raise RuntimeError("The separate authenticated gateway is unavailable. Restart companion.py first.")
    health = get_json(f"http://127.0.0.1:{port}/healthz")
    if health.get("authentication_required") is not True or health.get("gateway_version") != 1:
        raise RuntimeError("Refusing to expose a service without the authentication gateway.")
    previous = settings.origin()
    candidate = "https://preflight.invalid"
    settings.set_origin(candidate)
    try:
        for method, path in [("GET", "/api/chats"), ("POST", "/api/command"), ("POST", "/api/pairing")]:
            request = Request(f"http://127.0.0.1:{port}{path}", method=method, headers={"Host":"preflight.invalid", "Origin":candidate, "X-Forwarded-Proto":"https"})
            try:
                with urlopen(request, timeout=4):
                    raise RuntimeError("Unauthenticated access was allowed. Tunnel activation refused.")
            except HTTPError as error:
                try:
                    if error.code != 401:
                        raise RuntimeError("Authentication preflight failed. Tunnel activation refused.") from error
                finally:
                    error.close()
    finally:
        settings.set_origin(previous)
    return port


def named_configuration(state, hostname, tunnel_id, port, credentials_root=None):
    if not RemoteSettings.valid_origin("https://" + hostname):
        raise ValueError("A hostname without a scheme or path is required.")
    if not re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", tunnel_id):
        raise ValueError("A canonical tunnel UUID is required.")
    credentials = (credentials_root or Path.home() / ".cloudflared") / (tunnel_id + ".json")
    if not credentials.is_file():
        raise ValueError("The named tunnel's credentials file is unavailable.")
    config = state / "cloudflared-named.yml"
    # JSON is also valid YAML and avoids interpolation into a config template.
    config.write_text(json.dumps({"tunnel": tunnel_id, "credentials-file": str(credentials), "ingress": [
        {"hostname": hostname, "service": f"http://127.0.0.1:{port}"},
        {"service": "http_status:404"},
    ]}) + "\n")
    config.chmod(0o600)
    return config


def public_status(origin, address=None):
    # An explicit public DNS answer can verify a fresh hostname while the local
    # resolver retains NXDOMAIN. TLS still validates the original hostname.
    command = ["/usr/bin/curl", "--disable", "--silent", "--show-error", "--connect-timeout", "4", "--max-time", "8"]
    host = origin.removeprefix("https://")
    if address:
        parsed = ipaddress.ip_address(address)
        if parsed.version != 4 or not parsed.is_global:
            raise ValueError("Verification requires a public IPv4 DNS answer.")
        command.extend(["--resolve", f"{host}:443:{parsed}"])
    command.extend(["--output", os.devnull, "--write-out", "%{http_code}", origin + "/api/chats"])
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired as error:
        raise URLError("The public HTTPS verification request timed out.") from error
    if result.returncode or not re.fullmatch(r"[1-5][0-9]{2}",result.stdout):
        raise URLError("The public HTTPS probe failed; certificate verification remains enabled. " + result.stderr.strip()[:300])
    return int(result.stdout)


def monitor_tunnel(process, origin, address=None, interval=20, wait=time.sleep,reporter=None, recovery_grace=300, now=time.monotonic):
    failures = 0
    failure_since = None
    while process.poll() is None:
        wait(interval)
        if process.poll() is not None:
            return
        try:
            status = public_status(origin,address)
            if reporter:reporter.checked(status)
            if status == 200:
                if reporter:record_event(reporter.state,'authentication_bypass',status=200)
                raise RuntimeError("Public access bypassed authentication; stopping the tunnel.")
            failures = 0 if status == 401 else failures + 1
        except (URLError,TimeoutError):
            if reporter:reporter.checked(None)
            failures += 1
        if not failures:failure_since=None
        elif failure_since is None:failure_since=now()
        # A local DNS/proxy/probe failure is not proof that all registered edges
        # failed. Preserve them, and let cloudflared recover individual links.
        connections=reporter.value.get('connections',0) if reporter else 0
        if failures >= 3 and not connections and now()-failure_since >= recovery_grace:
            if reporter:record_event(reporter.state,'watchdog_recovery',controller_pid=os.getpid(),connections=0)
            raise RuntimeError("No registered tunnel connections after sustained public check failures; reconnecting.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check", "start"])
    parser.add_argument("--local-port", type=int, default=8765)
    parser.add_argument("--hostname", help="Stable hostname already routed to the named tunnel")
    parser.add_argument("--tunnel-id", help="Existing named tunnel UUID; requires --hostname")
    parser.add_argument("--verify-address", help="Public IPv4 DNS answer for verification only; preserves TLS hostname validation")
    args = parser.parse_args()
    if bool(args.hostname) != bool(args.tunnel_id):
        parser.error("--hostname and --tunnel-id must be supplied together")
    state = ROOT / ".state"
    state.mkdir(mode=0o700, exist_ok=True)
    settings = RemoteSettings(state / "remote.json")
    lock_path = state / "tunnel.lock"
    with lock_path.open("a+") as lock:
        lock_path.chmod(0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another tunnel controller is already running.")
        reporter=TunnelReporter(state) if args.action=='start' else None
        try:port = preflight(args.local_port, settings)
        except (OSError,ValueError,RuntimeError):
            if reporter:reporter.update('failed',reason='startup_failed')
            raise
        if args.action == "check":
            print("Authentication preflight passed. No tunnel was started.")
            return
        executable = shutil.which("cloudflared")
        if not executable:
            raise SystemExit("cloudflared is not installed.")
        if args.hostname:
            config = named_configuration(state, args.hostname, args.tunnel_id, port)
        else:
            config = state / "cloudflared.yml"
            config.write_text("{}\n")
            config.chmod(0o600)
        environment = {key:value for key,value in os.environ.items() if not key.startswith(("TUNNEL_", "CF_", "CLOUDFLARE_"))}
        # HTTP/2 avoids waiting on blocked UDP/QUIC before transport fallback.
        command = [executable,"tunnel","--config",str(config),"--no-autoupdate","--protocol","http2","--metrics","127.0.0.1:0","--loglevel","info","--transport-loglevel","warn"]
        command.extend(["run",args.tunnel_id] if args.hostname else ["--url",f"http://127.0.0.1:{port}"])
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=environment)
        reporter.update(connector_pid=process.pid)
        record_event(state,'connector_started',controller_pid=os.getpid(),connector_pid=process.pid)
        lines = queue.Queue(maxsize=1000)
        log_path = state / "tunnel.log"
        origin = "https://" + args.hostname if args.hostname else None
        if log_path.is_file() and log_path.stat().st_size>=512*1024:log_path.replace(state/'tunnel.previous.log')
        with log_path.open("a") as log:
            log_path.chmod(0o600)
            log.write(time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())+' controller_started pid='+str(os.getpid())+'\n');log.flush()
            def drain():
                for line in process.stdout:
                    log.write(line); log.flush()
                    reporter.log(line)
                    try:
                        lines.put_nowait(line)
                    except queue.Full:
                        pass
            reader = threading.Thread(target=drain, daemon=True)
            reader.start()
            try:
                deadline = time.monotonic() + 45
                while not origin and time.monotonic() < deadline and process.poll() is None:
                    try:
                        line = lines.get(timeout=1)
                    except queue.Empty:
                        continue
                    match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                    if match:
                        origin = match[0]
                        break
                if not origin:
                    raise RuntimeError("The tunnel did not provide a URL. No phone connection was enabled.")
                settings.set_origin(origin)
                reporter.update('verifying')
                verified = False
                # Fresh tunnel DNS can outlive a resolver's negative cache TTL.
                # Keep the gate closed to users until the public check succeeds.
                print("Tunnel allocated; waiting for public DNS and authentication verification.", flush=True)
                deadline = time.monotonic() + 150
                last_error = "no response"
                while time.monotonic() < deadline and process.poll() is None:
                    try:
                        status = public_status(origin,args.verify_address)
                        reporter.checked(status)
                        if status == 200:
                            raise RuntimeError("Public access bypassed authentication; stopping the tunnel.")
                        if status == 401:
                            verified = True
                            break
                        last_error = f"HTTP {status}"
                    except (URLError, TimeoutError) as error:
                        reporter.checked(None)
                        last_error = str(error)
                    time.sleep(1)
                if not verified:
                    raise RuntimeError(f"The public authentication gate could not be verified ({last_error}); stopping the tunnel.")
                print(("Phone address: " if args.hostname else "Temporary phone address: ") + origin, flush=True)
                print("Unauthenticated conversation access is blocked. Pair your phone from the Mac dashboard.", flush=True)
                print(("Keep the Mac awake. Control-C stops access." if args.hostname else "This address changes on restart. Keep the Mac awake. Control-C stops access."), flush=True)
                monitor_tunnel(process,origin,args.verify_address,reporter=reporter)
            except KeyboardInterrupt:
                reporter.update('stopped')
            except (OSError,ValueError,RuntimeError):
                reason=reporter.value.get('reason') or 'connector_exited'
                if reason=='edge_connection_closed':reason=inspected_tunnel_reason() or reason
                reporter.update('failed',reason=reason)
                raise
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait()
                record_event(state,'connector_exited',controller_pid=os.getpid(),connector_pid=process.pid,exit_code=process.returncode)
                reader.join(timeout=2)
                process.stdout.close()
                if settings.origin() == origin:
                    settings.set_origin(None)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, URLError) as error:
        raise SystemExit(str(error))
