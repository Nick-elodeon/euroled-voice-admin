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
import ui
from ui import E, page

log = logging.getLogger("web")
STATE = os.environ.get("WEB_STATE", "/var/lib/euroled-admin/web.json")


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

def sign(key: str, ts: int) -> str:
    return f"{ts}.{hmac.new(key.encode(), str(ts).encode(), hashlib.sha256).hexdigest()}"


def toast(request) -> str:
    return "<div class=toast>Сохранено</div>" if request.query.get("ok") else ""


def err_box(msg: str) -> str:
    return f"<div class=err>{E(msg)}</div>"


def make_app(eleven, state: dict) -> web.Application:
    cache: dict = {}

    async def load_calls():
        """Last 100 calls, cached 30 s so reloads do not hammer ElevenLabs."""
        if cache.get("t", 0) > time.time() - 30:
            return cache["v"], None
        try:
            data = await eleven.conversations(100)
            cache.update(t=time.time(), v=data.get("conversations", []))
            return cache["v"], None
        except Exception as e:
            log.warning("conversations failed: %s", e)
            return cache.get("v", []), f"Не удалось получить звонки из ElevenLabs: {e}"

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

    def login_page(msg=""):
        body = ("<div class=login><h1>EUROLED</h1><p class=sub>Админка голосового агента</p>" + msg +
                "<form method=post><label>Пароль<input type=password name=pw autofocus autocomplete=current-password></label>"
                "<button class=pri>Войти</button></form></div>")
        return page("Вход", body, nav=False)

    @r.get("/login")
    async def login_form(_):
        return login_page()

    @r.post("/login")
    async def login(request):
        d = await request.post()
        h = hashlib.sha256((state["pw_salt"] + d.get("pw", "")).encode()).hexdigest()
        if not hmac.compare_digest(h, state["pw_hash"]):
            await _sleep()
            return login_page(err_box("Пароль не подошёл. Он пришёл в Telegram при первом запуске."))
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
        convs, err = await load_calls()
        now = time.time()
        today = ui.local(int(now)).date()
        # per-day counts, last 14 days
        days = []
        for i in range(13, -1, -1):
            d = today - __import__("datetime").timedelta(days=i)
            n = sum(1 for c in convs if ui.local(c.get("start_time_unix_secs", 0)).date() == d)
            days.append((f"{d.day} {ui.MON[d.month - 1]}", n))
        wk = [c for c in convs if c.get("start_time_unix_secs", 0) > now - 7 * 86400]
        pw = [c for c in convs if now - 14 * 86400 < c.get("start_time_unix_secs", 0) <= now - 7 * 86400]
        mins = sum(c.get("call_duration_secs", 0) or 0 for c in wk) / 60
        ok = sum(1 for c in wk if ui.outcome(c) == "ok")
        bad = sum(1 for c in wk if ui.outcome(c) == "no")
        rate = f"{ok * 100 // (ok + bad)}%" if ok + bad else "—"
        avg = ui.fmt_dur(sum(c.get("call_duration_secs", 0) or 0 for c in wk) / len(wk)) if wk else "—"

        def delta(a, b):
            if not b:
                return ""
            p = (a - b) * 100 // b
            return f"<em class={'up' if p >= 0 else 'down'}>{'+' if p >= 0 else ''}{p}%</em>"
        hrs = [0] * 24
        for c in convs:
            hrs[ui.local(c.get("start_time_unix_secs", 0)).hour] += 1
        kp = ("<div class=kpis>"
              f"<div class=kpi><b>{len(wk)}</b><span>звонков за 7 дней{delta(len(wk), len(pw))}</span></div>"
              f"<div class=kpi><b>{mins:.0f}</b><span>минут разговора</span></div>"
              f"<div class=kpi><b>{rate}</b><span>успешных</span></div>"
              f"<div class=kpi><b>{avg}</b><span>средняя длина</span></div></div>")
        legend = ("<div class=legend><span><i class='bulb ok'>✓</i>успешно</span><span><i class='bulb no'>×</i>не удалось</span>"
                  "<span><i class='bulb na'>–</i>без оценки</span><span class=sub>размер лампы: длина звонка</span></div>")
        rows = ""
        more = ""
        for idx, c in enumerate(convs):
            o = ui.outcome(c)
            title = c.get("call_summary_title") or "Без заголовка"
            item = (f"<a class=row data-o={o} href='/call/{E(c['conversation_id'])}'><i class='bulb {o}'>{ui.OUT_GLYPH[o]}</i>"
                     f"<div><div class=t>{E(title)}</div><div class=m>{ui.fmt_dt(c.get('start_time_unix_secs', 0))} · {c.get('message_count', 0)} реплик</div></div>"
                     f"<div class=d>{ui.fmt_dur(c.get('call_duration_secs', 0))}</div></a>")
            if idx < 25:
                rows += item
            else:
                more += item
        if more:
            rows += f"<details class=tbl><summary>Показать ещё {len(convs) - 25}</summary>{more}</details>"
        if not rows:
            rows = "<p class=sub>Звонков пока нет. Позвоните на +371 67 660 171, первый появится здесь.</p>"
        chips = ("<div class=chips><button class=chip data-f=all aria-pressed=true>Все</button>"
                 "<button class=chip data-f=ok aria-pressed=false>Успешные</button>"
                 "<button class=chip data-f=no aria-pressed=false>Не удались</button>"
                 "<button class=chip data-f=na aria-pressed=false>Без оценки</button></div>")
        body = (f"<div class=top><h1>Звонки</h1><small>{ui.fmt_dt(int(now))}</small></div>{err_box(err) if err else ''}"
                f"<div class=panel><h2>Последние звонки</h2>{ui.bulbs(convs)}{legend}</div>{kp}"
                f"<div class=grid2><div class=panel><h2>Звонки по дням</h2>{ui.bars(days)}</div>"
                f"<div class=panel><h2>В какие часы звонят</h2>{ui.hours(hrs)}<p class=sub>Всё время в Риге. Чем темнее, тем больше звонков.</p></div></div>"
                f"<div class=panel><h2>Все звонки</h2>{chips}<div class=rows>{rows}</div></div>")
        return page("Звонки", body, "/")

    @r.get("/call/{cid}")
    async def call(request):
        cid = request.match_info["cid"]
        try:
            c = await eleven.conversation(cid)
        except Exception as e:
            return page("Звонок", f"<a class=back href=/>← Все звонки</a>{err_box(f'Не удалось открыть звонок: {e}')}", "/")
        meta = c.get("metadata") or {}
        ts = meta.get("start_time_unix_secs") or 0
        dur = meta.get("call_duration_secs") or 0
        an = c.get("analysis") or {}
        o = ui.outcome({"call_successful": an.get("call_successful")})
        lines = ""
        for t in c.get("transcript", []):
            if not t.get("message"):
                continue
            who = "u" if t.get("role") == "user" else "a"
            lab = "Клиент" if who == "u" else "Агент"
            lines += f"<div class='msg {who}'><small>{lab}, {ui.fmt_dur(t.get('time_in_call_secs') or 0)}</small>{E(t['message'])}</div>"
        title = an.get("call_summary_title") or "Звонок"
        summ = an.get("transcript_summary") or ""
        body = (f"<a class=back href=/>← Все звонки</a><div class=top><h1>{E(title)}</h1></div>"
                f"<div class=panel><p><span class='badge {o}'>{ui.OUT_GLYPH[o]} {ui.OUT_LABEL[o]}</span> "
                f"<span class=sub>{ui.fmt_dt(ts) if ts else ''} · {ui.fmt_dur(dur)}</span></p>"
                f"<p>{E(summ) or '<span class=sub>Краткого пересказа нет.</span>'}</p>"
                f"<audio controls preload=none src='/audio/{E(cid)}'></audio></div>"
                f"<div class=panel><h2>Разговор</h2><div class=chat>{lines or '<p class=sub>Текста нет.</p>'}</div></div>")
        return page("Звонок", body, "/")

    @r.get("/audio/{cid}")
    async def audio(request):
        data = await eleven.audio_url_bytes(request.match_info["cid"])
        return web.Response(body=data, content_type="audio/mpeg")

    @r.get("/voice")
    async def voice(request):
        try:
            tts = await eleven.get_voice()
            e = ""
        except Exception as ex:
            tts, e = {}, err_box(f"Не удалось прочитать голос: {ex}")

        def rng(k, lo, hi, step, d):
            v = tts.get(k, d)
            return (f"<label>{ {'speed': 'Скорость речи', 'stability': 'Стабильность', 'similarity_boost': 'Похожесть на оригинал'}[k] }"
                    f"<div class=rng><input type=range id={k} name={k} min={lo} max={hi} step={step} value='{E(str(v))}'><output id={k}_o></output></div></label>")
        body = (f"<div class=top><h1>Голос</h1></div>{toast(request)}{e}<div class=panel><form method=post>"
                f"<label>Voice ID<input name=voice_id value='{E(str(tts.get('voice_id', '')))}'><small>Идентификатор голоса в ElevenLabs.</small></label>"
                f"{rng('speed', 0.7, 1.2, 0.05, 1.0)}{rng('stability', 0, 1, 0.05, 0.5)}{rng('similarity_boost', 0, 1, 0.05, 0.8)}"
                "<button class=pri>Сохранить голос</button><p class=sub>Действует на новые звонки.</p></form></div>")
        return page("Голос", body, "/voice")

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
        raise web.HTTPFound("/voice?ok=1")

    @r.get("/prompt")
    async def prompt(request):
        try:
            p = await eleven.get_prompt()
            e = ""
        except Exception as ex:
            p, e = "", err_box(f"Не удалось прочитать промпт: {ex}")
        body = (f"<div class=top><h1>Промпт</h1><small>{len(p)} знаков</small></div>{toast(request)}{e}<div class=panel>"
                f"<form method=post><label>Инструкция агента<textarea name=prompt>{E(p)}</textarea>"
                "<small>Предыдущая версия сохраняется на сервере. Откат: /rollback в Telegram.</small></label>"
                "<button class=pri>Сохранить промпт</button></form></div>")
        return page("Промпт", body, "/prompt")

    @r.post("/prompt")
    async def prompt_save(request):
        d = await request.post()
        text = d.get("prompt", "").strip()
        if len(text) < 200:
            return page("Промпт", err_box("Промпт короче 200 знаков, не сохранил. Вернитесь назад и проверьте текст."), "/prompt")
        old = await eleven.get_prompt()
        os.makedirs("/var/lib/euroled-admin/prompts", exist_ok=True)
        open(f"/var/lib/euroled-admin/prompts/{int(time.time())}.txt", "w").write(old)
        await eleven.set_prompt(text)
        raise web.HTTPFound("/prompt?ok=1")

    @r.get("/catalog")
    async def cat(request):
        q = request.query
        st = catalog.stats()
        when = ui.fmt_dt(int(st["updated"])) if st["updated"] else "ни разу"
        pr = catalog.PROGRESS
        if pr["running"]:
            status = f"<div class=panel><b>Сбор идёт.</b> Страниц: {pr['pages']}, товаров: {pr['products']}, ошибок: {pr['failed']}.<p class=sub>Сейчас: {E(pr['last_url'])}</p>" + (f"<p class=sub>Последняя ошибка: {E(pr['last_error'])}</p>" if pr["last_error"] else "") + "<p class=sub>Обновите страницу через минуту.</p></div>"
        else:
            bad = f"<div class=err>Последняя ошибка: {E(pr['last_error'])}</div>" if pr["last_error"] else ""
            status = bad + "<div class=panel><form method=post action=/catalog/crawl><button class=pri>Собрать каталог сейчас</button></form></div>"
        form = ("<form><label>Что ищем<input name=q placeholder='virtene, aizkari, lenta' value='" + E(q.get("q", "")) + "'></label>"
                "<label>Длина, м<input name=length inputmode=decimal placeholder='15' value='" + E(q.get("length", "")) + "'></label>"
                "<label>Не дороже, €<input name=max_price inputmode=decimal placeholder='60' value='" + E(q.get("max_price", "")) + "'></label>"
                "<button class=pri>Найти</button></form>")
        res = ""
        if q.get("q") or q.get("length") or q.get("max_price"):
            try:
                rows = catalog.search(q.get("q", ""), length_m=float(q["length"]) if q.get("length") else None,
                                      length_tol=3 if q.get("length") else 0,
                                      max_price=float(q["max_price"]) if q.get("max_price") else None, limit=30)
            except ValueError:
                rows = []
            items = ""
            for x in rows:
                chips = "".join(f"<span class=chipx>{E(str(v))}</span>" for v in
                                (f"{x['length_m']} м" if x["length_m"] else "", f"IP{x['ip']}" if x["ip"] else "", f"{x['watts']} Вт" if x.get("watts") else "") if v)
                old = f"<s>{x['price_old']:.2f} €</s>" if x.get("price_old") else ""
                items += (f"<div class=prow><div><a href='{E(x['url'])}' target=_blank rel=noopener>{E(x['title'])}</a><div>{chips}"
                          f"<span class=chipx>{E(str(x['stock'] or 'нет данных'))}</span></div></div><div class=price>{x['price']:.2f} €{old}</div></div>")
            res = f"<div class=panel><h2>Нашлось: {len(rows)}</h2>{items or '<p class=sub>Ничего. Попробуйте другое слово или увеличьте цену.</p>'}</div>"
        body = (f"<div class=top><h1>Каталог</h1></div><div class=kpis><div class=kpi><b>{st['products']}</b><span>товаров в базе</span></div>"
                f"<div class=kpi><b style='font-size:17px'>{when}</b><span>последнее обновление</span></div></div>"
                f"{status}<div class=panel>{form}</div>{res}<p class=sub>Обновить каталог вручную: /catalog crawl в Telegram.</p>")
        return page("Каталог", body, "/catalog")

    @r.post("/catalog/crawl")
    async def crawl_now(request):
        import asyncio
        if not catalog.PROGRESS["running"]:
            asyncio.create_task(catalog.crawl())
            catalog.PROGRESS["running"] = True
        raise web.HTTPFound("/catalog")

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
