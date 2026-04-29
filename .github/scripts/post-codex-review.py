"""
╔══════════════════════════════════════════════════════════════════════╗
║                                                                        ║
║  FILE: .github/scripts/post-codex-review.py                            ║
║                                                                        ║
║  ⭐ THIS FILE IN ONE SENTENCE                                          ║
║  Reads Codex's structured JSON review and posts the findings as       ║
║  comments on the GitHub PR.                                            ║
║                                                                        ║
║  📖 EXPLAINED FOR A NON-PROGRAMMER                                     ║
║  After codex-review.py asks OpenAI for a review, the JSON sits on     ║
║  disk. This script reads it and turns each finding into either an     ║
║  inline comment (attached to a specific line) or a summary comment    ║
║  (attached to the PR overall). That's what coordinator + Rishi see    ║
║  when they look at the PR in GitHub.                                  ║
║                                                                        ║
║  🔗 HOW IT FITS                                                        ║
║  - Called by: .github/workflows/pr-codex-review.yml (after            ║
║    codex-review.py finishes)                                          ║
║  - Reads: --review (JSON from codex-review.py)                        ║
║  - Reads env: GITHUB_TOKEN (provided by Actions)                      ║
║  - Writes: PR comments via GitHub REST API                            ║
║                                                                        ║
║  ⭐ START HERE                                                         ║
║  Read main() — assembles + posts. post_summary_comment() is the      ║
║  single most important call (Rishi reads this).                       ║
║                                                                        ║
╚══════════════════════════════════════════════════════════════════════╝
"""

# IMPORTS
# ───────

import argparse
# Parses command-line flags.

import json
# Reads the review JSON from disk.

import os
# Reads GITHUB_TOKEN from env.

import sys
# Exit codes for error reporting back to the Action.

from pathlib import Path
# File reading.

import httpx
# HTTP client for talking to GitHub REST API. Installed in the workflow.


# ──────────────────────────────────────────────────────────────────────
# MAIN FUNCTION — start reading here
# ──────────────────────────────────────────────────────────────────────

def main() -> None:
    """
    WHAT — Reads Codex's review JSON and posts comments to the PR.
    WHEN — Called once per PR after codex-review.py succeeds.
    WHY  — Splits "call Codex" from "post comments" so each script has
           one job. Easier to debug + retry.
    """
    args = parse_arguments()

    # Verify GitHub token is present
    github_token = os.environ.get("GITHUB_TOKEN")
    if not github_token:
        print("ERROR: GITHUB_TOKEN env var is empty.", file=sys.stderr)
        sys.exit(1)

    # Read Codex's structured review
    review = json.loads(Path(args.review).read_text())

    # Always post the summary comment first — this is what Rishi reads
    post_summary_comment(args.repo, args.pr, review, github_token)

    # Then post each finding as a separate comment
    # (Inline comments require commit SHA + position which is fiddly;
    #  for now we post all findings as PR-level comments. Phase 1 we
    #  upgrade to inline-on-diff once we verify the path mapping works.)
    for finding in review.get("findings", []):
        post_finding_comment(args.repo, args.pr, finding, github_token)

    print(f"✅ Posted Codex review for PR #{args.pr}: "
          f"{len(review.get('findings', []))} findings + 1 summary")


# ──────────────────────────────────────────────────────────────────────
# HELPER FUNCTIONS (in priority order)
# ──────────────────────────────────────────────────────────────────────

def parse_arguments() -> argparse.Namespace:
    """
    WHAT — Parses --review, --pr, --repo command-line flags.
    WHEN — Called once at the start of main().
    """
    parser = argparse.ArgumentParser(
        description="Post Codex's structured review as PR comments."
    )
    parser.add_argument("--review", required=True,
                        help="Path to the JSON review from codex-review.py.")
    parser.add_argument("--pr", required=True,
                        help="GitHub PR number (e.g., 42).")
    parser.add_argument("--repo", required=True,
                        help="Repo in 'owner/name' format (e.g., dolr-ai/yral-rishi-agent).")
    return parser.parse_args()


def post_summary_comment(
    repo: str,
    pr_number: str,
    review: dict,
    token: str,
) -> None:
    """
    WHAT — Posts the overall Codex verdict + summary as a PR comment.
    WHEN — Called once before individual findings.
    WHY  — This is what Rishi sees at-a-glance. Coordinator references
           it when summarizing for Rishi's YES/NO decision.
    """
    overall = review.get("overall", "unknown")
    summary = review.get("summary", "(no summary)")
    top_three = review.get("top_three", [])

    # Map verdict to a readable header for Rishi
    verdict_header = {
        "approve": "✅ Codex APPROVE",
        "comment_only": "💬 Codex COMMENTS (informational, not blocking)",
        "request_changes": "❌ Codex REQUEST CHANGES",
    }.get(overall, "⚠️ Codex (unknown verdict)")

    # Build the comment body in markdown
    body_lines = [
        f"## {verdict_header}",
        "",
        f"**Summary:** {summary}",
        "",
    ]

    if top_three:
        body_lines.append("**Top 3 things to address:**")
        for i, item in enumerate(top_three, start=1):
            body_lines.append(f"{i}. {item}")
        body_lines.append("")

    body_lines.append("---")
    body_lines.append("*Independent Codex review per CONSTRAINTS I10. Coordinator will summarize for Rishi's YES/NO decision.*")

    body = "\n".join(body_lines)

    # POST the comment via GitHub REST API
    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    response = httpx.post(url, headers=headers, json={"body": body}, timeout=30)
    response.raise_for_status()


def post_finding_comment(
    repo: str,
    pr_number: str,
    finding: dict,
    token: str,
) -> None:
    """
    WHAT — Posts one finding as a PR-level comment.
    WHEN — Called once per finding in review.findings[].
    WHY  — Inline-on-diff comments need commit SHA + position; we'll
           upgrade to that in Phase 1. For now PR-level is sufficient.
    """
    severity = finding.get("severity", "concern")
    category = finding.get("category", "general")
    file = finding.get("file", "(no file)")
    line = finding.get("line", "?")
    issue = finding.get("issue", "(no issue text)")
    suggestion = finding.get("suggestion", "(no suggestion)")

    # Map severity to emoji for ADHD-friendly skim
    severity_emoji = {
        "blocker": "🛑",
        "concern": "⚠️",
        "nit": "💡",
    }.get(severity, "📝")

    body = (
        f"{severity_emoji} **{severity.upper()}** ({category}) — `{file}:{line}`\n\n"
        f"**Issue:** {issue}\n\n"
        f"**Suggestion:** {suggestion}"
    )

    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    response = httpx.post(url, headers=headers, json={"body": body}, timeout=30)
    response.raise_for_status()


# ══════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    main()


# ══════════════════════════════════════════════════════════════════════
# RELATED FILES
# ──────────────
# - .github/scripts/codex-review.py — produces the JSON we read
# - .github/workflows/pr-codex-review.yml — calls this script
# - yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/03-CODEX-REVIEW-WORKFLOW.md
# ══════════════════════════════════════════════════════════════════════
