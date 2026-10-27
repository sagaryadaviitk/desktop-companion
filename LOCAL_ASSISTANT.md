# Independent local assistant

Open **Local chats** from Companion’s sidebar or home screen, or visit `/local` at this Mac’s usual Companion address. The Mac control app also has a **Local chats** destination showing the same history as the paired phone. Chats are backed by Exo and Companion-owned storage. They do not appear in Exo’s separate browser chat list or in Codex’s native sidebar. Saved skills and original cloud chat history remain read-only.

To branch a desktop conversation, open its **Chat options → Fork & continue locally**. Review the completed visible text before creating the local branch. The copy is limited to 40 messages and 12,000 characters; unfinished turns, progress, photos, tools and reasoning are omitted. The preview explains when text was left out. Creation does not start inference. Send a new local instruction to continue. The source chat remains unchanged, and the local branch retains an origin link. If the source changes after preview, review it again before creating.

A Codex project name does not enforce a local model provider. The installed desktop’s ordinary new-chat action inherits its normal provider; provider settings in project-local `.codex/config.toml` are ignored. Do not change global cloud defaults or assume that moving a chat into a project switches its execution to Exo.

The first version supports text chat, up to three selected local skills, reading those skills' text references, read-only files in a selected workspace, and authenticated API calls. It does not run shell commands, edit project files, operate browsers, or inherit hosted tools/connectors. A skill that needs one of those capabilities must report the missing dependency. Model output still needs human judgment; skill availability does not establish the model can complete the entire workflow.

## Set up each Mac independently

