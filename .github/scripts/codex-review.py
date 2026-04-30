"""
╔══════════════════════════════════════════════════════════════════════╗
║                                                                        ║
║  FILE: .github/scripts/codex-review.py                                 ║
║                                                                        ║
║  ⭐ THIS FILE IN ONE SENTENCE                                          ║
║  Calls OpenAI's Codex (via the OpenAI API) to review a PR's diff      ║
║  against our CONSTRAINTS, then writes a structured review to JSON.    ║
║                                                                        ║
║  📖 EXPLAINED FOR A NON-PROGRAMMER                                     ║
║  Imagine asking a fresh-eyed senior engineer to review every PR       ║
║  before merge. That engineer is Codex. This script bundles the diff   ║
║  + our rules + the doc standard + the prompt, sends it to OpenAI,     ║
║  and saves Codex's response as JSON. The next script                  ║
║  (post-codex-review.py) reads that JSON and posts comments to GitHub. ║
║                                                                        ║
║  🔗 HOW IT FITS                                                        ║
║  - Called by: .github/workflows/pr-codex-review.yml                   ║
║  - Reads: --diff (the PR's git diff), --context-dir (constraint files)║
║  - Reads env: OPENAI_API_KEY (from secrets.OPENAI_CODEX_API_KEY)     ║
║  - Writes: --output JSON file (consumed by post-codex-review.py)      ║
║                                                                        ║
║  📥 INPUTS / 📤 OUTPUTS                                                ║
║  - Input: diff file + context dir + PR metadata                       ║
║  - Output: JSON review (overall, summary, findings, top_three)        ║
║                                                                        ║
║  ⚠️ SIDE EFFECTS                                                       ║
║  - Spends OpenAI API credits (~$0.10-0.50 per call)                   ║
║  - Network call to api.openai.com                                     ║
║                                                                        ║
║  ⭐ START HERE                                                         ║
║  Read main() — the orchestration. assemble_messages() builds what     ║
║  we send to Codex.                                                    ║
║                                                                        ║
╚══════════════════════════════════════════════════════════════════════╝
"""

# IMPORTS
# ───────

import argparse
# argparse parses command-line flags (--diff, --context-dir, --output, etc.).
# Standard library; no install needed.

import json
# json reads/writes the structured review output.

import os
# os reads the OPENAI_API_KEY environment variable.

import sys
# sys.exit returns non-zero on error so the GitHub Action knows it failed.

from pathlib import Path
# Path is a clean way to read files from disk. More reliable than open() strings.

from openai import OpenAI
# The OpenAI Python SDK. Installed via the workflow step's `pip install openai`.


# ──────────────────────────────────────────────────────────────────────
# MAIN FUNCTION — start reading here
# ──────────────────────────────────────────────────────────────────────

def main() -> None:
    """
    WHAT — Orchestrates: parse args, build context, call Codex, write JSON.
    WHEN — Called once per PR by pr-codex-review.yml.
    WHY  — We want ONE script per concern; this one ONLY does the API call
           and JSON output. Posting comments is a separate script.
    """
    # Parse the command-line flags the workflow passes in
    args = parse_arguments()

    # Verify the API key is set — fail fast with a clear error if missing
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY env var is empty. Set it via "
              "secrets.OPENAI_CODEX_API_KEY in the workflow.", file=sys.stderr)
        sys.exit(1)

    # Read the PR diff into memory (small, < 1 MB typical)
    diff_text = Path(args.diff).read_text()

    # Read each context file (CONSTRAINTS, scope, prompt) into memory
    context_files = read_context_files(args.context_dir)

    # Assemble the messages we send to Codex (system + user)
    messages = assemble_messages(
        diff_text=diff_text,
        context_files=context_files,
        pr_number=args.pr_number,
        pr_branch=args.pr_branch,
        pr_title=args.pr_title,
    )

    # The actual Codex call — this is where the API spend happens
    client = OpenAI(api_key=api_key)
    review_json = call_codex(client, messages)

    # Write the structured review to disk for post-codex-review.py to read
    Path(args.output).write_text(json.dumps(review_json, indent=2))
    print(f"✅ Codex review written to {args.output}")
    print(f"   Overall verdict: {review_json.get('overall', 'unknown')}")


# ──────────────────────────────────────────────────────────────────────
# HELPER FUNCTIONS (in priority order)
# ──────────────────────────────────────────────────────────────────────

