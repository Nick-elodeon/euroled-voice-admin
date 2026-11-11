"""Web admin (no domain needed): calls, transcripts, voice, prompt, catalog. Login with a generated password."""
from __future__ import annotations
import hashlib
import hmac
import html
import json
import logging
import os
import secrets
import time

from aiohttp import web

import catalog

log = logging.getLogger("web")
STATE = os.environ.get("WEB_STATE", "/var/lib/euroled-admin/web.json")
E = html.escape


def load_state() -> dict:
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def ensure_secrets() -> tuple[dict, str | None]:
    """Create password, cookie secret and tool token once. Returns (state, new_password_or_None)."""
    st = load_state()
    new_pw = None
    if "pw_hash" not in st:
        new_pw = secrets.token_urlsafe(9)
        st["pw_salt"] = secrets.token_hex(8)
        st["pw_hash"] = hashlib.sha256((st["pw_salt"] + new_pw).encode()).hexdigest()
        st["cookie_key"] = secrets.token_hex(32)
        st["tool_token"] = secrets.token_urlsafe(24)
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        json.dump(st, open(STATE, "w"))
        os.chmod(STATE, 0o600)
    return st, new_pw


CSS = """body{font:15px/1.5 system-ui,sans-serif;margin:0;background:#f5f6f8;color:#1b1f24}
nav{background:#1b1f24;padding:10px 16px;display:flex;gap:16px;flex-wrap:wrap}nav a{color:#fff;text-decoration:none}
main{max-width:1000px;margin:16px auto;padding:0 12px}.card{background:#fff;border-radius:10px;padding:14px 16px;margin:12px 0;box-shadow:0 1px 3px #0001}
table{width:100%;border-collapse:collapse}td,th{padding:8px 6px;border-bottom:1px solid #eee;text-align:left;vertical-align:top}
input,select,textarea{font:inherit;padding:8px;border:1px solid #ccd;border-radius:6px;width:100%;box-sizing:border-box}
button{font:inherit;padding:8px 16px;border:0;border-radius:6px;background:#2b6cb0;color:#fff;cursor:pointer}
.kpis{display:flex;gap:12px;flex-wrap:wrap}.kpi{flex:1;min-width:120px}.kpi b{font-size:26px;display:block}
.u{background:#e8f0fe;padding:6px 10px;border-radius:8px;margin:4px 0}.a{background:#eef6ee;padding:6px 10px;border-radius:8px;margin:4px 0}
small{color:#667}"""


def page(title: str, body: str, nav: bool = True) -> web.Response:
    n = ("<nav><a href='/'>Звонки</a><a href='/voice'>Голос</a><a href='/prompt'>Промпт</a>"
         "<a href='/catalog'>Каталог</a><a href='/logout'>Выйти</a></nav>") if nav else ""
    return web.Response(text=f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                             f"<title>{E(title)}</title><style>{CSS}</style>{n}<main>{body}</main>", content_type="text/html")


def sign(key: str, ts: int) -> str:
    return f"{ts}.{hmac.new(key.encode(), str(ts).encode(), hashlib.sha256).hexdigest()}"


