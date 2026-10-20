# Desktop Companion contributor guidance

This repository contains the Python desktop bridge, static mobile web UI, Swift macOS control app, optional Cloudflare outage page, and isolated regression tests. Read README.md and SETUP.md before changing installation or runtime behavior.

## Shared work across Macs

- Inspect `git status` and the current branch before editing. Preserve other work and local configuration. Use a separate feature worktree when the installed service uses this checkout; merge through a reviewed pull request.
- Pull `main` with `git pull --ff-only` from a clean checkout. Do not reset another Mac's work, force-push shared history, or automatically deploy a remote commit.
- Commit explicit source paths. Never commit `.state/`, photos, conversations, logs, pairing/browser records, credentials, `.env` files, compiled apps, archives, or `config.local.json`. Tests use fake credentials and temporary stores.
- Each Mac has its own desktop sign-in, history, app build, configuration, hostname/tunnel, and pairing. Git shares source, not active chat sessions or machine settings.

## Runtime boundaries

- Keep history adapters read-only. Mutations must go through the existing bounded native/CLI controls and Companion-owned journals.
- Do not replay an accepted or uncertain command. Preserve exact chat/turn identity, acknowledgement checks, and duplicate protection.
- Keep TLS verification, pairing, origin/CSRF checks, and security software enabled. Do not silently change permissions, power settings, or Locked Use setup.
- Static web changes need no service restart. Python HTTP/bridge changes use `python3 service.py restart-web`, preserving the tunnel and awake guard. Supervisor or machine-configuration changes require an explicit, coordinated full restart. Do not restart a working tunnel just to ship UI changes.
- Native changes use `python3 build_app.py`, followed by reopening the control app. Do not replace a running install for a read-only review.

## Validation

- Run meaningful focused tests while iterating; run the Python and JavaScript suites before merging shared behavior changes:
  - `python3 -m unittest discover -p 'test_*.py' -q`
  - `node --test test_*.cjs`
- Keep tests independent of the host's app installation, history, remote configuration, network, and credentials. Use temporary fixtures and bounded fakes.
- CI compiles the native app without installing it. Passing CI does not verify actual phone delivery, the physical keyboard, native haptics, lid-close connectivity, or interaction with a locked Mac. Report those separately from synthetic tests.
- Check relevant light/dark phone layouts after UI changes. Preserve accessible text contrast, touch targets, drafts, and selected photos.

See CONTRIBUTING.md for the two-Mac workflow and update commands.
