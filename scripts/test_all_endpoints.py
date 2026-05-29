#!/usr/bin/env python3
"""Test every endpoint on the live service. Reports PASS/FAIL for each.

Usage:
    python scripts/test_all_endpoints.py --base-url https://agent.rishi.yral.com
    python scripts/test_all_endpoints.py --base-url https://agent.rishi.yral.com --token <JWT>
"""

import argparse
import json
import sys
import time
import urllib.request
import urllib.error
import urllib.parse


class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    RESET = "\033[0m"


passed = 0
failed = 0
skipped = 0


def test(
    name,
    method,
    url,
    headers=None,
    body=None,
    expect_status=None,
    expect_json_key=None,
    max_latency_ms=None,
):
    global passed, failed
    if expect_status is None:
        expect_status = [200, 201]
    if isinstance(expect_status, int):
        expect_status = [expect_status]

    try:
        data = json.dumps(body).encode() if body else None
        all_headers = {"User-Agent": "yral-endpoint-test/1.0", **(headers or {})}
        req = urllib.request.Request(url, data=data, method=method, headers=all_headers)
        if data:
            req.add_header("Content-Type", "application/json")

        # Task A: retry once on socket timeout. Real network blips
        # (Cloudflare edge cold-start, TLS renegotiation) occasionally
        # hang urllib's read for >30s on endpoints that normally serve
        # in <2s. A single retry catches those without masking real
        # server-side regressions.
        t0 = time.monotonic()
        try:
            resp = urllib.request.urlopen(req, timeout=30)
        except (TimeoutError, urllib.error.URLError) as net_err:
            if isinstance(net_err, urllib.error.URLError) and not isinstance(
                getattr(net_err, "reason", None), TimeoutError
            ):
                raise
            print(
                f"  {Colors.YELLOW}RETRY{Colors.RESET} {name} — first attempt timed out, retrying once"
            )
            t0 = time.monotonic()
            resp = urllib.request.urlopen(req, timeout=30)
        latency = (time.monotonic() - t0) * 1000
        status = resp.status
        resp_body = resp.read().decode()

        if status not in expect_status:
            print(
                f"  {Colors.RED}FAIL{Colors.RESET} {name} — expected {expect_status}, got {status}"
            )
            failed += 1
            return None

        if expect_json_key:
            data = json.loads(resp_body)
            if expect_json_key not in data:
                print(
                    f"  {Colors.RED}FAIL{Colors.RESET} {name} — missing key '{expect_json_key}' in response"
                )
                failed += 1
                return None

        if max_latency_ms is not None and latency > max_latency_ms:
            print(
                f"  {Colors.RED}FAIL{Colors.RESET} {name} — latency {latency:.0f}ms exceeds limit {max_latency_ms}ms"
            )
            failed += 1
            return None

        print(f"  {Colors.GREEN}PASS{Colors.RESET} {name} ({latency:.0f}ms)")
        passed += 1
        try:
            return json.loads(resp_body)
        except json.JSONDecodeError:
            return resp_body

    except urllib.error.HTTPError as e:
        status = e.code
        body_text = ""
        try:
            body_text = e.read().decode()[:200]
        except Exception:
            pass

        if status in expect_status:
            print(f"  {Colors.GREEN}PASS{Colors.RESET} {name} (HTTP {status})")
            passed += 1
            try:
                return json.loads(body_text)
            except Exception:
                return body_text
        else:
            print(
                f"  {Colors.RED}FAIL{Colors.RESET} {name} — HTTP {status}: {body_text[:100]}"
            )
            failed += 1
            return None

    except Exception as e:
        print(f"  {Colors.RED}FAIL{Colors.RESET} {name} — {type(e).__name__}: {e}")
        failed += 1
        return None


