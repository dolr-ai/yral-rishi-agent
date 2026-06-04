#!/usr/bin/env python3
"""Phase 1.17 — automated p50/p95/p99 latency comparison vs chat-ai.

CLAUDE.md target: v2 must be 50% faster on user-facing endpoints. This
script is the first hard signal for that target before cutover.

Three matched endpoints, run against both backends back-to-back:
  - chat-send   POST /api/v1/chat/conversations/{id}/messages
                (the LLM hot path — this is what end-users feel)
  - inbox-list  GET  /api/v1/chat/conversations
                (the home screen)
  - inf-list    GET  /api/v1/influencers
                (the discovery screen)

Run from anywhere with network access to both hosts:

    python3 scripts/latency_comparison_phase_1_17.py \\
        --v2-url     https://agent.rishi.yral.com \\
        --chat-ai-url https://chat-ai.rishi.yral.com \\
        --n 25 \\
        --report     docs/PHASE-1-17-LATENCY-COMPARISON-2026-06-03.md

Auth uses the same `verify_signature=False` JWT trick as the eval
script — both services accept unsigned tokens (matches production
mobile behavior per the JWT shadow rig). NEVER ship to a service
that enforces signature verification.

Concurrency is intentionally low (default 3) to avoid skewing prod
latency. Total request volume per run with default args: ~150 — well
below normal traffic.
"""

import argparse
import asyncio
import base64
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx


# ─── JWT minting ─────────────────────────────────────────────────────────


def mint_jwt(sub: str = "latency-comparison") -> str:
    """Both services use verify_signature=False; signature can be any bytes.
    Same shape as scripts/eval_v2_vs_chat_ai.py to keep the auth surface
    consistent across diagnostic tools."""

    def b64u(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    header = b64u(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = b64u(
        json.dumps(
            {
                "sub": sub,
                "iss": "https://auth.yral.com",
                "exp": int(time.time()) + 3600,
                "iat": int(time.time()),
            }
        ).encode()
    )
    return f"{header}.{payload}.dW51c2Vk"


# ─── Single-request timers (one per endpoint) ────────────────────────────


async def time_get(
    client: httpx.AsyncClient,
    base_url: str,
    path: str,
    token: str,
) -> tuple[bool, float, int]:
    """Returns (ok, latency_ms, status_code)."""
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "yral-latency-runner/1.0",
    }
    t0 = time.monotonic()
    try:
        r = await client.get(f"{base_url}{path}", headers=headers)
        latency = (time.monotonic() - t0) * 1000
        return r.status_code < 400, latency, r.status_code
    except Exception as e:
        latency = (time.monotonic() - t0) * 1000
        print(f"  GET {base_url}{path} failed: {e}", file=sys.stderr)
        return False, latency, 0


async def time_chat_send(
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
    influencer_id: str,
    message: str,
) -> tuple[bool, float, int]:
    """Measure end-to-end POST /messages latency. The conversation create
    is NOT counted — it's a separate, one-time setup. The message POST
    is what end-users feel on every send.

    `token` is the PER-CALL JWT — the runner mints a unique sub per
    chat-send so concurrent calls don't race on the (user, influencer)
    UNIQUE constraint in v2's `conversations` table. Without that, with
    concurrency=N you'd see ~1/N success rate as the unique-constraint
    violation knocks out the rest of the batch."""
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "yral-latency-runner/1.0",
        "Content-Type": "application/json",
    }
    status = 0
    conv_id = None

    # Setup — not timed. Surfaces the real status on failure so the
    # report shows 409/429 instead of an opaque 0.
    try:
        r = await client.post(
            f"{base_url}/api/v1/chat/conversations",
            headers=headers,
            json={"influencer_id": influencer_id},
        )
        status = r.status_code
        if r.status_code >= 400:
            return False, 0.0, status
        conv_id = r.json()["id"]
    except Exception as e:
        print(f"  create-conv failed against {base_url}: {e}", file=sys.stderr)
        return False, 0.0, status

    # Timed payload — POST /messages
    t0 = time.monotonic()
    ok = False
    try:
        r = await client.post(
            f"{base_url}/api/v1/chat/conversations/{conv_id}/messages",
            headers=headers,
            json={"content": message, "message_type": "text"},
        )
        latency = (time.monotonic() - t0) * 1000
        status = r.status_code
        ok = r.status_code < 400
    except Exception as e:
        latency = (time.monotonic() - t0) * 1000
        print(f"  send-message failed against {base_url}: {e}", file=sys.stderr)

    # Cleanup — best-effort
    try:
        await client.delete(
            f"{base_url}/api/v1/chat/conversations/{conv_id}", headers=headers
        )
    except Exception:
        pass

    return ok, latency, status


