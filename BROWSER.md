# Desktop browser on your phone

Open **… → This browser → Browser** in Companion. It opens separately so your chat composer and its draft remain intact. The same authenticated phone pairing protects page lists, screenshots and inputs. Tap the image to click, swipe to scroll, use the navigation bar, or tap a field in the image and insert text below it. Typing is hidden by default and is not saved. Controls affect the actual desktop page and its current sign-in.

## One-time setup on each Mac

Browser sharing starts disabled. The existing chat IPC interface does not expose browser screenshots/input; this adapter uses the desktop app's local Chromium DevTools connection. The desktop must be launched with that connection enabled, so a relaunch is required. Do not interrupt active desktop work to apply this automatically.

Enabling a debugging listener gives local programs powerful access to the signed-in desktop browser. Review and approve this separately from installing the source. Keep it on loopback, never on a LAN/public address, and never route its port through the phone tunnel. Companion verifies that the listener belongs to the configured desktop app and this Mac user before using it. It will reject wildcard listeners, other programs and malformed debugger addresses.

Once work is safe to stop, quit the desktop app normally and launch your configured app with a dedicated local port. For the default installation:

```sh
open -a /Applications/ChatGPT.app --args --remote-debugging-address=127.0.0.1 --remote-debugging-port=9223
python3 browser_remote.py enable --port 9223 --browser-control-approved
python3 browser_remote.py status
```

Use your configured app's path if it differs. This does not alter its installation or launch settings permanently; a later normal launch without these arguments disables the connection. Browser sharing then reports unavailable until the connection is enabled again. If the app or security policy refuses debugging, leave sharing disabled; do not change security software, app signing, certificates or permissions to bypass it. This project does not modify the app or reuse agent/session tokens.

To turn sharing off immediately, run `python3 browser_remote.py disable`. Close/relaunch the desktop app normally to close the underlying debugger listener as well. No background service, native app rebuild or tunnel restart is required for changing the per-Mac browser settings. The first source update containing this feature needs `python3 service.py restart-web`.

## Boundaries

- Only existing regular HTTP/HTTPS page targets are listed, including localhost previews. Native app UI, browser internals, file URLs, embedded MCP apps and Companion's own administration pages are excluded. A local HTML preview needs to be opened through a local web server to appear.
- Phone input is a fixed allowlist: click, scroll, insert text, selected page keys, HTTP/HTTPS navigation, history and reload. No arbitrary JavaScript, cookies, filesystem, downloads, permissions, browser configuration or debugger commands are exposed to the phone.
- Each action needs a recent frame bound to the paired session, page, document identity, viewport and scroll position. Changed/stale views are rejected. A frame can be acted on once. An accepted or uncertain input is never automatically replayed; check the refreshed page before another action.
- Screenshot reads are serialized and bounded, carry no-store headers, pause while hidden/offline, and reconnect with capped exponential backoff and server cooldown guidance. An old image remains visible but cannot be controlled. Typed text and screenshots are not persisted in history, journals or logs.
- The desktop and phone share a live page. This is screenshot control, not a separately reflowed mobile copy. Native OS dialogs, file pickers, protected video, browser/chrome controls and OS authentication prompts may require the Mac. Use the phone's Zoom control to inspect small text. In Zoom, drag to pan the image and use the scroll buttons to move the actual page.
- Closed-lid/locked-screen rendering and the app's compatibility must be tested on the destination Mac after setup. Simulated protocol tests do not certify those behaviors. Existing CUA/agent browser actions can run concurrently, so always check the fresh view before clicking.

The adapter uses Electron's [documented debugging switch](https://www.electronjs.org/docs/latest/api/command-line-switches) and the Chromium DevTools [Page](https://chromedevtools.github.io/devtools-protocol/tot/Page/) and [Input](https://chromedevtools.github.io/devtools-protocol/tot/Input/) APIs. The public phone gateway continues to expose only Companion's paired, CSRF-protected API; the raw debugger never belongs in Cloudflare ingress.
