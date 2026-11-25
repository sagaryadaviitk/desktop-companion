# Install Desktop Companion on a fresh Mac

Give Codex the release ZIP, its SHA-256 checksum from the sender, the new phone hostname and the intended desktop app. Ask it to install this release on **this Mac** using this guide. The operator completes the desktop sign-in, Cloudflare login, phone pairing and any macOS permission prompts in their own UI.

1. **Verify and unpack.** Compare `shasum -a 256 /path/to/package.zip` with the supplied checksum. Extract into an unused permanent folder such as `~/Projects/desktop-companion`. The service root is the extracted directory containing `manage.py`. Keep it at that path. The supplied release archive includes `RELEASE.json` with the source commit and `SHA256SUMS` inside that directory. From that directory run `shasum -a 256 -c SHA256SUMS`. Stop on a mismatch.
2. **Check the Mac.** Read SETUP.md and COMPATIBILITY.md. Check for an existing Companion LaunchAgent, Desktop Companion.app, and listeners on 8765 and 8766. An occupied destination requires the existing-install upgrade procedure in SETUP.md. For managed Macs, follow the organization's background-service and endpoint-security policy.
3. **Prepare locally.** Use Python 3.11+, Apple command-line tools for the native build, Node 24 for tests and cloudflared for remote access. Open the chosen ChatGPT or Codex app, sign in and create a disposable local chat. Set `COMPANION_PYTHON` to the absolute supported interpreter. Set `COMPANION_APP` to the chosen .app path. From the permanent source directory run:

   ```sh
   "$COMPANION_PYTHON" manage.py configure --desktop-app "$COMPANION_APP"
   "$COMPANION_PYTHON" manage.py doctor
   "$COMPANION_PYTHON" -m unittest discover -p 'test_*.py' -q
   node --test test_*.cjs
   "$COMPANION_PYTHON" manage.py setup
   ```

4. **Configure this Mac's tunnel.** Set `COMPANION_HOSTNAME` to the new hostname the operator controls. Confirm it is free in the intended Cloudflare zone. Set `COMPANION_TUNNEL_NAME` to a unique new tunnel name. Use this Mac's Cloudflare session. Capture the UUID reported by the create command as `COMPANION_TUNNEL_ID`. If resuming, verify the existing mapping before continuing. Keep credentials and pairing local.

   ```sh
   cloudflared tunnel login
   cloudflared tunnel create "$COMPANION_TUNNEL_NAME"
   # Set COMPANION_TUNNEL_ID to the UUID returned above before continuing.
   cloudflared tunnel route dns "$COMPANION_TUNNEL_ID" "$COMPANION_HOSTNAME"
   "$COMPANION_PYTHON" manage.py configure --hostname "$COMPANION_HOSTNAME" --tunnel-id "$COMPANION_TUNNEL_ID"
   "$COMPANION_PYTHON" manage.py doctor
   ```

5. **Start and verify.** Install the login service once, from the permanent folder, after the operator has authorized installation. Companion starts its own cloudflared process. Verify the tunnel routes the new hostname to 127.0.0.1:8766. Keep the local dashboard on loopback.

   ```sh
   "$COMPANION_PYTHON" service.py install --background-install-approved
   "$COMPANION_PYTHON" service.py status
   curl --fail --max-time 10 http://127.0.0.1:8765/healthz
   curl --fail --max-time 10 http://127.0.0.1:8766/healthz
   curl --max-time 15 -sS -o /dev/null -w '%{http_code}\n' "https://$COMPANION_HOSTNAME/api/chats"
   open "$HOME/Applications/Desktop Companion.app"
   ```

   Both health responses must show ok and the same server PID. The gateway must report authentication_required true. Require a TLS-verified public 401 before pairing. If chat data is accessible anonymously, stop the newly installed service and correct the route. For intermittent failures check DNS, the Mac's network and outbound TCP 7844. The optional outage Worker has separate per-host instructions in cloudflare_fallback/README.md.
6. **Accept on a phone.** Pair a browser using Phone access on the new Mac, then connect to the disposable desktop chat. Generate two async questions. Answer one on the phone, check that its answer appears once in the intended chat, refresh, and verify only the unanswered sibling remains in both the prompt and the chat-list badge. Answer the second, reopen the browser, and verify both prompts stay cleared. A queued reply must show its queue state until consumed or successfully steered. An ambiguous acknowledgement requires checking the desktop before further action.
7. **Accept recovery.** Follow COMPATIBILITY.md for photo/queue delivery, browser revocation, Wi-Fi reconnect, post-login recovery and physical lock/lid checks. If the operator wants unattended closed-lid access, use the native Unattended mode and their local administrator authentication. Verify the below-20% transition and charger recovery on the destination Mac. Record the commit, app and CLI versions, hostname and pass/fail/untested checks without credentials or private chat content.
8. **If acceptance fails**, record the exact stage and preserve local state. For a fresh installation, `"$COMPANION_PYTHON" service.py stop` unloads the current job. Use `service.py uninstall` to remove the newly installed login job when retiring the installation. If a power helper was enabled, turn off its setting and apply through the native app first. Remove only this new hostname's DNS/tunnel resources when separately authorized. Retain the release and per-Mac data for diagnosis.