# ─── Endpoint runner (semaphore-limited, repeated N times) ───────────────


@dataclass
class Sample:
    label: str
    base_url: str
    latencies_ms: list[float] = field(default_factory=list)
    errors: int = 0
    statuses: dict[int, int] = field(default_factory=dict)


async def run_endpoint(
    label: str,
    base_url: str,
    timed_call_factory,
    n: int,
    concurrency: int,
) -> Sample:
    """Run `timed_call_factory(i)` n times against base_url with at most
    `concurrency` in flight. The factory takes the iteration index so
    callers can mint a per-call JWT sub (necessary for chat-send to
    dodge the (user, influencer) UNIQUE constraint on concurrent
    creates). Each factory return is a no-arg async returning
    (ok, latency, status)."""
    sample = Sample(label=label, base_url=base_url)
    sem = asyncio.Semaphore(concurrency)

    async def one(i: int):
        async with sem:
            ok, lat, status = await timed_call_factory(i)()
            if ok:
                sample.latencies_ms.append(lat)
            else:
                sample.errors += 1
            sample.statuses[status] = sample.statuses.get(status, 0) + 1

    await asyncio.gather(*(one(i) for i in range(n)))
    return sample


# ─── Percentile + report formatting ──────────────────────────────────────


def percentile(values: list[float], p: float) -> float:
    """p in [0,100]. Linear interpolation between adjacent points."""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[int(k)]
    return s[f] + (s[c] - s[f]) * (k - f)


def summarize(sample: Sample) -> dict:
    lats = sample.latencies_ms
    return {
        "label": sample.label,
        "base_url": sample.base_url,
        "n_success": len(lats),
        "n_error": sample.errors,
        "p50_ms": percentile(lats, 50),
        "p95_ms": percentile(lats, 95),
        "p99_ms": percentile(lats, 99),
        "mean_ms": statistics.mean(lats) if lats else 0.0,
        "min_ms": min(lats) if lats else 0.0,
        "max_ms": max(lats) if lats else 0.0,
        "statuses": sample.statuses,
    }


def fmt_pct(delta_pct: float) -> str:
    arrow = "↓" if delta_pct < 0 else "↑"
    return f"{arrow}{abs(delta_pct):.0f}%"


