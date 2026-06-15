#!/usr/bin/env python3
"""Enable Google SSO on Metabase via API.

Sets three settings:
    google-auth-client-id                       — OAuth client ID from Google Cloud
    google-auth-auto-create-accounts-domain    — restricts auto-created accounts
    google-auth-enabled                         — turns the feature ON

Run once:
    export METABASE_API_KEY='mb_...'
    python3 scripts/metabase_enable_google_sso.py

Idempotent: re-running prints current state but doesn't fail.
"""
from __future__ import annotations

import json
import os
import sys
from urllib import error, request

METABASE_URL = os.environ.get("METABASE_URL", "https://metabase.rishi.yral.com")
API_KEY = os.environ.get("METABASE_API_KEY")

CLIENT_ID = "138948609758-qv3qitf231mmi0m9k55gk011et0qucv9.apps.googleusercontent.com"
AUTO_CREATE_DOMAIN = "gobazzinga.io"

if not API_KEY:
    print("ERROR: METABASE_API_KEY env var not set.", file=sys.stderr)
    sys.exit(1)

HEADERS = {
    "x-api-key": API_KEY,
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


def api(method: str, path: str, payload: dict | None = None):
    url = f"{METABASE_URL}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = request.Request(url, data=data, method=method, headers=HEADERS)
    try:
        with request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode() or "{}"
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return body
    except error.HTTPError as e:
        msg = e.read().decode()[:500] if hasattr(e, "read") else str(e)
        raise RuntimeError(f"HTTP {e.code} on {method} {path}: {msg}") from None


def set_setting(key: str, value) -> None:
    print(f"  setting {key} = {value!r}")
    api("PUT", f"/api/setting/{key}", {"value": value})


def main() -> int:
    print(f"Metabase URL: {METABASE_URL}")
    print()
    print("Setting Google SSO config...")
    # Order matters: set client-id + domain FIRST, enable LAST. Otherwise
    # Metabase may reject 'enabled=true' if the dependencies aren't satisfied.
    set_setting("google-auth-client-id", CLIENT_ID)
    set_setting("google-auth-auto-create-accounts-domain", AUTO_CREATE_DOMAIN)
    set_setting("google-auth-enabled", True)
    print()

    print("Verifying...")
    props = api("GET", "/api/session/properties")
    print(f"  google-auth-enabled:    {props.get('google-auth-enabled')}")
    print(f"  google-auth-client-id:  {props.get('google-auth-client-id')}")
    print()

    if props.get("google-auth-enabled"):
        print("=" * 60)
        print("DONE. Google SSO is ON.")
        print("=" * 60)
        print("Tell Neha to:")
        print(f"  1. Refresh {METABASE_URL}/ in her browser")
        print(f"  2. Click 'Sign in with Google'")
        print(f"  3. Use her @gobazzinga.io account")
        print()
        print(f"Auto-create restricted to: @{AUTO_CREATE_DOMAIN}")
        print("(Other Google accounts will be rejected at the consent screen.)")
        return 0
    else:
        print("WARNING: enable flag returned false after PUT.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
