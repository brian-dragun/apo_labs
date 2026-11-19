"""
Lab 2 take-home — explain_decision.py
======================================
A model may explain a decision. It may not change it.

This script reads the last record from the Lab 2 audit log (apo_audit.jsonl)
and turns it into the message an on-call engineer would receive: what happened,
what APO did, whether it worked, and what to check. The decision was made and
recorded before this script runs, so nothing the model writes can change it.

Models can still state things that are not true. So the script checks the
explanation against the record before showing it: every number and every
action or symptom name in the text must appear in the record. If anything
does not, the model's text is rejected and a plain template is used instead.

Usage (from the repo root, after running Lab 2 at least once):
  python3 lab2/explain_decision.py                 # template message, no model call
  python3 lab2/explain_decision.py --llm           # model writes the message, code checks it
  python3 lab2/explain_decision.py --llm --show-rejected   # also print a rejected draft

Choosing a model (only for --llm):
  Claude (default):          export ANTHROPIC_API_KEY=...
                             export APO_LLM_MODEL=claude-haiku-5-5        # optional
  Any OpenAI-compatible API: export APO_LLM_PROVIDER=openai
                             export APO_LLM_BASE_URL=https://api.example.com/v1
                             export APO_LLM_API_KEY=...                   # if the endpoint needs one
                             export APO_LLM_MODEL=<model name>
  A local model (no key):    export APO_LLM_PROVIDER=openai
                             export APO_LLM_BASE_URL=http://localhost:11434/v1   # Ollama
                             export APO_LLM_MODEL=llama3.2
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

try:
    import requests
except ImportError:  # requests is in requirements.txt; only needed for --llm
    requests = None

DEFAULT_AUDIT = Path("apo_audit.jsonl")
TIMEOUT_S = 30
# Some models reason before answering, and that reasoning counts against this budget.
# 400 tokens was not enough in testing; the visible message itself is short.
MAX_TOKENS = 2000

SYSTEM_PROMPT = """You write the notification an on-call engineer receives after an
automated remediation system has acted, or declined to act.

Rules:
- Use only facts present in the JSON record you are given. Do not add numbers,
  causes, services or actions that are not in it.
- Plain language, 3 to 5 sentences, no headings, no bullet points.
- Say what was detected, what the system did (or why it did not act), whether
  it worked, and what the engineer should check next.
