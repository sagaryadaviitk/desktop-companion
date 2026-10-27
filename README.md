# Desktop Companion

Desktop Companion is an experimental, self-hosted bridge for using supported local ChatGPT/Codex desktop chats from a phone browser. A Mac runs the bridge and desktop app; the phone connects through a paired HTTPS gateway. It also includes an optional local Exo assistant and a paired media viewer.

**Beta:** the integration depends on desktop-app history and owner protocols that can change without notice. Compatibility is not guaranteed across app versions, and a fresh-Mac install and physical-phone end-to-end acceptance have not yet been established for this revision. Check [compatibility and acceptance](COMPATIBILITY.md) on each Mac before relying on it. This project is independent and is not an official desktop app integration.

## What it does

- Read supported local desktop chats and send messages from a paired phone; create chats, attach photos, and use supported queue, Steer, question and approval controls when their required desktop state is available.
- Show service status and manage phone pairing from the locally built macOS control app or CLI.
- Optionally use a separately configured Exo model with local chats, selected skills, workspace reads and configured APIs. See [local assistant setup and its limits](LOCAL_ASSISTANT.md).
- Optionally serve explicitly selected video review assets to paired browsers.

Some operations still need the desktop: cloud-only/other-host chats, Stop, secure macOS dialogs, sign-in and verification, unsupported approval forms, and non-photo attachments. Desktop-browser viewing/control is not included in the default branch. Queue edits use optimistic checks and can race with simultaneous desktop edits. An accepted or uncertain delivery is not automatically replayed; inspect the desktop before retrying.

## Optional media review

Publish an explicitly selected local video to the paired phone interface from the installed checkout:

```sh
"$COMPANION_PYTHON" media.py /absolute/path/video.mp4 --id review-video \
  --title "Review video" --version "Review" --duration 50 \
  --poster /absolute/path/poster.png --captions /absolute/path/captions.vtt
```

Use your own file paths, duration and identifier; omit optional poster/captions when unavailable. Open `https://YOUR-PHONE-HOSTNAME/media?item=review-video` in a paired browser. Files are private snapshots on the owning Mac and must be explicitly shared. Revocation prevents future reads but cannot erase a copy already downloaded by a browser.

## Requirements and installation

The repository is currently private; a GitHub account needs access from the owner before cloning. Each Mac needs a compatible signed-in desktop app with a local chat, Python 3.11+, Git, and, for the optional native control app, Apple command-line tools with a usable Swift/macOS SDK. Node 24 is needed only for tests. Phone access additionally needs `cloudflared`, a Cloudflare account, a domain configured with Cloudflare and a hostname you control. No pip packages or separate model API key are needed for desktop chats.

Follow [SETUP.md](SETUP.md) for interpreter selection, clone, preflight, optional app build, explicit background installation, tunnel setup, phone pairing, recovery and removal. The source stays in a permanent location because the app and login job reference it. The control app is built locally and ad-hoc signed; a signed/notarized binary distribution is not provided.

