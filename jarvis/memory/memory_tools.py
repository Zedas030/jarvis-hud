"""
Memory you can control: where a fact came from, why it is there, correct it,
forget it, see everything, export it.

Provenance: memory_save adds a short tag to each new bullet,
  "- Likes short answers _(said, 2026-10-22)_"           the user said it
  "- Probably plays golf on Sundays _(inferred, 2026-10-22)_"  Jarvis concluded it
Derived facts are treated as assumptions: the prompt shows them marked.

  overview(note)   "Show me what you have stored about me"
  why(text)        "Why did you store that?" — tag plus the conversation of that day
  forget(...)      "Forget that" — the bullet goes to Trash/Forgotten (card first)
  export()/backup  the whole vault as a zip in backups/ (daily backup keeps JARVIS_BACKUP_KEEP)

Correcting is memory_save with `replace` (existing tool).
"""
import datetime as dt
import os
import pathlib
import re
import time
import zipfile

import memory

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repository (this file: jarvis/<group>/)
BACKUP_DIR = pathlib.Path(os.environ.get("JARVIS_BACKUP_DIR", ROOT / "backups"))
BACKUP_KEEP = int(os.environ.get("JARVIS_BACKUP_KEEP", "14"))
TAG = re.compile(r"\s*_\((said|inferred|guessed), (\d{4}-\d{2}-\d{2})(?:, until (\d{4}-\d{2}-\d{2}))?\)_\s*$")
FORGOTTEN = "Forgotten"


def tag(source="inferred", day=None, until=None):
    s = str(source or "").lower()
    source = "said" if s.startswith("sa") else "guessed" if s.startswith("gu") else "inferred"
    end = ""
    if until:
        try:
            end = ", until " + dt.date.fromisoformat(str(until)[:10]).isoformat()
        except ValueError:
            end = ""
    return f" _({source}, {(day or dt.date.today()).isoformat()}{end})_"


def expiry(bullet):
    """date or None from a bullet's tag."""
    m = TAG.search(bullet)
    if not (m and m.group(3)):
        return None
    return dt.date.fromisoformat(m.group(3))


def expire_bullets(today=None):
    """Moves bullets whose 'until' date has passed to the trash note. Returns how many."""
    today = today or dt.date.today()
    moved = 0
    for path in _facts():
        gone = [b for b in _bullets(path) if (expiry(b) or today) < today]
        for b in gone:
            forget(split_tag(b)[0], _title(path))
            moved += 1
    return moved


def split_tag(bullet):
    """('text', 'said'|'inferred'|'guessed'|None, 'yyyy-mm-dd'|None)"""
    m = TAG.search(bullet)
    if not m:
        return bullet.strip(), None, None
    return bullet[:m.start()].strip(), m.group(1), m.group(2)


def for_prompt(bullet):
    """What the system prompt sees: no dates, derived facts marked as such."""
    text, source, _ = split_tag(bullet)
    return text + {"inferred": " (inferred, uncertain)", "guessed": " (guess)"}.get(source, "")


def _bullets(path):
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [ln[2:].strip() for ln in lines if ln.startswith("- ")]


def _facts():
    """Notes with lasting facts (no diaries, no trash)."""
    return [p for p in memory.MEMORY_DIR.rglob("*.md")] if memory.MEMORY_DIR.exists() else []


def _title(path):
    return path.relative_to(memory.MEMORY_DIR).with_suffix("").as_posix()


def overview(note=""):
    note = (note or "").strip()
    if note:
        path = memory.note_path(note)
        if not path.exists():
            return f"No note '{note}'."
        rows = []
        for b in _bullets(path):
            text, source, day = split_tag(b)
            rows.append(f"- {text}" + (f" [{source}, {day}]" if source else ""))
        return f"{note}:\n" + ("\n".join(rows) or "(no entries)")
    rows = []
    for path in sorted(_facts()):
        bullets = _bullets(path)
        if bullets:
            derived = sum(1 for b in bullets if split_tag(b)[1] == "inferred")
            rows.append(f"- {_title(path)}: {len(bullets)} entries" + (f", {derived} of them inferred" if derived else ""))
    return "Stored about you:\n" + "\n".join(rows) if rows else "Nothing stored yet."