def parse_arguments() -> argparse.Namespace:
    """
    WHAT — Parses --diff, --context-dir, --pr-number, --pr-branch, --pr-title, --output.
    WHEN — Called once at the start of main().
    """
    # Set up the argument parser with descriptions
    parser = argparse.ArgumentParser(
        description="Call OpenAI Codex to review a PR diff against our constraints."
    )
    parser.add_argument("--diff", required=True,
                        help="Path to the PR diff file (git diff format).")
    parser.add_argument("--context-dir", required=True,
                        help="Directory containing CONSTRAINTS.md + prompt + other context.")
    parser.add_argument("--pr-number", required=True,
                        help="GitHub PR number (e.g., 42).")
    parser.add_argument("--pr-branch", required=True,
                        help="PR branch name (e.g., session-1/sentry-baseline).")
    parser.add_argument("--pr-title", required=True,
                        help="PR title (1-line summary from author).")
    parser.add_argument("--output", required=True,
                        help="Where to write the structured review JSON.")
    return parser.parse_args()


def read_context_files(context_dir: str) -> dict[str, str]:
    """
    WHAT — Reads every file in context_dir into a name→contents dict.
    WHEN — Called once before assembling the prompt messages.
    WHY  — Codex needs the constraints, the doc standard, and the prompt
           text to do its job. We bundle them all up here.
    """
    # Walk the directory; only read .md and .txt files (skip binaries)
    context = {}
    for file_path in Path(context_dir).iterdir():
        if file_path.suffix in {".md", ".txt"}:
            context[file_path.name] = file_path.read_text()
    return context


def assemble_messages(
    diff_text: str,
    context_files: dict[str, str],
    pr_number: str,
    pr_branch: str,
    pr_title: str,
) -> list[dict]:
    """
    WHAT — Builds the list of messages to send to OpenAI's chat completions API.
    WHEN — Called once before the API call.
    WHY  — We use the system role for Codex's persona/prompt and the user role
           for the actual diff + PR metadata. Industry-standard ChatGPT API shape.
           Also enforces a token budget so we don't blow past OpenAI Tier 1's
           30k TPM limit on gpt-4o (~120k chars budget for whole prompt).
    """
    # The system prompt is Codex's "job description" — what kind of reviewer it is
    system_prompt = context_files.get("codex-prompt.txt",
        "You are a code reviewer. Return JSON.")

    # Token budget for whole prompt (system + user). Tier 1 cap is 30k TPM.
    # Conservative: aim for ~20k tokens total (~80k chars) so we stay well under.
    MAX_PROMPT_CHARS = 80_000

    # Reserve room for system prompt + PR metadata + context files headers
    fixed_overhead_chars = len(system_prompt) + 2_000

    # Reserve room for context files (CONSTRAINTS, scope doc, etc.). If they're
    # huge, truncate each to a max so the diff still has room.
    context_budget_chars = MAX_PROMPT_CHARS - fixed_overhead_chars - 20_000  # 20k for diff
    per_context_max = max(2_000, context_budget_chars // max(1, len(context_files)))

    # Build the user message: PR metadata + constraints + diff
    user_parts = [
        f"# PR being reviewed",
        f"- Number: {pr_number}",
        f"- Branch: {pr_branch}",
        f"- Title: {pr_title}",
        "",
        "# Context files (read these to understand the project's rules)",
    ]

    # Append every context file (CONSTRAINTS, scope doc, etc.) — truncated if needed
    for name, contents in context_files.items():
        if name == "codex-prompt.txt":
            continue  # already in system prompt
        truncated = truncate_with_marker(contents, per_context_max,
                                         f"context file {name}")
        user_parts.append(f"\n## {name}\n\n{truncated}")

    # Compute remaining budget for the diff
    so_far = sum(len(p) for p in user_parts)
    diff_budget = MAX_PROMPT_CHARS - fixed_overhead_chars - so_far

    # Truncate the diff if it's too big to fit
    diff_to_send = truncate_diff_smart(diff_text, diff_budget)

    # Finally, the diff — this is the actual code under review
    user_parts.append("\n# DIFF UNDER REVIEW\n")
    user_parts.append("```diff")
    user_parts.append(diff_to_send)
    user_parts.append("```")

    user_message = "\n".join(user_parts)

    # Final size check + log so we can debug if Codex still rejects
    total_chars = len(system_prompt) + len(user_message)
    print(f"Prompt size: {total_chars} chars (~{total_chars // 4} tokens)")

    # Standard OpenAI chat-completion message format
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]


