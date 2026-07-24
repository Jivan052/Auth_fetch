"""
oauth_app.py — the whole OAuth2 flow, one file, one thing.

Handles ONLY the 45 apps in your dataset that use OAuth2 (Salesforce,
HubSpot, Slack, GitHub, Notion, Zendesk, ...). The other 55 apps (API key /
manual-gated) are a different, separate problem — don't mix them in here.

The 4 steps, mapped 1:1 to the 4 things this file does:

  1. User clicks "Connect X"        -> GET /connect/<app>
  2. Redirect to provider's consent -> (done inside step 1's response)
  3. Provider redirects back w/code -> GET /callback/<app>
  4. Exchange code for a token      -> (done inside step 3's handler)
  5. ACTUALLY USE the token to call the app's real API -> GET /call/<app>

Steps 1-4 only get you a token sitting in tokens.json - that's auth, not
usage. Step 5 is the part that makes the connection actually do something:
it takes the stored token and makes a real, authenticated API call to the
app. Every downstream feature you build (pull contacts from Salesforce,
post a Slack message, whatever) is just this same pattern - stored token +
Authorization header + your own endpoint instead of the test one below.

Storage: tokens saved to tokens.json, one row per (user, app). That's it —
no database, no encryption library, nothing fancy. Swap this file for a real
database column once this makes sense in production; the shape stays the same.

Setup per app (one-time, not per user):
  1. Register your app with the provider (e.g. Salesforce Connected App,
     Slack App, GitHub OAuth App) to get a client_id + client_secret.
  2. Add its authorize_url / token_url / scopes to oauth_apps.json (this
     file's config).
  3. Put the client_id/secret in environment variables.

Run:
    export VAULT... no wait, no vault here. Just:
    export SALESFORCE_CLIENT_ID=xxx
    export SALESFORCE_CLIENT_SECRET=yyy
    python oauth_app.py
"""
import json
import os
import secrets
import time
from pathlib import Path

import requests
from flask import Flask, jsonify, redirect, request

app = Flask(__name__)

# --- Config: one row per OAuth2 app. Load once at startup. -----------------
APPS = {row["name"]: row for row in json.loads(Path("oauth_apps.json").read_text())}
PROVIDER_URLS = json.loads(Path("oauth_provider_configs.json").read_text())

TOKENS_FILE = Path("tokens.json")
PENDING = {}  # state -> {user_id, app_name}   (in-memory; fine for a demo)


def _tokens() -> dict:
    return json.loads(TOKENS_FILE.read_text()) if TOKENS_FILE.exists() else {}


def _save_token(user_id: str, app_name: str, access_token: str, refresh_token: str | None):
    tokens = _tokens()
    tokens[f"{user_id}:{app_name}"] = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "connected_at": time.time(),
    }
    TOKENS_FILE.write_text(json.dumps(tokens, indent=2))


def _get_token(user_id: str, app_name: str) -> str | None:
    tokens = _tokens()
    row = tokens.get(f"{user_id}:{app_name}")
    return row["access_token"] if row else None


# --- Step 1 & 2: send the user to the provider ------------------------------
@app.route("/connect/<app_name>")
def connect(app_name):
    user_id = request.args.get("user_id")
    if not user_id:
        return jsonify({"error": "user_id required, e.g. ?user_id=alice"}), 400

    provider = PROVIDER_URLS.get(app_name)
    if not provider:
        return jsonify({"error": f"No OAuth config for '{app_name}' yet — add it to oauth_provider_configs.json"}), 404

    client_id = os.environ.get(provider["client_id_env"])
    if not client_id:
        return jsonify({"error": f"Set {provider['client_id_env']} in your environment first"}), 400

    state = secrets.token_urlsafe(16)
    PENDING[state] = {"user_id": user_id, "app_name": app_name}

    redirect_uri = f"{os.environ.get('BASE_URL', 'http://localhost:5000')}/callback/{app_name}"
    scope = " ".join(provider.get("scopes", []))
    url = (
        f"{provider['authorize_url']}"
        f"?client_id={client_id}&redirect_uri={redirect_uri}"
        f"&response_type=code&scope={scope}&state={state}"
    )
    return redirect(url)


# --- Step 3 & 4: provider sends the user back with a code, swap it for a token
@app.route("/callback/<app_name>")
def callback(app_name):
    code = request.args.get("code")
    state = request.args.get("state")
    pending = PENDING.pop(state, None)
    if not pending or not code:
        return jsonify({"error": "invalid or expired connect attempt, try /connect again"}), 400

    provider = PROVIDER_URLS[app_name]
    resp = requests.post(provider["token_url"], data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": f"{os.environ.get('BASE_URL', 'http://localhost:5000')}/callback/{app_name}",
        "client_id": os.environ[provider["client_id_env"]],
        "client_secret": os.environ[provider["client_secret_env"]],
    })
    data = resp.json()

    _save_token(pending["user_id"], app_name, data.get("access_token"), data.get("refresh_token"))
    return jsonify({"app": app_name, "user": pending["user_id"], "status": "connected"})


# --- Just to see what's connected -------------------------------------------
@app.route("/connections/<user_id>")
def connections(user_id):
    tokens = _tokens()
    return jsonify([k.split(":")[1] for k in tokens if k.startswith(f"{user_id}:")])


# --- Step 5: ACTUALLY USE the token — this is the part that was missing ----
@app.route("/call/<app_name>")
def call_api(app_name):
    """Proves the connection works by making one real, authenticated call.
    This is the pattern for every future feature: get the stored token,
    put it in the Authorization header, hit the app's real API."""
    user_id = request.args.get("user_id")
    token = _get_token(user_id, app_name) if user_id else None
    if not token:
        return jsonify({"error": f"{app_name} isn't connected for this user yet — hit /connect/{app_name} first"}), 400

    provider = PROVIDER_URLS.get(app_name, {})
    test_endpoint = provider.get("test_endpoint")
    if not test_endpoint:
        return jsonify({"error": f"No test_endpoint configured for {app_name} in oauth_provider_configs.json"}), 400

    resp = requests.get(test_endpoint, headers={"Authorization": f"Bearer {token}"})
    return jsonify({
        "app": app_name,
        "endpoint_called": test_endpoint,
        "status_code": resp.status_code,
        "response": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text[:300],
    })


@app.route("/")
def home():
    return jsonify({
        "message": "OAuth demo running.",
        "usage": {
            "connect": "/connect/<app_name>?user_id=<user_id>",
            "callback": "/callback/<app_name>",
            "connections": "/connections/<user_id>",
            "call": "/call/<app_name>?user_id=<user_id>"
        }
    })


@app.route("/favicon.ico")
def favicon():
    return "", 204


if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "0").lower() in ("1", "true", "yes")
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 5000))
    app.run(host=host, port=port, debug=debug_mode)
