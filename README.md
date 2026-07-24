# Simple OAuth2 flow

Currently, this pipeline supports only the 45 applications that use OAuth 2.0 authentication. The remaining applications require manual intervention to generate and provide API keys or access tokens, while some are gated and require additional approval to access.

## The whole idea, in 4 steps

```
1. User clicks "Connect Salesforce"
        │
        ▼
2. Your app redirects them to Salesforce's login/consent screen
        │
        ▼
3. Salesforce redirects back to your app with a temporary code
        │
        ▼
4. Your app swaps that code for an access_token (one server-to-server call)
        │
        ▼
   Save the token
        │
        ▼
5. USE the token — call the app's real API with it (this is the actual point)
```

Steps 1–4 are just "auth" — they get you a token sitting in a file, nothing
more. Step 5 is what makes the connection useful: take the stored token, put
it in an `Authorization: Bearer <token>` header, and call the app's real
API. Every feature you build later (pull Salesforce contacts, post a Slack
message, read a Notion page) is this exact same pattern with a different
endpoint.

Every OAuth2 app does this exact same 5-step dance — only the URLs and
scopes change. So it's one set of functions + one config file, not 45
separate integrations.

## Files (3 total)

| File | What it is |
|---|---|
| `oauth_app.py` | The whole flow — 2 routes (`/connect/<app>`, `/callback/<app>`), ~90 lines |
| `oauth_provider_configs.json` | Per-app settings: authorize_url, token_url, scopes. 5 filled in as examples (Salesforce, HubSpot, Slack, GitHub, Notion) |
| `oauth_apps.json` | Your 45 OAuth2 apps, straight from your research pipeline — just here for reference |
| `tokens.json` | Created automatically — where connected users' tokens land |

## Run it

```bash
pip install flask requests

# one-time setup per app: register your app with the provider to get these
export SALESFORCE_CLIENT_ID=...
export SALESFORCE_CLIENT_SECRET=...

python oauth_app.py
```

Then visit:
```
http://localhost:5000/connect/Salesforce?user_id=alice
```
→ redirects to Salesforce's real login/consent page. Approve it → Salesforce
sends the user back to `/callback/Salesforce` → token gets saved to
`tokens.json` → done.

Check what's connected:
```
http://localhost:5000/connections/alice
```

Actually use the connection — call the app's real API with the stored token:
```
http://localhost:5000/call/GitHub?user_id=alice
```
This is the part that matters: it takes the saved token and makes one real
authenticated call (a "who am I" endpoint each provider exposes for exactly
this). Every real feature you build later — pull contacts, post a message,
read a doc — is this same call shape, just against a different endpoint.

## Adding app #6 through #45

Same 2 steps every time, no new code:
1. Register your app with that provider (one-time, gets you a client_id/secret)
2. Add one entry to `oauth_provider_configs.json` with its authorize_url,
   token_url, scopes, and a `test_endpoint` (a cheap read-only "who am I"
   endpoint to prove the token works) — all in that app's own dev docs,
   which your pipeline's `docs_url` field already points to

## What's deliberately left out of this simple version

- No database (just a JSON file) — swap in real storage once this works
- No token refresh — access tokens expire; add that back once the basic
  flow is solid
- No encryption on stored tokens — fine for local testing, not for
  production
- The other 55 apps (API key / manual-gated) — separate, different problem,
  intentionally not mixed in here so this stays easy to read
