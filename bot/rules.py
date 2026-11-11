"""Rules learned from mistakes: a note on a bad call becomes one numbered rule in the agent prompt.
Rules live in a sqlite table and in a marked block at the end of the prompt, so they can be switched off."""
from __future__ import annotations
import os
import re
import sqlite3
import time

DB_PATH = os.environ.get("RULES_DB", os.path.join(
    os.path.dirname(os.environ.get("CATALOG_DB", "/var/lib/euroled-admin/catalog.db")) or ".", "rules.db"))
START, END = "<!-- RULES:START -->", "<!-- RULES:END -->"
SCHEMA = """CREATE TABLE IF NOT EXISTS rules(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, conv_id TEXT, note TEXT, rule TEXT, active INTEGER DEFAULT 1);"""


def db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def add(conv_id: str, note: str, rule: str) -> int:
    c = db()
    cur = c.execute("INSERT INTO rules(ts, conv_id, note, rule) VALUES(?,?,?,?)",
                    (int(time.time()), conv_id, note.strip(), rule.strip()))
    c.commit()
    n = cur.lastrowid
    c.close()
    return n


def set_active(rule_id: int, active: bool) -> None:
    c = db()
    c.execute("UPDATE rules SET active=? WHERE id=?", (int(active), rule_id))
    c.commit()
    c.close()


def all_rules() -> list[sqlite3.Row]:
    c = db()
    rows = c.execute("SELECT * FROM rules ORDER BY id DESC").fetchall()
    c.close()
    return rows


def active_rules() -> list[sqlite3.Row]:
    c = db()
    rows = c.execute("SELECT * FROM rules WHERE active=1 ORDER BY id").fetchall()
    c.close()
    return rows


def block(rows) -> str:
    if not rows:
        return ""
    lines = "\n".join(f"{i}. {r['rule']}" for i, r in enumerate(rows, 1))
    return (f"{START}\n## Правила из разбора ошибок (строго соблюдай, они важнее остального текста)\n{lines}\n{END}")


def merge(prompt: str, rows) -> str:
    """Replace (or add, or remove) the rules block inside the prompt."""
    cleaned = re.sub(re.escape(START) + r".*?" + re.escape(END), "", prompt, flags=re.S).rstrip()
    b = block(rows)
    return cleaned + ("\n\n" + b if b else "") + "\n"


def to_rule(note: str) -> str:
    """Make the manager's remark into one imperative sentence. No model call: keep the words, tidy the form."""
    t = re.sub(r"\s+", " ", note).strip()
    if not t:
        return ""
    if t[-1] not in ".!?":
        t += "."
    return t[0].upper() + t[1:]
