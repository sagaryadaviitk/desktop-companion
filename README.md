# Desktop Companion

Browse the desktop app’s local projects, chats, history, and recorded progress from a paired phone browser. Send text/photos, steer active work, answer questions, and choose a model through the running Mac. No Slack or Telegram integration is required.

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

Doctor reports missing dependencies or incompatible history before setup. Setup builds `~/Applications/Desktop Companion.app` for this Mac's Python/source paths and installs its login LaunchAgent. Keep the source folder at this location. Use `python3 manage.py setup --without-app` if you deliberately want only the browser/service. Open **Raycast → Desktop Companion** or <http://127.0.0.1:8765>. A fresh clone starts locally only; setup does not change power, password, or Locked Use settings.

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
- An idle connected chat offers **Send**. A running connected chat offers **Queue** and **Steer** once there is content to send. Choose Steer, then press the send arrow. Queued messages show photo counts/previews with **Steer now**, **Edit**, and **Delete**. Editing updates that entry in place and retains its photos; deletion requires a review. Unsupported attachments still require the desktop.
- A submitted message appears immediately with **Sending to your Mac**, then **Accepted by Mac · Syncing conversation** after verified Send/Steer acknowledgement. It merges into saved history only by the exact desktop client message ID. Queue entries stay in the queue panel; rejected or uncertain requests keep the draft. This feedback is local to the current browser tab and never sends another command.
- **Working on your Mac…** appears at the end only for a recently confirmed active desktop turn. The open conversation refreshes independently (about 1.5 seconds while working or syncing, five seconds when idle), with no overlapping polls and no reads while hidden. Conditional reads recheck live controls but omit unchanged transcript bodies.
- Reopening a chat starts at the latest message and follows late photo/layout changes. Deliberately scrolling into history or loading earlier messages pauses following; **Latest messages ↓** resumes it.
- A confirmed send clears only the submitted text and photos. New text typed during the request remains. Photo acknowledgements compare normalized image bytes; an uncertain result keeps the draft and never automatically resends it.
- **Questions for you → View & reply** shows saved asynchronous questions, choices, and a typed-answer field. Answers go through Steer when active or Send when idle; disconnected answers explicitly queue. The panel closes when its final answer is accepted. Queued replies can be edited or deleted; a confirmed deletion permits answering that question afresh.
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

The per-user LaunchAgent starts at login and supervises the web server, idle-sleep guard, optional tunnel, and desktop-app availability. Use **Restart service** in a component's native recovery controls, or `restart-web`, after Python/web-server changes: it replaces only the web server and its authentication gateway, preserving the tunnel and awake guard. A crashed web server also recovers independently after a short backoff. Static web assets need no process restart. Use `python3 service.py restart` only for explicit full-system recovery or supervisor/configuration changes; it stops and recreates the whole group, including the tunnel.

A guard failure restarts the service group. An exited tunnel retries after 30 seconds while leaving the local server/guard alive. A debounced network watcher accelerates recovery of an absent connector after connectivity changes; it preserves a live controller so interface metadata changes cannot discard established edges. The public watchdog preserves registered edges during failed probes. With no registered edges, it allows five minutes of continuous public failure before requesting recovery; an anonymous HTTP 200 still stops access immediately. TLS verification stays enabled. Network outages still need the underlying connection to recover.

Credential-free lifecycle records in private `.state/tunnel-events.jsonl` distinguish supervisor starts, connector exits, network-change decisions, watchdog recovery, and public health transitions. They rotate at 256 KB with one previous file and accept only fixed event names, numeric process/status fields, and known reason codes. Raw connector logs are retained between attempts instead of being overwritten on every retry. Neither file is included in a source export.

Deployment investigation, 2026-10-20: the public gateway returned the expected anonymous HTTP 401 at 16:17:53 UTC even while connection resets were recorded. Later full-group restarts at 16:57:53, 17:01:17, and 17:03:30 UTC necessarily tore down the tunnel. Fresh HTTP/2 registration now fails, and a certificate probe confirms Zscaler TLS inspection on that path. Reconnection after the full restarts may have exposed the fresh-handshake failure; the exact onset and policy change remain unproven. Do not infer that every reset meant public access was already down. Web-only recovery was subsequently verified to replace the server while preserving supervisor and awake-guard PIDs and restoring local health. The tunnel was already in retry backoff, so that live check does not establish continuity of a healthy public tunnel.

For foreground debugging, stop the installed service first so copies do not compete for ports:

```sh
python3 service.py stop
python3 companion.py
```

Use `python3 service.py install` to reinstall login startup, or `python3 service.py uninstall` to remove it while retaining private state. Rebuild native changes with `python3 build_app.py`, then reopen the control app. Machine settings are in ignored `config.local.json`; source-only exports never include it.

## Unattended operation and lock state