1. Follow [SETUP prerequisites and interpreter selection](SETUP.md#prerequisites) in this terminal, then update Companion from the reviewed release using CONTRIBUTING.md. Preserve that Mac's own tunnel, hostname, sessions, sign-in and configuration. Do not copy `.state/` from another Mac.
2. Install Exo from its [official repository/release link](https://github.com/exo-explore/exo). Review its current platform requirements on the destination Mac. Put the app in a permanent Applications folder rather than running it from the mounted installer volume. Exo is not installed or configured by pulling Companion source.
3. Open Exo and download/load `mlx-community/Qwen3.5-2B-MLX-8bit`. Keep one model loaded initially. Verify its ready state; the supported-model catalog is not a downloaded-model inventory. The tested Qwen model emitted real function calls; Llama-3.2-3B printed invocation text without executing, so it is not the default tool model.
4. From the permanent Companion checkout, enable local mode and explicitly choose skill roots and read-only workspace folders:

```sh
"$COMPANION_PYTHON" local_settings.py --enable \
  --skill-root "$HOME/.codex/skills" \
  --skill-root "$HOME/.agents/skills" \
  --workspace "debug=$HOME/work/debug-project"
"$COMPANION_PYTHON" service.py restart-web
```

Use only paths that exist on that Mac. A skill root can be a single skill folder or a folder containing skill subfolders; symlinks outside a configured root are ignored. Plugin cache directories are not scanned automatically. Add a specific plugin skill folder only when its dependencies are available and its workflow is appropriate for this local model. Original skill files are not copied or rewritten. Each turn loads only selected skills and records their content hashes in the model's local instructions.

The configuration command reports `.state/local-settings.json`, which must have mode 600. Modify it on the owning Mac to configure API profiles. `local-settings.example.json` is a credential-free reference; do not copy its placeholder API profile into a working configuration. Keep the existing endpoint and model values. Run `"$COMPANION_PYTHON" local_settings.py` to inspect configuration metadata without printing credential values, then restart only the web service after changes. The default Exo endpoint is loopback `http://127.0.0.1:52415/v1`; external model endpoints are rejected. There is no automatic cloud-model fallback.

`models` lists selectable model IDs. `tool_models` lists only models verified with structured tool calling; the tested Qwen is included by default. Other models are chat-only until tested and explicitly added there. Each chat retains its chosen model, skill IDs and workspace; changing defaults does not switch an existing chat.

Exo must be open and the Mac must be awake/reachable. Configure Exo's supported startup behavior locally if needed; Companion does not install a second model supervisor. The phone continues using the existing paired HTTPS gateway. Its traffic still traverses the configured Cloudflare tunnel; local inference does not change that transport into end-to-end encryption from Cloudflare.

## Office API authentication

API calls execute from the Mac, using that Mac's network/VPN, DNS and valid credentials. The phone does not need direct VPN access. Configure a named profile with a fixed base URL and an explicit method list. Paths requested by the model are relative to that base prefix, such as `diagnostics/health`, without a leading slash or full URL. Query parameters and JSON/text bodies are separate tool arguments.

```json
{
  "api_profiles": {
    "staging": {
      "name": "Staging diagnostics",
      "base_url": "https://staging.example.com/api/v1/",
      "methods": ["GET", "HEAD"],
      "headers": {"Accept": "application/json"},
      "secret_headers": {
        "Authorization": {"file": "/absolute/private/path/token.txt", "prefix": "Bearer "},
        "X-API-Key": {"env": "OFFICE_STAGING_API_KEY"}
      }
    }
  }
}
```

Secret headers support any valid header name, so a profile can use a bearer token, basic-auth header, API key, cookie, or a combination. Configure either `file` or `env` for each header, with optional `prefix`. A cookie file contains the Cookie header's value; a basic-auth file contains its encoded credential value and uses prefix `Basic `. Token files must be small regular files, mode 600, and outside readable workspaces/skill roots. Do not put live credentials into tracked examples, prompts, UI fields, fixed `headers`, or source files. The model chooses profile IDs, never credential sources or header values.

A login background service normally does not inherit variables exported in an interactive terminal. A private token file is the straightforward option for that service; an existing approved login flow can update that file. This release does not automate SSO login, refresh browser cookies, bypass MFA, or renew expired credentials. Expiration appears as the API's actual authentication failure. Keep normal company sign-in and security controls in place.

TLS verification is always enabled. An optional profile `ca_file` can specify an absolute path to a trusted CA bundle when the normal Python trust store cannot verify an approved internal certificate. HTTP is permitted only for loopback testing. The API client connects directly, does not implement a configurable HTTP proxy, and never follows redirects; APIs requiring a proxy or redirect-based login need an approved network/authentication setup first. Redirect responses are returned for inspection without forwarding credentials.

Start with `GET`/`HEAD`. Enabling `POST`, `PUT`, `PATCH`, or `DELETE` in a profile grants local chats access to those methods; enable them only for the intended environment. There is no per-request approval dialog in this first version. Request/response sizes and timeouts are bounded. Credentials are injected at execution time and redacted from model-visible responses and local history. API responses can contain other sensitive business data, so expose only appropriate endpoints.

API intent is durably recorded before network dispatch. No API request is automatically retried. An uncertain outcome stops the turn and remains reviewable; a request may have reached the service before its connection failed. Repeating the exact same tool action within one turn reuses its result. Starting a new user request is a new action and may execute again. This is duplicate protection inside Companion, not an exactly-once guarantee from an external API.

## Isolation, limits and recovery

- Local chats have `local-` IDs, separate API routes and `.state/local-assistant.sqlite`; the native desktop's chat databases and global model/provider configuration are untouched.
- Local command receipts and API-call receipts are independent of cloud command journals. A local model generation does not block cloud sends.
- Only one local turn runs at a time, with at most six model/tool steps, bounded output/context, and no automatic continuation after restart. A restarted in-flight turn becomes interrupted; it is never replayed.
- Only complete recent turns are retained in the model context. The full stored chat is not an unlimited context window; start a fresh local chat with a narrow brief for long tasks. No cloud summary is imported automatically.
- Original skill directories and configured workspaces are read-only. File reads are bounded and refuse path/symlink escapes and common credential files. This is a read/debug assistant, not a general shell agent.
- The mobile page preserves drafts while polling, stores only pending request IDs for reload recovery, and never resends an uncertain mutation. Inspect the result before dismissing its recovery notice.
- `/api/local/*` uses existing pairing, origin, CSRF and version checks. `--read-only` and an alternate Codex home disable local mutations as well.
- To disable local execution, run `"$COMPANION_PYTHON" local_settings.py --disable`, then `"$COMPANION_PYTHON" service.py restart-web`. Stored local history remains on that Mac. Source exports exclude settings, credentials, history and API receipts.

## Verify on the destination Mac

After setup, open `/local`, confirm Qwen is ready, and send a plain text question. Choose one office skill and the intended read-only workspace. Ask it to inspect a small known file, then call a harmless API endpoint in the configured profile. Verify the server's request ID/status against its real response. Test expired credentials and an unavailable endpoint too; the assistant must show the actual failure instead of claiming success. Phone delivery, office VPN/SSO and the other Mac's installed models must be verified there; tests from the first Mac do not certify them.

Run `"$COMPANION_PYTHON" local_smoke.py` for a repeatable local integration check. It creates a disposable synthetic skill/file, a loopback diagnostic API with a fake private key, and a fresh local conversation. The model must actually read the file, call the authenticated API once, and report both unpredictable identifiers. It performs no office API call and changes no existing conversation, settings or saved skill. The result reports latency and verification booleans without printing the fake credential.

Repository checks:

```sh
"$COMPANION_PYTHON" -m unittest discover -p 'test_*.py' -q
node --test test_*.cjs
```

The first integration proof used a temporary synthetic diagnostic API on loopback, with a nonce returned by that server. It did not call an office API or carry office credentials.
