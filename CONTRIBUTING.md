# Work on Desktop Companion from multiple Macs

The private repository is the shared source of truth:
[sagaryadaviitk/desktop-companion](https://github.com/sagaryadaviitk/desktop-companion).
It contains the native Mac control app, mobile web app, Python bridge, tests, and setup documentation.

Each Mac runs independently. Source changes can be shared; active chats, desktop sign-ins, browser pairing, photos, runtime state, power settings, and tunnel credentials stay on the Mac that owns them.

## Set up the second Mac

Sign in to your personal GitHub account using the normal GitHub CLI/browser flow, then clone:

```sh
gh auth login
gh repo clone sagaryadaviitk/desktop-companion
cd desktop-companion
python3 manage.py doctor
python3 manage.py setup
```

Follow [SETUP.md](SETUP.md) for prerequisites and that Mac's phone access. The new checkout defaults to local-only mode. Give each Mac its own hostname and Cloudflare tunnel, then pair its browser independently. Build the native app on its destination Mac; a compiled app contains that Mac's local source and Python paths.

The existing Mac can keep running from its current source folder. Do not move that folder after installation: the LaunchAgent and native control app refer to it.

## Make a change

Before switching branches or pulling, check for unfinished work with `git status --short`. If files are modified, finish/commit that work or choose a separate checkout before updating. Do not discard changes to make a pull succeed.

Keep the installed checkout on `main`. Use a separate feature worktree so editing or switching branches does not change files used by the running Companion service. Do not run `manage.py setup` from a temporary feature worktree: each Mac has one installed service and one set of ports.

Create the feature worktree from your clean installed checkout:

```sh
git fetch origin
git worktree add -b feature/chat-theme ../desktop-companion-theme origin/main
cd ../desktop-companion-theme
```

Edit and test the change. For example, for a theme change:

```sh
node --test test_appearance.cjs
git add web/theme.css
git commit -m "Refine the chat theme"
git push -u origin HEAD
gh pr create --base main --fill
```

Use a distinct branch for each agent's work. Review the diff and CI checks, merge the pull request, and pull `main` on the other Mac when it is ready. Git does not automatically apply changes to the other laptop.

## Apply an update on a Mac

From a clean checkout:

```sh
git fetch origin
git switch main
git pull --ff-only
python3 manage.py doctor
python3 -m unittest discover -p 'test_*.py' -q
node --test test_*.cjs
```

Node 24 is used by CI; Python 3.11 or newer is required. There are no Python package dependencies. Read the [release-specific action in README](README.md#one-time-upgrade-from-999f022-or-an-older-recovery-service), then use the commands appropriate to the files changed:

| Changed source | Apply locally |
| --- | --- |
| Documentation or tests only | No runtime action. |
| Static files in `web/` | No service restart. Open tabs detect the update; refresh when a draft is safe. |
| Python HTTP/bridge code | `python3 service.py restart-web` preserves the tunnel and awake guard. |
| Native Swift code or `build_app.py` | `python3 build_app.py`, then reopen Desktop Companion from Raycast. |
| `service.py` supervisor logic or machine configuration | Schedule one `python3 service.py restart`; this recreates the whole group and disconnects the existing tunnel. |
| LaunchAgent definition, startup guard, Python path or login-log location | Update the existing approved job with `python3 service.py install --background-install-approved`. This restarts once; an extra `restart` is unnecessary. |
| Optional outage page | Follow [cloudflare_fallback/README.md](cloudflare_fallback/README.md) for that Mac's hostname. |

Check `python3 service.py status` and `python3 manage.py doctor` afterward. Local health, public authentication, real message delivery, and locked-screen native interaction are separate checks.

The recovery-hardening release in `21499f3` requires that one-time job update when upgrading from `999f022` or another installation with the old LaunchAgent definition. Pulling source or restarting the old job does not rewrite its definition. Preserve each Mac's own local configuration and runtime stores. Network-specific diagnostics or managed-proxy changes stay local to that Mac; a documentation update does not authorize or deploy them elsewhere.

The native app's Appearance setting is local to that Mac, and web appearance is local to each browser. Pulling source updates the available design without sharing these preferences.

## Tests and CI

Pull requests and pushes to `main` run Python tests on macOS with Python 3.11 and 3.14, JavaScript tests with Node 24, and one native compilation. CI uses fake desktop protocols and temporary stores; it does not need your desktop account, pairing records, tunnel credential, or running Companion service. It does not install the compiled app or deploy anything.

Do not add real chat contents, screenshots, photos, tokens, `.state/`, or local configuration to reproduce a test. Add synthetic fixtures instead. Review `git diff --cached` before pushing. The ignore rules prevent ordinary staging of private/runtime files; they do not override an explicit `git add -f`.
