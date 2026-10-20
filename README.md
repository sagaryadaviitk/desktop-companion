# Desktop Companion

Browse the desktop app’s local projects, chats, history, and recorded progress from a paired phone browser. Send text/photos, steer active work, answer questions, and choose a model through the running Mac. No Slack or Telegram integration is required.

For a second Mac, start with [SETUP.md](SETUP.md). Each Mac has independent settings, history, pairing, and tunnel access. A clean source copy starts locally only.

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
- A confirmed send clears only the submitted text and photos. New text typed during the request remains. Photo acknowledgements compare normalized image bytes; an uncertain result keeps the draft and never automatically resends it.
- **Questions for you → View & reply** shows saved asynchronous questions, choices, and a typed-answer field. Answers go through Steer when active or Send when idle; disconnected answers explicitly queue. The panel closes when its final answer is accepted. Queued replies can be edited or deleted; a confirmed deletion permits answering that question afresh.
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

Pull inside the chat list or reader for a **soft refresh** that keeps drafts and selected photos. Pull from the top/header for Chrome’s native full-page refresh. The header icon also soft refreshes. Native haptics belong to the browser/device; optional soft-refresh vibration is used only where supported. Gesture ownership follows [Chrome’s overscroll guidance](https://developer.chrome.com/blog/overscroll-behavior).

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

The installed OpenAI Locked Use component was updated to match its bundled version. Earlier checks received ALLOW, but a later strict locked-console test received DENY; locked desktop interaction is not certified. Mac status shows the component and last check. Physical lid-close/mobile-data continuity and native desktop access must be checked separately on each Mac. Setup on a second Mac does not apply power or security settings automatically.

## Architecture and safety boundaries

- `companion.py`: HTTP routes and a version-specific read-only history adapter.
- `desktop_runtime.py`: owner-routed IPC for bounded live snapshots, Send/Steer/model changes, and queue promotion. It validates the socket owner/mode, method versions, snapshot source/identity, and turn acknowledgements. It never claims ownership, resumes chats through an independent daemon, or forwards arbitrary caller-selected methods.
- `runtime.py`, `new_chat.py`: read-only account queries and bounded queue, new-empty-chat, and naming APIs. A separate app-server never runs an existing chat. The existing native desktop owner starts the first question after the new chat and its destination are verified.
- `access.py`, `inbox.py`, `photos.py`, `promotions.py`: Companion-owned private stores under `.state/`.
- `web/`: the UI, with separate clipboard, queue controls, new-chat, appearance, status, refresh, update, and Markdown modules. `theme.css` owns light/dark design tokens and common typography; `style.css` retains layout and gesture sizing.
- `system_status.py`: allowlisted tunnel health and freshness; `control.py` assembles component checks shared by the native app and authenticated web viewer.
- `configuration.py`, `manage.py`, `service.py`, `control.py`, `build_app.py`, `native/`: per-Mac configuration, setup/export, supervision, and Raycast control app.

The native control app uses shared adaptive colors in `native/Appearance.swift` and follows the Mac appearance by default. **Appearance** in its sidebar also offers Light and Dark. On macOS 26+ with a supporting SDK, selected navigation and secondary controls use system Liquid Glass; older systems and Reduce Transparency use conventional controls. Primary actions and content cards retain opaque surfaces for predictable contrast. This does not change the desktop ChatGPT app's appearance or system settings.

The loopback phone gateway requires a configured HTTPS origin, session authentication, exact-origin checks, and CSRF for changes. The tunnel targets that gateway exclusively. Cloudflare terminates public TLS and carries pairing traffic, displayed chat text, photos, and commands; application pairing controls access. Conversation bodies are not end-to-end encrypted from Cloudflare.

An optional [independent outage page](cloudflare_fallback/README.md) can explain a tunnel failure even when the Mac is unreachable. It replaces only failed HTML homepage requests and preserves HTTP 503. API traffic keeps its original route and is never replayed. The source bundle includes its dependency-free builder and tests. The certificate diagnostic can identify TLS inspection on a failing connection; that observation alone does not prove the inspection caused an outage or explain its onset. Keep certificate validation and network security software enabled, and ask IT to verify the approved Cloudflare Tunnel policy on port 7844 when fresh connections fail.

Photo uploads are normalized to bounded JPEGs in the browser and decoded/re-encoded by macOS `sips`. Files live outside the web root with private permissions, session-owned draft access, authenticated reads, three-photo/4 MB message bounds, and a 128 MB store cap. Clipboard HTML/remote image URLs are never fetched as attachments. Ordinary drafts stay in browser memory and clear on full reload/sign-out.

New-chat photos have a separate browser-owned draft scope. They bind only to the verified created chat. Personal chats use `~/Library/Application Support/Desktop Companion/Chats`, outside the Scripts project. Legacy Personal workspaces are recognized without rewriting desktop databases or moving their files. Creation and first delivery have separate durable checkpoints: a connection retry reuses the created chat, and an uncertain first delivery is never automatically repeated. Reload recovery stores only an opaque request ID in session storage, not the question or photo contents.

Commands are durably journaled. Accepted or uncertain submissions are never automatically replayed or silently changed from Steer to Queue. Queue promotion verifies the exact original message, claims it through queue APIs, verifies removal, and steers its original identity. A preclaim failure can be explicitly retried; uncertain delivery leaves a reviewable recovery item. Question answers use native question IDs and the desktop’s answer envelope.

`--read-only` disables mutations, and an alternate `--codex-home` cannot mutate another active home. Cloud-only/other-host chats, runtime approvals, Stop, worktree controls, and non-photo files still require the desktop app. App updates may change local schemas/protocols; unsupported integration states fail visibly.

## Validation

```sh
python3 -m unittest discover -p 'test_*.py'
node --test test_*.cjs
python3 manage.py bundle --output Desktop-Companion-source.zip
```

Tests use isolated databases and fake desktop protocols. They cover authentication/CSRF, origin/TLS enforcement, read-only history, receipts and races, deduplication, exact-turn delivery, stale status overrides, photo normalization, queue promotion/retry, setup/export isolation, two refresh zones, clipboard behavior, draft-safe version updates, project verification, first-message retries, naming, theme persistence, status colors, and component freshness.

The combined source passed **234 Python tests and 41 JavaScript tests** on 2026-10-21. Coverage includes scheduler-message filtering across historical pages/previews, normalized photo acknowledgements, revision-guarded queue edit/delete, retained attachments, composer clearing, final-answer panel dismissal, web-only restart isolation/backoff, origin preservation, live-connector preservation, lifecycle-log privacy/rotation, and the independent outage page. These results verify the covered local behavior; they do not establish public tunnel recovery.

Live UI verification on 2026-10-21: a disposable photo message queued successfully and cleared the composer, then was edited in place with its photo retained and deleted before delivery. The queue and delivered history both contained zero copies afterward. The native app rebuilt/installed and both appearances were checked, restoring Match Mac. Neutral home cards, blue actions, queue previews, and header typography were checked in mobile layouts. Web-only deployment preserved the supervisor, awake guard, and tunnel controller; four edge connections and the protected public HTTP 401 remained after the hostname-preservation repair. A fresh startup record must match the configured hostname and a live controller to retain that approved origin; cold or stale starts still require normal activation.

The subsequent post-fix boot passed an observation through ten minutes after supervisor startup: 27 public probes returned the expected anonymous HTTP 401, both local health endpoints returned 200, four edge connections remained registered, and the supervisor/server/guard/tunnel-controller PIDs stayed unchanged. No connector exits or forced restarts were recorded. A paired browser also authenticated after boot. The certificate-only diagnostic still identified Zscaler during this working connection, so that result alone cannot explain the earlier outage. This establishes a bounded live stability result, not a guarantee about future network handoffs. The private observation is in `.state/postboot-observation.json` and is excluded from exports.

Live verification on 2026-10-20: a pasted image produced a removable preview; mobile-layout Steer marker `dc-live-state-20261020-1` appeared exactly once in the desktop conversation and zero times in its queue. The native control app compiled successfully. Phone layouts were checked at 320px and 390px with a 53px inline header and no document overflow. Physical phone haptics/keyboard behavior are not emulated by these desktop tools.

The prompt-first flow was also verified in real Personal and Scripts chats: each had exactly one user question, completed its requested response, and had no queued message. The Personal test included one pasted photo and was renamed through the UI with native readback. Test chats were archived afterward. Both themes were visually checked, including a 440px reduced-height composer. Battery and weekly header text both compute to 12px on phones; refresh/menu targets are 44px. Core text and project-label contrast was checked against 4.5:1 in both palettes. Public tunnel recovery is a separate check from these local UI and message-delivery results.

For the 116-chat catalog, local full reads measured a median **103.5 ms** and about **112 KB** of JSON; unchanged conditional reads measured **1.3 ms** and no response body. Conditional ETags cover history plus Companion read receipts, skip unchanged history scans, and do not cache live execution checks. These are local measurements, not mobile-network latency guarantees.
