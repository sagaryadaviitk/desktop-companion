# Independent outage page

This optional Cloudflare Worker serves a small, static recovery page when the Mac cannot be reached. It does not run chat work, authenticate users, or prove that a command was delivered. The Mac remains the source of truth.

## Build and deploy

Run `python3 cloudflare_fallback/build.py` from the companion directory. Upload the generated `.state/exports/companion-fallback.mjs` as an ES module Worker. No packages, secrets, storage bindings, or account credentials are part of the generated source. Disable this Worker's request logging and caching before attaching it to the hostname; URLs may contain private chat identifiers. Its `workers.dev` address is a static preview and never relays dashboard requests.

In the hostname's Cloudflare **Workers Routes**, create these routes in this order, replacing `agents.example.com` with your own hostname:

1. `agents.example.com/api/*` → **None** (disable Workers for this route).
2. `agents.example.com/*` → your fallback Worker.

Cloudflare matches the full URL, including its query string. An exact `agents.example.com/` route misses links such as `/?chat=...`; the wildcard is intentional. The more-specific API exclusion keeps photo uploads, history, authentication, health checks, and command submission outside the Worker. Preserve existing routes on other hostnames. On quota exhaustion, passing through to the existing protected origin is safe for this fallback; it is not an authentication layer.

The Worker substitutes only HTML GET/HEAD requests whose path is `/`, and only after a network error or a known gateway/tunnel failure status. Healthy pages, redirects, and authentication failures pass through. Other methods and paths are forwarded once, unchanged. It never reads request bodies, retries commands, caches private responses, stores tokens, or emits application logs. The fallback returns HTTP 503 with `no-store`, a restrictive CSP, and a normal Try again link that preserves the current URL. It contains no scripts or external assets.

## Verify

- During an outage, both `/` and `/?chat=example` with `Accept: text/html` must return the recovery page and HTTP 503.
- `/api/chats` must retain the origin/Cloudflare response, never the recovery HTML. An anonymous request must never expose conversation data.
- After recovery, the actual pairing screen or authenticated app must pass through unchanged.
- Run `node --test test_cloudflare_fallback.cjs` for isolated forwarding, no-replay, healthy-response, and preview tests.

The page is static: it cannot know battery state, whether work finished, or when connectivity will return. Automatic recovery runs on the Mac. A truly sleeping, shut-down, or discharged Mac cannot run the supervisor. Saved Wi-Fi Auto-Join is managed by macOS; the supervisor reconnects the tunnel after a usable interface returns.

## Managed-network diagnosis

After an initial HTTP/2 reset, the Mac performs one bounded certificate-only check of the documented tunnel endpoint. A Zscaler issuer is reported as TLS inspection, without exposing certificates, IP addresses, or raw logs in the status API. This does not change certificate verification, trust stores, proxy settings, or endpoint protection. It sends no chat content or credentials. An IT-approved exception for Cloudflare Tunnel may be required: outbound TCP 7844 for HTTP/2 (UDP 7844 for QUIC), with the tunnel hostnames and SNI names listed in Cloudflare's network requirements. More retries cannot repair a policy-blocked connection. Do not disable Zscaler or TLS validation as a workaround.

## Rollback and another Mac

To remove this optional page, detach the fallback wildcard route; the existing protected tunnel then handles requests directly. The API exclusion can remain inert or be removed afterward. Keep the source so it can be redeployed. Another Mac needs its own supported companion configuration and tunnel credentials; do not copy `.state`, pairing records, browser sessions, or account certificates. Deploy the same Worker source with routes for the new hostname. Cloudflare plan quotas and feature availability must be checked in that account before rollout.

References: [Cloudflare 1033](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1033/), [Worker route matching](https://developers.cloudflare.com/workers/configuration/routing/routes/), and [tunnel network requirements](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/tunnel-with-firewall/).