The service runs `caffeinate -is` to prevent idle system sleep on battery and charger power. It does not request display-awake or simulated-user-activity assertions. The screen can turn off and lock while background work continues.

**Unattended mode → Enable unattended mode…** requires a native macOS administrator prompt to apply `pmset -a disablesleep 1 displaysleep 1`: system sleep is disabled and displays turn off after one minute. The app verifies the kernel flag and timers afterward. **Restore lid sleep…** applies `pmset -a disablesleep 0`; the display timeout remains. **Lock now & keep working** is enabled only when immediate password-on-display-off can be verified. The app never reads or stores the administrator password.

The sleep setting persists beyond stopping/uninstalling Companion and also blocks manual sleep. It does not change FileVault, automatic login, password requirements, or native authorization policy. A login LaunchAgent cannot run before login/FileVault unlock. Depleted power, shutdown, or an internet outage ends connectivity. Staying awake and native desktop interaction while locked are separate capabilities.

The OpenAI Locked Use component can be current and enabled while an automatic unlock is refused. OpenAI scopes unlock to active trusted Computer Use turns and pauses it after local keyboard/pointer input until a manual unlock. The recovery procedure is to unlock manually once, confirm Locked use is enabled, lock again, and start a fresh native-app test from the phone. Companion cannot override that authorization or promise that it will never require manual recovery. A 2026-10-21 check reproduced the refusal, then successfully read native Chrome after a manual unlock and a new user-confirmed lock. This is a bounded test, not continuous certification. See the [official Locked Use guide](https://learn.chatgpt.com/docs/computer-use#locked-use). Mac status shows the component and last check. Physical lid-close/mobile-data continuity and native desktop access must be checked separately on each Mac. Setup on a second Mac does not apply power or security settings automatically.

## Architecture and safety boundaries

- `companion.py`: HTTP routes and a version-specific read-only history adapter.
- `desktop_runtime.py`: owner-routed IPC for bounded live snapshots, Send/Steer/model changes, and queue promotion. It validates the socket owner/mode, method versions, snapshot source/identity, and turn acknowledgements. It never claims ownership, resumes chats through an independent daemon, or forwards arbitrary caller-selected methods.
- `runtime.py`, `new_chat.py`: read-only account queries and bounded queue, new-empty-chat, and naming APIs. A separate app-server never runs an existing chat. The existing native desktop owner starts the first question after the new chat and its destination are verified.
- `access.py`, `inbox.py`, `photos.py`, `promotions.py`: Companion-owned private stores under `.state/`.
- `web/`: the UI, with separate clipboard, queue controls, new-chat, appearance, status, refresh, update, conversation feedback/scrolling, and Markdown modules. `theme.css` owns light/dark design tokens and common typography; `style.css` retains layout and gesture sizing.
- `system_status.py`: allowlisted tunnel health and freshness; `control.py` assembles component checks shared by the native app and authenticated web viewer.
- `configuration.py`, `manage.py`, `service.py`, `control.py`, `build_app.py`, `native/`: per-Mac configuration, setup/export, supervision, and Raycast control app.

The native control app uses shared adaptive colors in `native/Appearance.swift` and follows the Mac appearance by default. **Appearance** in its sidebar also offers Light and Dark. On macOS 26+ with a supporting SDK, selected navigation and secondary controls use system Liquid Glass; older systems and Reduce Transparency use conventional controls. Primary actions and content cards retain opaque surfaces for predictable contrast. This does not change the desktop ChatGPT app's appearance or system settings.

The loopback phone gateway requires a configured HTTPS origin, session authentication, exact-origin checks, and CSRF for changes. The tunnel targets that gateway exclusively. Cloudflare terminates public TLS and carries pairing traffic, displayed chat text, photos, and commands; application pairing controls access. Conversation bodies are not end-to-end encrypted from Cloudflare.

An optional [independent outage page](cloudflare_fallback/README.md) can explain a tunnel failure even when the Mac is unreachable. It replaces only failed HTML homepage requests and preserves HTTP 503. API traffic keeps its original route and is never replayed. The source bundle includes its dependency-free builder and tests. The certificate diagnostic can identify TLS inspection on a failing connection; that observation alone does not prove the inspection caused an outage or explain its onset. Keep certificate validation and network security software enabled, and ask IT to verify the approved Cloudflare Tunnel policy on port 7844 when fresh connections fail.

Photo uploads are normalized to bounded JPEGs in the browser and decoded/re-encoded by macOS `sips`. Files live outside the web root with private permissions, session-owned draft access, authenticated reads, three-photo/4 MB message bounds, and a 128 MB store cap. Clipboard HTML/remote image URLs are never fetched as attachments. Ordinary drafts stay in browser memory and clear on full reload/sign-out.

New-chat photos have a separate browser-owned draft scope. They bind only to the verified created chat. Personal chats use `~/Library/Application Support/Desktop Companion/Chats`, outside the Scripts project. Legacy Personal workspaces are recognized without rewriting desktop databases or moving their files. Creation and first delivery have separate durable checkpoints: a connection retry reuses the created chat, and an uncertain first delivery is never automatically repeated. Reload recovery stores only an opaque request ID in session storage, not the question or photo contents.

Commands are durably journaled. Accepted or uncertain submissions are never automatically replayed or silently changed from Steer to Queue. Queue promotion verifies the exact original message, claims it through queue APIs, verifies removal, and steers its original identity. A preclaim failure can be explicitly retried; uncertain delivery leaves a reviewable recovery item. Question answers use native question IDs and the desktop’s answer envelope.

`--read-only` disables mutations, and an alternate `--codex-home` cannot mutate another active home. Cloud-only/other-host chats, secure macOS dialogs and unsupported approval forms, Stop, worktree controls, and non-photo files still require the desktop app. App updates may change local schemas/protocols; unsupported integration states fail visibly.

## Validation

```sh
python3 -m unittest discover -p 'test_*.py'
node --test test_*.cjs
python3 manage.py bundle --output Desktop-Companion-source.zip
```

Tests use isolated databases and fake desktop protocols. They cover authentication/CSRF, origin/TLS enforcement, read-only history, receipts and races, deduplication, exact-turn delivery, stale status overrides, photo normalization, queue promotion/retry, setup/export isolation, two refresh zones, clipboard behavior, draft-safe version updates, project verification, first-message retries, naming, theme persistence, status colors, and component freshness.

The combined source passed **238 Python tests and 63 JavaScript tests** on 2026-10-21. Coverage includes immediate delivery receipts, exact client-identity reconciliation, stale working-indicator removal, delayed-photo scroll following, Home Screen gesture ownership, swipe keyboard dismissal with selection/zoom guards and no same-gesture refresh, draft-protected manual reload, textarea sizing and caret preservation, optional haptic degradation, non-overlapping reads, conditional transcript responses, confirmed retry recovery after a turn changes, scheduler-message filtering across historical pages/previews, normalized photo acknowledgements, revision-guarded queue edit/delete, retained attachments, composer clearing, final-answer panel dismissal, web-only restart isolation/backoff, origin preservation, live-connector preservation, lifecycle-log privacy/rotation, and the independent outage page. These results verify the covered local behavior; they do not establish public tunnel recovery.

Live UI verification on 2026-10-21: a disposable photo message queued successfully and cleared the composer, then was edited in place with its photo retained and deleted before delivery. The queue and delivered history both contained zero copies afterward. The native app rebuilt/installed and both appearances were checked, restoring Match Mac. Neutral home cards, blue actions, queue previews, and header typography were checked in mobile layouts. Web-only deployment preserved the supervisor, awake guard, and tunnel controller; four edge connections and the protected public HTTP 401 remained after the hostname-preservation repair. A fresh startup record must match the configured hostname and a live controller to retain that approved origin; cold or stale starts still require normal activation.

The subsequent post-fix boot passed an observation through ten minutes after supervisor startup: 27 public probes returned the expected anonymous HTTP 401, both local health endpoints returned 200, four edge connections remained registered, and the supervisor/server/guard/tunnel-controller PIDs stayed unchanged. No connector exits or forced restarts were recorded. A paired browser also authenticated after boot. The certificate-only diagnostic still identified Zscaler during this working connection, so that result alone cannot explain the earlier outage. This establishes a bounded live stability result, not a guarantee about future network handoffs. The private observation is in `.state/postboot-observation.json` and is excluded from exports.

Live verification on 2026-10-20: a pasted image produced a removable preview; mobile-layout Steer marker `dc-live-state-20261020-1` appeared exactly once in the desktop conversation and zero times in its queue. The native control app compiled successfully. Phone layouts were checked at 320px and 390px with a 53px inline header and no document overflow. Physical phone haptics/keyboard behavior are not emulated by these desktop tools.

The prompt-first flow was also verified in real Personal and Scripts chats: each had exactly one user question, completed its requested response, and had no queued message. The Personal test included one pasted photo and was renamed through the UI with native readback. Test chats were archived afterward. Both themes were visually checked, including a 440px reduced-height composer. Battery and weekly header text both compute to 12px on phones; refresh/menu targets are 44px. Core text and project-label contrast was checked against 4.5:1 in both palettes. Public tunnel recovery is a separate check from these local UI and message-delivery results.

For the 116-chat catalog, local full reads measured a median **103.5 ms** and about **112 KB** of JSON; unchanged conditional reads measured **1.3 ms** and no response body. Conditional ETags cover history plus Companion read receipts, skip unchanged history scans, and do not cache live execution checks. These are local measurements, not mobile-network latency guarantees.
