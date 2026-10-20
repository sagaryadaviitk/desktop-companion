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

The setup builds a fresh **Desktop Companion.app** in your own Applications folder and installs the login service. Raycast can open the app by name. Use `python3 manage.py setup --without-app` to install the browser/service without compiling the control app.

The native build check runs the Swift compiler and verifies the selected macOS SDK. Finding `xcrun` alone is insufficient on a Mac whose command-line tools have not finished setup. Failed checks leave installation for you to resolve through Apple's normal tools.

Native app updates copy the completed build into a temporary folder beside the destination before replacing the installed app. A failed copy leaves the working app untouched; a failed activation restores it. The previous version is retained at `~/Applications/.Desktop Companion.previous`. Because the replacement renames stay on the destination disk, the source folder can live on a different volume. A running copy is not restarted by this installer; reopen the control app after a successful update.

A clean copy starts **locally only** at <http://127.0.0.1:8765>. Setup does not change sleep, passwords, FileVault, or Locked Use. Choose those settings separately in the native app when needed. The setup check reads history metadata and checks dependencies; it does not read credentials or send chat instructions.

## Phone access

Give each Mac a distinct Cloudflare tunnel and hostname. Obtain that Mac’s tunnel credential through your normal Cloudflare setup, keeping it under `~/.cloudflared`. Do not copy another Mac’s tunnel credential or reuse its active tunnel: that can route a phone to the wrong laptop.

After routing the new hostname to its own tunnel, run:

```sh
python3 manage.py configure --hostname agents-second.example.com --tunnel-id YOUR-TUNNEL-UUID
python3 manage.py doctor
python3 service.py restart
```

The phone gateway requires pairing and verifies anonymous requests are blocked before accepting a public route. Use **Desktop Companion → Phone access** to pair your browser afresh on the new Mac. The source bundle does not move login sessions or pairing access.

For a recovery page that remains reachable independently of the Mac, follow [cloudflare_fallback/README.md](cloudflare_fallback/README.md). It is optional and uses separate per-hostname routes. It does not carry API requests or repair a blocked tunnel transport. An inspection proxy or network policy may require an approved IT exception before Cloudflare Tunnel can connect.

If an existing tunnel stops working after deployment, distinguish an established connection from a new handshake. A full-system restart closes existing tunnel connections; web-only deployment should preserve them. The investigation recorded in [README.md](README.md#running-and-recovery) found TLS inspection on a failing fresh connection, but did not prove the exact outage onset or a policy change. Verify local service health, component freshness, tunnel registration, and anonymous public authentication separately before declaring recovery.

Use the private `tunnel-events.jsonl` lifecycle history to check why a connection was replaced. Network metadata changes now preserve an existing controller. The watchdog also preserves registered edge connections and gives an edge-less connector a five-minute recovery grace period; authentication bypass remains an immediate stop. Keep the tunnel client current through its trusted package manager. A successful reboot alone does not establish sustained connectivity or explain a later failure.

For an app installed in a different folder, set its absolute `.app` path with `python3 manage.py configure --desktop-app /absolute/path/ChatGPT.app`, then rebuild with `python3 build_app.py`. Automatic detection covers Applications and your user Applications folder, with ChatGPT and Codex app names.

To return to local-only mode, run `python3 manage.py configure --local` and restart the service.

## Update, validate, and export

Keep `config.local.json` and `.state/` on the destination Mac when updating source. They contain machine settings and private runtime data. Rebuild the control app after native changes. For Python/web-server updates, use `python3 service.py restart-web` (or the native component's **Restart service** action), which preserves the tunnel and awake guard. Static assets in `web/` need no service restart. Supervisor changes in `service.py` and machine configuration changes require one full `python3 service.py restart`; reserve that command for those changes or explicit full-system recovery because it also disconnects the tunnel. Open tabs detect web updates. A tab created before update detection was installed needs one full browser reload.

```sh
python3 -m unittest discover -p 'test_*.py'
node --test test_*.cjs
python3 manage.py bundle --output Desktop-Companion-source.zip
```

Node is needed only for JavaScript tests, not for running Companion. The allowlisted export includes source, documentation, example configuration, and tests. It excludes `.state`, local configuration, conversations, photos, browser sessions, pairing codes, tunnel credentials, logs, compiled apps, and old Slack setup artifacts. Build and pair independently on every new Mac.

## Layout

- `web/`: the browser UI; clipboard attachments, prompt-first creation, appearance, component status, refresh gestures, and version updates have separate modules. Light/dark tokens and shared typography are in `theme.css`.
- `companion.py`: HTTP routes and the read-only history adapter.
- `desktop_runtime.py`, `runtime.py`, `new_chat.py`: bounded desktop/CLI integrations.
- `access.py`, `inbox.py`, `photos.py`, `promotions.py`: Companion-owned private stores.
- `configuration.py`, `service.py`, `control.py`, `manage.py`: machine configuration, supervision, and setup.
- `system_status.py`: bounded tunnel health records and freshness checks, with no raw logs or credentials exposed.
- `native/`, `build_app.py`: the locally built Raycast control app.
- `native/Appearance.swift`: shared native colors and material fallbacks. The control app follows the Mac appearance, with a sidebar override. macOS 26+ builds use Liquid Glass for navigation/secondary controls when available; older compiler/runtime versions retain solid controls.
- `test_*`: isolated Python/JavaScript regression checks.

App updates can change the local history schema or coordination protocol. Run the setup check and tests after an update; passing installation checks does not prove native app access while the Mac is locked. Stop and runtime approvals still require the desktop. Send, Steer, and model controls require a verified live desktop connection; saved history alone does not enable them.

Personal chats use this Mac’s own `~/Library/Application Support/Desktop Companion/Chats` folder. Saved project targets are discovered from this Mac’s desktop history and verified before the first question runs. Browser appearance preferences are local to each browser; chats, photo drafts, pairing, request journals, and private configuration are not carried in the source export.
