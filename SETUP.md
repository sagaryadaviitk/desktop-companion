# Set up on another Mac

This is a local companion for the desktop app’s own chats. It uses macOS APIs and version-specific local protocols. Each Mac runs its own desktop app, history, and Companion service.

## Install

1. Install and sign in to the ChatGPT/Codex desktop app normally on the new Mac. Open a local chat once so its history exists.
2. Install Python 3.11 or newer and Apple command-line tools. Install `cloudflared` only if this Mac needs remote phone access.
3. Clone the private repository using the [README installation steps](README.md#install-on-a-second-mac), or extract the source bundle to a permanent folder. Keep that folder in place: the control app and LaunchAgent refer to it.
4. From that source folder (`desktop-companion` for Git, `desktop_companion` for a ZIP), run:

```sh
python3 manage.py doctor
python3 manage.py setup
```

The setup prepares local configuration and builds **Desktop Companion.app** in your own Applications folder. It does not create login startup or start the web service. Raycast can open the app by name. `python3 manage.py setup --without-app` skips the native build; background installation remains a separate explicit step below.

The native build check runs the Swift compiler and verifies the selected macOS SDK. Finding `xcrun` alone is insufficient on a Mac whose command-line tools have not finished setup. Failed checks leave installation for you to resolve through Apple's normal tools.

Native app updates copy the completed build into a temporary folder beside the destination before replacing the installed app. A failed copy leaves the working app untouched; a failed activation restores it. The previous version is retained at `~/Applications/.Desktop Companion.previous`. Because the replacement renames stay on the destination disk, the source folder can live on a different volume. A running copy is not restarted by this installer; reopen the control app after a successful update.

A clean copy is configured **locally only**; after explicit service installation, the dashboard is <http://127.0.0.1:8765>. Setup does not change sleep, passwords, FileVault, or Locked Use. Choose those settings separately in the native app when needed. The setup check reads history metadata and checks dependencies; it does not read credentials or send chat instructions.

## Background installation and security recovery

On a managed Mac, confirm your organization permits this background service before installing it. If endpoint protection quarantines any app, source or helper, stop here: retain the security incident evidence and use your approved IT/Security recovery process. Do not restore flagged files from Git or a ZIP, copy them to another location, keep reinstalling the job, or disable protection to regain access. The commands below are for an approved installation, not a quarantine workaround.

Once approved, opt in explicitly:

```sh
python3 service.py install --background-install-approved
python3 service.py status
```

Alternatively, `python3 manage.py setup --install-service` performs preparation plus the explicit background install. The native **Start system** action starts an existing approved job; it never creates a missing one. The flag expresses operator intent and grants no system permission or security exception. No recurring SSO/session-refresh job is installed by Companion.

The new job stops retrying when its entrypoint or Python executable is missing, a required import/configuration is unavailable, or authenticated gateway startup fails. Its shell guard passes file paths as positional arguments, preserving spaces without evaluating them. These conditions require operator repair. Unexpected supervisor failures remain throttled by launchd to 60 seconds. The web and awake guard have independent failure budgets. Tunnel network failures keep retrying with a five-minute maximum delay; permanent local errors pause instead. For all components, inspect private `~/Library/Logs/Desktop Companion/service.log` and `service-error.log` when a component pauses. These login logs live outside the source directory, so a removed source folder cannot prevent the missing-runtime guard from starting. Older installations retain their earlier `.state/` logs. A fixed tunnel repair notice also appears in component status. Companion never automatically opens or relaunches ChatGPT.

Existing jobs retain their old definition until explicitly reinstalled. Applying this supervisor/LaunchAgent change requires one approved, coordinated installation/restart; `restart-web` alone cannot apply it. Keep source in a permanent location and do not edit a running checkout. Immutable release directories and rollback-safe runtime deployment are not implemented by this patch.

## Phone access

Give each Mac a distinct Cloudflare tunnel and hostname. Obtain that Mac’s tunnel credential through your normal Cloudflare setup, keeping it under `~/.cloudflared`. Do not copy another Mac’s tunnel credential or reuse its active tunnel: that can route a phone to the wrong laptop.

After routing the new hostname to its own tunnel, run:

```sh
python3 manage.py configure --hostname agents-second.example.com --tunnel-id YOUR-TUNNEL-UUID
python3 manage.py doctor
python3 service.py restart
```

The phone gateway requires pairing and verifies anonymous requests are blocked before accepting a public route. Use **Desktop Companion → Phone access** to pair your browser afresh on the new Mac. The source bundle does not move login sessions or pairing access.

On iPhone, you can save the paired site to the Home Screen using the browser’s Share menu. In that app, pull down from the app header to **reload the full page** (release at “Release to reload app”), or pull inside a list/conversation at its top for a **soft refresh**. The header reload keeps the chat URL, asks before discarding unsent drafts/photos, and blocks while an operation is in progress. The **↻** button works from any scroll position. Use **… → This browser → Reload app** for a full interface reload; unsent drafts and photos require confirmation, and active operations block reloading. With the keyboard open, swipe down on ordinary message text to dismiss it without sending or refreshing; drafts and photos stay intact. The arrows/Done bar above the keyboard is native iOS UI. The mobile shell reserves the device’s safe area and uses solid header/composer backgrounds. Soft refresh has a visible spinner and completion tick; iPhone WebKit does not support standard web vibration. The app does not install a service worker or cache private conversations for offline use.

For a recovery page that remains reachable independently of the Mac, follow [cloudflare_fallback/README.md](cloudflare_fallback/README.md). It is optional and uses separate per-hostname routes. It does not carry API requests or repair a blocked tunnel transport. An inspection proxy or network policy may require an approved IT exception before Cloudflare Tunnel can connect.

If an existing tunnel stops working after deployment, distinguish an established connection from a new handshake. A full-system restart closes existing tunnel connections; web-only deployment should preserve them. Verify local service health, component freshness, tunnel registration, and anonymous public authentication separately before declaring recovery. A public-probe failure with registered edges is not, by itself, a tunnel disconnect; see [Running and recovery](README.md#running-and-recovery).

Managed-proxy/security investigations apply only to the affected Mac. Keep that Mac's diagnostics and any approved network adjustments local. A Mac without Zscaler does not need another Mac's Zscaler configuration. Source updates do not change system proxies, certificate trust or managed security policies. Inspection observed during a failed handshake is evidence to correlate with policy logs, not proof that inspection caused the failure.

Use the private `tunnel-events.jsonl` lifecycle history to check why a connection was replaced. Network metadata changes now preserve an existing controller. The watchdog also preserves registered edge connections and gives an edge-less connector a five-minute recovery grace period; authentication bypass remains an immediate stop. Keep the tunnel client current through its trusted package manager. A successful reboot alone does not establish sustained connectivity or explain a later failure.

For an app installed in a different folder, set its absolute `.app` path with `python3 manage.py configure --desktop-app /absolute/path/ChatGPT.app`, then rebuild with `python3 build_app.py`. Automatic detection covers Applications and your user Applications folder, with ChatGPT and Codex app names.

To return to local-only mode, run `python3 manage.py configure --local` and restart the service.

## Update, validate, and export

Follow [After pulling an update](README.md#after-pulling-an-update), including its one-time action for installations upgrading from `999f022` or the old recovery service. Keep `config.local.json` and `.state/` on the destination Mac; they contain machine settings and private runtime data. A source pull does not require copying another Mac's configuration, rerunning setup or changing its hostname.

Rebuild the control app only after native changes. Python/web-server updates use `python3 service.py restart-web` (or the native component's **Restart service** action), preserving the tunnel and awake guard. Static web assets and documentation need no service restart. Supervisor behavior or intentional machine-configuration changes require one full `python3 service.py restart`. Changes to the installed LaunchAgent definition, startup guard, Python path or login-log location instead require `python3 service.py install --background-install-approved`; it replaces the approved job and already restarts once. `restart` alone cannot update a job definition, and an additional restart after installation is unnecessary. Both full operations disconnect this Mac's tunnel briefly. Open tabs detect web updates; an older tab without update detection needs one full browser reload.

```sh
python3 -m unittest discover -p 'test_*.py'
node --test test_*.cjs
python3 manage.py bundle --output Desktop-Companion-source.zip
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