- Do not speculate about root cause beyond the symptom class in the record."""

NUM_RE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w])")
NAME_RE = re.compile(r"\b(?:[a-z]+\.[a-z_]+|[A-Z][A-Z_]{4,})\b")


# ---------------------------------------------------------------- record

def load_last_record(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"No audit log at {path}. Run Lab 2 first, for example:\n"
                 f"  python3 lab2/solution/autonomous_agent.py --simulate")
    last = None
    with open(path) as f:
        for line in f:
            if line.strip():
                last = line
    if last is None:
        sys.exit(f"{path} is empty. Run Lab 2 first.")
    return json.loads(last)


def template_message(r: dict) -> str:
    """Deterministic fallback. Built only from the record, so it is always grounded."""
    c = (r.get("confidence") or {}).get("C")
    conf = f" at confidence {c:g}" if isinstance(c, (int, float)) else ""
    head = f"{r.get('symptom_class', 'An incident')} detected on {r.get('deployment')} in {r.get('namespace')}{conf}."
    outcome = r.get("outcome")
    if outcome == "GATE_BLOCKED":
        body = (f"{r.get('action_id')} was proposed but blocked by the gate "
                f"({', '.join(r.get('gate_blocked_by') or []) or 'see record'}). No action was taken; please review.")
    elif outcome == "ESCALATED" and not r.get("execution"):
        body = f"Confidence was too low to act on {r.get('action_id')}, so this was escalated to you. No action was taken."
    elif outcome == "EXECUTION_FAILED":
        err = (r.get("execution") or {}).get("error") or "unknown error"
        body = f"{r.get('action_id')} was attempted and failed: {err}. Please take over."
    else:
        v = r.get("verification") or {}
        if v.get("recovered"):
            body = (f"{r.get('action_id')} was executed and recovery was verified after "
                    f"{v.get('elapsed_seconds')} s (P90 {v.get('final_p90_ms')} ms, threshold {v.get('threshold_ms')} ms). "
                    f"No action needed; check whether this recurs.")
        else:
            body = (f"{r.get('action_id')} was executed but recovery was not confirmed "
                    f"(P90 {v.get('final_p90_ms')} ms, threshold {v.get('threshold_ms')} ms). Please investigate.")
    return f"{head} {body}"


# ---------------------------------------------------------------- grounding check

def allowed_facts(record: dict) -> tuple[set[str], set[str]]:
    text = json.dumps(record)
    nums: set[str] = set()
    for tok in NUM_RE.findall(text):
        x = float(tok)
        nums |= {tok, f"{x:g}", f"{x:.1f}", f"{x:.2f}", str(round(x))}
        if 0 < x <= 1:  # fractions may be written as percentages
            nums |= {f"{x * 100:g}", f"{x * 100:.1f}", str(round(x * 100))}
        if x >= 1000:  # milliseconds may be written as seconds
            nums |= {f"{x / 1000:g}", f"{x / 1000:.1f}"}
    names = set(NAME_RE.findall(text))
    return nums, names


def check_grounding(explanation: str, record: dict) -> list[str]:
    """Return the claims in the explanation that the record does not support."""
    nums, names = allowed_facts(record)
    unsupported = []
    for tok in NUM_RE.findall(explanation):
        if tok not in nums and f"{float(tok):g}" not in nums:
            unsupported.append(tok)
    for name in NAME_RE.findall(explanation):
        if name not in names:
            unsupported.append(name)
    return sorted(set(unsupported))


# ---------------------------------------------------------------- model back ends

def ask_model(record: dict) -> tuple[str, str]:
    if requests is None:
        raise RuntimeError("the requests package is not installed")
    provider = os.environ.get("APO_LLM_PROVIDER", "anthropic").lower()
    user = "Audit record:\n" + json.dumps(record, indent=2)

    if provider == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        model = os.environ.get("APO_LLM_MODEL", "claude-haiku-5-5")
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": model, "max_tokens": MAX_TOKENS, "system": SYSTEM_PROMPT,
                  "messages": [{"role": "user", "content": user}]},
            timeout=TIMEOUT_S,
        )
        resp.raise_for_status()
        j = resp.json()
        text = "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text").strip()
        if not text:
            types = [b.get("type") for b in j.get("content", [])]
            raise RuntimeError(f"no text in the reply (stop_reason={j.get('stop_reason')}, blocks={types}); "
                               f"if stop_reason is max_tokens, the model used its budget thinking — raise MAX_TOKENS")
        return text, f"anthropic:{model}"

    if provider == "openai":
        base = os.environ.get("APO_LLM_BASE_URL")
        model = os.environ.get("APO_LLM_MODEL")
        if not base or not model:
            raise RuntimeError("set APO_LLM_BASE_URL and APO_LLM_MODEL for an OpenAI-compatible endpoint")
        headers = {"content-type": "application/json"}
        if os.environ.get("APO_LLM_API_KEY"):
            headers["authorization"] = f"Bearer {os.environ['APO_LLM_API_KEY']}"
        resp = requests.post(
            base.rstrip("/") + "/chat/completions",
            headers=headers,
            json={"model": model, "max_tokens": MAX_TOKENS, "temperature": 0,
                  "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                               {"role": "user", "content": user}]},
            timeout=TIMEOUT_S,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip(), f"openai-compatible:{model}"

    raise RuntimeError(f"unknown APO_LLM_PROVIDER {provider!r} (use anthropic or openai)")


# ---------------------------------------------------------------- main

def main() -> None:
    p = argparse.ArgumentParser(description="Lab 2 take-home: explain an APO decision, checked against the record.")
    p.add_argument("--audit", type=Path, default=DEFAULT_AUDIT, help="path to the audit log (default: apo_audit.jsonl)")
    p.add_argument("--llm", action="store_true", help="let a model write the message (see the docstring for setup)")
    p.add_argument("--show-rejected", action="store_true", help="print the model's draft even if the check rejects it")
    a = p.parse_args()

    record = load_last_record(a.audit)
    print("\n" + "=" * 65)
    print("LAB 2 TAKE-HOME — A MODEL MAY EXPLAIN A DECISION, NOT CHANGE IT")
    print("=" * 65)
    print(f"Record: run {record.get('run_id')} · {record.get('action_id')} · outcome {record.get('outcome')}\n")

    source, message = "template", template_message(record)

    if a.llm:
        try:
            draft, source_name = ask_model(record)
            problems = check_grounding(draft, record)
            if not draft:
                print("Model returned no text. Using the template.\n")
            elif problems:
                print(f"Grounding check FAILED. Not in the record: {', '.join(problems)}")
                print("Using the template instead.\n")
                if a.show_rejected:
                    print("Rejected draft:\n" + draft + "\n")
            else:
                print("Grounding check passed: every number and name appears in the record.\n")
                source, message = source_name, draft
        except Exception as e:  # noqa: BLE001  any failure falls back to the template
            print(f"Model unavailable ({e}). Using the template.\n")

    print(f"Message for the on-call engineer  [{source}]")
    print("-" * 65)
    print(message)
    print()


if __name__ == "__main__":
    main()
