"""
Which requests still go to the LLM — and what they cost.

    .venv\\Scripts\\python.exe scripts\\router_report.py [days]

Lists recurring LLM requests (candidates for a new router rule or a routine)
and the wordings the router has already learned (config/learned.json).
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import paths  # noqa: E402,F401
import learning  # noqa: E402

days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
r = learning.report(days)
if not r.get("total"):
    sys.exit(f"No entries in the last {days} days ({learning.USAGE}).")
print(f"Last {days} days: {r['total']} requests, {r['local']} local "
      f"({round(100 * r['local'] / r['total'])} %), {r['llm']} to the LLM, ~{r['tokens']:,} tokens.\n")
print("Recurring LLM requests (candidates for a rule or a routine):")
for msg, count, tokens in r["repeat"] or []:
    print(f"  {count:3}×  {tokens:>8,} tokens  {msg}")
if not r["repeat"]:
    print("  none")
print("\nLearned wordings:")
for k, v in sorted((learning.load().get("aliases") or {}).items()):
    print(f"  {k:30} → {', '.join(v['entities'])}  (since {v.get('learned')})")
