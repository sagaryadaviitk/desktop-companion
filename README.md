# Desktop Companion

Browse the desktop app’s local projects, chats, history, and recorded progress from a paired phone browser. Send text/photos, answer questions and live approvals, and choose a model through the running Mac. Steer uses the existing desktop owner, matching the Mac composer’s active-chat behavior. No Slack or Telegram integration is required.

For a new installation, start with [SETUP.md](SETUP.md) and the [compatibility and acceptance checklist](COMPATIBILITY.md). Each Mac has independent settings, history, pairing, and tunnel access. A clean source copy starts locally only.

For an existing installation, follow [After pulling an update](#after-pulling-an-update). Pulling code alone does not replace a running process or update an installed LaunchAgent definition.

## Responsive updates and recovery

An open conversation checks for changes every 750 ms while working or awaiting delivery, and every 3 seconds while idle. These are polling intervals after a completed read, not a delivery-latency guarantee. The workspace checks every 5 seconds, or every 15 seconds inside a conversation. Hidden/offline views pause and resume with a fresh check. Failed reads use capped exponential backoff with equal jitter (up to 30 seconds), honoring bounded server `Retry-After` guidance. Sends, approvals and uncertain commands are never automatically replayed.

Connection interruptions preserve the last conversation and unsent draft. Sending remains disabled until fresh controls are available. GET requests time out after 8 seconds; media-library refreshes are bounded and coalesced without interrupting the playing cut.

The native app reads status through a local-only HTTP endpoint, with a bounded process fallback when the web service is unavailable. Active status refreshes every 2 seconds, inactive status every 15 seconds, and failures back off with jitter. Readiness checks do not enumerate active chat owners. Concurrent observational reads share short-lived results (750 ms for execution observations, 1 second for component snapshots), retain their original timestamps, and are invalidated after authenticated writes. Authorization and command preflight always use their existing live checks. Supervisor failure recovery also adds jitter while retaining its retry budget and circuit breaker.

Use the [changed-file update table](#after-pulling-an-update) to apply a release. Setup/doctor and documentation changes alone do not require restarting a working service or rebuilding the native app. Per-Mac configuration and pairing stay local.

### Chat connection and large activity snapshots

**Connect this chat to desktop** opens the selected chat once and waits up to 15 seconds for fresh owner confirmation, showing **Connecting…** while waiting. Opening the desktop window alone is not a connection acknowledgement. Switching chats or signing out cancels the confirmation; a failed or uncertain open is never automatically replayed.

A verified, resumed desktop owner is connected even if its activity snapshot is too large or temporarily unreadable. The button disappears in that case, while activity is explicitly unknown. Queue and verified model controls remain available; Send, Steer and live approvals still require their existing fresh state checks. Oversized activity observations retain the 32 MiB frame limit and use a bounded, per-owner 30-second cooldown to avoid repeatedly serializing a huge history. This cooldown never substitutes for action preflight. This fix needs a web-service restart and a browser update; no native rebuild or tunnel restart is needed.

## Internal approval reviews in the chat list

Guardian approval-review sessions are excluded from Companion catalogs, unread counts, project totals, and live-status candidates. Both current `guardian_review` metadata and older Guardian subagent sources are recognized. Delegated subagents without an agent path are classified from desktop source metadata and remain available under **Subagent chats**. User, automation, and agent-created main chats remain visible; titles do not determine visibility. Desktop history is retained read-only, including direct diagnostic access to review sessions.

This Python adapter update requires `python3 service.py restart-web`; no native rebuild or tunnel restart is needed.

## Install on a second Mac

The source is in the **private** repository [sagaryadaviitk/desktop-companion](https://github.com/sagaryadaviitk/desktop-companion). The owner must grant your GitHub account access before cloning. Each Mac runs independently; Git shares code, not chats, sign-ins, settings, pairing, or credentials.

1. Follow [prerequisites and interpreter selection](SETUP.md#prerequisites) first. You need a compatible, signed-in desktop app with local history, Python **3.11+**, Git/GitHub CLI and Apple command-line tools for the native app. Raycast is optional; Finder can open `~/Applications/Desktop Companion.app`. There are no pip dependencies or separate OpenAI API keys for the desktop bridge. Node **24** is only for JavaScript tests.
2. In the terminal where you selected `COMPANION_PYTHON`, authenticate with the account granted repository access and choose a permanent source location:

```sh
gh auth login
mkdir -p ~/Developer
cd ~/Developer
gh repo clone sagaryadaviitk/desktop-companion
cd desktop-companion
"$COMPANION_PYTHON" manage.py doctor
"$COMPANION_PYTHON" manage.py setup
```

Doctor checks local prerequisites, the required history columns and CLI queue support. It does not certify sign-in, live owner/protocol compatibility, phone delivery, or lock/lid behavior. If both ChatGPT and Codex apps are installed, explicitly choose the app before these commands; see [SETUP.md](SETUP.md#install). For an installation without the native control app, use `manage.py doctor --without-app` and `manage.py setup --without-app` instead.

Setup prepares local configuration and builds the native app; it **does not install or start a background service**. After reviewing [background installation and security recovery](SETUP.md#background-installation-and-security-recovery), opt in with:

```sh
"$COMPANION_PYTHON" service.py install --background-install-approved
"$COMPANION_PYTHON" service.py status
curl --fail http://127.0.0.1:8765/healthz
open "$HOME/Applications/Desktop Companion.app"
```

The installation flag records operator intent, not an IT exception. Keep the source folder in place. `manage.py setup --without-app` skips the native build; use the [CLI pairing instructions](SETUP.md#pair-without-the-native-control-app) if needed. Setup does not change power, password or Locked Use settings.

3. For remote phone access, follow the complete [Cloudflare and pairing recipe](SETUP.md#phone-access). You need your own Cloudflare account, a domain configured there, a hostname you control, and a separate tunnel for each Mac. The gateway listens on loopback; never point a public tunnel at the unauthenticated local dashboard.
4. Complete the [destination-Mac acceptance checklist](COMPATIBILITY.md#destination-mac-acceptance-checklist). Check local health, anonymous public **401**, paired-browser delivery exactly once, and photos/queue/approval paths separately. Keeping the Mac awake and operating native apps while locked require independent checks.

### Feature scope of a default clone

`main` contains the desktop-chat companion, private media review and the optional Exo local assistant. Exo needs its own [installation and model/API configuration](LOCAL_ASSISTANT.md); it is disabled by default. Desktop-browser viewing/control is currently in **draft [PR #15](https://github.com/sagaryadaviitk/desktop-companion/pull/15)**, outside `main`. It requires a separate, coordinated desktop-app relaunch and real compatibility checks before release. Do not enable a debugger, switch the installed checkout to that branch, or treat passing CI as browser delivery proof just to finish basic setup.

## After pulling an update

These Git commands apply to cloned repositories. For a source ZIP, follow [the bundle upgrade guidance](SETUP.md#update-validate-and-export). Use the permanent source folder already installed on **that Mac**. First run `git status --short`; if it reports changes, preserve that work before switching branches or pulling. Select the same supported `COMPANION_PYTHON` as in [SETUP.md](SETUP.md#prerequisites) in each new terminal. Append `--without-app` to doctor when the native app was skipped. Then:

```sh
git fetch origin
git switch main
git pull --ff-only
git log -1 --oneline
"$COMPANION_PYTHON" manage.py doctor
"$COMPANION_PYTHON" -m unittest discover -p 'test_*.py' -q
node --test test_*.cjs
```

Apply only the action required by the changed files or the release note below. Avoid a full-system restart for every pull: it closes established phone-tunnel connections.

| Change pulled | Action on this Mac |
| --- | --- |
| Documentation or tests only | No runtime action. |
| Static assets in `web/` | No service restart. Refresh the browser when its draft/photos are safe. |
| Python HTTP/desktop bridge | `"$COMPANION_PYTHON" service.py restart-web`; the tunnel and awake guard stay running. |
| Native Swift or `build_app.py` | `"$COMPANION_PYTHON" build_app.py`, then reopen Desktop Companion from Raycast. |
| Supervisor behavior in `service.py`, or intentional machine configuration changes | One coordinated `"$COMPANION_PYTHON" service.py restart`. |
| LaunchAgent definition, startup guard, Python path or login-log location | `"$COMPANION_PYTHON" service.py install --background-install-approved` updates the approved job and restarts it once. No additional `restart` is needed. |
| Optional Cloudflare outage page | Follow [its separate deployment instructions](cloudflare_fallback/README.md) for this Mac's hostname. |

When an update changes both supervisor behavior and its LaunchAgent definition, use the installation action once; it covers both changes.

### One-time upgrade from `999f022` or an older recovery service

The recovery-hardening release `3f10e4f` (#8), included with media review in `21499f3`, changes both the supervisor and the installed LaunchAgent definition. Existing installations that have not applied that definition must run this **once after pulling**:

```sh
"$COMPANION_PYTHON" service.py install --background-install-approved
"$COMPANION_PYTHON" service.py status
```

This replaces the existing approved Companion job and briefly disconnects this Mac's phone tunnel. `restart-web` or `restart` alone cannot rewrite the old job definition. This release has no native Swift change, so it does not require rebuilding the native app. Later documentation-only pulls do not require repeating the installation.

Keep this Mac's `config.local.json`, `.state/`, hostname, tunnel credentials and pairing. An existing installation does not need to run `manage.py setup` or reconfigure its hostname just to apply this update. The approval flag expresses operator intent; it does not grant a security exception.

After the applicable action, check `"$COMPANION_PYTHON" service.py status`, both local health endpoints, and **System status**. An anonymous request to **this Mac's** public `/api/chats` should return **401**. Verify an actual paired-phone message separately if command-delivery behavior changed. A green health panel is not an end-to-end delivery test.

### Shared code and per-Mac network settings

Recovery isolation and safe startup are general improvements for every Mac. Proxy settings, corporate security policy, certificate trust, Cloudflare client installations and any approved network-specific adjustments belong to the individual Mac. Pulling source does not install Zscaler or apply another Mac's network settings. A Mac without Zscaler does not need the managed Mac's Zscaler investigation or policy changes.

Keep machine-specific diagnostics in ignored/private local files. Never copy them into shared configuration or apply a managed-network workaround to all Macs. [CONTRIBUTING.md](CONTRIBUTING.md) covers feature worktrees and PRs; [SETUP.md](SETUP.md) covers independent installation and troubleshooting.

## Everyday use

### Private video review on your phone

Open **… → Media review** to play explicitly shared review videos on the same phone hostname. The library, video bytes, posters, captions, and downloads use the existing browser pairing. An unpaired visitor sees a generic connection screen, without titles or media. Native controls support inline phone playback, seeking, captions, fullscreen, and downloading the original snapshot. The Mac must remain awake and connected; these are not public uploads or cloud storage.

Publish only the specific review assets needed, from this installed source folder:

```sh
"$COMPANION_PYTHON" media.py /absolute/path/pilot.mp4 --id episode-pilot-v2 \
  --title "Episode pilot" --version "Pilot v2 · Review" --duration 50 \
  --poster /absolute/path/poster.png --captions /absolute/path/narration.vtt
```

Then open `https://YOUR-PHONE-HOSTNAME/media?item=episode-pilot-v2`. The command makes private snapshots under ignored `.state/media/`; it does not expose the source folder or accept remote filesystem paths. Publishing a new snapshot needs no service restart. New source checkouts and exports contain the player code, never the media or private catalog. Each Mac publishes its own selected files independently.

Media responses are streamed with byte ranges for seeking, carry private/no-store headers, and are reauthenticated on every request. Signing out or revoking a browser prevents subsequent media reads. It cannot erase bytes already downloaded to that browser. Keep browser pairing, TLS, and origin checks enabled when extending these routes.

Open **Raycast → Desktop Companion** for the native control app. It shows the web server, desktop connection, tunnel, battery/lid state, unattended settings, pairing-code history, and browser sessions. It can start/restart the service even if the dashboard is unavailable. Closing the control app leaves the background service running.

The local dashboard is <http://127.0.0.1:8765>. Phone access uses the hostname configured for that Mac and an authenticated gateway on loopback port 8766. Create a pairing code in **Phone access**, enter it on your own phone browser, and use the same phone address afterward. Pairing codes are one-use and expire after five minutes. Browser sessions default to seven days; in **Phone access → Default browser duration**, choose 1–365 days or **Never expires** for future pairings. Each Mac stores its own setting. Changing the default preserves existing browser expiry dates.

Under **Browser access → Change expiry**, set an active browser's expiry to a duration from now or **Never expires**. Refresh that browser before its previous expiry to receive an extension: the Mac cannot update an offline browser's cookie. Expired or revoked browsers must pair again. Never expires removes Companion's scheduled token expiry; its persistent cookie is renewed for a year on authenticated requests, and the browser may independently clear or limit cookies. You can revoke either kind of session immediately from the Mac. Duration changes use the native Mac controls; a paired remote browser cannot extend its own access. Code/token values are never recoverable from history.

When applying this feature, restart the Python web service with `service.py restart-web` and rebuild/reopen the native app as described in [CONTRIBUTING.md](CONTRIBUTING.md#apply-an-update-on-a-mac). Existing pairing records migrate in place and retain their deadlines; no re-pairing is required for active browsers. Older server versions do not support duration changes or never-expiring sessions, so update the server and native app together before choosing those options.

On the phone:

- **Recent** sorts by the latest recorded action in each chat. Unread badges never reorder the list. Search and project/folder filters include all available local chats; archived and subagent chats have separate filters.
- Home cards use consistent neutral surfaces. A blue dot/edge means **unread**, a green badge means **Working**, and gray means **Idle**. Project/folder chips keep their own muted colors. A remembered selection never colors a Home card.
- Open a chat for Conversation, Activity, Changes, and Details. Earlier messages load with explicit pagination. Recorded activity is allowlisted metadata; arbitrary tool output and reasoning are excluded.
- **New chat** at the bottom right starts with your question and optional pasted/chosen photos. Pick a saved project/folder or Personal chat, then press **Start chat**. The initial name comes from the question; a photo-only chat starts as “Photo question.” The desktop’s model and permission defaults apply.
- The project picker uses the current project when opening a new draft. It cannot silently fall back to Personal if that project is unavailable. Existing drafts keep their selected destination. Native project and folder assignment are verified before the first question can run.
- **Chat options → Rename chat** updates the saved desktop name. The same menu offers **New chat in this project** and **Copy chat link**; copied phone links still require a paired browser.
- Paste a photo directly into the message box, or use **+ → Paste photo / Choose photos**. Preview/remove attachments before sending. Up to three photos are supported.
- An idle connected chat offers **Send**; a working connected chat offers **Queue** and **Steer**. Steer submits once through the existing desktop owner. A fresh preflight rejects an already-changed turn. If the native owner resolves a rollover during delivery, Companion records its actual successful turn acknowledgement instead of treating delivery as failure or retrying. An existing shared channel with an atomic expected-turn API remains preferred when available. No second daemon or automatic queue fallback is created.
- Queued messages show photo counts/previews with **Steer now**, **Edit**, and **Delete** for supported text and Companion photos. Editing keeps the same entry/client identity, position and photos. Deletion has a review step. Requests check the displayed revision and re-read native contents before mutation; lost acknowledgements are never automatically replayed. Edit/delete preserve the last observed version under collapsed **Previous queue versions**, where **Copy to draft** restores text/photos without sending. Replacing an existing draft asks first. Confirmed backups older than seven days are removed during a later queue change; uncertain recovery stays until reviewed. Unsupported attachments still require the Mac.
- Native concurrency limit: queue update/delete have no atomic content precondition spanning the preflight and mutation. Two simultaneous Mac/phone edits can still race; avoid editing the same entry on both devices at once. Saved versions preserve what Companion observed, not an unseen intervening Mac edit. This is an optimistic native-API integration, not a transaction or CAS guarantee. Queue promotion first verifies and removes its original entry, then rechecks active state and steers the original client identity. If the turn ends after removal or delivery becomes uncertain, the saved recovery item remains for manual review and is never resent automatically.
- A submitted message appears immediately with **Sending to your Mac**, then **Accepted by Mac · Syncing conversation** after verified Send/Steer acknowledgement. It merges into saved history only by the exact desktop client message ID. Queue entries stay in the queue panel; rejected or uncertain requests keep the draft. This feedback is local to the current browser tab and never sends another command. Only in-flight sends block Reload app; accepted receipts waiting for history in another chat do not.
- **Working on your Mac…** appears at the end only for a recently confirmed active desktop turn. The open conversation refreshes independently (750 ms while working or syncing, 3 seconds when idle), with no overlapping polls and no reads while hidden. Conditional reads recheck live controls but omit unchanged transcript bodies.
- Reopening a chat starts at the latest message and follows late photo/layout changes. Deliberately scrolling into history or loading earlier messages pauses following; **Latest messages ↓** resumes it.
- A confirmed send clears only the submitted text and photos. New text typed during the request remains. Photo acknowledgements compare normalized image bytes; an uncertain result keeps the draft and never automatically resends it.
- **Questions for you → View & reply** shows saved asynchronous questions, choices, and a typed-answer field. Answers use Steer when connected and active, or Send when idle; disconnected answers explicitly queue. Questions are scoped to the latest in-progress desktop turn: completed, interrupted and superseded turns no longer contribute prompts or home badges. A fresh owner check clears saved-history lag and rejects late answers to closed prompts. Ordinary progress within the same active turn keeps unanswered questions. The panel closes when its final answer is accepted. Queued replies remain visible with the supported queue controls.
- **Requests from your Mac → Review** displays live command, network, file-change, extra-permission and app/tool approvals, plus native runtime questions. The home list marks these chats **Needs response** and includes them in **To reply**. Review the command, working folder, complete patch or exact requested permissions before choosing a response. Conversation and turn scopes are explicit; no approval is automatic or queued as a follow-up message.
- Approval responses go through the running desktop owner's existing request handler, with a fresh owner/request/content check, exact typed request ID, a persistent cross-browser duplicate journal and a subsequent check that the request cleared. This confirms desktop handling, not successful completion of the approved action. A lost acknowledgement locks that request against automatic replay. Opening/reloading the page never approves anything. The final answered request closes its panel automatically.
- Secure macOS dialogs, passwords, sign-ins, verification challenges, execution-bound confirmations, unsupported forms, permanent grants/policy amendments, and approvals with hidden/incomplete details still need the Mac. Pending requests exposed by the owner are listed with that limitation. A macOS system dialog outside the owner's request stream cannot be mirrored by this adapter. Browser pairing grants access to this approval surface; revoke a paired browser if it is no longer trusted.
- Model/effort choices apply to subsequent turns and are verified by readback. Permissions and global defaults are not changed.
- For an older unloaded chat, **Connect this chat to desktop** opens that existing chat in the Mac app. This connects controls without starting inference.
- Weekly allowance remaining stays inline beside Mac status. Tap it for reset time and freshness details. Unknown or stale usage is labeled accordingly.
- **… → Appearance** offers Light, Dark, and System. System follows the viewing device. The choice is remembered per browser and updates other open tabs on the same origin. System fonts and local assets require no external font or theme service.
- Dark mode follows the desktop reference: charcoal canvas, gray navigation/controls, soft white text, blue user bubbles and primary actions. Project/activity colors stay restrained. Questions use blue; amber and red indicate attention or failure. Translucent web materials are limited to navigation and floating controls; messages and forms stay opaque. Reduced transparency and increased contrast preferences remove those blur effects where supported.
- Mac percentage turns yellow when plugged in without active charging, green with a lightning icon while charging, and red below 20%. Low battery takes precedence over the charging color. Weekly remaining allowance is green above 25%, yellow from 11–25%, and red at 10% or less. Stale readings use neutral colors.
- **… → System status** shows a live system map, component-ready count, linked connection path, and supporting services. Tap any node for its current check, or expand **All component details**. Checks refresh every 15 seconds only while visible; unchanged checks keep the same node elements and focus. Interrupted checks are marked as last known. Recovery controls remain in the Raycast app on the Mac; an unreachable service cannot be restarted through its own web page.
- Sign out is under **… → This browser**, separated from refresh. Signing out clears this browser’s in-memory drafts and access, while desktop work continues.

## Refresh, updates, and seen state

Scheduler trigger instructions are omitted from the conversation and last-message preview; human messages and assistant reminders/results remain. The reader uses `user.heartbeat` provenance when present. The current history projection omits that metadata, so the fallback requires the complete scheduler envelope, scheduler fields, and absence of a human client receipt. Quoted/fenced examples and ordinary text mentioning heartbeat remain visible. This does not change the scheduler or its execution.

Pull down inside the chat list or reader **when it is at the top** for a soft refresh that keeps drafts and selected photos. The **↻** header button works from any scroll position. In a normal browser tab, pulling from the header leaves the browser’s own refresh gesture intact. In an app saved to the **Home Screen**, pulling from the app header reloads the **entire page**, even when the reader is scrolled. It uses the same draft/photo confirmation and active-operation guards as Reload app, and keeps the current chat URL. The gesture says **Release to reload app**, distinct from the panel’s soft refresh. HTML and assets use `Cache-Control: no-store`, and assets include a build version; there is no offline service worker to retain an old interface. Soft refresh shows a spinner and completion tick; a full reload restarts the page and its connection screen. **… → This browser → Reload app** reloads the interface; it waits for active operations and asks before discarding unsent drafts or photos.

Optional vibration is requested only where the browser supports the standard API. iPhone Safari/WebKit does not support it, so Home Screen mode cannot promise native refresh haptics; see [Vibration API support](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/vibrate#browser_compatibility). Mobile headers and composers use solid backgrounds with safe-area spacing. The DC button and Projects & filters have larger touch targets, and photo uploads re-anchor the current text selection without stealing focus. Physical iPhone keyboard/caret rendering still requires a device check.

When the keyboard is open, swipe down on ordinary conversation text to dismiss it. This gesture only dismisses the keyboard: it preserves the draft/photos and does not also refresh or reload. Text selection, links/buttons, code/table interactions, upward/horizontal gestures, dialogs and pinch zoom retain their own behavior. The arrows/checkmark bar above an iPhone keyboard is supplied by iOS; it is not a Companion toolbar or removable through page CSS. Its checkmark remains another way to dismiss the keyboard. Standard web vibration is unavailable in iPhone WebKit; browser-native refresh haptics are not exposed to this Home Screen app.

HTML stamps a frontend build into every asset URL. Visible polling, foregrounding, and refresh compare it with the server. An outdated idle tab reloads once per new build. Drafts, uploads, active operations, focused inputs, and dialogs prevent automatic reload; an update banner lets the user choose when to reload. The server rejects mutations from a known obsolete frontend before submission. Pages restored through Back/Forward fetch fresh data. There is no service worker or offline asset cache. Tabs opened before this mechanism was installed need one full reload to acquire it.

Seen receipts refer to the exact revision displayed at the visible reader’s end. Hidden readers, older history, and open dialogs do not mark newer replies seen. Stale catalog responses cannot undo a same-revision read acknowledgement. Validation/persistence is serialized across paired browsers in Companion’s own store. The desktop databases are never modified.

Working status comes from a bounded snapshot of the running desktop owner. A saved `inProgress` row alone cannot establish Working. An unavailable live status is shown as **Status unavailable**. Send/Steer use fresh live state, including the current turn ID; the runtime checks again immediately before delivery.

## Running and recovery

The core remains Python standard library plus the native Swift app; it adds no Python packages, cloud service, or resident model. Recovery runs in the existing supervisor. Photo RPC capability is checked lazily on first use using the installed CLI's read-only schema generator; a successful result is reused.

The supervisor checks both loopback listeners every ten seconds, with a two-second limit per request. Three consecutive failures restart only the web process through its existing bounded backoff; the tunnel and awake guard keep running. Health checks verify the child PID and detect a command stuck for two minutes without waiting for its journal lock. Uncertain command records survive the restart and are never automatically replayed. Runtime RPC deadlines include both pipe writes and reply reads, including large Unicode inputs.

Missing or inaccessible tunnel executables and missing local credentials use a repair exit code, pausing that component instead of retrying them as network outages. After repairing the approved installation, use **Reconnect tunnel**. Normal network interruptions keep retrying automatically, and macOS joining a saved Wi-Fi network triggers the existing reconnection path.

When upgrading to this combined startup, expiry, battery and recovery release, update the complete source, rebuild/reopen the native app, and perform one coordinated `service.py restart` while no command is pending. `restart-web` alone cannot load the new supervisor. Preserve the Mac's own state and configuration. The battery helper is enabled separately through **Unattended mode → Apply settings…**, using the standard macOS administrator prompt; pulling or merging code does not install it or change power settings.

Python 3.11+ is required; the desktop bridge has no pip dependencies or separate OpenAI API keys. Optional Exo API profiles have their own authentication. Select `COMPANION_PYTHON` using [SETUP](SETUP.md#prerequisites) in each terminal. From this folder:

```sh
"$COMPANION_PYTHON" manage.py doctor
"$COMPANION_PYTHON" service.py status
"$COMPANION_PYTHON" service.py restart-web
```

The explicitly installed per-user LaunchAgent starts at login and supervises the web server, idle-sleep guard and optional tunnel. It checks desktop-app availability for status but never opens or relaunches ChatGPT. Use **Restart service** in a component's native recovery controls, or `restart-web`, after Python/web-server changes: it replaces only the web server and its authentication gateway, preserving the tunnel and awake guard. A crashed web server recovers independently with increasing backoff. The server and awake guard retry after 3, 6, 12 and 24 seconds; a fifth consecutive rapid failure pauses recovery. A minute of runtime resets that component’s failure budget. Missing-runtime/configuration exit codes pause immediately. Explicit **Restart service** resets the web budget after the cause is repaired. Static web assets need no process restart. Use `"$COMPANION_PYTHON" service.py restart` only for explicit full-system recovery or supervisor/configuration changes; it stops and recreates the whole group, including the tunnel.

An awake-guard failure recovers independently and leaves the web server and tunnel running; once paused, repair the cause and request a full restart to reset its budget. An exited tunnel controller retries after 30, 60, 120 and 240 seconds, then every five minutes, so a temporary network outage never exhausts recovery. Permanent local exit codes and missing, inaccessible or invalid executables pause immediately instead. An explicit tunnel reconnect resumes it after repair. Child spawn errors are contained to the affected component. Recovery notices use fixed component labels in private `~/Library/Logs/Desktop Companion/` logs, separate from the source checkout. A debounced network watcher accelerates recovery of an absent connector after connectivity changes; it preserves a live controller so interface metadata changes cannot discard established edges. The public watchdog preserves registered edges during failed probes. With no registered edges, it allows five minutes of continuous public failure before requesting recovery; an anonymous HTTP 200 still stops access immediately. TLS verification stays enabled. Network outages still need the underlying connection to recover.

Credential-free lifecycle records in private `.state/tunnel-events.jsonl` distinguish supervisor starts, connector exits, network-change decisions, watchdog recovery, and public health transitions. They rotate at 256 KB with one previous file and accept only fixed event names, numeric process/status fields, and known reason codes. Raw connector logs are retained between attempts instead of being overwritten on every retry. Neither file is included in a source export.

A public health warning does not always mean the tunnel disconnected. `public_degraded` with status `0` means the HTTPS probe did not complete; with registered edges and unchanged connector/controller PIDs, this is a public-reachability uncertainty, not evidence of a connector restart. A subsequent `public_ready` with status `401` confirms that probe recovered. Keep the warning visible while reachability is unverified, and inspect connector exits, edge registrations and public probes separately. A registered edge alone does not prove that a phone request succeeded.

Full-group restarts always close established tunnel connections. A network may preserve an existing connection while resetting a fresh handshake, so repeated group restarts can worsen an intermittent outage. Use web-only recovery when appropriate and retain the actual machine's failure evidence privately. TLS inspection observed on one managed Mac does not establish a fault on another Mac or, by itself, explain a reset.

For foreground debugging, stop the installed service first so copies do not compete for ports. Unlike the login service, a direct server launch uses the terminal PATH; add the configured desktop app’s bundled CLI directory before starting it:

```sh
"$COMPANION_PYTHON" service.py stop
COMPANION_CLI_DIR="$("$COMPANION_PYTHON" -c 'from configuration import load; from pathlib import Path; p=load().codex_path(); assert p, "Run doctor and resolve the missing CLI first"; print(Path(p).parent)')"
PATH="$COMPANION_CLI_DIR:$PATH" "$COMPANION_PYTHON" companion.py
```

After reviewing the security requirements, use `"$COMPANION_PYTHON" service.py install --background-install-approved` to explicitly reinstall login startup, or `"$COMPANION_PYTHON" service.py uninstall` to remove it while retaining private state. Rebuild native changes with `"$COMPANION_PYTHON" build_app.py`, then reopen the control app. Machine settings are in ignored `config.local.json`; source-only exports never include it.

## Unattended operation and lock state

Automatic recovery starts after login/FileVault unlock. On an awake Mac, attaching power resumes the awake guard and returning network connectivity resumes phone access without reopening the control app. While macOS is fully asleep, Companion cannot poll power or Wi-Fi; it resumes after a system/hardware wake. Charger/lid/network wake behavior must be checked on each Mac. Companion does not add periodic wake timers, alter firmware wake settings, enable automatic login, or bypass FileVault. The small root-owned power helper below is the additional macOS background item needed to manage the closed-lid sleep override safely.

The service runs `caffeinate -is` on charger power and on battery at 20% or above. It releases that guard below 20% on battery, or if the power source/percentage cannot be read, and resumes it when power recovers. It does not request display-awake or simulated-user-activity assertions. The screen can turn off and lock while background work continues.

**Unattended mode** separates **Keep working when the lid is closed**, **Screen off & lock**, and background/phone readiness. Screen timers default to **5 minutes on battery** and **10 minutes plugged in**, with bounded picker choices from 1 to 60 minutes and **Use default timers**. Choices remain pending until **Apply settings…** completes the native macOS administrator prompt. The app applies per-source `displaysleep` and installs a root-owned, launchd-supervised power helper when closed-lid operation is enabled. Every 15 seconds the helper enables the system-wide sleep override on charger power or battery at 20% or above, and clears it below 20% on battery or when the source is unknown or a battery reading is unavailable. The app verifies the actual sleep/display values and a fresh helper heartbeat; a permanent override alone is not reported as a configured cutoff. It reports partial/unverified application honestly. Changing a picker or pulling source never changes machine power settings by itself. **Lock screen now** requires a freshly verified immediate password-on-display-off setting. The app never reads or stores the administrator password.

The helper runs independently of the control app and user service, including after reboot, so closing the app or stopping the chat service does not remove the battery cutoff. While active, the override also blocks manual sleep. To remove it, turn off **Keep working when the lid is closed** and **Apply settings…** before uninstalling Companion. This unloads/removes its LaunchDaemon and helper and restores normal system sleep; display timers retain your selected values. Other apps can still hold their own idle-sleep assertions. The cutoff is polled, so the transition can take about 15 seconds; it is not a battery shutdown mechanism. It does not change FileVault, automatic login, password requirements, or native authorization policy. A login LaunchAgent cannot run before login/FileVault unlock. Depleted power, shutdown, or an internet outage ends connectivity. Staying awake and native desktop interaction while locked are separate capabilities.

The OpenAI Locked Use component can be current and enabled while an automatic unlock is refused. OpenAI scopes unlock to active trusted Computer Use turns and pauses it after local keyboard/pointer input until a manual unlock. The recovery procedure is to unlock manually once, confirm Locked use is enabled, lock again, and start a fresh native-app test from the phone. Companion cannot override that authorization or promise that it will never require manual recovery. A 2026-10-21 check reproduced the refusal, then successfully read native Chrome after a manual unlock and a new user-confirmed lock. This is a bounded test, not continuous certification. See the [official Locked Use guide](https://learn.chatgpt.com/docs/computer-use#locked-use). Mac status shows the component and last check. Physical lid-close/mobile-data continuity and native desktop access must be checked separately on each Mac. Setup on a second Mac does not apply power or security settings automatically.

## Independent local assistant

**Local chats** in Companion’s sidebar, home screen and Mac control app opens the same Exo-backed history on Mac and phone. Use **Chat options → Fork & continue locally** on a desktop conversation to review a bounded copy of its completed visible text. The branch stays separate and does not replay earlier actions or alter the source chat. Local chats support light work, selected saved skills, read-only workspace files and authenticated API diagnostics. Codex’s native project name does not enforce an Exo provider, and these Companion chats do not appear in the native Codex or Exo browser chat lists. Configure Exo, skill roots and private API profiles independently on each Mac; this feature defaults to disabled. See [local setup, office authentication and limits](LOCAL_ASSISTANT.md).

## Architecture and safety boundaries

- `companion.py`: HTTP routes and a version-specific read-only history adapter.
- `desktop_runtime.py`: owner-routed IPC for bounded live snapshots, Send/model changes and exact request approval replies; owner-routed Steer and optimistic queue mutation checks. It validates the socket owner/mode, method versions, snapshot source/identity, and turn acknowledgements. It never claims ownership, resumes chats through an independent daemon, or forwards arbitrary caller-selected methods.
- `runtime.py`, `new_chat.py`: read-only account queries and bounded queue, new-empty-chat, and naming APIs. A separate app-server never runs an existing chat. The existing native desktop owner starts the first question after the new chat and its destination are verified.
- `access.py`, `inbox.py`, `photos.py`, `promotions.py`: Companion-owned private stores under `.state/`.
- `web/`: the UI, with separate clipboard, queue controls, new-chat, appearance, status, refresh, update, conversation feedback/scrolling, and Markdown modules. `theme.css` owns light/dark design tokens and common typography; `style.css` retains layout and gesture sizing.
- Native overview health becomes **unknown** immediately after a failed read or after 20 seconds without a fresh snapshot. Historical values remain available; neither green map nodes nor “All components are ready” survive a stale reading.
- `system_status.py`: allowlisted tunnel health and freshness; `control.py` assembles component checks shared by the native app and authenticated web viewer.
- `configuration.py`, `manage.py`, `service.py`, `control.py`, `build_app.py`, `native/`: per-Mac configuration, setup/export, supervision, and Raycast control app.

The native control app uses shared adaptive colors in `native/Appearance.swift` and follows the Mac appearance by default. **Appearance** in its sidebar also offers Light and Dark. On macOS 26+ with a supporting SDK, selected navigation and secondary controls use system Liquid Glass; older systems and Reduce Transparency use conventional controls. Primary actions and content cards retain opaque surfaces for predictable contrast. This does not change the desktop ChatGPT app's appearance or system settings.

The loopback phone gateway requires a configured HTTPS origin, session authentication, exact-origin checks, and CSRF for changes. The tunnel targets that gateway exclusively. Cloudflare terminates public TLS and carries pairing traffic, displayed chat text, photos, and commands; application pairing controls access. Conversation bodies are not end-to-end encrypted from Cloudflare.

An optional [independent outage page](cloudflare_fallback/README.md) can explain a tunnel failure even when the Mac is unreachable. It replaces only failed HTML homepage requests and preserves HTTP 503. API traffic keeps its original route and is never replayed. The source bundle includes its dependency-free builder and tests. The certificate diagnostic can identify TLS inspection on a failing connection; that observation alone does not prove the inspection caused an outage or explain its onset. Keep certificate validation and network security software enabled, and ask IT to verify the approved Cloudflare Tunnel policy on port 7844 when fresh connections fail.

Photo uploads are normalized to bounded JPEGs in the browser and decoded/re-encoded by macOS `sips`. Files live outside the web root with private permissions, session-owned draft access, authenticated reads, three-photo/4 MB message bounds, and a 128 MB store cap. Clipboard HTML/remote image URLs are never fetched as attachments. Ordinary composer drafts stay in browser memory and clear on full reload/sign-out. **… → Photo storage** lists this browser’s recoverable unsent photos and protected storage usage; removing a draft never removes submitted attachments. Unsubmitted drafts expire after seven days during a subsequent upload, recovering abandoned quota. Sent, queued and uncertain photos never expire automatically. The 128 MB bound remains deliberate: if protected attachments fill it, Companion reports the limitation rather than deleting referenced photos to make room. Submission rechecks and protects files under the photo-store lock before delivering a command.

New-chat photos have a separate browser-owned draft scope. They bind only to the verified created chat. Personal chats use `~/Library/Application Support/Desktop Companion/Chats`, outside the Scripts project. Legacy Personal workspaces are recognized without rewriting desktop databases or moving their files. Creation and first delivery have separate durable checkpoints: a connection retry reuses the created chat, and an uncertain first delivery is never automatically repeated. Reload recovery stores only an opaque request ID in session storage, not the question or photo contents.

Commands are durably journaled. Accepted or uncertain submissions are never automatically replayed or silently changed from Steer to Queue. Queue edit/delete retain the last observed version for explicit recovery; their revision checks are optimistic and cannot lock simultaneous Mac edits. Promotion removes its original entry before steering, and incomplete promotions retain a reviewable recovery item. Question answers use native question IDs and the desktop’s answer envelope; native live request replies use the original typed RPC request ID.

`--read-only` disables mutations, and an alternate `--codex-home` cannot mutate another active home. Cloud-only/other-host chats, secure macOS dialogs and unsupported approval forms, Stop, worktree controls, and non-photo files still require the desktop app. App updates may change local schemas/protocols; unsupported integration states fail visibly.

## Validation

```sh
"$COMPANION_PYTHON" -m unittest discover -p 'test_*.py'
node --test test_*.cjs
"$COMPANION_PYTHON" manage.py bundle --output Desktop-Companion-source.zip
```

Tests use isolated databases and fake desktop protocols. They cover authentication/CSRF, origin/TLS enforcement, read-only history, receipts and races, deduplication, active-turn preflight and rollover acknowledgements, stale queue-content rejection and reversible queue edits, stale status overrides, native screen-timer policies, photo normalization and abandoned-draft recovery, setup/export isolation, two refresh zones, clipboard behavior, draft-safe version updates, project verification, first-message retries, naming, theme persistence, status colors, and component freshness.

Historical validation on **2026-10-21** passed **268 Python tests and 74 JavaScript tests**, and the complete native app compiled. These counts describe that earlier snapshot, not the current head. For the current commit, inspect the [GitHub Checks workflow](https://github.com/sagaryadaviitk/desktop-companion/actions/workflows/checks.yml) and rerun the suites above. Checks cover exact approval identity/scope and uncertain-response protection, stale-question turn boundaries and live overrides, restored optimistic queue controls, native-owner Steer with actual-turn acknowledgements, cross-chat receipt reload recovery, photo retention and abandoned-draft reclamation, and native screen-timer/freshness policies, alongside the existing transport/authentication, setup and mobile regressions. Isolated light/dark phone previews verified a synthetic approval denial, automatic panel dismissal and photo-storage UI; a disposable native preview verified the 5/10-minute choices and loss-of-health transition. These checks do not certify a real phone approval, applied power settings, native haptics, or locked-screen continuity.

The earlier blanket block on Steer/Edit/Delete has been removed. The native owner may resolve a rollover, and queue mutations remain optimistic because the installed protocol lacks a cross-device CAS. Tests verify what Companion can enforce: exact chat/client identity, a fresh preflight, content checks, acknowledgements, recovery and no automatic replay. They do not establish serializable editing across the Mac and phone. [Official Steer and Queue behavior](https://learn.chatgpt.com/docs/prompting#steering-and-queuing).

Live UI verification on 2026-10-21: a disposable photo message queued successfully and cleared the composer, then was edited in place with its photo retained and deleted before delivery. The queue and delivered history both contained zero copies afterward. The native app rebuilt/installed and both appearances were checked, restoring Match Mac. Neutral home cards, blue actions, queue previews, and header typography were checked in mobile layouts. Web-only deployment preserved the supervisor, awake guard, and tunnel controller; four edge connections and the protected public HTTP 401 remained after the hostname-preservation repair. A fresh startup record must match the configured hostname and a live controller to retain that approved origin; cold or stale starts still require normal activation.

The subsequent post-fix boot passed an observation through ten minutes after supervisor startup: 27 public probes returned the expected anonymous HTTP 401, both local health endpoints returned 200, four edge connections remained registered, and the supervisor/server/guard/tunnel-controller PIDs stayed unchanged. No connector exits or forced restarts were recorded. A paired browser also authenticated after boot. The certificate-only diagnostic still identified Zscaler during this working connection, so that result alone cannot explain the earlier outage. This establishes a bounded live stability result, not a guarantee about future network handoffs. The private observation is in `.state/postboot-observation.json` and is excluded from exports.

Live verification on 2026-10-20: a pasted image produced a removable preview; mobile-layout Steer marker `dc-live-state-20261020-1` appeared exactly once in the desktop conversation and zero times in its queue. The native control app compiled successfully. Phone layouts were checked at 320px and 390px with a 53px inline header and no document overflow. Physical phone haptics/keyboard behavior are not emulated by these desktop tools.

The prompt-first flow was also verified in real Personal and Scripts chats: each had exactly one user question, completed its requested response, and had no queued message. The Personal test included one pasted photo and was renamed through the UI with native readback. Test chats were archived afterward. Both themes were visually checked, including a 440px reduced-height composer. Battery and weekly header text both compute to 12px on phones; refresh/menu targets are 44px. Core text and project-label contrast was checked against 4.5:1 in both palettes. Public tunnel recovery is a separate check from these local UI and message-delivery results.

For the 116-chat catalog, local full reads measured a median **103.5 ms** and about **112 KB** of JSON; unchanged conditional reads measured **1.3 ms** and no response body. Conditional ETags cover history plus Companion read receipts, skip unchanged history scans, and do not cache live execution checks. These are local measurements, not mobile-network latency guarantees.
