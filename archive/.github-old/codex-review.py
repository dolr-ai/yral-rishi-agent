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

    # Assemble the messages we send to Codex (system + user) AND track
    # whether any truncation happened (used by fail-closed guard below)
    messages, truncation_info = assemble_messages(
        diff_text=diff_text,
        context_files=context_files,
        pr_number=args.pr_number,
        pr_branch=args.pr_branch,
        pr_title=args.pr_title,
    )

    # The actual Codex call — this is where the API spend happens
    client = OpenAI(api_key=api_key)
    review_json = call_codex(client, messages)

    # FAIL-CLOSED GUARD (per Codex's I10 audit-hole flag).
    # If we truncated the diff, Codex never saw all of it — so it CANNOT
    # have audited the full set of changes. We override any "approve" to
    # "request_changes" with a clear reason so coordinator + Rishi know
    # they have to do a full manual review.
    review_json = enforce_truncation_fail_closed(review_json, truncation_info)

    # Write the structured review to disk for post-codex-review.py to read
    Path(args.output).write_text(json.dumps(review_json, indent=2))
    print(f"✅ Codex review written to {args.output}")
    print(f"   Overall verdict: {review_json.get('overall', 'unknown')}")
    if truncation_info["diff_truncated"] or truncation_info["context_truncated"]:
        print(f"   Truncation occurred — fail-closed guard applied: "
              f"{truncation_info}")