def render_report(
    args: argparse.Namespace,
    v2: dict[str, dict],
    chat_ai: dict[str, dict],
    started_at: str,
    finished_at: str,
) -> str:
    """Compose the markdown report. Layout mirrors PHASE-25 reports so
    Rishi can skim it the same way."""
    lines: list[str] = []
    lines.append("# Phase 1.17 — v2 vs chat-ai latency comparison")
    lines.append("")
    lines.append(f"**Run:** {started_at} → {finished_at}")
    lines.append(f"**v2 URL:** `{args.v2_url}`")
    lines.append(f"**chat-ai URL:** `{args.chat_ai_url}`")
    lines.append(
        f"**Samples per endpoint per backend:** {args.n}  "
        f"**Concurrency:** {args.concurrency}"
    )
    lines.append("")
    lines.append(
        "**CLAUDE.md target:** v2 must be **≥50% faster** than chat-ai on "
        "user-facing endpoints."
    )
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(
        "| Endpoint | v2 p50 | chat-ai p50 | Δp50 | v2 p95 | chat-ai p95 | Δp95 | Target met? |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|:---:|")

    for endpoint in v2.keys():
        v = v2[endpoint]
        c = chat_ai[endpoint]
        if not v["n_success"] or not c["n_success"]:
            lines.append(
                f"| `{endpoint}` | {v['p50_ms']:.0f}ms | {c['p50_ms']:.0f}ms | — | "
                f"{v['p95_ms']:.0f}ms | {c['p95_ms']:.0f}ms | — | "
                f"INSUFFICIENT DATA (v2 ok={v['n_success']}, chat-ai ok={c['n_success']}) |"
            )
            continue

        delta_50 = (v["p50_ms"] - c["p50_ms"]) / c["p50_ms"] * 100 if c["p50_ms"] else 0
        delta_95 = (v["p95_ms"] - c["p95_ms"]) / c["p95_ms"] * 100 if c["p95_ms"] else 0
        # v2 faster ⇔ delta < 0. Target = at least -50% (v2 half or less).
        target_met = delta_50 <= -50.0
        lines.append(
            f"| `{endpoint}` | {v['p50_ms']:.0f}ms | {c['p50_ms']:.0f}ms | "
            f"{fmt_pct(delta_50)} | {v['p95_ms']:.0f}ms | {c['p95_ms']:.0f}ms | "
            f"{fmt_pct(delta_95)} | {'✅' if target_met else '❌'} |"
        )

    lines.append("")
    lines.append("## Per-endpoint detail")
    lines.append("")

    for endpoint in v2.keys():
        lines.append(f"### `{endpoint}`")
        lines.append("")
        lines.append("| Stat | v2 | chat-ai |")
        lines.append("|---|---:|---:|")
        v = v2[endpoint]
        c = chat_ai[endpoint]
        lines.append(f"| n (success) | {v['n_success']} | {c['n_success']} |")
        lines.append(f"| n (error)   | {v['n_error']}   | {c['n_error']}   |")
        lines.append(f"| p50 ms      | {v['p50_ms']:.0f} | {c['p50_ms']:.0f} |")
        lines.append(f"| p95 ms      | {v['p95_ms']:.0f} | {c['p95_ms']:.0f} |")
        lines.append(f"| p99 ms      | {v['p99_ms']:.0f} | {c['p99_ms']:.0f} |")
        lines.append(f"| mean ms     | {v['mean_ms']:.0f} | {c['mean_ms']:.0f} |")
        lines.append(f"| min ms      | {v['min_ms']:.0f} | {c['min_ms']:.0f} |")
        lines.append(f"| max ms      | {v['max_ms']:.0f} | {c['max_ms']:.0f} |")
        lines.append(f"| status mix  | `{v['statuses']}` | `{c['statuses']}` |")
        lines.append("")

    lines.append("## Notes")
    lines.append("")
    lines.append(
        "- Both backends served the SAME JWT (verify_signature=false) so auth"
        " path is held constant."
    )
    lines.append(
        f"- Concurrency capped at {args.concurrency} per backend to avoid "
        "skewing production latency."
    )
    lines.append(
        "- Chat-send latency excludes conversation-create — the timed payload"
        " is the POST /messages (LLM round-trip + persistence)."
    )
    lines.append(
        "- chat-ai p95+ on chat-send may include cold-start/queueing variance"
        " from its older runtime; v2 is on the new Patroni cluster with the"
        " 25.4 hot-routing in place."
    )
    lines.append("")
    return "\n".join(lines)


# ─── Influencer probe ────────────────────────────────────────────────────


async def pick_shared_influencer(
    client: httpx.AsyncClient, v2_url: str, chat_ai_url: str, token: str
) -> str:
    """Find an influencer ID that exists on BOTH services. Strategy: list
    v2's active influencers, then probe each on chat-ai until one resolves.
    First hit wins so a runner doesn't fight conversation-create races."""
    headers = {"Authorization": f"Bearer {token}"}
    r = await client.get(f"{v2_url}/api/v1/influencers?limit=50", headers=headers)
    r.raise_for_status()
    items = r.json().get("influencers") or r.json().get("items") or r.json()
    if isinstance(items, dict):
        items = list(items.values())

    for inf in items:
        inf_id = inf.get("id") if isinstance(inf, dict) else None
        if not inf_id:
            continue
        # Confirm chat-ai has the same UUID
        probe = await client.get(
            f"{chat_ai_url}/api/v1/influencers/{inf_id}", headers=headers
        )
        if probe.status_code == 200:
            print(
                f"Picked shared influencer: {inf_id} "
                f"({inf.get('display_name') or inf.get('name')})"
            )
            return inf_id
    raise RuntimeError(
        "No shared influencer between v2 and chat-ai. ETL state likely "
        "inconsistent — surface to Rishi before continuing."
    )