def truncate_with_marker(text: str, max_chars: int, label: str) -> str:
    """
    WHAT — Truncates text to max_chars, leaving a clear marker so Codex
           knows it was truncated (and what to ask about if needed).
    WHEN — Called for any context file or diff that exceeds budget.
    WHY  — Without a marker, Codex might assume the partial content is
           the full content. The marker tells Codex "there's more we
           cut for token-budget reasons."
    """
    if len(text) <= max_chars:
        return text
    keep = max_chars - 200  # leave room for the truncation marker
    return (
        text[:keep]
        + f"\n\n... [TRUNCATED at {keep} chars / {len(text)} total — "
        f"{label} too large for token budget; review the trimmed portion above] ..."
    )


def truncate_diff_smart(diff_text: str, max_chars: int) -> str:
    """
    WHAT — Smarter diff truncation: prefer file headers + first N lines per file
           over raw cut-at-byte-N (which loses critical context like file names).
    WHEN — Called when the full diff exceeds the token budget.
    WHY  — On big PRs (like the foundational PR #1 with 50+ files), Codex
           reviewing only the first 20k chars of raw diff would miss most
           file names. Better to summarize: file list + truncated bodies.
    """
    if len(diff_text) <= max_chars:
        return diff_text

    # Split diff into per-file chunks (each starts with `diff --git`)
    chunks = []
    current_chunk = []
    for line in diff_text.split("\n"):
        if line.startswith("diff --git ") and current_chunk:
            chunks.append("\n".join(current_chunk))
            current_chunk = [line]
        else:
            current_chunk.append(line)
    if current_chunk:
        chunks.append("\n".join(current_chunk))

    if not chunks:
        # Fallback: no `diff --git` markers — just truncate raw
        return truncate_with_marker(diff_text, max_chars, "diff (no file boundaries)")

    # Distribute budget across chunks (with per-chunk minimum so file headers survive)
    per_chunk_max = max(800, max_chars // len(chunks))
    truncated_chunks = []
    for chunk in chunks:
        truncated_chunks.append(truncate_with_marker(chunk, per_chunk_max,
                                                    "file diff body"))

    result = "\n".join(truncated_chunks)
    summary = (
        f"## NOTE TO REVIEWER\n"
        f"This PR contains {len(chunks)} file(s). Each file's diff was "
        f"truncated to ~{per_chunk_max} chars to fit OpenAI's TPM budget. "
        f"Total diff was {len(diff_text)} chars; sent {len(result)}. "
        f"Review file boundaries + visible portions; flag if you need more.\n\n"
    )
    return summary + result


def call_codex(client: OpenAI, messages: list[dict]) -> dict:
    """
    WHAT — Calls OpenAI's chat completions API; parses JSON from the response.
    WHEN — Called once per PR review.
    WHY  — Codex returns prose; we ask it to return strict JSON via the
           response_format parameter so post-codex-review.py can parse it cleanly.
    """
    # Use the newest model — per Rishi (2026-04-30): "get the best model".
    # gpt-5.5 is the latest as of build time (released ~6 days ago per
    # OpenAI dashboard). More expensive per call (~$0.50-$2 per review)
    # but better reasoning + larger context window. If gpt-5.5 ever gets
    # deprecated or renamed, fallback options: "gpt-5", "gpt-4o".
    # gpt-5.5 does NOT support custom temperature (only default value=1).
    # Newer reasoning models lock this param. Omit it; rely on the prompt's
    # explicit "be concise, return JSON" instructions for consistency.
    response = client.chat.completions.create(
        model="gpt-5.5",
        messages=messages,
        response_format={"type": "json_object"},  # forces JSON output
    )

    # Extract the JSON content from the response
    content = response.choices[0].message.content

    try:
        # Parse the JSON Codex returned
        return json.loads(content)
    except json.JSONDecodeError as exc:
        # If Codex returns malformed JSON, fail loudly with the raw content for debugging
        print(f"ERROR: Codex returned non-JSON: {content[:500]}", file=sys.stderr)
        sys.exit(2)


# ══════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # When this script is run directly (not imported), call main()
    main()


# ══════════════════════════════════════════════════════════════════════
# RELATED FILES
# ──────────────
# - .github/workflows/pr-codex-review.yml — calls this script
# - .github/scripts/post-codex-review.py — reads our output, posts to PR
# - .github/scripts/codex-prompt.txt — Codex's system prompt
# - yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/03-CODEX-REVIEW-WORKFLOW.md — the full design
# ══════════════════════════════════════════════════════════════════════
