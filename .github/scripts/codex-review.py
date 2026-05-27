#!/usr/bin/env python3
"""Codex PR review — posts findings as PR comments. Never blocks merge."""

import json
import os
import subprocess
import sys

from openai import OpenAI

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

    diff = subprocess.run(
        ["git", "diff", "origin/main...HEAD", "--", "app/", "infra/"],
        capture_output=True,
        text=True,
    ).stdout

    if not diff.strip():
        print("No app/ or infra/ changes — skipping review")
        return

    if len(diff) > 100_000:
        diff = diff[:100_000] + "\n... (truncated)"

    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": REVIEW_PROMPT},
            {"role": "user", "content": f"PR diff:\n```\n{diff}\n```"},
        ],
        temperature=0.1,
        max_tokens=2000,
    )

    text = response.choices[0].message.content or "[]"
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