def find(text):
    """[(path, bullet)] of fact bullets containing `text` (case-insensitive)."""
    want = " ".join((text or "").lower().split())
    if len(want) < 3:
        return []
    out = []
    for path in _facts():
        for b in _bullets(path):
            # saves get [[links]] added (memory_intel.link_entities); "Anna hat …" must still find "[[Anna]] hat …"
            plain = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]+)\]\]", r"\1", b)
            if want in " ".join(b.lower().split()) or want in " ".join(plain.lower().split()):
                out.append((path, b))
    return out


def why(text):
    hits = find(text)
    if not hits:
        return "I have nothing stored about that."
    if len(hits) > 3:
        return f"{len(hits)} entries match — please be more precise."
    out = []
    for path, b in hits:
        fact, source, day = split_tag(b)
        line = f"“{fact}” is in {_title(path)}"
        if not source:
            out.append(line + " (older entry without origin).")
            continue
        line += f" — {'you said it' if source == 'said' else 'a guess of mine' if source == 'guessed' else 'inferred by me'}, on {day}."
        diary = memory.DIARY_DIR / f"Conversation {day}.md"
        words = [w for w in re.split(r"\W+", fact.lower()) if len(w) > 3][:4]
        if diary.exists() and words:
            hits_d = [ln.strip() for ln in diary.read_text(encoding="utf-8", errors="replace").splitlines()
                      if ln.startswith("**") and sum(w in ln.lower() for w in words) >= min(2, len(words))]
            if hits_d:
                line += " From the conversation: " + " / ".join(h[:160] for h in hits_d[:2])
        out.append(line)
    return "\n".join(out)


def forget(text, note=""):
    """Moves matching bullets to the trash note. Refuses if the match is ambiguous."""
    hits = find(text)
    if note:
        hits = [(p, b) for p, b in hits if _title(p).lower() == note.strip().lower()]
    if not hits:
        return "I have nothing stored about that."
    if len({b for _, b in hits}) > 1:
        return ("Several entries match, I do not forget anything on a hunch:\n" +
                "\n".join(f"- {_title(p)}: {split_tag(b)[0]}" for p, b in hits[:6]))
    memory.TRASH_DIR.mkdir(parents=True, exist_ok=True)
    trash = memory.TRASH_DIR / f"{FORGOTTEN}.md"
    if not trash.exists():
        trash.write_text("---\ntype: trash\n---\n\n# Forgotten\n\nEntries forgotten on request "
                         "(deleted with the trash after 30 days).\n", encoding="utf-8")
    today = time.strftime("%Y-%m-%d")
    with trash.open("a", encoding="utf-8") as fh:
        for path, b in hits:
            fh.write(f"- {today} from {_title(path)}: {b}\n")
    for path, b in hits:
        body = path.read_text(encoding="utf-8")
        body = body.replace(f"- {b}\n", "", 1) if f"- {b}\n" in body else body.replace(f"- {b}", "", 1)
        body = re.sub(r"(?m)^updated: .*$", f"updated: {today}", body, count=1)
        path.write_text(body, encoding="utf-8")
    return f"Forgotten: “{split_tag(hits[0][1])[0]}” (from {_title(hits[0][0])})."


def export(prefix="vault-export"):
    """Zips the vault into backups/. Returns the zip path."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"{prefix}-{time.strftime('%Y-%m-%d_%H%M%S')}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for path in memory.VAULT.rglob("*"):
            if path.is_file() and ".obsidian" not in path.parts:
                z.write(path, path.relative_to(memory.VAULT).as_posix())
    return target


def backup():
    """Daily backup; keeps the newest BACKUP_KEEP. Returns the new zip or None."""
    target = export("vault-backup")
    old = sorted(BACKUP_DIR.glob("vault-backup-*.zip"))[:-BACKUP_KEEP]
    for p in old:
        p.unlink()
    return target


def start():
    import background
    import audit

    def step():
        today = time.strftime("%Y-%m-%d")
        if background.claim("vault-backup", today):
            backup()
            audit.trim()
            expire_bullets()
    background.loop("vault_backup", 3600, step, pausable=False)