def make_app(eleven, state: dict) -> web.Application:
    @web.middleware
    async def auth(request, handler):
        if request.path in ("/login", "/health") or request.path.startswith("/api/"):
            return await handler(request)
        c = request.cookies.get("s", "")
        try:
            ts = int(c.split(".")[0])
            ok = hmac.compare_digest(c, sign(state["cookie_key"], ts)) and time.time() - ts < 30 * 86400
        except Exception:
            ok = False
        if not ok:
            raise web.HTTPFound("/login")
        return await handler(request)

    app = web.Application(middlewares=[auth])
    r = web.RouteTableDef()

    @r.get("/health")
    async def health(_):
        return web.Response(text="ok")

    @r.get("/login")
    async def login_form(_):
        return page("Вход", "<div class=card><h3>EUROLED, админка</h3><form method=post><input type=password name=pw placeholder=Пароль autofocus><br><br><button>Войти</button></form></div>", nav=False)

    @r.post("/login")
    async def login(request):
        d = await request.post()
        h = hashlib.sha256((state["pw_salt"] + d.get("pw", "")).encode()).hexdigest()
        if not hmac.compare_digest(h, state["pw_hash"]):
            await _sleep()
            return page("Вход", "<div class=card>Неверный пароль. <a href=/login>Ещё раз</a></div>", nav=False)
        resp = web.HTTPFound("/")
        resp.set_cookie("s", sign(state["cookie_key"], int(time.time())), httponly=True, samesite="Strict", max_age=30 * 86400)
        raise resp

    @r.get("/logout")
    async def logout(_):
        resp = web.HTTPFound("/login")
        resp.del_cookie("s")
        raise resp

    @r.get("/")
    async def home(request):
        data = await eleven.conversations(50)
        convs = data.get("conversations", [])
        total = len(convs)
        dur = sum(c.get("call_duration_secs", 0) for c in convs)
        rows = ""
        for c in convs:
            t = time.strftime("%d.%m %H:%M", time.localtime(c.get("start_time_unix_secs", 0)))
            rows += (f"<tr><td><a href='/call/{E(c['conversation_id'])}'>{t}</a></td><td>{c.get('call_duration_secs', 0)} с</td>"
                     f"<td>{c.get('message_count', 0)}</td><td>{E(str(c.get('call_successful', '')))}</td>"
                     f"<td>{E(str(c.get('call_summary_title') or ''))}</td></tr>")
        body = (f"<div class='card kpis'><div class=kpi><b>{total}</b><small>последних звонков</small></div>"
                f"<div class=kpi><b>{dur // 60}</b><small>минут разговоров</small></div></div>"
                f"<div class=card><table><tr><th>Когда<th>Длина<th>Реплик<th>Успех<th>О чём</tr>{rows}</table></div>")
        return page("Звонки", body)

    @r.get("/call/{cid}")
    async def call(request):
        cid = request.match_info["cid"]
        c = await eleven.conversation(cid)
        lines = ""
        for t in c.get("transcript", []):
            who = "u" if t.get("role") == "user" else "a"
            label = "Клиент" if who == "u" else "Бот"
            if t.get("message"):
                lines += f"<div class={who}><small>{label} · {int(t.get('time_in_call_secs') or 0)} с</small><br>{E(t['message'])}</div>"
        summ = (c.get("analysis") or {}).get("transcript_summary") or ""
        body = (f"<div class=card><h3>Звонок</h3><p>{E(summ)}</p><audio controls preload=none src='/audio/{E(cid)}' style='width:100%'></audio></div>"
                f"<div class=card>{lines or 'Нет текста'}</div>")
        return page("Звонок", body)

    @r.get("/audio/{cid}")
    async def audio(request):
        data = await eleven.audio_url_bytes(request.match_info["cid"])
        return web.Response(body=data, content_type="audio/mpeg")

    @r.get("/voice")
    async def voice(request):
        tts = await eleven.get_voice()
        f = lambda k, d="": E(str(tts.get(k, d)))
        body = (f"<div class=card><h3>Голос</h3><form method=post><label>Voice ID<input name=voice_id value='{f('voice_id')}'></label>"
                f"<label>Скорость (0.7–1.2)<input name=speed value='{f('speed', 1.0)}'></label>"
                f"<label>Стабильность (0–1)<input name=stability value='{f('stability', 0.5)}'></label>"
                f"<label>Похожесть (0–1)<input name=similarity_boost value='{f('similarity_boost', 0.8)}'></label><br>"
                f"<button>Сохранить</button></form><small>Применяется к новым звонкам.</small></div>")
        return page("Голос", body)

    @r.post("/voice")
    async def voice_save(request):
        d = await request.post()
        tts: dict = {}
        if d.get("voice_id"):
            tts["voice_id"] = d["voice_id"].strip()
        for k in ("speed", "stability", "similarity_boost"):
            try:
                tts[k] = float(d.get(k, ""))
            except ValueError:
                pass
        await eleven.set_voice(tts)
        raise web.HTTPFound("/voice")

    @r.get("/prompt")
    async def prompt(request):
        p = await eleven.get_prompt()
        body = (f"<div class=card><h3>Промпт агента</h3><form method=post><textarea name=prompt rows=24>{E(p)}</textarea><br><br>"
                f"<button>Сохранить</button></form><small>Предыдущая версия сохраняется на сервере (откат: /rollback в Telegram).</small></div>")
        return page("Промпт", body)

    @r.post("/prompt")
    async def prompt_save(request):
        d = await request.post()
        text = d.get("prompt", "").strip()
        if len(text) < 200:
            return page("Ошибка", "<div class=card>Слишком короткий промпт, не сохранил.</div>")
        old = await eleven.get_prompt()
        os.makedirs("/var/lib/euroled-admin/prompts", exist_ok=True)
        open(f"/var/lib/euroled-admin/prompts/{int(time.time())}.txt", "w").write(old)
        await eleven.set_prompt(text)
        raise web.HTTPFound("/prompt")

    @r.get("/catalog")
    async def cat(request):
        q = request.query
        st = catalog.stats()
        form = ("<form><input name=q placeholder='Слова: virtene, aizkari...' value='" + E(q.get("q", "")) + "'><br><br>"
                "<input name=length placeholder='Длина, м (например 15)' value='" + E(q.get("length", "")) + "'><br><br>"
                "<input name=max_price placeholder='Макс. цена, €' value='" + E(q.get("max_price", "")) + "'><br><br>"
                "<button>Найти</button></form>")
        res = ""
        if q:
            rows = catalog.search(q.get("q", ""), length_m=float(q["length"]) if q.get("length") else None,
                                  length_tol=3 if q.get("length") else 0,
                                  max_price=float(q["max_price"]) if q.get("max_price") else None, limit=30)
            res = "<table><tr><th>Цена<th>Товар<th>IP<th>М<th>Склад</tr>" + "".join(
                f"<tr><td>{r['price']:.2f} €<td><a href='{E(r['url'])}' target=_blank>{E(r['title'])}</a><td>{r['ip'] or ''}<td>{r['length_m'] or ''}<td>{r['stock']}</tr>"
                for r in rows) + "</table>"
        when = time.strftime("%d.%m %H:%M", time.localtime(st["updated"])) if st["updated"] else "никогда"
        return page("Каталог", f"<div class=card><b>{st['products']}</b> товаров, обновлён: {when}<br><small>Обновить: /catalog crawl в Telegram</small></div><div class=card>{form}</div><div class=card>{res}</div>")

    @r.get("/api/search")
    async def api_search(request):
        """Server tool for the voice agent. Auth: header X-Tool-Token."""
        if not hmac.compare_digest(request.headers.get("X-Tool-Token", ""), state["tool_token"]):
            raise web.HTTPUnauthorized()
        q = request.query

        def fl(k):
            try:
                return float(q[k]) if q.get(k) else None
            except ValueError:
                return None
        rows = catalog.search(q.get("query", ""), category=q.get("category", ""), length_m=fl("length_m"),
                              length_tol=fl("length_tol") or 0, max_price=fl("max_price"),
                              min_ip=int(q["min_ip"]) if q.get("min_ip", "").isdigit() else None,
                              in_stock=q.get("in_stock", "1") != "0", sort=q.get("sort", "price"),
                              limit=min(int(q.get("limit", 5)), 10) if q.get("limit", "5").isdigit() else 5)
        out = [{"name": x["title"], "price_eur": x["price"], "was_eur": x["price_old"], "stock": x["stock"],
                "length_m": x["length_m"], "ip": x["ip"], "url": x["url"], "category": x["category"]} for x in rows]
        return web.json_response({"count": len(out), "products": out})

    app.add_routes(r)
    return app


async def _sleep():
    import asyncio
    await asyncio.sleep(1.5)