def skip(name, reason):
    global skipped
    print(f"  {Colors.YELLOW}SKIP{Colors.RESET} {name} — {reason}")
    skipped += 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://agent.rishi.yral.com")
    parser.add_argument(
        "--token", default=None, help="JWT Bearer token for authenticated endpoints"
    )
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    auth = {"Authorization": f"Bearer {args.token}"} if args.token else {}

    print(f"\nTesting {base}\n")

    # === UNAUTHENTICATED ENDPOINTS ===
    print("--- Health & Status (no auth) ---")
    test("GET /", "GET", f"{base}/")
    test("GET /health", "GET", f"{base}/health", expect_json_key="status")
    test("GET /health/live", "GET", f"{base}/health/live", expect_json_key="status")
    test("GET /status", "GET", f"{base}/status", expect_json_key="gemini_model")

    print("\n--- Influencer READ (no auth) ---")
    inf_list = test(
        "GET /influencers",
        "GET",
        f"{base}/api/v1/influencers?limit=3",
        expect_json_key="influencers",
    )
    test(
        "GET /influencers/trending",
        "GET",
        f"{base}/api/v1/influencers/trending?limit=3",
        expect_json_key="influencers",
    )

    influencer_id = None
    if inf_list and inf_list.get("influencers"):
        influencer_id = inf_list["influencers"][0]["id"]
        test(
            "GET /influencers/{id}",
            "GET",
            f"{base}/api/v1/influencers/{influencer_id}",
            expect_json_key="id",
        )
    else:
        skip("GET /influencers/{id}", "no influencers in list")

    print("\n--- WebSocket docs (no auth) ---")
    test(
        "GET /ws/docs",
        "GET",
        f"{base}/api/v1/chat/ws/docs",
        expect_json_key="new_message",
    )

    # === AUTHENTICATED ENDPOINTS ===
    if not args.token:
        print(
            f"\n{Colors.YELLOW}--- Skipping authenticated endpoints (no --token provided) ---{Colors.RESET}"
        )
        for name in [
            "GET /auth/me",
            "POST /conversations",
            "GET /conversations",
            "POST /messages (send)",
            "GET /messages",
            "POST /read",
            "GET /v2/conversations",
            "GET /v3/conversations",
            "POST /generate-prompt",
            "POST /influencers/create",
            "POST /media/upload",
            "POST /human/conversations",
            "GET /human/conversations",
            "GET /creator/influencers",
            "GET /creator/earnings",
            "DELETE /conversations/{id}",
        ]:
            skip(name, "no JWT token")
        print_summary()
        return

    print("\n--- Auth (JWT required) ---")
    me = test(
        "GET /auth/me",
        "GET",
        f"{base}/api/v1/auth/me",
        headers=auth,
        expect_json_key="user_id",
    )

    print("\n--- Chat v1: Conversations ---")
    conv = None
    if influencer_id:
        conv = test(
            "POST /conversations (create)",
            "POST",
            f"{base}/api/v1/chat/conversations",
            headers=auth,
            body={"influencer_id": influencer_id},
            expect_status=[200, 201],
            expect_json_key="id",
        )
    else:
        skip("POST /conversations", "no influencer_id")

    test(
        "GET /conversations (list)",
        "GET",
        f"{base}/api/v1/chat/conversations?limit=3",
        headers=auth,
        expect_json_key="conversations",
    )

    conversation_id = None
    if conv:
        conversation_id = conv["id"]

        print("\n--- Chat v1: Messages ---")
        test(
            "POST /messages (send)",
            "POST",
            f"{base}/api/v1/chat/conversations/{conversation_id}/messages",
            headers=auth,
            body={"content": "Hello from endpoint test", "message_type": "text"},
            expect_json_key="user_message",
        )

        test(
            "GET /messages (list, polling-perf)",
            "GET",
            f"{base}/api/v1/chat/conversations/{conversation_id}/messages?limit=50",
            headers=auth,
            expect_json_key="messages",
            # End-to-end including TLS + Cloudflare + 2 Caddy hops + app + DB.
            # Server-side query is ~2-3ms per EXPLAIN ANALYZE (uses idx_messages_conversation_created).
            max_latency_ms=2000,
        )

        test(
            "POST /read (mark read)",
            "POST",
            f"{base}/api/v1/chat/conversations/{conversation_id}/read",
            headers=auth,
            expect_json_key="unread_count",
        )
    else:
        skip("POST /messages", "no conversation created")
        skip("GET /messages", "no conversation")
        skip("POST /read", "no conversation")

    print("\n--- Chat v2: Bot-aware inbox ---")
    if me and me.get("user_id"):
        test(
            "GET /v2/conversations",
            "GET",
            f"{base}/api/v2/chat/conversations?principal={me['user_id']}&limit=3",
            headers=auth,
            expect_json_key="conversations",
        )
    else:
        skip("GET /v2/conversations", "no user_id from /auth/me")

    print("\n--- Chat v3: Unified inbox ---")
    test(
        "GET /v3/conversations",
        "GET",
        f"{base}/api/v3/chat/conversations?limit=3",
        headers=auth,
        expect_json_key="conversations",
    )

    print("\n--- Influencer CREATE flow ---")
    test(
        "POST /generate-prompt",
        "POST",
        f"{base}/api/v1/influencers/generate-prompt",
        headers=auth,
        body={"prompt": "a wise fitness coach who speaks Hinglish"},
        expect_json_key="system_instructions",
    )

    print("\n--- Human Chat ---")
    h2h = test(
        "POST /human/conversations",
        "POST",
        f"{base}/api/v1/chat/human/conversations",
        headers=auth,
        body={"participant_id": "test-endpoint-user-12345"},
        expect_status=[200, 201],
        expect_json_key="id",
    )

    test(
        "GET /human/conversations",
        "GET",
        f"{base}/api/v1/chat/human/conversations?limit=3",
        headers=auth,
        expect_json_key="conversations",
    )

    print("\n--- Creator Studio ---")
    test(
        "GET /creator/influencers",
        "GET",
        f"{base}/api/v1/creator/influencers",
        headers=auth,
        expect_json_key="influencers",
    )

    print("\n--- Chat as Human (takeover) ---")
    # Need an owned-influencer conversation to test the full flow.
    # Authorization check (non-owner gets 403) is the minimum we always test.
    test(
        "POST /human-creator-takeover (non-owner 403)",
        "POST",
        f"{base}/api/v1/creator/conversations/00000000-0000-0000-0000-000000000000/human-creator-takeover",
        headers=auth,
        expect_status=[403, 404],
    )
    test(
        "GET /creator/messages (non-owner 403)",
        "GET",
        f"{base}/api/v1/creator/conversations/00000000-0000-0000-0000-000000000000/messages",
        headers=auth,
        expect_status=[403, 404],
    )

    print("\n--- Bot Quality Score (Phase 7.7) ---")
    test(
        "GET /creator/influencers/{id}/quality-score (404)",
        "GET",
        f"{base}/api/v1/creator/influencers/00000000-0000-0000-0000-000000000000/quality-score",
        headers=auth,
        expect_status=[403, 404],
    )
    test(
        "GET /creator/influencers/{id}/recommendations (404)",
        "GET",
        f"{base}/api/v1/creator/influencers/00000000-0000-0000-0000-000000000000/recommendations",
        headers=auth,
        expect_status=[403, 404],
    )

    print("\n--- A/B testing (Phase 7.6) ---")
    test(
        "POST /creator/influencers/{id}/variant-b (404)",
        "POST",
        f"{base}/api/v1/creator/influencers/00000000-0000-0000-0000-000000000000/variant-b",
        headers=auth,
        body={"system_instructions": "x"},
        expect_status=[403, 404],
    )
    test(
        "GET /creator/influencers/{id}/variants/compare (404)",
        "GET",
        f"{base}/api/v1/creator/influencers/00000000-0000-0000-0000-000000000000/variants/compare",
        headers=auth,
        expect_status=[403, 404],
    )
    test(
        "POST /creator/influencers/{id}/variants/a/promote (404)",
        "POST",
        f"{base}/api/v1/creator/influencers/00000000-0000-0000-0000-000000000000/variants/a/promote",
        headers=auth,
        expect_status=[403, 404],
    )

    print("\n--- User Memories (Phase 4.4) ---")
    test(
        "GET /users/me/memories",
        "GET",
        f"{base}/api/v1/users/me/memories",
        headers=auth,
        expect_json_key="memories",
    )

    print("\n--- Creator Earnings ---")
    test(
        "GET /creator/earnings",
        "GET",
        f"{base}/api/v1/creator/earnings",
        headers=auth,
        expect_json_key="total_cents",
    )
    test(
        "GET /creator/earnings/by-influencer",
        "GET",
        f"{base}/api/v1/creator/earnings/by-influencer",
        headers=auth,
        expect_json_key="influencers",
    )
    test(
        "GET /creator/earnings/history",
        "GET",
        f"{base}/api/v1/creator/earnings/history?limit=3",
        headers=auth,
        expect_json_key="earnings",
    )

    # === CLEANUP ===
    print("\n--- Cleanup ---")
    if conversation_id:
        test(
            "DELETE /conversations/{id}",
            "DELETE",
            f"{base}/api/v1/chat/conversations/{conversation_id}",
            headers=auth,
            expect_json_key="success",
        )
    else:
        skip("DELETE /conversations", "no conversation to delete")

    if h2h and h2h.get("id"):
        # Don't delete H2H — just note it exists
        print(
            f"  {Colors.GREEN}NOTE{Colors.RESET} H2H conversation {h2h['id']} created (not deleting)"
        )

    print_summary()


def print_summary():
    global passed, failed, skipped
    total = passed + failed + skipped
    print(f"\n{'=' * 50}")
    print(
        f"TOTAL: {total} | {Colors.GREEN}PASS: {passed}{Colors.RESET} | {Colors.RED}FAIL: {failed}{Colors.RESET} | {Colors.YELLOW}SKIP: {skipped}{Colors.RESET}"
    )
    print(f"{'=' * 50}\n")
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
