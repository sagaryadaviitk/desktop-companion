# Contributing

Desktop Companion contains a Python bridge, a mobile web interface, a locally built Swift control app and an optional Cloudflare outage page. Read [SETUP.md](SETUP.md), [COMPATIBILITY.md](COMPATIBILITY.md) and [SECURITY.md](SECURITY.md) before changing installation, security or runtime behavior. The repository is currently private; access requires an invitation. No response-time or maintenance commitment is published.

## Development

Preserve existing work and machine configuration. If an installed service uses the checkout, create a separate worktree so edits and branch changes do not affect a running installation:

```sh
git status --short
git fetch origin
git worktree add -b feature/example ../desktop-companion-feature origin/main
cd ../desktop-companion-feature
```

Do not run setup from a temporary worktree or share one Mac's configuration or state with another. Keep history adapters read-only, preserve identity/acknowledgement checks, and never replay accepted or uncertain commands. Keep pairing, TLS, origin/CSRF and security software enabled. Use synthetic data and isolated temporary fixtures; never commit local configuration, `.state/`, credentials, photos, chats, sessions, logs or compiled apps. Review the staged diff before sharing.

## Tests and CI

Select `COMPANION_PYTHON` as described in [SETUP prerequisites](SETUP.md#prerequisites). Run focused checks while iterating and both suites for shared behavior changes:

```sh
"$COMPANION_PYTHON" -m unittest discover -s tests/python -p 'test_*.py' -q
node --test tests/js/test_*.cjs
```

For example, run `node --test tests/js/test_appearance.cjs` for appearance work. Python tests require 3.11+; Node 24 is used in CI. There are no pip runtime dependencies. CI uses isolated fixtures and compiles the native app; it does not install the app or test a physical phone, real delivery, locked-screen access or lid-close behavior. Verify those separately when applicable. Test light and dark phone layouts for UI changes.

## Applying updates

Keep the installed checkout in its permanent location and preserve each Mac's own state. Follow [the update table](README.md#after-pulling-an-update), including package-layout and older-service upgrade actions where applicable. A full service restart closes tunnel connections. Do not restart a working tunnel for static web changes. Rebuild the locally installed app after native changes and reopen it. Validate actual service and phone behavior separately from automated tests.
