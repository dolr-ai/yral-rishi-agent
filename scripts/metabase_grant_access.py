#!/usr/bin/env python3
"""Grant selective access to YRAL team dashboards.

Idempotent setup:
    1. Create Collection "YRAL Team Dashboards" (if not present)
    2. Move all dashboards + cards from root → that collection
    3. Create Group "YRAL Team — Dashboard Viewers" (if not present)
    4. Set permissions:
         - All Users → new collection: No access
         - Viewer group → new collection: View
         - Viewer group → Yral Agent v2 DB: view data, no query creation
    5. Pre-create each INVITEE as a Metabase user in the viewer group.
       (When they sign in via Google SSO with the same email, Metabase
        matches and applies the group permissions.)

To add more people later: edit the INVITEES list + re-run. Existing users
are detected by email and skipped.

Usage:
    export METABASE_API_KEY='mb_...'
    python3 scripts/metabase_grant_access.py
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any
from urllib import error, request

METABASE_URL = os.environ.get("METABASE_URL", "https://metabase.rishi.yral.com")
DATABASE_ID = int(os.environ.get("METABASE_DB_ID", "2"))
API_KEY = os.environ.get("METABASE_API_KEY")

COLLECTION_NAME = "YRAL Team Dashboards"
GROUP_NAME = "YRAL Team — Dashboard Viewers"

INVITEES: list[tuple[str, str, str]] = [
    # (first_name, last_name, email)
    ("Neha", "Tiwari", "neha@gobazzinga.io"),
]


if not API_KEY:
    print("ERROR: METABASE_API_KEY env var not set.", file=sys.stderr)
    sys.exit(1)


def api(method: str, path: str, payload: dict | None = None) -> Any:
    url = f"{METABASE_URL}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = request.Request(
        url,
        data=data,
        method=method,
        headers={
            "x-api-key": API_KEY,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        },
    )
    try:
        with request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode() or "{}"
            return json.loads(body)
    except error.HTTPError as e:
        msg = e.read().decode()[:500] if hasattr(e, "read") else str(e)
        raise RuntimeError(f"HTTP {e.code} on {method} {path}: {msg}") from None


def find_or_create_collection() -> int:
    print(f"\n[1/5] Ensuring collection '{COLLECTION_NAME}' exists...")
    existing = api("GET", "/api/collection")
    if isinstance(existing, dict) and "data" in existing:
        existing = existing["data"]
    for c in existing:
        if c.get("name") == COLLECTION_NAME and not c.get("archived"):
            print(f"      found existing id={c['id']}")
            return c["id"]
    created = api(
        "POST",
        "/api/collection",
        {
            "name": COLLECTION_NAME,
            "description": "Shared dashboards for the YRAL team. "
                           "Permissions managed via 'YRAL Team — Dashboard Viewers' group.",
            "color": "#509EE3",
            "parent_id": None,
        },
    )
    print(f"      created id={created['id']}")
    return created["id"]


def move_root_items_into_collection(collection_id: int) -> tuple[int, int]:
    print(f"\n[2/5] Moving root-level dashboards + cards → '{COLLECTION_NAME}'...")
    dashboards_moved = 0
    cards_moved = 0

    dashes = api("GET", "/api/dashboard")
    if isinstance(dashes, dict) and "data" in dashes:
        dashes = dashes["data"]
    for d in dashes:
        if d.get("archived"):
            continue
        if d.get("collection_id") in (None, "root"):
            try:
                api("PUT", f"/api/dashboard/{d['id']}", {"collection_id": collection_id})
                print(f"      moved dashboard '{d['name']}'")
                dashboards_moved += 1
            except Exception as e:
                print(f"      SKIP dashboard '{d['name']}': {e}", file=sys.stderr)

    cards = api("GET", "/api/card")
    if isinstance(cards, dict) and "data" in cards:
        cards = cards["data"]
    for c in cards:
        if c.get("archived"):
            continue
        if c.get("collection_id") in (None, "root"):
            try:
                api("PUT", f"/api/card/{c['id']}", {"collection_id": collection_id})
                cards_moved += 1
            except Exception as e:
                print(f"      SKIP card '{c['name']}': {e}", file=sys.stderr)
    if cards_moved:
        print(f"      moved {cards_moved} saved questions (so dashboards can render them)")
    return dashboards_moved, cards_moved


def find_or_create_group() -> int:
    print(f"\n[3/5] Ensuring group '{GROUP_NAME}' exists...")
    groups = api("GET", "/api/permissions/group")
    for g in groups:
        if g.get("name") == GROUP_NAME:
            print(f"      found existing id={g['id']}")
            return g["id"]
    created = api("POST", "/api/permissions/group", {"name": GROUP_NAME})
    print(f"      created id={created['id']}")
    return created["id"]


def set_collection_permissions(collection_id: int, viewer_group_id: int) -> None:
    print(f"\n[4/5] Setting collection + database permissions...")

    # Collection: viewer group = View, All Users = No access, Admin = unchanged
    graph = api("GET", "/api/collection/graph")
    revision = graph.get("revision", 0)
    groups_perms = graph.get("groups", {})
    # All Users group has id 1 in every Metabase install
    all_users_id = "1"
    viewer_id_str = str(viewer_group_id)

    groups_perms.setdefault(all_users_id, {})[str(collection_id)] = "none"
    groups_perms.setdefault(viewer_id_str, {})[str(collection_id)] = "read"

    api(
        "PUT",
        "/api/collection/graph",
        {"revision": revision, "groups": groups_perms},
    )
    print(f"      collection perms: All Users=No access, Viewers=View")

    # Database: viewer group can view data, but not write new queries
    db_graph = api("GET", "/api/permissions/graph")
    db_revision = db_graph.get("revision", 0)
    db_groups = db_graph.get("groups", {})
    db_id_str = str(DATABASE_ID)
    db_groups.setdefault(viewer_id_str, {})[db_id_str] = {
        "view-data": "unrestricted",
        "create-queries": "no",
    }
    api(
        "PUT",
        "/api/permissions/graph",
        {"revision": db_revision, "groups": db_groups},
    )
    print(f"      database perms: Viewers can view data, cannot write new queries")


def ensure_users_in_group(viewer_group_id: int) -> None:
    print(f"\n[5/5] Pre-creating invitees in viewer group...")
    existing_users_resp = api("GET", "/api/user")
    if isinstance(existing_users_resp, dict) and "data" in existing_users_resp:
        existing_users = existing_users_resp["data"]
    else:
        existing_users = existing_users_resp
    by_email = {u["email"].lower(): u for u in existing_users if u.get("email")}

    for first, last, email in INVITEES:
        email_norm = email.lower()
        if email_norm in by_email:
            user = by_email[email_norm]
            print(f"      existing user {email} (id={user['id']}) — checking group membership")
            # Add to viewer group if not already in it
            current_groups = {m["id"] for m in user.get("group_ids", [])} if isinstance(
                user.get("group_ids", []), list
            ) and user["group_ids"] and isinstance(user["group_ids"][0], dict) else set(
                user.get("group_ids") or []
            )
            if viewer_group_id not in current_groups:
                try:
                    api(
                        "POST",
                        "/api/permissions/membership",
                        {"group_id": viewer_group_id, "user_id": user["id"]},
                    )
                    print(f"        added to '{GROUP_NAME}'")
                except Exception as e:
                    print(f"        SKIP membership: {e}", file=sys.stderr)
            else:
                print(f"        already in '{GROUP_NAME}'")
            continue

        try:
            created = api(
                "POST",
                "/api/user",
                {
                    "first_name": first,
                    "last_name": last,
                    "email": email,
                    "user_group_memberships": [
                        {"id": 1},                  # All Users (default; required)
                        {"id": viewer_group_id},   # Our viewer group
                    ],
                },
            )
            print(f"      created user {email} (id={created.get('id')}) in viewer group")
            if created.get("invite_url"):
                print(f"        invite URL (if SMTP not configured): {created['invite_url']}")
        except Exception as e:
            # Fallback: older Metabase API takes group_ids as a flat list
            try:
                created = api(
                    "POST",
                    "/api/user",
                    {
                        "first_name": first,
                        "last_name": last,
                        "email": email,
                        "group_ids": [1, viewer_group_id],
                    },
                )
                print(f"      created user {email} (id={created.get('id')}) in viewer group (legacy API)")
            except Exception as e2:
                print(f"      FAILED to create {email}: {e2}", file=sys.stderr)


def main() -> int:
    print(f"Metabase URL: {METABASE_URL}")
    print(f"Database ID:  {DATABASE_ID}")
    print(f"Invitees:     {[i[2] for i in INVITEES]}")

    collection_id = find_or_create_collection()
    n_dash, n_cards = move_root_items_into_collection(collection_id)
    group_id = find_or_create_group()
    set_collection_permissions(collection_id, group_id)
    ensure_users_in_group(group_id)

    print("\n" + "=" * 60)
    print("DONE.")
    print("=" * 60)
    print(f"  Collection URL: {METABASE_URL}/collection/{collection_id}")
    print(f"  Group ID:       {group_id}  ({GROUP_NAME})")
    print(f"  Dashboards moved into collection: {n_dash}")
    print(f"  Cards moved into collection:      {n_cards}")
    print()
    print("How an invitee signs in:")
    print(f"  1. They open {METABASE_URL}/")
    print(f"  2. Click 'Sign in with Google'")
    print(f"  3. Authenticate with their @gobazzinga.io account")
    print(f"  4. Metabase matches their email to the pre-created user")
    print(f"  5. They land on dashboards with View-only access")
    print()
    print("To add more people: edit INVITEES at top of this script + re-run.")
    print("The script is idempotent — existing setup is preserved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