# ─── Main ────────────────────────────────────────────────────────────────


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--v2-url", default="https://agent.rishi.yral.com")
    ap.add_argument("--chat-ai-url", default="https://chat-ai.rishi.yral.com")
    ap.add_argument(
        "--n", type=int, default=25, help="samples per endpoint per backend"
    )
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument(
        "--report",
        default=f"docs/PHASE-1-17-LATENCY-COMPARISON-{datetime.now().date()}.md",
        help="output markdown path",
    )
    ap.add_argument(
        "--message",
        default="hello, quick check-in — how are things?",
        help="text used for chat-send timing (kept short to keep LLM latency stable)",
    )
    args = ap.parse_args()

    base_token = mint_jwt()
    started_at = datetime.now(timezone.utc).isoformat()
    print(f"Start: {started_at}")
    print(f"v2: {args.v2_url}")
    print(f"chat-ai: {args.chat_ai_url}")
    print(f"n={args.n} concurrency={args.concurrency}")

    timeout = httpx.Timeout(60.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        influencer_id = await pick_shared_influencer(
            client, args.v2_url, args.chat_ai_url, base_token
        )

        # Factory shape: make_factory(url) → factory(i) → no-arg async.
        # chat-send mints a per-iteration JWT (different sub) so concurrent
        # calls don't collide on the (user, influencer) UNIQUE constraint.
        endpoints = (
            (
                "chat-send",
                lambda url: (
                    lambda i: (
                        lambda: time_chat_send(
                            client,
                            url,
                            mint_jwt(sub=f"latency-cs-{i}"),
                            influencer_id,
                            args.message,
                        )
                    )
                ),
            ),
            (
                "inbox-list",
                lambda url: (
                    lambda i: (
                        lambda: time_get(
                            client, url, "/api/v1/chat/conversations", base_token
                        )
                    )
                ),
            ),
            (
                "inf-list",
                lambda url: (
                    lambda i: (
                        lambda: time_get(client, url, "/api/v1/influencers", base_token)
                    )
                ),
            ),
        )

        v2_summaries: dict[str, dict] = {}
        chat_ai_summaries: dict[str, dict] = {}

        for label, make_factory in endpoints:
            print(f"\n--- {label} ---")
            # Run v2 first then chat-ai back-to-back so any time-of-day
            # drift hits both roughly equally.
            v2_sample = await run_endpoint(
                label,
                args.v2_url,
                make_factory(args.v2_url),
                args.n,
                args.concurrency,
            )
            chat_ai_sample = await run_endpoint(
                label,
                args.chat_ai_url,
                make_factory(args.chat_ai_url),
                args.n,
                args.concurrency,
            )
            v2_summaries[label] = summarize(v2_sample)
            chat_ai_summaries[label] = summarize(chat_ai_sample)
            v = v2_summaries[label]
            c = chat_ai_summaries[label]
            print(
                f"  v2:      p50={v['p50_ms']:.0f}ms p95={v['p95_ms']:.0f}ms "
                f"p99={v['p99_ms']:.0f}ms (ok={v['n_success']}/{args.n})"
            )
            print(
                f"  chat-ai: p50={c['p50_ms']:.0f}ms p95={c['p95_ms']:.0f}ms "
                f"p99={c['p99_ms']:.0f}ms (ok={c['n_success']}/{args.n})"
            )

    finished_at = datetime.now(timezone.utc).isoformat()
    report = render_report(
        args, v2_summaries, chat_ai_summaries, started_at, finished_at
    )
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report)
    print(f"\nReport written: {report_path}")
    print(f"Finished: {finished_at}")


if __name__ == "__main__":
    asyncio.run(main())