After selecting `COMPANION_PYTHON` in [SETUP](SETUP.md#prerequisites) and cloning into a permanent folder:

```sh
"$COMPANION_PYTHON" manage.py doctor
"$COMPANION_PYTHON" manage.py setup
```

If both desktop apps are installed, choose the intended app before these commands as described in the setup guide. For CLI-only setup, append `--without-app` to both commands. Setup does not start a service or set up phone access. Follow the separate [background installation](SETUP.md#background-installation-and-security-recovery) and [phone access](SETUP.md#phone-access) steps, then complete the [acceptance checklist](COMPATIBILITY.md#destination-mac-acceptance-checklist).

`doctor` checks prerequisites and selected schema and CLI capabilities; it does not prove sign-in, live desktop IPC, pairing, message delivery, or operation while locked or with the lid closed.

## Trust and security

A paired browser has broad access to supported chats and actions, including supported approvals. Pair only browsers you trust and revoke lost or shared devices. The local dashboard listens on loopback and is not an authorization boundary against untrusted local processes or other users with sufficient local access. The public tunnel must target the authenticated loopback gateway, never the unauthenticated dashboard. The gateway uses pairing, exact-origin and CSRF checks. [SETUP.md](SETUP.md#phone-access) describes how to verify unpaired access is denied.

Cloudflare terminates HTTPS and carries displayed chats, photos, pairing traffic and commands; traffic is not end-to-end encrypted from Cloudflare. Local configuration, journals, photos, browser sessions, and logs may contain sensitive data and remain on the owning Mac. Do not copy them to another installation or publish them. A source export excludes them.

The optional Exo assistant runs with the privileges and API profiles configured on the Mac. If an API profile permits write methods, the model can invoke them without a per-request confirmation; restrict methods and credentials to what you intend to allow. See [LOCAL_ASSISTANT.md](LOCAL_ASSISTANT.md#office-api-authentication). See [SECURITY.md](SECURITY.md) for reporting limitations and handling sensitive diagnostics.

## After pulling an update

Use the permanent installed checkout. Preserve any uncommitted changes and select the same supported `COMPANION_PYTHON` as in [SETUP](SETUP.md#prerequisites). Source bundles without Git history need a separately reviewed replacement; see [bundle upgrade guidance](SETUP.md#update-validate-and-export). Before pulling the [package-layout upgrade](#package-layout-upgrade), wait until no command is pending and stop the existing service with `"$COMPANION_PYTHON" service.py stop`; this briefly disconnects phone access. This prevents old processes from importing files while the checkout is being reorganized.

```sh
git status --short
git fetch origin
git switch main
git pull --ff-only
"$COMPANION_PYTHON" manage.py doctor
"$COMPANION_PYTHON" -m unittest discover -s tests/python -p 'test_*.py' -q
node --test tests/js/test_*.cjs
```

For a CLI-only installation use `doctor --without-app`. Apply the action appropriate to the changed files and release notes. Check service status and repeat the relevant [acceptance checks](COMPATIBILITY.md#destination-mac-acceptance-checklist); passing tests alone does not prove delivery.

| Change | Action on this Mac |
| --- | --- |
| Documentation or tests | No runtime action. |
| `web/` static assets | No service restart; refresh when drafts and photos are safe. |
| Python HTTP/bridge code | `"$COMPANION_PYTHON" service.py restart-web` preserves the tunnel and awake guard. |
| Native Swift or `build_app.py` | `"$COMPANION_PYTHON" build_app.py`, then reopen the control app. |
| Supervisor behavior or deliberate machine configuration | One coordinated `"$COMPANION_PYTHON" service.py restart` disconnects the tunnel briefly. |
| LaunchAgent definition, startup guard, Python path or login-log location | `"$COMPANION_PYTHON" service.py install --background-install-approved` updates the existing job and restarts it once. |
| Optional outage page | Follow its [separate instructions](cloudflare_fallback/README.md). |

### Package-layout upgrade

This revision moves Python implementation modules into `desktop_companion/`. For this upgrade, stop the service before pulling as described above; after pulling and testing, use `"$COMPANION_PYTHON" service.py restart` to start the existing approved job. The tunnel is unavailable during the update. A web-only restart does not reload the running supervisor. The LaunchAgent still points to the root `service.py` entrypoint, so the relocation alone does not require reinstalling it. Repeat the relevant acceptance checks afterward.

### Older recovery-service upgrade

If upgrading from `999f022` or an older recovery service without the updated LaunchAgent definition, run `"$COMPANION_PYTHON" service.py install --background-install-approved` once after pulling and check `"$COMPANION_PYTHON" service.py status`. A normal restart cannot rewrite the job. This disconnects the tunnel briefly; retain this Mac's configuration and private state.

## Running and recovery

The explicitly installed login service supervises the web server, awake guard and configured tunnel; it does not automatically open the desktop app. `"$COMPANION_PYTHON" service.py status` and the native control app show component state. Use `restart-web` for web-service recovery and a full restart only when needed, because it closes tunnel connections. Check local health, anonymous public **401**, paired access and actual message delivery independently. A healthy process or tunnel does not prove delivery. See [setup and troubleshooting](SETUP.md#background-installation-and-security-recovery).

Unattended mode and Locked Use are separate from ordinary phone access. Sleep changes persist after stopping or uninstalling Companion, and a login service cannot run before login/FileVault unlock. Locked native interaction may require manual unlock. If Locked Use refuses access, unlock manually, confirm it is enabled, lock again and start a fresh user-initiated native test; Companion cannot override that authorization. Lid-close, power and network behavior must be checked on each Mac. Review [compatibility](COMPATIBILITY.md) before enabling these features.

## Development and support

`desktop_companion/` contains Python implementation modules; root Python command files are compatibility entrypoints. `web/` holds the browser UI, `native/` the Swift app, `tests/python/` and `tests/js/` the isolated tests, and `cloudflare_fallback/` an optional independently deployed outage page. See [CONTRIBUTING.md](CONTRIBUTING.md) for development and verification.

This is an experimental project with no published support or response-time commitment. For reproducible non-sensitive bugs, use the repository's issue tracker when available; do not put chat contents, credentials, pairing codes or private logs in an issue. Private vulnerability reporting is planned for public launch and must be enabled and verified first; see [SECURITY.md](SECURITY.md).

The source is licensed under the [MIT License](LICENSE).
