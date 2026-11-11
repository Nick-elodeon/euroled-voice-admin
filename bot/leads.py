"""Leads from finished calls: stored, shown in the admin, mailed out, and used as caller memory."""
from __future__ import annotations
import json
import os
import re
import sqlite3
import time

DB_PATH = os.environ.get("LEADS_DB", os.path.join(
    os.path.dirname(os.environ.get("CATALOG_DB", "/var/lib/euroled-admin/catalog.db")) or ".", "leads.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads(
  conv_id TEXT PRIMARY KEY, ts INTEGER, dur INTEGER, caller TEXT, caller_key TEXT, name TEXT, contact_phone TEXT,
  title TEXT, summary TEXT, fields TEXT, success TEXT, is_lead INTEGER, emailed INTEGER DEFAULT 0, created REAL);
CREATE INDEX IF NOT EXISTS i_key ON leads(caller_key, ts);
"""

FIELD_RU = [("customer_type", "Кто"), ("object_type", "Объект"), ("room_and_task", "Задача"), ("area_m2", "Площадь, м²"),
            ("ceiling_height_m", "Потолок, м"), ("products_proposed", "Предложено"), ("missing_products", "Нет в каталоге"),
            ("unanswered_questions", "Без ответа"), ("urgency", "Срочность"), ("callback_time", "Когда перезвонить"),
            ("call_language", "Язык")]


def db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def key(phone: str | None) -> str:
    """Comparable phone key: digits only, Latvian local numbers get the 371 prefix."""
    d = re.sub(r"\D", "", phone or "")
    if len(d) == 8:
        d = "371" + d
    return d


def _val(dc: dict, k: str):
    v = (dc.get(k) or {}).get("value")
    if v in (None, "", "null", "None", "unknown", "n/a"):
        return None
    return v


def extract(conv: dict) -> dict:
    meta = conv.get("metadata") or {}
    an = conv.get("analysis") or {}
    dc = an.get("data_collection_results") or {}
    pc = meta.get("phone_call") or {}
    caller = pc.get("external_number") or ((conv.get("conversation_initiation_client_data") or {})
                                           .get("dynamic_variables") or {}).get("system__caller_id") or ""
    contact = _val(dc, "customer_phone")
    name = _val(dc, "customer_name")
    fields = {k: _val(dc, k) for k, _ in FIELD_RU if _val(dc, k) is not None}
    dur = meta.get("call_duration_secs") or 0
    is_lead = int(bool(name or contact or fields.get("callback_time")))
    return {"conv_id": conv.get("conversation_id", ""), "ts": meta.get("start_time_unix_secs") or int(time.time()), "dur": dur,
            "caller": str(caller or ""), "caller_key": key(contact) or key(str(caller or "")),
            "name": str(name) if name else "", "contact_phone": str(contact) if contact else "",
            "title": an.get("call_summary_title") or "", "summary": an.get("transcript_summary") or "",
            "fields": json.dumps(fields, ensure_ascii=False), "success": an.get("call_successful") or "",
            "is_lead": is_lead}


def save(conv: dict) -> dict | None:
    """Store the call; returns the lead only when it is new."""
    d = extract(conv)
    if not d["conv_id"]:
        return None
    c = db()
    try:
        with c:
            cur = c.execute(
                "INSERT OR IGNORE INTO leads(conv_id,ts,dur,caller,caller_key,name,contact_phone,title,summary,fields,success,is_lead,created) "
                "VALUES(:conv_id,:ts,:dur,:caller,:caller_key,:name,:contact_phone,:title,:summary,:fields,:success,:is_lead,:created)",
                dict(d, created=time.time()))
        return d if cur.rowcount else None
    finally:
        c.close()


def card(d: dict) -> str:
    f = json.loads(d["fields"]) if isinstance(d["fields"], str) else d["fields"]
    head = "🔔 Новая заявка" if d["is_lead"] else "📞 Звонок без заявки"
    who = " · ".join(x for x in (d["name"], d["contact_phone"] or d["caller"]) if x)
    lines = [f"{head}, {d['dur']} с"]
    if d["title"]:
        lines.append(d["title"])
    if who:
        lines.append(f"👤 {who}")
    for k, lab in FIELD_RU:
        if k in f and k != "call_language":
            lines.append(f"• {lab}: {f[k]}")
    if d["summary"]:
        lines.append("\n" + d["summary"])
    return "\n".join(lines)[:3900]


def recent(limit: int = 100, only_leads: bool = False) -> list[dict]:
    c = db()
    rows = c.execute(f"SELECT * FROM leads {'WHERE is_lead = 1' if only_leads else ''} ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def unsent(limit: int = 20) -> list[dict]:
    c = db()
    rows = c.execute("SELECT * FROM leads WHERE is_lead = 1 AND emailed = 0 ORDER BY ts LIMIT ?", (limit,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def mark_sent(ids: list[str]) -> int:
    c = db()
    with c:
        n = sum(c.execute("UPDATE leads SET emailed = 1 WHERE conv_id = ?", (i,)).rowcount for i in ids)
    c.close()
    return n


def memory(phone: str | None) -> dict:
    """What the agent should know when this number calls again (dynamic variables)."""
    k = key(phone)
    if not k:
        return {"caller_name": "", "caller_history": "первый звонок, данных нет"}
    c = db()
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM leads WHERE caller_key = ? ORDER BY ts DESC LIMIT 3", (k,)).fetchall()]
    c.close()
    if not rows:
        return {"caller_name": "", "caller_history": "первый звонок, данных нет"}
    name = next((r["name"] for r in rows if r["name"]), "")
    last = rows[0]
    days = max(0, int((time.time() - last["ts"]) // 86400))
    f = json.loads(last["fields"] or "{}")
    bits = [f"последний звонок {days} дн. назад", last["title"] or (last["summary"] or "")[:160]]
    if f.get("products_proposed"):
        bits.append(f"предлагали: {f['products_proposed']}")
    if f.get("room_and_task"):
        bits.append(f"задача: {f['room_and_task']}")
    return {"caller_name": name, "caller_history": f"звонил {len(rows)} раз; " + "; ".join(b for b in bits if b)[:400]}
