# Set up on another Mac

This is a local companion for the desktop app’s own chats. It uses macOS APIs and version-specific local protocols. Each Mac runs its own desktop app, history, and Companion service.

## Prerequisites

- **Repository access:** this is a private repository. Ask its owner to grant your GitHub account access, then authenticate through `gh auth login`. Authentication alone does not grant access. A source-only ZIP is an alternative when supplied by an authorized owner.
- **Compatible Mac and desktop app:** the native app declares macOS 13.0 as its minimum, but older OS/SDK combinations are not certified by that declaration. Read the [tested baseline and limits](COMPATIBILITY.md#compatibility-baseline). Install and sign in to the intended desktop app normally, then create or open a local chat. It must provide this adapter's supported local history and native owner protocols; an unrelated desktop app or a cloud-only chat is insufficient.
- **Python 3.11 or newer:** select the actual interpreter below. The macOS-provided `python3` can still be older even when a newer Python is installed. The desktop bridge needs no pip packages or separate OpenAI API key.
- **Git and GitHub CLI** for cloning/updating; **Apple command-line tools and a usable Swift/macOS SDK** for building the optional native app. Install Apple tools with `xcode-select --install`, complete any normal setup prompts, and let `doctor` check them. Raycast is optional; Finder or `open` can launch the app.
- **Remote phone access only:** `cloudflared`, your own Cloudflare account, and a domain configured on Cloudflare with a hostname you control. Each Mac needs its own locally managed tunnel and credential. A temporary Quick Tunnel is not the configured, paired HTTPS-origin workflow described here.
- **Tests only:** Node 24 for the JavaScript suite. Node is not needed by the running service. Optional Exo inference has [separate prerequisites](LOCAL_ASSISTANT.md).

Install dependencies through your trusted package manager or official installers. If you already use [Homebrew](https://brew.sh), one option is:

```sh
brew install python@3.14 gh
# Only for remote phone access:
brew install cloudflared
# Only for JavaScript tests:
brew install node@24
```

See the official formula pages for [Python](https://formulae.brew.sh/formula/python@3.14), [GitHub CLI](https://formulae.brew.sh/formula/gh), [cloudflared](https://formulae.brew.sh/formula/cloudflared), and [Node 24](https://formulae.brew.sh/formula/node@24). Use installers/package versions compatible with the destination Mac; current Homebrew support can be narrower than the app's declared OS floor. Companion supervises its own tunnel process; do not install an additional `cloudflared` background service for the same tunnel.

Select Python in each new terminal. For the Homebrew example:

```sh
COMPANION_PYTHON="$(brew --prefix python@3.14)/bin/python3.14"
"$COMPANION_PYTHON" -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"; print(sys.executable); print(sys.version)'
```

With an official Python installer or another environment, set `COMPANION_PYTHON` to that interpreter's absolute path instead. All Python commands below use this selected interpreter. If you skip the native control app, append `--without-app` to every `manage.py doctor` command throughout this guide, phone configuration and future updates; other prerequisites still apply. For Node installed through Homebrew, add its versioned bin directory to this terminal before tests: `export PATH="$(brew --prefix node@24)/bin:$PATH"`, then confirm `node --version` reports 24.x.

## Install

1. From your permanent source parent folder, run `gh repo clone sagaryadaviitk/desktop-companion`, then `cd desktop-companion`. For a supplied source bundle, extract it and enter `desktop_companion`. Keep this folder in place: the control app and LaunchAgent refer to it. Never copy another Mac's `.state/`, `config.local.json`, sign-in or tunnel credentials.
2. If the desktop app is outside the usual Applications folders, or both ChatGPT and Codex are installed, choose the intended app **before doctor/setup**: `"$COMPANION_PYTHON" manage.py configure --desktop-app /absolute/path/ChatGPT.app`. Automatic detection prefers ChatGPT and does not verify that it is the app you intended. The default history home is `~/.codex`; a custom history home is not transferred from another Mac by setup.
3. Check prerequisites and prepare the app:

```sh
"$COMPANION_PYTHON" manage.py doctor
"$COMPANION_PYTHON" manage.py setup
```

Doctor reads schema metadata and checks required CLI queue flags with bounded `--help`; it does not send a question, read credential files, change history, or certify current sign-in/live IPC. Required history-column failures need a compatible app/adapter, not manual database edits. If you will not build the control app, use `manage.py doctor --without-app` and `manage.py setup --without-app` instead of the two commands above. A successful doctor run is the start of the [acceptance checklist](COMPATIBILITY.md#destination-mac-acceptance-checklist).

The setup prepares local configuration and builds **Desktop Companion.app** in your own Applications folder. It does not create login startup or start the web service. Raycast can open the app by name. `"$COMPANION_PYTHON" manage.py setup --without-app` skips the native build; background installation remains a separate explicit step below.

The native build check runs the Swift compiler and verifies the selected macOS SDK. Finding `xcrun` alone is insufficient on a Mac whose command-line tools have not finished setup. Failed checks leave installation for you to resolve through Apple's normal tools.

Native app updates copy the completed build into a temporary folder beside the destination before replacing the installed app. A failed copy leaves the working app untouched; a failed activation restores it. The previous version is retained at `~/Applications/.Desktop Companion.previous`. Because the replacement renames stay on the destination disk, the source folder can live on a different volume. A running copy is not restarted by this installer; reopen the control app after a successful update.

A clean copy is configured **locally only**; after explicit service installation, the dashboard is <http://127.0.0.1:8765>. Setup does not change sleep, passwords, FileVault, or Locked Use. Choose those settings separately in the native app when needed. The setup check reads history metadata and checks dependencies; it does not read credentials or send chat instructions.

## Background installation and security recovery

On a managed Mac, confirm your organization permits this background service before installing it. If endpoint protection quarantines any app, source or helper, stop here: retain the security incident evidence and use your approved IT/Security recovery process. Do not restore flagged files from Git or a ZIP, copy them to another location, keep reinstalling the job, or disable protection to regain access. The commands below are for an approved installation, not a quarantine workaround.

Once approved, opt in explicitly:

```sh
"$COMPANION_PYTHON" service.py install --background-install-approved
"$COMPANION_PYTHON" service.py status
```

Alternatively, `"$COMPANION_PYTHON" manage.py setup --install-service` performs preparation plus the explicit background install. The native **Start system** action starts an existing approved job; it never creates a missing one. The flag expresses operator intent and grants no system permission or security exception. No recurring SSO/session-refresh job is installed by Companion.

The new job stops retrying when its entrypoint or Python executable is missing, a required import/configuration is unavailable, or authenticated gateway startup fails. Its shell guard passes file paths as positional arguments, preserving spaces without evaluating them. These conditions require operator repair. Unexpected supervisor failures remain throttled by launchd to 60 seconds. The web and awake guard have independent failure budgets. Tunnel network failures keep retrying with a five-minute maximum delay; permanent local errors pause instead. For all components, inspect private `~/Library/Logs/Desktop Companion/service.log` and `service-error.log` when a component pauses. These login logs live outside the source directory, so a removed source folder cannot prevent the missing-runtime guard from starting. Older installations retain their earlier `.state/` logs. A fixed tunnel repair notice also appears in component status. Companion never automatically opens or relaunches ChatGPT.

Existing jobs retain their old definition until explicitly reinstalled. Applying this supervisor/LaunchAgent change requires one approved, coordinated installation/restart; `restart-web` alone cannot apply it. Keep source in a permanent location and do not edit a running checkout. Immutable release directories and rollback-safe runtime deployment are not implemented by this patch.

## Phone access

Follow Cloudflare's [locally managed tunnel prerequisites](https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/create-local-tunnel/), including domain/DNS setup. Give each Mac a distinct hostname and tunnel. Obtain the credential on this Mac under `~/.cloudflared`; reusing another Mac's active tunnel can route a phone to the wrong laptop.

Replace the example hostname with one **you control**, and replace the UUID with the value returned by `tunnel create`:

```sh
COMPANION_HOSTNAME=companion.your-domain.example
cloudflared tunnel login
cloudflared tunnel create desktop-companion-this-mac
cloudflared tunnel route dns desktop-companion-this-mac "$COMPANION_HOSTNAME"
COMPANION_TUNNEL_ID=REPLACE_WITH_TUNNEL_UUID
"$COMPANION_PYTHON" manage.py configure --hostname "$COMPANION_HOSTNAME" --tunnel-id "$COMPANION_TUNNEL_ID"
"$COMPANION_PYTHON" manage.py doctor
"$COMPANION_PYTHON" service.py restart
```

The placeholder values are not working credentials or DNS records. Complete the explicit background installation above before restarting. Companion manages the gateway and tunnel: do not create another tunnel service or route directly to unauthenticated port 8765. Its public route must target the authenticated gateway on loopback port 8766.

Verify local service and public authentication before pairing:

```sh
curl --fail --max-time 10 http://127.0.0.1:8765/healthz
curl --fail --max-time 10 http://127.0.0.1:8766/healthz
curl --max-time 15 -s -o /dev/null -w '%{http_code}\n' "https://$COMPANION_HOSTNAME/api/chats"
```

Both local endpoints should return healthy JSON; the gateway reports `authentication_required: true`. The public request, without browser cookies, must return **401**. A TLS failure, redirect, timeout or HTTP 200 is not a successful authentication check. Keep TLS verification enabled. Open `https://YOUR-ACTUAL-HOSTNAME` on your phone, then **Desktop Companion → Phone access** on the Mac to create a one-use code and pair that browser. Confirm a harmless message reaches the intended desktop chat exactly once. The bundle does not move sign-ins or pairing access.

### Pair without the native control app

For a service prepared with `manage.py setup --without-app`, use the same configured hostname, explicit service installation and health checks. Apple native-build tools are not required for this mode. Use `manage.py doctor --without-app` for preflight; both that command and `setup --without-app` skip the native-build-tools check while retaining the other prerequisites. From the owning checkout:

```sh
"$COMPANION_PYTHON" service.py status
"$COMPANION_PYTHON" control.py status
"$COMPANION_PYTHON" control.py pair
```

`pair` prints the one-use code locally only after the public route is protected. Enter it in the phone's connection screen within five minutes; do not paste it into an issue, chat or shared log. `control.py status` reports access records; use the relevant record's ID for `control.py revoke --id BROWSER_RECORD_ID` or `control.py cancel_code --id PAIRING_RECORD_ID`. These commands change only Companion access records. Pairing failure needs component/status investigation, not repeated replays of chat commands.

On iPhone, you can save the paired site to the Home Screen using the browser’s Share menu. In that app, pull down from the app header to **reload the full page** (release at “Release to reload app”), or pull inside a list/conversation at its top for a **soft refresh**. The header reload keeps the chat URL, asks before discarding unsent drafts/photos, and blocks while an operation is in progress. The **↻** button works from any scroll position. Use **… → This browser → Reload app** for a full interface reload; unsent drafts and photos require confirmation, and active operations block reloading. With the keyboard open, swipe down on ordinary message text to dismiss it without sending or refreshing; drafts and photos stay intact. The arrows/Done bar above the keyboard is native iOS UI. The mobile shell reserves the device’s safe area and uses solid header/composer backgrounds. Soft refresh has a visible spinner and completion tick; iPhone WebKit does not support standard web vibration. The app does not install a service worker or cache private conversations for offline use.

For a recovery page that remains reachable independently of the Mac, follow [cloudflare_fallback/README.md](cloudflare_fallback/README.md). It is optional and uses separate per-hostname routes. It does not carry API requests or repair a blocked tunnel transport. An inspection proxy or network policy may require an approved IT exception before Cloudflare Tunnel can connect.

If an existing tunnel stops working after deployment, distinguish an established connection from a new handshake. A full-system restart closes existing tunnel connections; web-only deployment should preserve them. Verify local service health, component freshness, tunnel registration, and anonymous public authentication separately before declaring recovery. A public-probe failure with registered edges is not, by itself, a tunnel disconnect; see [Running and recovery](README.md#running-and-recovery).

Managed-proxy/security investigations apply only to the affected Mac. Keep that Mac's diagnostics and any approved network adjustments local. A Mac without Zscaler does not need another Mac's Zscaler configuration. Source updates do not change system proxies, certificate trust or managed security policies. Inspection observed during a failed handshake is evidence to correlate with policy logs, not proof that inspection caused the failure.

Use the private `tunnel-events.jsonl` lifecycle history to check why a connection was replaced. Network metadata changes now preserve an existing controller. The watchdog also preserves registered edge connections and gives an edge-less connector a five-minute recovery grace period; authentication bypass remains an immediate stop. Keep the tunnel client current through its trusted package manager. A successful reboot alone does not establish sustained connectivity or explain a later failure.

If you later change the configured desktop app path, rerun doctor and rebuild with `"$COMPANION_PYTHON" build_app.py`, then reopen the control app. If the app/CLI path changes, update the approved login job once with `"$COMPANION_PYTHON" service.py install --background-install-approved` so its stored CLI PATH changes too; a plain restart cannot rewrite that definition. This operation already restarts the service once. Do not change apps during an active delivery.

To return to local-only mode, run `"$COMPANION_PYTHON" manage.py configure --local` and restart the service.

## Update, validate, and export

For a Git clone, follow [After pulling an update](README.md#after-pulling-an-update), including its one-time action for installations upgrading from `999f022` or the old recovery service. Keep `config.local.json` and `.state/` on the destination Mac; they contain machine settings and private runtime data. A source pull does not require copying another Mac's configuration, rerunning setup or changing its hostname.

A source-only ZIP has no Git history: obtain a reviewed replacement bundle and release/upgrade instructions from its owner. Preserve private state and do not overwrite a working installation or move the installed source folder during an active operation. Use a coordinated replacement/rollback plan for that folder, then apply the same changed-file runtime actions below. Do not run Git pull commands in an extracted ZIP.

Rebuild the control app only after native changes. Python/web-server updates use `"$COMPANION_PYTHON" service.py restart-web` (or the native component's **Restart service** action), preserving the tunnel and awake guard. Static web assets and documentation need no service restart. Supervisor behavior or intentional machine-configuration changes require one full `"$COMPANION_PYTHON" service.py restart`. Changes to the installed LaunchAgent definition, startup guard, Python path or login-log location instead require `"$COMPANION_PYTHON" service.py install --background-install-approved`; it replaces the approved job and already restarts once. `restart` alone cannot update a job definition, and an additional restart after installation is unnecessary. Both full operations disconnect this Mac's tunnel briefly. Open tabs detect web updates; an older tab without update detection needs one full browser reload.

```sh
"$COMPANION_PYTHON" -m unittest discover -p 'test_*.py'
node --test test_*.cjs
"$COMPANION_PYTHON" manage.py bundle --output Desktop-Companion-source.zip
```

Node is needed only for JavaScript tests, not for running Companion. The allowlisted export includes source, documentation, example configuration, and tests. It excludes `.state`, local configuration, conversations, photos, browser sessions, pairing codes, tunnel credentials, logs, compiled apps, and old Slack setup artifacts. Build and pair independently on every new Mac.

## Layout

- `web/`: the browser UI; clipboard attachments, prompt-first creation, appearance, component status, refresh gestures, Home Screen shell/caret behavior, and version updates have separate modules. Light/dark tokens and shared typography are in `theme.css`.
- `companion.py`: HTTP routes and the read-only history adapter.
- `desktop_runtime.py`, `runtime.py`, `new_chat.py`: bounded desktop/CLI integrations.
- `access.py`, `inbox.py`, `photos.py`, `promotions.py`: Companion-owned private stores.
- `configuration.py`, `service.py`, `control.py`, `manage.py`: machine configuration, supervision, and setup.
- `system_status.py`: bounded tunnel health records and freshness checks, with no raw logs or credentials exposed.
- `native/`, `build_app.py`: the locally built Raycast control app.
- `native/Appearance.swift`: shared native colors and material fallbacks. The control app follows the Mac appearance, with a sidebar override. macOS 26+ builds use Liquid Glass for navigation/secondary controls when available; older compiler/runtime versions retain solid controls.
- `test_*`: isolated Python/JavaScript regression checks.

App updates can change the local history schema or coordination protocol. Run the setup check and tests after an update; passing installation checks does not prove native app access while the Mac is locked. Live approvals require an owner snapshot and the supported follower reply methods. Secure macOS dialogs, sign-ins, verification, permanent grants/policy changes and unsupported forms still require the desktop. See README for exact approval behavior and duplicate protection. Stop still requires the desktop. Send and model controls require a verified live desktop connection. Steer uses the existing desktop owner and records the actual accepting turn if it advances during delivery. Queue edit/delete use optimistic content checks with saved versions for recovery; avoid editing the same entry on both devices simultaneously because the native API has no cross-device CAS. Queue promotion removes the original before steering; uncertain outcomes remain reviewable and are never automatically replayed. Saved history alone does not enable live Steer.

Personal chats use this Mac’s own `~/Library/Application Support/Desktop Companion/Chats` folder. Saved project targets are discovered from this Mac’s desktop history and verified before the first question runs. Browser appearance preferences are local to each browser; chats, photo drafts, pairing, request journals, and private configuration are not carried in the source export.