def enforce_truncation_fail_closed(
    review_json: dict, truncation_info: dict
) -> dict:
    """
    WHAT — If any part of the diff or context was truncated, override any
           Codex `approve` verdict to `request_changes` with a clear reason.
    WHEN — Called once after Codex returns a verdict.
    WHY  — Per Codex's own I10 review of this script: truncation creates an
           audit hole — Codex could approve a PR with bugs hidden in cut
           hunks. Fail-closed means coordinator + Rishi MUST manually review
           any PR where truncation happened. Cost of false-positive is one
           extra manual review; cost of false-negative is shipping a bug.
    """
    if not truncation_info.get("diff_truncated"):
        # No truncation — Codex saw everything; trust its verdict
        return review_json

    original_overall = review_json.get("overall", "unknown")
    if original_overall == "approve":
        # The dangerous case — override to request_changes
        original_summary = review_json.get("summary", "")
        review_json["overall"] = "request_changes"
        review_json["summary"] = (
            f"FAIL-CLOSED GUARD: diff was truncated to fit Codex's token "
            f"budget (original {truncation_info['original_diff_chars']} chars, "
            f"sent within {truncation_info['diff_budget_chars']} char budget). "
            f"Codex did not see all changes, so cannot APPROVE per I10. "
            f"Manual coordinator + Rishi review required, OR split the PR "
            f"into smaller pieces. Original Codex verdict (advisory only): "
            f"{original_overall}. Original summary: {original_summary}"
        )
        # Insert a synthetic blocker finding at the top so it shows up
        # prominently in the PR comments
        findings = review_json.get("findings", [])
        findings.insert(0, {
            "file": "(workflow)",
            "line": 0,
            "severity": "blocker",
            "category": "audit_hole",
            "issue": (
                "Diff was truncated for token budget; Codex review is "
                "incomplete. Cannot approve per I10."
            ),
            "suggestion": (
                "Either (a) split this PR into smaller chunks until each "
                "fits within the Codex token budget, or (b) coordinator + "
                "Rishi conduct a manual full-diff review and use admin "
                "override."
            ),
        })
        review_json["findings"] = findings
    return review_json


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
) -> tuple[list[dict], dict]:
    """
    WHAT — Builds the list of messages to send to OpenAI's chat completions API.
           Returns (messages, truncation_info) so callers can fail-closed if
           any truncation occurred.
    WHEN — Called once before the API call.
    WHY  — We use the system role for Codex's persona/prompt and the user role
           for the actual diff + PR metadata. Industry-standard ChatGPT API shape.
           Enforces a strict token budget AND prioritizes critical context
           (CONSTRAINTS.md never truncated) so Codex always has the binding
           rules it needs to flag scope violations.

    Returns:
      messages: list[dict] in OpenAI chat format
      truncation_info: dict with keys:
        - diff_truncated: bool — True if any part of the diff was cut
        - context_truncated: bool — True if any non-critical context was cut
        - critical_truncated: bool — True if critical context overflowed
                                      (this triggers a hard-failure upstream)
    """
    # The system prompt is Codex's "job description" — never truncated
    system_prompt = context_files.get("codex-prompt.txt",
        "You are a code reviewer. Return JSON.")

    # Total prompt budget in CHARACTERS — leaves room under OpenAI's TPM cap.
    # Configurable via env var per C7 (no hardcoded thresholds in code).
    # Default 80,000 chars (~20,000 tokens) keeps us safely under Tier 1's
    # 30k TPM cap on gpt-5.5 / gpt-4o with margin for the response.
    # Renamed from prior MAX_PROMPT_CHARS (B2 banned abbreviation `CHARS`).
    maximum_prompt_characters = int(
        os.environ.get("CODEX_REVIEW_MAX_PROMPT_CHARACTERS", "80000")
    )

    # CRITICAL context that must NEVER be truncated (per Codex's flag — these
    # files contain the binding rules; truncating them defeats the review).
    # If any of these alone blows the budget, Codex review fails loudly so
    # we know to redesign rather than silently shipping a partial review.
    CRITICAL_CONTEXT = {"CONSTRAINTS.md", "01-SESSION-SHARDING-AND-OWNERSHIP.md"}

    # Build the user message header
    user_parts = [
        f"# PR being reviewed",
        f"- Number: {pr_number}",
        f"- Branch: {pr_branch}",
        f"- Title: {pr_title}",
        "",
        "# Context files (read these to understand the project's rules)",
    ]

    # Pass 1: include critical context untruncated (sets the floor budget usage)
    used_chars = len(system_prompt) + sum(len(p) for p in user_parts) + 1_000
    for name, contents in context_files.items():
        if name == "codex-prompt.txt":
            continue
        if name in CRITICAL_CONTEXT:
            user_parts.append(f"\n## {name}\n\n{contents}")
            used_chars += len(contents) + 50

    # Track if critical context overflowed — this is a hard-fail upstream
    critical_truncated = used_chars > maximum_prompt_characters

    # Pass 2: budget remaining for non-critical context + diff
    remaining_budget = maximum_prompt_characters - used_chars
    diff_reserved = max(8_000, remaining_budget // 2)  # at least 8k for diff
    other_context_budget = max(0, remaining_budget - diff_reserved)

    # Distribute non-critical context across the remaining budget
    context_truncated = False
    non_critical = [(n, c) for n, c in context_files.items()
                    if n != "codex-prompt.txt" and n not in CRITICAL_CONTEXT]
    if non_critical:
        per_other_max = max(500, other_context_budget // len(non_critical))
        for name, contents in non_critical:
            truncated, was_trunc = truncate_with_marker(
                contents, per_other_max, f"context file {name}")
            if was_trunc:
                context_truncated = True
            user_parts.append(f"\n## {name}\n\n{truncated}")
            used_chars += min(len(contents), per_other_max) + 50

    # Whatever remains is for the diff (with a hard floor of 4k chars)
    diff_budget = max(4_000, maximum_prompt_characters - used_chars)
    diff_to_send, diff_truncated = truncate_diff_smart(diff_text, diff_budget)

    # Append the diff
    user_parts.append("\n# DIFF UNDER REVIEW\n")
    user_parts.append("```diff")
    user_parts.append(diff_to_send)
    user_parts.append("```")

    user_message = "\n".join(user_parts)

    # Final size + sanity log
    total_chars = len(system_prompt) + len(user_message)
    print(f"Prompt size: {total_chars} chars (~{total_chars // 4} tokens). "
          f"CRITICAL untruncated; non-critical budget {other_context_budget}; "
          f"diff budget {diff_budget}; "
          f"diff_truncated={diff_truncated}; context_truncated={context_truncated}.")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    truncation_info = {
        "diff_truncated": diff_truncated,
        "context_truncated": context_truncated,
        "critical_truncated": critical_truncated,
        "original_diff_chars": len(diff_text),
        "diff_budget_chars": diff_budget,
    }
    return messages, truncation_info


def truncate_with_marker(text: str, max_chars: int, label: str) -> tuple[str, bool]:
    """
    WHAT — Truncates text to max_chars, leaving a clear TRUNCATED marker.
           Returns (truncated_text, was_truncated_flag).
    WHEN — Called for any context file or diff exceeding its share of budget.
    WHY  — Without a marker, Codex might think the partial content is the
           full content. The marker tells Codex "more was cut; flag if
           you need it." The boolean flag is consumed upstream by the
           fail-closed truncation guard (per Codex's I10 audit-hole flag).
    """
    if max_chars <= 0:
        return (f"[OMITTED — no budget for {label}]", True)
    if len(text) <= max_chars:
        return (text, False)
    keep = max(50, max_chars - 200)  # leave room for the truncation marker
    truncated = (
        text[:keep]
        + f"\n\n... [TRUNCATED at {keep} chars / {len(text)} total — "
        f"{label} too large for token budget; review the trimmed portion above] ..."
    )
    return (truncated, True)


def truncate_diff_smart(diff_text: str, max_chars: int) -> tuple[str, bool]:
    """
    WHAT — Truncates a multi-file git diff to a hard total char budget while
           preserving file boundaries. Returns (truncated_text, was_truncated).
    WHEN — Called when the diff exceeds its share of the prompt budget.
    WHY  — On huge PRs we want Codex to at least see file names + a sample
           of each file's diff. The sum of per-file budgets must NEVER
           exceed the total budget. The was_truncated flag is consumed by
           the fail-closed truncation guard so Codex cannot APPROVE a PR
           where it never saw the full diff (Codex I10 audit-hole flag).
    """
    if max_chars <= 0:
        return (
            f"[DIFF OMITTED — no budget remaining; total diff is {len(diff_text)} chars]",
            True,
        )
    if len(diff_text) <= max_chars:
        return (diff_text, False)

    # Split diff into per-file chunks (each starts with `diff --git`)
    chunks: list[str] = []
    current_chunk: list[str] = []
    for line in diff_text.split("\n"):
        if line.startswith("diff --git ") and current_chunk:
            chunks.append("\n".join(current_chunk))
            current_chunk = [line]
        else:
            current_chunk.append(line)
    if current_chunk:
        chunks.append("\n".join(current_chunk))

    if not chunks:
        # No file markers — just truncate raw
        return truncate_with_marker(diff_text, max_chars, "diff (no file boundaries)")

    # Header text describing the truncation (counts toward budget)
    header = (
        f"## NOTE TO REVIEWER\n"
        f"This PR contains {len(chunks)} file(s). Each file's diff is truncated "
        f"below to keep total prompt under {max_chars} chars (full diff: "
        f"{len(diff_text)} chars). Review file boundaries + visible portions; "
        f"flag if you need more. The fail-closed guard upstream will force "
        f"`request_changes` on this review since truncation occurred.\n\n"
    )
    body_budget = max(0, max_chars - len(header))

    # CORRECT math: per_chunk = body_budget / n_chunks, no minimum overflow.
    # Floor of 50 chars per chunk so file path is visible; if even 50/chunk
    # overflows budget, drop the lowest-priority chunks.
    per_chunk_max = max(50, body_budget // max(1, len(chunks)))
    truncated_chunks: list[str] = []
    if per_chunk_max * len(chunks) > body_budget:
        # Even at 50 chars/chunk we'd overflow. Keep only as many chunks
        # as fit; show file list for the rest.
        n_keep = max(1, body_budget // 50)
        kept = chunks[:n_keep]
        dropped_files = [
            (dropped.split("\n", 1)[0] if dropped else "(empty)")
            for dropped in chunks[n_keep:]
        ]
        for chunk in kept:
            chunk_text, _was_trunc = truncate_with_marker(chunk, per_chunk_max,
                                                          "file diff body")
            truncated_chunks.append(chunk_text)
        if dropped_files:
            visible = ", ".join(dropped_files[:20])
            ellipsis = "..." if len(dropped_files) > 20 else ""
            truncated_chunks.append(
                f"\n... [{len(dropped_files)} more file(s) DROPPED for budget; "
                f"file list: {visible}{ellipsis}]"
            )
    else:
        for chunk in chunks:
            chunk_text, _was_trunc = truncate_with_marker(chunk, per_chunk_max,
                                                          "file diff body")
            truncated_chunks.append(chunk_text)

    result = header + "\n".join(truncated_chunks)
    # Final clamp: if floating-point or off-by-one nudges us over, hard-cap
    if len(result) > max_chars:
        result = result[: max_chars - 100] + "\n\n... [HARD-CAPPED at total budget]"
    return (result, True)


# Model preference order (newest first). Per Rishi (2026-04-30): "best model".
# Each fallback step handles its own quirks (e.g. older models support
# custom temperature; newer reasoning models reject it).
#
# Override at runtime via env var CODEX_MODEL_PREFERENCE (comma-separated).
DEFAULT_MODEL_PREFERENCES: list[str] = ["gpt-5.5", "gpt-5", "gpt-4o"]


def call_codex(client: OpenAI, messages: list[dict]) -> dict:
    """
    WHAT — Calls OpenAI chat completions; parses JSON; falls back across
           model preferences if the preferred model is unavailable.
    WHEN — Called once per PR review.
    WHY  — Per Codex's own flag: hard-switching to a single model is brittle.
           This implementation tries gpt-5.5 first (best reasoning), falls
           back to gpt-5 (next best), then gpt-4o (battle-tested) on:
             - NotFoundError (model name unrecognized)
             - BadRequestError with message about unsupported parameter
           Other errors (rate limit, timeout, etc.) bubble up — they're
           NOT model-specific and retry-on-different-model wouldn't help.
    """
    import openai  # imported here so the fallback class refs are local

    # Allow override via env var (CODEX_MODEL_PREFERENCE=gpt-5.5,gpt-5,gpt-4o)
    override = os.environ.get("CODEX_MODEL_PREFERENCE", "").strip()
    if override:
        models = [m.strip() for m in override.split(",") if m.strip()]
    else:
        models = DEFAULT_MODEL_PREFERENCES

    last_error: Exception | None = None
    for model_name in models:
        # Build kwargs — older models accept temperature; newer models reject
        # custom temperature. Try without first; only retry with temperature
        # if the API explicitly demands it.
        kwargs = {
            "model": model_name,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        try:
            print(f"Trying Codex review with model={model_name}...")
            response = client.chat.completions.create(**kwargs)
            print(f"✅ Got Codex response from {model_name}")
        except openai.NotFoundError as exc:
            # Model name not recognized by OpenAI account — try next
            print(f"Model {model_name} not found; trying next preference. ({exc})",
                  file=sys.stderr)
            last_error = exc
            continue
        except openai.BadRequestError as exc:
            # Most often: unsupported param. We already minimized our params,
            # so this likely means the model variant has stricter requirements.
            # Try next preference rather than guessing what to remove.
            print(f"Model {model_name} bad request; trying next preference. ({exc})",
                  file=sys.stderr)
            last_error = exc
            continue
        # If we got here, the call succeeded — extract + parse JSON
        content = response.choices[0].message.content
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            print(f"ERROR: {model_name} returned non-JSON: {content[:500]}",
                  file=sys.stderr)
            sys.exit(2)

    # All models exhausted
    print(f"ERROR: all {len(models)} model preferences failed. Last error: {last_error}",
          file=sys.stderr)
    sys.exit(3)


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
