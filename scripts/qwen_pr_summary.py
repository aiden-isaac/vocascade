#!/usr/bin/env python3
"""Post a "dumb" draft summary comment on open PRs using the local Qwen.

Draft-only by design: this never gates merges, approves, reviews, or applies
code. The model is asked to *describe* the diff, not judge it — so a wrong
answer is ignorable text. Runs on the hermes server via cron in the off-peak
window. Reuses the project's LLM_BASE_URL convention + the gh CLI; no new deps.

  python scripts/qwen_pr_summary.py            # summarize all open PRs missing one
  python scripts/qwen_pr_summary.py --pr 170 --dry-run
  python scripts/qwen_pr_summary.py --selftest
"""
import argparse
import json
import os
import subprocess

from openai import OpenAI

# GH_REPO wins; CI provides GITHUB_REPOSITORY automatically.
REPO = os.getenv("GH_REPO") or os.getenv("GITHUB_REPOSITORY", "")
MARKER = "<!-- qwen-summary -->"
DIFF_BUDGET = 12000  # chars of diff sent to the model
PROMPT = (
    "Describe in 5 bullets or fewer what this pull request changes. "
    "Plain factual description only. Do NOT judge correctness, do NOT review, "
    "do NOT suggest fixes.\n\nDIFF:\n{diff}"
)


def gh(*args):
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout


def open_prs():
    out = gh("pr", "list", "-R", REPO, "--state", "open", "--json", "number,title")
    return json.loads(out)


def already_summarized(num):
    bodies = gh("pr", "view", str(num), "-R", REPO, "--json", "comments", "-q", ".comments[].body")
    return MARKER in bodies


def truncate(diff):
    if len(diff) <= DIFF_BUDGET:
        return diff
    return diff[:DIFF_BUDGET] + "\n…[diff truncated]"


def summarize(client, model, diff):
    r = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": PROMPT.format(diff=truncate(diff))}],
        temperature=0.2,
    )
    return r.choices[0].message.content.strip()


def run(num, client, model, dry_run):
    diff = gh("pr", "diff", str(num), "-R", REPO)
    summary = summarize(client, model, diff)
    body = f"🤖 Qwen draft summary — not a review, verify before trusting.\n\n{summary}\n\n{MARKER}"
    if dry_run:
        print(f"--- PR #{num} (dry-run) ---\n{body}\n")
        return
    gh("pr", "comment", str(num), "-R", REPO, "--body", body)
    print(f"posted summary on PR #{num}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pr", type=int, help="summarize one PR (default: all open without a summary)")
    ap.add_argument("--dry-run", action="store_true", help="print instead of posting")
    ap.add_argument("--selftest", action="store_true", help="run the truncation self-check and exit")
    args = ap.parse_args()

    if args.selftest:
        assert truncate("short") == "short"
        assert len(truncate("x" * (DIFF_BUDGET + 5000))) <= DIFF_BUDGET + 40
        print("selftest ok")
        return

    base_url, model = os.getenv("LLM_BASE_URL"), os.getenv("LLM_MODEL")
    if not base_url or not model or not REPO:
        raise SystemExit("LLM_BASE_URL, LLM_MODEL and GH_REPO/GITHUB_REPOSITORY must be set")
    client = OpenAI(
        base_url=base_url,
        api_key=os.getenv("LLM_API_KEY") or "none",
    )

    if args.pr:
        run(args.pr, client, model, args.dry_run)
        return
    for pr in open_prs():
        if already_summarized(pr["number"]):
            continue
        run(pr["number"], client, model, args.dry_run)


if __name__ == "__main__":
    main()
