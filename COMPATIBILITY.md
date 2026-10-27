# Compatibility and new-Mac acceptance

Complete these checks on each destination Mac before relying on remote control. Healthy processes, passing tests and a working tunnel are separate from successful instruction delivery.

## Compatibility baseline

- **Access:** this private repository requires an owner-granted GitHub invitation. Authentication alone does not grant access. Use your own signed-in desktop account and product entitlements.
- **macOS:** the native control app declares **13.0** as its installation floor. That does not certify older OS/SDK combinations, every architecture, or the desktop product's own requirements.
- **Tools:** Python **3.11+**, Git/GitHub CLI for cloning, and Apple Swift/macOS SDK tools for the optional native build. No pip packages or separate model API key are needed for desktop chats. Node **24** is for tests; Raycast is optional.
- **Desktop integration:** a compatible native ChatGPT/Codex app must supply local history, a compatible bundled/PATH Codex CLI and live owner protocols. The adapter requires matching columns in `state_5.sqlite` (`threads`, `projects`, `project_roots`) and `thread_history_1.sqlite` (`thread_items`, `thread_turns`), plus exact IPC method/snapshot versions in `desktop_companion/desktop_runtime.py`. Cloud-only/other-host chats are outside this adapter. There is no established desktop-version support range.
- **Phone access:** use `cloudflared`, your own Cloudflare domain/DNS permissions, and a unique hostname, tunnel and credential per Mac. The owning Mac needs an awake, logged-in session and network connectivity.

`doctor` checks prerequisites, history columns and CLI capabilities; it does not certify sign-in, live IPC or delivery. App updates can break internal schemas/protocols. Resolve incompatibility rather than editing desktop databases or disabling security checks.

### Evidence as of 2026-10-27

| Evidence | Verified scope and limits |
| --- | --- |
| Main CI at [`5d0aae0`](https://github.com/sagaryadaviitk/desktop-companion/commit/5d0aae0) | Passing isolated tests: macOS 15 Intel, Python 3.11/3.14, Node 24; native compilation without installation. Check CI for the commit you install. |
| Previously observed environment | macOS 27.0.1, arm64; ChatGPT 26.1016.51333; `codex-cli 0.163.0-alpha.11`; Swift 6.4/SDK 27.0; Python 3.14.6. Inventory only, not fresh-install or every-feature certification. |
| Historical live checks, 2026-10-20/21 | Bounded delivery, photo/queue, layout and selected network/lock observations. These do not certify another Mac or later app builds. |

No fresh-Mac installation was performed for this documentation revision.

**Exo local chats** are optional, disabled by default, and require independent model, skills/workspaces and API configuration; follow [LOCAL_ASSISTANT.md](LOCAL_ASSISTANT.md) with the selected Python below. Desktop-browser controls in [PR #15](https://github.com/sagaryadaviitk/desktop-companion/pull/15) were an unmerged draft on 2026-10-27, outside `main`. Neither optional feature is implied by core acceptance.

## Destination-Mac acceptance checklist

Select `COMPANION_PYTHON` in this terminal using [SETUP prerequisites](SETUP.md#prerequisites). Record **pass / fail / not tested**, date, source commit (or supplied ZIP version/checksum), OS/architecture, app build, Python and tunnel-client versions in private notes. Keep codes, tokens, conversations and logs out of shared source. Use disposable chats and harmless synthetic content.

1. **Prepare:** follow [SETUP](SETUP.md), including security requirements, permanent source location and intended app selection. Run `"$COMPANION_PYTHON" manage.py doctor`; for a no-native-app install use `doctor --without-app`, then `setup --without-app`. Resolve failed prerequisites. Verify the native build locally if using it.

2. **Run isolated tests** with Node 24 installed; record these separately from live checks:

   ```sh
   "$COMPANION_PYTHON" -m unittest discover -s tests/python -p 'test_*.py' -q
   node --test tests/js/test_*.cjs
   ```

3. **Check local operation** after explicit service installation and opening the desktop app yourself:

   ```sh
   "$COMPANION_PYTHON" service.py status
   curl --fail --max-time 10 http://127.0.0.1:8765/healthz
   curl --fail --max-time 10 http://127.0.0.1:8766/healthz
   ```

   Both responses need `ok: true`; port 8766 also needs `authentication_required: true`, `gateway_version: 1`. Confirm your own chats appear locally. This does not prove delivery.

4. **Check public authentication** after configuring this Mac's tunnel. Replace the placeholder:

   ```sh
   curl --max-time 15 -sS -o /dev/null -w '%{http_code}\n' https://YOUR-PHONE-HOSTNAME/api/chats
   ```

   Expect anonymous **401**. Investigate other results; stop public access on anonymous **200**. A 401 proves protected-route reachability, not pairing.

5. **Pair:** use native **Phone access**, or `"$COMPANION_PYTHON" control.py pair`. Enter the one-use code on this Mac's HTTPS site within five minutes. Check paired access and unpaired denial. Follow [CLI access management](SETUP.md#pair-without-the-native-control-app) for status/revoke/cancel commands; test revocation with a disposable browser.

6. **Verify delivery:** connect a disposable native-desktop chat, send one unique marker from the phone, and confirm exactly one matching desktop user message and response in the intended chat/turn, without silent queueing. Inspect uncertain receipts on the desktop before any manual retry. Test new-chat creation separately if needed.

7. **Verify selected features:** deliver a harmless photo. During an active disposable turn, check queue creation/edit/delete and Steer acknowledgement separately. Use harmless queue text in case the turn finishes before deletion. Test approvals/questions against a real pending request with complete visible details; confirm the exact request clears. Mark unavailable states **not tested**. Never approve an unsafe action just to finish testing.

8. **Verify recovery:** when no command is pending, follow the [update/restart table](README.md#after-pulling-an-update), then repeat health, public 401, paired access and a new delivery. Full restarts disconnect the tunnel. Verify normal login startup if required; a job definition alone does not prove startup recovery.

9. **Verify unattended use separately:** test mobile-data access with the lid open, then explicitly configured power/Locked Use, a fresh locked native-app turn, and physical lid-close continuity. Check actual setting readback and final outcome. LaunchAgents cannot run before login/FileVault unlock; power, network and authorization can interrupt access. Record failures and untested scope, remove disposable artifacts, and revoke test sessions. Verify optional Exo/office APIs and each additional Mac independently.
