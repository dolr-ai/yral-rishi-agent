#!/usr/bin/env python3
"""Codex PR review — posts findings as PR comments. Never blocks merge."""

import json
import os
import subprocess
import sys

from openai import OpenAI

# Was gpt-4o — two generations old, and on the retired chat.completions
# endpoint. gpt-6-astra is the current flagship for reasoning and code work.
#
# Cost: the diff is capped at 100k chars (~25k tokens) below, so the ceiling is
# about $0.35 a review at $10/$50 per M in/out; a normal PR runs well under
# that. If that is more than the review is worth, `gpt-5.6-sol` is the same
# generation at roughly a third the price — change this one line.
#
# Reasoning models use the Responses API, not chat.completions, and do not take
# a temperature. Effort is the dial instead: "high" because the failures worth
# catching here are subtle (PR #501 shipped a response-model type that 500'd
# every valid request for four days, and no reviewer ran on it at all).
MODEL = "gpt-6-astra"
REASONING_EFFORT = "high"
MAX_OUTPUT_TOKENS = 4000

REVIEW_PROMPT = """You are a code reviewer for yral-rishi-agent, an AI chat backend (FastAPI + asyncpg + Gemini).

Review this PR diff for ONLY these categories:

1. REAL BUGS — missing auth checks, SQL injection, data loss, unhandled errors that crash the server, race conditions
2. MOBILE CONTRACT VIOLATIONS — response JSON doesn't match the Pydantic models in models.py (wrong field names, wrong types, missing required fields)
3. OVER-ENGINEERING — files over 400 lines, unnecessary abstractions, premature generalization

DO NOT comment on: naming style, comment density, test coverage, documentation, formatting, import order.

For each finding, output a JSON array of objects:
[{"file": "app/routes/chat.py", "line": 42, "severity": "bug|contract|overeng", "message": "description"}]

If no findings, output: []

Be concise. Only flag things that would break production or confuse the mobile app."""


def main():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("::error::OPENAI_API_KEY not set — Codex review cannot run")
        sys.exit(1)

    # Pathspec must match the workflow's `paths:` filter, otherwise the
    # workflow fires but this script no-ops.
    diff = subprocess.run(
        [
            "git",
            "diff",
            "origin/main...HEAD",
            "--",
            "app/",
            "infra/",
            "bootstrap/",
            ".github/workflows/",
            "migrations/",
            "scripts/",
            "tests/",
        ],
        capture_output=True,
        text=True,
    ).stdout

    if not diff.strip():
        print("No reviewable changes — skipping review")
        return

    if len(diff) > 100_000:
        diff = diff[:100_000] + "\n... (truncated)"

    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=MODEL,
        instructions=REVIEW_PROMPT,
        input=f"PR diff:\n```\n{diff}\n```",
        reasoning={"effort": REASONING_EFFORT},
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )

    # A reasoning model spends max_output_tokens on thinking BEFORE it writes
    # anything, so a budget that runs out yields status="incomplete" and an
    # EMPTY output_text — which would fall through below as a cheerful "no
    # issues found". Say so loudly instead: a review that silently reviewed
    # nothing is the failure this job exists to prevent.
    if getattr(response, "status", None) == "incomplete":
        reason = getattr(
            getattr(response, "incomplete_details", None), "reason", "unknown"
        )
        print(
            f"Codex review INCOMPLETE ({reason}) — treat this run as no review at all."
        )
        if reason == "max_output_tokens":
            print("Raise MAX_OUTPUT_TOKENS or lower REASONING_EFFORT in this script.")
        return

    # output_text is the SDK accessor; the raw `output` array's shape varies by
    # model and is not safe to index blindly.
    text = response.output_text or "[]"
    start = text.find("[")
    end = text.rfind("]") + 1
    if start < 0 or end <= start:
        print("No structured findings from Codex")
        return

    try:
        findings = json.loads(text[start:end])
    except json.JSONDecodeError:
        print(f"Failed to parse Codex response: {text[:500]}")
        return

    if not findings:
        print("Codex review: no issues found")
        return

    pr_number = os.environ.get("PR_NUMBER", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    gh_token = os.environ.get("GITHUB_TOKEN", "")

    body_lines = ["### Codex Review Findings\n"]
    for f in findings:
        severity = {"bug": "BUG", "contract": "CONTRACT", "overeng": "OVERENG"}.get(
            f.get("severity", ""), "NOTE"
        )
        body_lines.append(
            f"- **[{severity}]** `{f.get('file', '?')}:{f.get('line', '?')}` — {f.get('message', '')}"
        )

    body = "\n".join(body_lines)
    print(body)

    if pr_number and repo and gh_token:
        subprocess.run(
            ["gh", "pr", "comment", pr_number, "--body", body],
            env={**os.environ, "GH_TOKEN": gh_token},
        )


if __name__ == "__main__":
    main()
