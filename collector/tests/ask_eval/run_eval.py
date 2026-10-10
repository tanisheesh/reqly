"""Ask Reqly eval: canned questions over the load generator's demo data,
each with facts the answer must contain.

Not part of the default test run -- it needs a collector with GROQ_API_KEY
and fresh load-generator data (the scenarios are relative to when the
generator started). Run it by hand after changing prompts, tools or models:

    python tests/ask_eval/run_eval.py --url http://localhost:8000 --key demo-read-key
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

QUESTIONS = Path(__file__).with_name("questions.json")
PAUSE_SECONDS = 13  # /v1/ask allows 5 questions a minute per client


def placeholders() -> dict[str, str]:
    # Mirrors load_generator/generate.py: flask-demo v2 went out 36h before
    # the generator started (assumed to be shortly before this run).
    deploy = (datetime.now(timezone.utc) - timedelta(hours=36)).replace(minute=0, second=0, microsecond=0)
    return {"deploy_date": deploy.strftime("%Y-%m-%d")}


def ask(url: str, key: str, service: str, question: str) -> dict:
    req = urllib.request.Request(
        f"{url}/v1/ask",
        data=json.dumps({"service_name": service, "question": question}).encode(),
        headers={"Content-Type": "application/json", "X-Reqly-Key": key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.load(resp)


def check(case: dict, answer: str, values: dict[str, str]) -> list[str]:
    """Problems with the answer; empty when it passes."""
    problems = []
    for pattern in case.get("expect_all", []):
        if not re.search(pattern.format(**values), answer, re.IGNORECASE):
            problems.append(f"missing /{pattern}/")
    any_of = case.get("expect_any", [])
    if any_of and not any(re.search(p.format(**values), answer, re.IGNORECASE) for p in any_of):
        problems.append(f"none of {any_of}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--key", default="demo-read-key")
    parser.add_argument("--only", type=int, nargs="*", help="question numbers to run (1-based)")
    parser.add_argument("--min-pass", type=float, default=0.8, help="fail below this pass rate")
    args = parser.parse_args()

    cases = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    values = placeholders()
    selected = [(i, c) for i, c in enumerate(cases, 1) if not args.only or i in args.only]
    passed = 0
    for n, (i, case) in enumerate(selected):
        if n:
            time.sleep(PAUSE_SECONDS)
        try:
            result = ask(args.url, args.key, case["service"], case["question"])
        except urllib.error.HTTPError as exc:
            print(f"[{i:2}] ERROR HTTP {exc.code}: {exc.read().decode(errors='replace')[:200]}")
            continue
        problems = check(case, result["answer"], values)
        passed += not problems
        tools = ", ".join(s["tool"] for s in result["steps"]) or "no tools"
        print(f"[{i:2}] {'PASS' if not problems else 'FAIL'} {case['service']}: {case['question']}")
        print(f"     tools: {tools}")
        if problems:
            print(f"     {'; '.join(problems)}")
        if result.get("unverified_numbers"):
            print(f"     numbers not found in the tool results: {', '.join(result['unverified_numbers'])}")
        print("     " + result["answer"].replace("\n", "\n     "))
    rate = passed / len(selected) if selected else 0
    print(f"\n{passed}/{len(selected)} passed ({rate:.0%})")
    return 0 if rate >= args.min_pass else 1


if __name__ == "__main__":
    sys.exit(main())
