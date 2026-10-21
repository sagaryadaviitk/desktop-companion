# Desktop Companion

Browse the desktop app’s local projects, chats, history, and recorded progress from a paired phone browser. Send text/photos, answer questions and live approvals, and choose a model through the running Mac. Steer uses the existing desktop owner, matching the Mac composer’s active-chat behavior. No Slack or Telegram integration is required.

For a second Mac, start with [SETUP.md](SETUP.md). Each Mac has independent settings, history, pairing, and tunnel access. A clean source copy starts locally only.

## Install on a second Mac

The source is in the private personal repository [sagaryadaviitk/desktop-companion](https://github.com/sagaryadaviitk/desktop-companion). Each Mac runs independently; Git shares code, not chat sessions, settings, browser pairing, or credentials. The agent on the new Mac should read this section, [AGENTS.md](AGENTS.md), and [SETUP.md](SETUP.md) before installing.

1. Install and sign in to the ChatGPT/Codex Mac app normally. Create or open a local chat once. Install Python **3.11 or newer**, Git/GitHub CLI, and Apple command-line tools (`xcode-select --install` if missing). Node **24** is used for tests; it is not needed to run the app. Install `cloudflared` only for remote phone access.
2. Sign in to your **personal** GitHub account through `gh auth login`, and choose a permanent source location. Do not copy another Mac's `.state/`, `config.local.json`, desktop sign-in, or tunnel credentials.

```sh
mkdir -p ~/Developer
cd ~/Developer
gh repo clone sagaryadaviitk/desktop-companion
cd desktop-companion
python3 manage.py doctor
python3 -m unittest discover -p 'test_*.py' -q
node --test test_*.cjs
python3 manage.py setup
```

Doctor reports missing dependencies or incompatible history before setup. Setup prepares local configuration and builds `~/Applications/Desktop Companion.app`; it **does not install or start a background service**. Keep the source folder at this location. `--without-app` skips the native build. After reviewing [background installation and security recovery](SETUP.md#background-installation-and-security-recovery), explicitly enable approved login startup with `python3 service.py install --background-install-approved` (or `python3 manage.py setup --install-service`). The flag records operator intent; it does not grant an IT exception or override endpoint protection. Then open **Raycast → Desktop Companion** or <http://127.0.0.1:8765>. Setup does not change power, password, or Locked Use settings.

3. If phone access is needed, use a **separate hostname and tunnel for this Mac**. Through your own Cloudflare account, authenticate the CLI and create/route the new tunnel. The following example uses a new hostname; do not reuse the first Mac's live hostname or connector credential.

```sh
cloudflared tunnel login
cloudflared tunnel create desktop-companion-second
cloudflared tunnel route dns desktop-companion-second agents-second.sagaryadav.me
python3 manage.py configure --hostname agents-second.sagaryadav.me --tunnel-id YOUR-NEW-TUNNEL-UUID
python3 manage.py doctor
python3 service.py restart
```

Use the UUID reported by `tunnel create`; its credential stays in this Mac's `~/.cloudflared`. This one full restart applies the new machine configuration. Open **Phone access** in the native app, create a one-use code, and pair the phone browser with this Mac's hostname. Keep TLS verification and network security software enabled. A network policy that blocks the tunnel needs an approved network fix; reinstalling Companion does not bypass it.

4. Verify each layer separately:

```sh
python3 service.py status
curl --fail http://127.0.0.1:8765/healthz
curl -s -o /dev/null -w '%{http_code}\n' https://agents-second.sagaryadav.me/api/chats
```

The local health endpoint should return `ok: true`. An **unpaired** public API request should return **401**; this proves the protected route is reachable, not that the phone is paired. From the paired phone, open a chat, send a harmless message, and verify it appears exactly once in desktop history. Test photos, a fresh locked native-app turn, and lid-close/mobile-data continuity separately. Do not mark those tests passed based only on a running process, installed plug-in, or green tunnel node.

For subsequent updates, use a clean `main` checkout and `git pull --ff-only`, then run the tests. Static web assets require no restart. Python HTTP/bridge updates use `python3 service.py restart-web`; native Swift changes use `python3 build_app.py` and reopening the control app. A supervisor or machine-configuration update needs one coordinated full restart. Work in a separate feature worktree so edits do not change the installed service underneath it. [CONTRIBUTING.md](CONTRIBUTING.md) has the exact update/PR workflow; [SETUP.md](SETUP.md) covers troubleshooting and per-Mac configuration.

## Everyday use

### Private video review on your phone

Open **… → Media review** to play explicitly shared review videos on the same phone hostname. The library, video bytes, posters, captions, and downloads use the existing browser pairing. An unpaired visitor sees a generic connection screen, without titles or media. Native controls support inline phone playback, seeking, captions, fullscreen, and downloading the original snapshot. The Mac must remain awake and connected; these are not public uploads or cloud storage.

Publish only the specific review assets needed, from this installed source folder:

```sh
python3 media.py /absolute/path/pilot.mp4 --id episode-pilot-v2 \
  --title "Episode pilot" --version "Pilot v2 · Review" --duration 50 \
  --poster /absolute/path/poster.png --captions /absolute/path/narration.vtt
```

Then open `https://YOUR-PHONE-HOSTNAME/media?item=episode-pilot-v2`. The command makes private snapshots under ignored `.state/media/`; it does not expose the source folder or accept remote filesystem paths. Publishing a new snapshot needs no service restart. New source checkouts and exports contain the player code, never the media or private catalog. Each Mac publishes its own selected files independently.

Media responses are streamed with byte ranges for seeking, carry private/no-store headers, and are reauthenticated on every request. Signing out or revoking a browser prevents subsequent media reads. It cannot erase bytes already downloaded to that browser. Keep browser pairing, TLS, and origin checks enabled when extending these routes.

Open **Raycast → Desktop Companion** for the native control app. It shows the web server, desktop connection, tunnel, battery/lid state, unattended settings, pairing-code history, and browser sessions. It can start/restart the service even if the dashboard is unavailable. Closing the control app leaves the background service running.

The local dashboard is <http://127.0.0.1:8765>. Phone access uses the hostname configured for that Mac and an authenticated gateway on loopback port 8766. Create a pairing code in **Phone access**, enter it on your own phone browser, and use the same phone address afterward. Pairing codes are one-use and expire after five minutes; browser sessions expire after seven days or immediately upon revocation. Code/token values are never recoverable from history.

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
- **Working on your Mac…** appears at the end only for a recently confirmed active desktop turn. The open conversation refreshes independently (about 1.5 seconds while working or syncing, five seconds when idle), with no overlapping polls and no reads while hidden. Conditional reads recheck live controls but omit unchanged transcript bodies.
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

Python 3.11+ is required; there are no Python package dependencies or API keys. See SETUP for installation and per-Mac configuration. From this folder:

```sh
python3 manage.py doctor
python3 service.py status
python3 service.py restart-web
```

The explicitly installed per-user LaunchAgent starts at login and supervises the web server, idle-sleep guard and optional tunnel. It checks desktop-app availability for status but never opens or relaunches ChatGPT. Use **Restart service** in a component's native recovery controls, or `restart-web`, after Python/web-server changes: it replaces only the web server and its authentication gateway, preserving the tunnel and awake guard. A crashed web server recovers independently with increasing backoff. The server and awake guard retry after 3, 6, 12 and 24 seconds; a fifth consecutive rapid failure pauses recovery. A minute of runtime resets that component’s failure budget. Missing-runtime/configuration exit codes pause immediately. Explicit **Restart service** resets the web budget after the cause is repaired. Static web assets need no process restart. Use `python3 service.py restart` only for explicit full-system recovery or supervisor/configuration changes; it stops and recreates the whole group, including the tunnel.

An awake-guard failure recovers independently and leaves the web server and tunnel running; once paused, repair the cause and request a full restart to reset its budget. An exited tunnel controller retries after 30, 60, 120 and 240 seconds, then every five minutes, so a temporary network outage never exhausts recovery. Permanent local exit codes and missing, inaccessible or invalid executables pause immediately instead. An explicit tunnel reconnect resumes it after repair. Child spawn errors are contained to the affected component. Recovery notices use fixed component labels in private `~/Library/Logs/Desktop Companion/` logs, separate from the source checkout. A debounced network watcher accelerates recovery of an absent connector after connectivity changes; it preserves a live controller so interface metadata changes cannot discard established edges. The public watchdog preserves registered edges during failed probes. With no registered edges, it allows five minutes of continuous public failure before requesting recovery; an anonymous HTTP 200 still stops access immediately. TLS verification stays enabled. Network outages still need the underlying connection to recover.

Credential-free lifecycle records in private `.state/tunnel-events.jsonl` distinguish supervisor starts, connector exits, network-change decisions, watchdog recovery, and public health transitions. They rotate at 256 KB with one previous file and accept only fixed event names, numeric process/status fields, and known reason codes. Raw connector logs are retained between attempts instead of being overwritten on every retry. Neither file is included in a source export.

Deployment investigation, 2026-10-20: the public gateway returned the expected anonymous HTTP 401 at 16:17:53 UTC even while connection resets were recorded. Later full-group restarts at 16:57:53, 17:01:17, and 17:03:30 UTC necessarily tore down the tunnel. Fresh HTTP/2 registration now fails, and a certificate probe confirms Zscaler TLS inspection on that path. Reconnection after the full restarts may have exposed the fresh-handshake failure; the exact onset and policy change remain unproven. Do not infer that every reset meant public access was already down. Web-only recovery was subsequently verified to replace the server while preserving supervisor and awake-guard PIDs and restoring local health. The tunnel was already in retry backoff, so that live check does not establish continuity of a healthy public tunnel.

For foreground debugging, stop the installed service first so copies do not compete for ports:

```sh
python3 service.py stop
python3 companion.py
```

After reviewing the security requirements, use `python3 service.py install --background-install-approved` to explicitly reinstall login startup, or `python3 service.py uninstall` to remove it while retaining private state. Rebuild native changes with `python3 build_app.py`, then reopen the control app. Machine settings are in ignored `config.local.json`; source-only exports never include it.

## Unattended operation and lock state

The service runs `caffeinate -is` to prevent idle system sleep on battery and charger power. It does not request display-awake or simulated-user-activity assertions. The screen can turn off and lock while background work continues.

**Unattended mode** separates **Keep working when the lid is closed**, **Screen off & lock**, and background/phone readiness. Screen timers default to **5 minutes on battery** and **10 minutes plugged in**, with bounded picker choices from 1 to 60 minutes and **Use default timers**. Choices remain pending until **Apply settings…** completes the native macOS administrator prompt. The app applies per-source `displaysleep` and the selected system-wide `disablesleep` flag, then verifies all three actual values. It reports partial/unverified application honestly. Changing a picker or pulling source never changes machine power settings by itself. **Lock screen now** requires a freshly verified immediate password-on-display-off setting. The app never reads or stores the administrator password.

The sleep setting persists beyond stopping/uninstalling Companion and also blocks manual sleep. It does not change FileVault, automatic login, password requirements, or native authorization policy. A login LaunchAgent cannot run before login/FileVault unlock. Depleted power, shutdown, or an internet outage ends connectivity. Staying awake and native desktop interaction while locked are separate capabilities.

The OpenAI Locked Use component can be current and enabled while an automatic unlock is refused. OpenAI scopes unlock to active trusted Computer Use turns and pauses it after local keyboard/pointer input until a manual unlock. The recovery procedure is to unlock manually once, confirm Locked use is enabled, lock again, and start a fresh native-app test from the phone. Companion cannot override that authorization or promise that it will never require manual recovery. A 2026-10-21 check reproduced the refusal, then successfully read native Chrome after a manual unlock and a new user-confirmed lock. This is a bounded test, not continuous certification. See the [official Locked Use guide](https://learn.chatgpt.com/docs/computer-use#locked-use). Mac status shows the component and last check. Physical lid-close/mobile-data continuity and native desktop access must be checked separately on each Mac. Setup on a second Mac does not apply power or security settings automatically.

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
python3 -m unittest discover -p 'test_*.py'
node --test test_*.cjs
python3 manage.py bundle --output Desktop-Companion-source.zip
```

Tests use isolated databases and fake desktop protocols. They cover authentication/CSRF, origin/TLS enforcement, read-only history, receipts and races, deduplication, active-turn preflight and rollover acknowledgements, stale queue-content rejection and reversible queue edits, stale status overrides, native screen-timer policies, photo normalization and abandoned-draft recovery, setup/export isolation, two refresh zones, clipboard behavior, draft-safe version updates, project verification, first-message retries, naming, theme persistence, status colors, and component freshness.

The current combined source passed **268 Python tests and 74 JavaScript tests** on 2026-10-21, and the complete native app compiled. Checks cover exact approval identity/scope and uncertain-response protection, stale-question turn boundaries and live overrides, restored optimistic queue controls, native-owner Steer with actual-turn acknowledgements, cross-chat receipt reload recovery, photo retention and abandoned-draft reclamation, and native screen-timer/freshness policies, alongside the existing transport/authentication, setup and mobile regressions. Isolated light/dark phone previews verified a synthetic approval denial, automatic panel dismissal and photo-storage UI; a disposable native preview verified the 5/10-minute choices and loss-of-health transition. These checks do not certify a real phone approval, applied power settings, native haptics, or locked-screen continuity.

The earlier blanket block on Steer/Edit/Delete has been removed. The native owner may resolve a rollover, and queue mutations remain optimistic because the installed protocol lacks a cross-device CAS. Tests verify what Companion can enforce: exact chat/client identity, a fresh preflight, content checks, acknowledgements, recovery and no automatic replay. They do not establish serializable editing across the Mac and phone. [Official Steer and Queue behavior](https://learn.chatgpt.com/docs/prompting#steering-and-queuing).

Live UI verification on 2026-10-21: a disposable photo message queued successfully and cleared the composer, then was edited in place with its photo retained and deleted before delivery. The queue and delivered history both contained zero copies afterward. The native app rebuilt/installed and both appearances were checked, restoring Match Mac. Neutral home cards, blue actions, queue previews, and header typography were checked in mobile layouts. Web-only deployment preserved the supervisor, awake guard, and tunnel controller; four edge connections and the protected public HTTP 401 remained after the hostname-preservation repair. A fresh startup record must match the configured hostname and a live controller to retain that approved origin; cold or stale starts still require normal activation.

The subsequent post-fix boot passed an observation through ten minutes after supervisor startup: 27 public probes returned the expected anonymous HTTP 401, both local health endpoints returned 200, four edge connections remained registered, and the supervisor/server/guard/tunnel-controller PIDs stayed unchanged. No connector exits or forced restarts were recorded. A paired browser also authenticated after boot. The certificate-only diagnostic still identified Zscaler during this working connection, so that result alone cannot explain the earlier outage. This establishes a bounded live stability result, not a guarantee about future network handoffs. The private observation is in `.state/postboot-observation.json` and is excluded from exports.

Live verification on 2026-10-20: a pasted image produced a removable preview; mobile-layout Steer marker `dc-live-state-20261020-1` appeared exactly once in the desktop conversation and zero times in its queue. The native control app compiled successfully. Phone layouts were checked at 320px and 390px with a 53px inline header and no document overflow. Physical phone haptics/keyboard behavior are not emulated by these desktop tools.

The prompt-first flow was also verified in real Personal and Scripts chats: each had exactly one user question, completed its requested response, and had no queued message. The Personal test included one pasted photo and was renamed through the UI with native readback. Test chats were archived afterward. Both themes were visually checked, including a 440px reduced-height composer. Battery and weekly header text both compute to 12px on phones; refresh/menu targets are 44px. Core text and project-label contrast was checked against 4.5:1 in both palettes. Public tunnel recovery is a separate check from these local UI and message-delivery results.

For the 116-chat catalog, local full reads measured a median **103.5 ms** and about **112 KB** of JSON; unchanged conditional reads measured **1.3 ms** and no response body. Conditional ETags cover history plus Companion read receipts, skip unchanged history scans, and do not cache live execution checks. These are local measurements, not mobile-network latency guarantees.
