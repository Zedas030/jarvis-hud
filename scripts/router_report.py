"""
Which requests still go to the LLM — and what they cost.

    .venv\\Scripts\\python.exe scripts\\router_report.py [Tage]

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
    sys.exit(f"Keine Einträge in den letzten {days} Tagen ({learning.USAGE}).")
print(f"Letzte {days} Tage: {r['total']} Anfragen, {r['local']} lokal "
      f"({round(100 * r['local'] / r['total'])} %), {r['llm']} ans LLM, ~{r['tokens']:,} Tokens.\n".replace(",", "."))
print("Wiederkehrende LLM-Anfragen (Kandidaten für eine Regel oder Routine):")
for msg, count, tokens in r["repeat"] or []:
    print(f"  {count:3}×  {tokens:>8,} Tokens  {msg}".replace(",", "."))
if not r["repeat"]:
    print("  keine")
print("\nGelernte Formulierungen:")
for k, v in sorted((learning.load().get("aliases") or {}).items()):
    print(f"  {k:30} → {', '.join(v['entities'])}  (seit {v.get('learned')})")
