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
import leads
import rules
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
                f"<div class=panel><h2>Разговор</h2><div class=chat>{lines or '<p class=sub>Текста нет.</p>'}</div></div>"
                f"<div class=panel><h2>Бот ошибся?</h2><form method=post action='/rules/add'>"
                f"<input type=hidden name=conv_id value='{E(cid)}'>"
                "<label>Что сделать иначе<textarea name=note rows=3 placeholder='Например: не предлагай уличную гирлянду без IP44'></textarea>"
                "<small>Станет правилом в промпте для всех следующих звонков. Отключить можно на странице правил.</small></label>"
                "<button class=pri>Добавить правило</button></form></div>")
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
                "<small>Предыдущая версия сохраняется на сервере. Откат: /rollback в Telegram. <a href='/rules'>Правила из разбора звонков</a></small></label>"
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

    @r.post("/rules/add")
    async def rules_add(request):
        d = await request.post()
        note = (d.get("note") or "").strip()
        rule = rules.to_rule(note)
        if len(rule) < 8:
            return page("Правила", err_box("Правило слишком короткое. Вернитесь назад и опишите, что бот должен делать иначе."), "/prompt")
        rules.add(d.get("conv_id", ""), note, rule)
        await _apply_rules()
        raise web.HTTPFound("/rules?ok=1")

    @r.post("/rules/toggle")
    async def rules_toggle(request):
        d = await request.post()
        rules.set_active(int(d.get("id", "0")), d.get("on") == "1")
        await _apply_rules()
        raise web.HTTPFound("/rules?ok=1")

    @r.get("/rules")
    async def rules_page(request):
        items = ""
        for x in rules.all_rules():
            on = bool(x["active"])
            link = f" · <a href='/call/{E(x['conv_id'])}'>звонок</a>" if x["conv_id"] else ""
            items += (f"<div class=prow><div><div{'' if on else ' style=opacity:.5'}>{E(x['rule'])}</div>"
                      f"<div class=sub>{ui.fmt_dt(x['ts'])}{link} · {'действует' if on else 'отключено'}</div></div>"
                      f"<form method=post action='/rules/toggle'><input type=hidden name=id value={x['id']}>"
                      f"<input type=hidden name=on value={0 if on else 1}><button>{'Отключить' if on else 'Включить'}</button></form></div>")
        body = (f"<div class=top><h1>Правила</h1></div>{toast(request)}<div class=panel>"
                f"{items or '<p class=sub>Правил пока нет. Откройте звонок и нажмите «Добавить правило» в блоке «Бот ошибся?».</p>'}"
                "</div><p class=sub><a href='/prompt'>К промпту</a></p>")
        return page("Правила", body, "/prompt")

    async def _apply_rules():
        """Write the active rules into the agent prompt (previous prompt is saved first)."""
        old = await eleven.get_prompt()
        new = rules.merge(old, rules.active_rules())
        if new.strip() == old.strip():
            return
        os.makedirs("/var/lib/euroled-admin/prompts", exist_ok=True)
        open(f"/var/lib/euroled-admin/prompts/{int(time.time())}.txt", "w").write(old)
        await eleven.set_prompt(new)

    @r.get("/catalog")
    async def cat(request):
        q = request.query
        st = catalog.stats()
        when = ui.fmt_dt(int(st["updated"])) if st["updated"] else "ни разу"
        pr = catalog.PROGRESS
        hl = catalog.health()
        hl_html = ("<div class=err><b>Синхронизация под вопросом:</b> " + E("; ".join(hl["problems"])) + "</div>") if hl["problems"] else (
            f"<div class=panel><span class=sub>Синхронизация в порядке: итоговые цены подтверждены у {hl['m_ok']} из {hl['m_len']}, "
            f"коробки известны у {hl['m2_ok']} из {hl['m2']}. Цена сверяется с сайтом в момент разговора.</span></div>")
        if pr["running"]:
            sh = f" ({E(pr.get('shop', ''))})" if pr.get("shop") else ""
            status = f"<div class=panel><b>Сбор идёт{sh}{(': ' + pr['phase']) if pr.get('phase') else ''}.</b> Страниц: {pr['pages']}, товаров: {pr['products']}, ошибок: {pr['failed']}.<p class=sub>Сейчас: {E(pr['last_url'])}</p>" + (f"<p class=sub>Последняя ошибка: {E(pr['last_error'])}</p>" if pr["last_error"] else "") + "<p class=sub>Обновите страницу через минуту.</p></div>"
        else:
            bad = f"<div class=err>Последняя ошибка: {E(pr['last_error'])}</div>" if pr["last_error"] else ""
            status = bad + "<div class=panel><form method=post action=/catalog/crawl><button class=pri>Собрать каталог сейчас</button></form></div>"
        shop_sel = ("<label>Магазин<select name=shop>" + "".join(
            f"<option value='{k}'{' selected' if q.get('shop', '') == k else ''}>{v}</option>"
            for k, v in (("", "Оба"), ("euroled", "euroled.lv"), ("euroflizes", "euroflizes.lv"))) + "</select></label>")
        form = ("<form>" + shop_sel + "<label>Что ищем<input name=q placeholder='virtene, aizkari, lenta' value='" + E(q.get("q", "")) + "'></label>"
                "<label>Длина, м<input name=length inputmode=decimal placeholder='15' value='" + E(q.get("length", "")) + "'></label>"
                "<label>Не дороже, €<input name=max_price inputmode=decimal placeholder='60' value='" + E(q.get("max_price", "")) + "'></label>"
                "<button class=pri>Найти</button></form>")
        res = ""
        if q.get("q") or q.get("length") or q.get("max_price"):
            try:
                rows = catalog.search(q.get("q", ""), length_m=float(q["length"]) if q.get("length") else None,
                                      length_tol=3 if q.get("length") else 0,
                                      max_price=float(q["max_price"]) if q.get("max_price") else None, limit=30, shop=q.get("shop", ""))
            except ValueError:
                rows = []
            items = ""
            for x in rows:
                chips = "".join(f"<span class=chipx>{E(str(v))}</span>" for v in
                                (x.get("shop") or "", f"{x['length_m']} м" if x["length_m"] else "", x.get("size_cm") or "", f"IP{x['ip']}" if x["ip"] else "", f"{x['watts']} Вт" if x.get("watts") else "") if v)
                u = x.get('unit')
                if u in ('m', 'm2'):
                    sub1, val1 = {'m': 'за метр', 'm2': 'за м²'}[u], f"{x['price']:.2f} €"
                else:
                    sub1, val1 = "за единицу", "—"
                if u == 'm':
                    ts = x.get('total_src')
                    sub2 = "целиком" if ts == 'site' else "целиком"
                    val2 = (f"{x['total_eur']:.2f} €" if x.get('total_eur') else
                            ("⚠ расходится с сайтом" if ts == 'check' else "загружается…"))
                elif u == 'm2':
                    sub2 = f"коробка {x['pack_m2']:g} м²" if x.get('pack_m2') else "коробка"
                    val2 = f"{x['box_price_eur']:.2f} €" if x.get('box_price_eur') else "—"
                else:
                    sub2, val2 = "целиком", f"{x['price']:.2f} €"
                items += (f"<div class=prow><div><a href='{E(x['url'])}' target=_blank rel=noopener>{E(x['title'])}</a><div>{chips}"
                          f"<span class=chipx>{E(str(x['stock'] if x['stock'] is not None else 'нет данных'))}{' м²' if u == 'm2' else ''}</span></div></div>"
                          f"<div class=prices><div><span class=sub>{sub1}</span><div class=price>{val1}</div></div>"
                          f"<div><span class=sub>{sub2}</span><div class=price>{val2}</div></div></div></div>")
            res = f"<div class=panel><h2>Нашлось: {len(rows)}</h2>{items or '<p class=sub>Ничего. Попробуйте другое слово или увеличьте цену.</p>'}</div>"
        body = (f"<div class=top><h1>Каталог</h1></div><div class=kpis><div class=kpi><b>{st['products']}</b><span>товаров в базе ({E(', '.join(f'{k} {v}' for k, v in st['per_shop'].items()) or 'пусто')})</span></div>"
                f"<div class=kpi><b style='font-size:17px'>{when}</b><span>последнее обновление</span></div></div>"
                f"{hl_html}{status}<div class=panel>{form}</div>{res}<p class=sub>Обновить каталог вручную: /catalog crawl в Telegram.</p>")
        return page("Каталог", body, "/catalog")

    @r.post("/catalog/crawl")
    async def crawl_now(request):
        import asyncio
        if not catalog.PROGRESS["running"]:
            asyncio.create_task(catalog.crawl())
            catalog.PROGRESS["running"] = True
        raise web.HTTPFound("/catalog")

    @r.get("/leads")
    async def leads_page(request):
        import json as _json
        only = request.query.get("all") != "1"
        rows = leads.recent(100, only_leads=only)
        items = ""
        for x in rows:
            f = _json.loads(x["fields"] or "{}")
            who = " · ".join(v for v in (x["name"], x["contact_phone"] or x["caller"]) if v) or "без имени"
            chips = "".join(f"<span class=chipx>{E(str(f[k]))}</span>" for k in ("customer_type", "urgency", "callback_time") if k in f)
            items += (f"<div class=prow><div><a href='/call/{E(x['conv_id'])}'>{E(x['title'] or x['summary'][:80] or 'Звонок')}</a>"
                      f"<div class=sub>{E(who)} · {ui.fmt_dt(x['ts'])}</div><div>{chips}</div>"
                      f"<div class=sub>{E(str(f.get('products_proposed') or f.get('room_and_task') or ''))}</div></div></div>")
        sw = ("<a href='/leads?all=1'>Показать и звонки без заявки</a>" if only else "<a href='/leads'>Только заявки</a>")
        body = (f"<div class=top><h1>Заявки</h1></div><div class=panel>"
                f"{items or '<p class=sub>Заявок пока нет. Они появятся после первого звонка с контактами.</p>'}</div><p class=sub>{sw}</p>")
        return page("Заявки", body, "/leads")

    def _tool_auth(request):
        if not hmac.compare_digest(request.headers.get("X-Tool-Token", ""), state["tool_token"]):
            raise web.HTTPUnauthorized()

    @r.get("/api/leads")
    async def api_leads(request):
        """New leads that still need an e-mail. Used by the scheduled mailer."""
        _tool_auth(request)
        out = []
        for x in leads.unsent(20):
            out.append({"id": x["conv_id"], "when": ui.fmt_dt(x["ts"]), "name": x["name"], "phone": x["contact_phone"] or x["caller"],
                        "title": x["title"], "summary": x["summary"], "fields": __import__("json").loads(x["fields"] or "{}"),
                        "call_url": f"https://92-5-6-32.sslip.io/call/{x['conv_id']}"})
        return web.json_response({"count": len(out), "leads": out})

    @r.post("/api/leads/mark")
    async def api_leads_mark(request):
        _tool_auth(request)
        ids = (await request.json()).get("ids", [])
        return web.json_response({"marked": leads.mark_sent([str(i) for i in ids])})

    @r.post("/api/init")
    async def api_init(request):
        """ElevenLabs conversation-initiation webhook: tell the agent who is calling."""
        _tool_auth(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        mem = leads.memory(body.get("caller_id"))
        return web.json_response({"type": "conversation_initiation_client_data", "dynamic_variables": mem})

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
                              in_stock=q.get("in_stock", "1") != "0", sort=q.get("sort", "price"), shop=q.get("shop", ""),
                              use=q.get("use", ""), christmas=q.get("christmas") == "1",
                              limit=min(int(q.get("limit", 5)), 10) if q.get("limit", "5").isdigit() else 5)
        area = fl("area_m2")
        qty = int(fl("qty") or 1)
        qty = max(1, min(qty, 1000))
        live = await catalog.verify_live(rows[:3])
        try:
            catalog.record_hits([r["url"] for r in rows[:3]])
        except Exception:
            log.exception("record_hits failed")
        for x in rows:  # prices may have changed on the site since the last crawl: recompute what depends on them
            if x["url"] in live and live[x["url"]]["changed"]:
                x["total_eur"] = x["total"] if x.get("unit") == "m" and x.get("total_src") == "site" else None
                x["box_price_eur"] = round(x["price"] * x["pack_m2"], 2) if x.get("unit") == "m2" and x.get("pack_m2") else None
        out = []
        for x in rows:
            u, p = x["unit"], x["price"]
            tot, box, plan = x.get("total_eur"), x.get("box_price_eur"), None
            if u == "m" and tot:
                say = f"{tot:.2f} € за всю гирлянду/ленту {x['length_m']:g} м ({p:.2f} € за метр)"
                if qty > 1:
                    tot = round(tot * qty, 2)
                    say += f". {qty} шт. итого {tot:.2f} €"
            elif u == "m" and x.get("length_m"):
                say = (f"{p:.2f} € за метр; точный итог на длину {x['length_m']:g} м не подтверждён, "
                       f"скажи «примерно {p * x['length_m']:.0f} €, точную сумму подтвердит менеджер»")
            elif u == "m":
                say = f"{p:.2f} € за метр; общая цена зависит от длины, уточни у клиента нужную длину"
            elif u == "m2" and x.get("pack_m2"):
                say = (f"{p:.2f} € за м². Продаётся коробками: в коробке {x['pack_m2']:g} м² "
                       f"({x['pack_pieces']} шт.), коробка стоит {box:.2f} €")
                if area:
                    plan = catalog.tile_plan(p, x["pack_m2"], area)
                    tot = plan["total_eur"]
                    say += (f". На {area:g} м² с запасом 10% ({plan['need_m2']:g} м²) нужно {plan['boxes']} коробок "
                            f"({plan['boxes_m2']:g} м²), итого {tot:.2f} €")
            elif u == "m2":
                say = f"{p:.2f} € за м². Размер коробки не подтверждён: итог на объём посчитает менеджер"
            else:
                tot = round(p * qty, 2)
                say = f"{p:.2f} € за штуку" + (f". {qty} шт. итого {tot:.2f} €" if qty > 1 else ", итого за заказ столько же")
            lv = live.get(x["url"])
            checked = bool(lv and lv["ok"])
            if lv is not None and not checked:
                say += ". Страницу товара сейчас проверить не удалось: скажи, что актуальную цену подтвердит менеджер"
            out.append({"name": x["title"], "price_to_say": say, "price_checked_live": checked if lv is not None else None,
                        "price_changed_just_now": bool(lv and lv["changed"]),
                        "unit_price_eur": p,
                        "unit_price_per": {"m2": "за м²", "m": "за метр", "gab": "за штуку"}.get(u, "за штуку"),
                        "order_total_eur": tot,
                        "box_price_eur": box, "box_m2": x.get("pack_m2"), "pieces_per_box": x.get("pack_pieces"),
                        "boxes_needed": plan["boxes"] if plan else None,
                        "quantity": qty,
                        "total_is_confirmed_by_site": bool(tot) and (u == "m2" and bool(plan) or (u == "m" and x.get("total_src") == "site") or u not in ("m", "m2")),
                        "in_stock": x["stock"], "length_m": x["length_m"], "ip": x["ip"], "size_cm": x["size_cm"],
                        "shop": x["shop"], "url": x["url"], "kind": x.get("kind_ru"), "use": x.get("use"),
                        "note": x.get("note"), "warning": x.get("warning")})
        return web.json_response({"count": len(out), "products": out})

    app.add_routes(r)
    return app


async def _sleep():
    import asyncio
    await asyncio.sleep(1.5)
