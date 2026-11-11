"""UI kit for the web admin: tokens, CSS, JS, page shell, charts. No build step, no external JS."""
from __future__ import annotations
import datetime as dt
import html
from zoneinfo import ZoneInfo

E = html.escape
TZ = ZoneInfo("Europe/Riga")
MON = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]

CSS = """
:root{--bg:#EEF1F5;--sf:#fff;--ink:#0F1B2D;--ink2:#4B5668;--mut:#6B778B;--line:#E1E6ED;
--bar:#3B5BDB;--led:#C47F00;--ledglow:#F2A90066;--good:#1F8A5B;--bad:#C8372D;--soft:#F5F7FA;--btn:#0F1B2D;--btnt:#fff;
--r:14px;--rs:9px}
@media(prefers-color-scheme:dark){:root{--bg:#0B1220;--sf:#131C2E;--ink:#E8EDF5;--ink2:#A9B4C6;--mut:#8593A9;--line:#223049;
--bar:#6F86F0;--led:#D79A00;--ledglow:#FFC53D55;--good:#2FA878;--bad:#FF7468;--soft:#18233A;--btn:#E8EDF5;--btnt:#0B1220}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 Onest,system-ui,-apple-system,"Segoe UI",sans-serif;
padding-bottom:84px}
a{color:inherit}
h1,h2,h3{font-family:Unbounded,Onest,system-ui,sans-serif;font-weight:600;margin:0;letter-spacing:-.01em}
h1{font-size:22px}h2{font-size:16px}h3{font-size:15px}
.wrap{max-width:1040px;margin:0 auto;padding:18px 14px}
.top{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin-bottom:14px}
.top small{color:var(--mut)}
.panel{background:var(--sf);border-radius:var(--r);padding:16px;margin:0 0 14px;border:1px solid var(--line)}
.panel>h2{margin-bottom:10px}
.sub{color:var(--mut);font-size:13px}
/* nav */
.nav{position:fixed;left:0;right:0;bottom:0;z-index:5;display:flex;background:var(--sf);border-top:1px solid var(--line);
padding:6px 6px calc(6px + env(safe-area-inset-bottom))}
.nav a{flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;padding:6px 2px;text-decoration:none;color:var(--mut);font-size:12px;border-radius:var(--rs)}
.nav a svg{width:22px;height:22px;stroke:currentColor;fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.nav a[aria-current=page]{color:var(--ink);font-weight:600}
.nav a:focus-visible,a:focus-visible,button:focus-visible,input:focus-visible,textarea:focus-visible,select:focus-visible{outline:2px solid var(--bar);outline-offset:2px}
@media(min-width:900px){
body{padding:0 0 0 220px}
.nav{top:0;bottom:auto;right:auto;width:220px;height:100vh;flex-direction:column;border-top:0;border-right:1px solid var(--line);padding:20px 12px;gap:4px}
.nav a{flex:none;flex-direction:row;gap:12px;padding:10px 12px;font-size:15px}
.nav .brand{display:block}
}
.brand{display:none;font-family:Unbounded,sans-serif;font-weight:600;padding:4px 12px 18px;font-size:15px}
.brand i{display:inline-block;width:10px;height:10px;border-radius:50%;background:var(--led);box-shadow:0 0 10px 2px var(--ledglow);margin-right:8px}
/* strip of bulbs */
.strip{position:relative;display:flex;align-items:center;gap:7px;overflow-x:auto;padding:22px 4px 14px;scrollbar-width:thin}
.strip:before{content:"";position:absolute;left:0;right:0;top:50%;height:2px;background:var(--line);transform:translateY(-50%)}
.bulb{position:relative;flex:none;border-radius:50%;display:grid;place-items:center;text-decoration:none;font-size:11px;font-weight:700;line-height:1;
width:var(--d);height:var(--d);background:var(--sf);border:2px solid var(--mut);color:var(--mut)}
.bulb.ok{background:var(--led);border-color:var(--led);color:#fff;box-shadow:0 0 14px 3px var(--ledglow)}
.bulb.no{border-color:var(--bad);color:var(--bad)}
.bulb.na{border-style:dashed}
.bulb:hover{transform:translateY(-3px)}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:13px;color:var(--ink2)}
.legend span{display:inline-flex;align-items:center;gap:6px}
.legend .bulb{width:14px;height:14px;font-size:9px;box-shadow:none}
.legend .bulb.ok{box-shadow:0 0 8px 1px var(--ledglow)}
/* kpis */
.kpis{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:14px}
@media(min-width:700px){.kpis{grid-template-columns:repeat(4,1fr)}}
.kpi{background:var(--sf);border:1px solid var(--line);border-radius:var(--r);padding:14px}
.kpi b{display:block;font-family:Unbounded,sans-serif;font-weight:600;font-size:26px;letter-spacing:-.02em;line-height:1.15}
.kpi span{color:var(--mut);font-size:13px}
.kpi em{font-style:normal;font-size:12px;font-weight:600;margin-left:6px}
.up{color:var(--good)}.down{color:var(--bad)}
.grid2{display:grid;gap:14px}
@media(min-width:760px){.grid2{grid-template-columns:3fr 2fr}}
/* charts */
svg.ch{width:100%;height:auto;display:block;overflow:visible}
svg.ch text{fill:var(--mut);font:11px Onest,sans-serif}
svg.ch .g{stroke:var(--line);stroke-width:1}
svg.ch .b{fill:var(--bar)}
svg.ch .hit{fill:transparent;cursor:pointer}
svg.ch .hit:hover+.b,svg.ch .hit:focus+.b{opacity:.75}
.hours{display:grid;grid-template-columns:repeat(24,1fr);gap:2px;margin-top:6px}
.hours i{display:block;height:34px;border-radius:4px;background:var(--bar);cursor:pointer}
.hours-l{display:flex;justify-content:space-between;color:var(--mut);font-size:11px;margin-top:4px}
.tip{position:fixed;z-index:9;pointer-events:none;background:var(--ink);color:var(--bg);padding:6px 10px;border-radius:8px;font-size:13px;display:none;max-width:240px}
details.tbl{margin-top:10px;font-size:13px;color:var(--ink2)}
details.tbl summary{cursor:pointer;color:var(--mut)}
details.tbl table{margin-top:6px}
/* chips + list */
.chips{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}
.chip{border:1px solid var(--line);background:var(--sf);color:var(--ink2);padding:6px 12px;border-radius:99px;font:inherit;font-size:14px;cursor:pointer}
.chip[aria-pressed=true]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.rows{display:flex;flex-direction:column}
.row{display:grid;grid-template-columns:34px 1fr auto;gap:12px;align-items:center;padding:12px 4px;border-top:1px solid var(--line);text-decoration:none}
.row:first-child{border-top:0}
.row:hover{background:var(--soft)}
.row .bulb{width:28px;height:28px;font-size:12px}
.row .t{font-weight:500;overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.row .m{color:var(--mut);font-size:13px}
.row .d{font-variant-numeric:tabular-nums;color:var(--ink2);font-size:14px;text-align:right}
/* chat */
.chat{display:flex;flex-direction:column;gap:8px}
.msg{max-width:84%;padding:9px 13px;border-radius:16px;font-size:15px;white-space:pre-wrap;word-wrap:break-word}
.msg small{display:block;color:inherit;opacity:.65;font-size:11px;margin-bottom:2px}
.msg.u{align-self:flex-start;background:var(--soft);border-bottom-left-radius:5px}
.msg.a{align-self:flex-end;background:var(--ink);color:var(--bg);border-bottom-right-radius:5px}
.badge{display:inline-flex;align-items:center;gap:6px;font-size:13px;font-weight:600;padding:3px 10px;border-radius:99px;border:1.5px solid currentColor}
.badge.ok{color:var(--good)}.badge.no{color:var(--bad)}.badge.na{color:var(--mut)}
.back{display:inline-block;margin-bottom:10px;color:var(--ink2);text-decoration:none}
audio{width:100%;margin-top:10px}
/* forms */
label{display:block;font-weight:500;margin:0 0 14px}
label small{display:block;color:var(--mut);font-weight:400;margin-top:2px}
input,textarea,select{font:inherit;width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:var(--rs);background:var(--sf);color:var(--ink);margin-top:6px}
input[type=range]{padding:0;accent-color:var(--bar)}
textarea{min-height:420px;line-height:1.55;resize:vertical}
button.pri,a.pri{display:inline-block;font:inherit;font-weight:600;padding:11px 20px;border:0;border-radius:var(--rs);background:var(--btn);color:var(--btnt);cursor:pointer;text-decoration:none}
.toast{background:var(--good);color:#fff;padding:10px 14px;border-radius:var(--rs);margin-bottom:12px;font-weight:500}
.err{border:1.5px solid var(--bad);color:var(--ink);border-radius:var(--rs);padding:12px 14px;margin-bottom:12px}
.rng{display:flex;align-items:center;gap:12px}.rng output{min-width:42px;text-align:right;font-variant-numeric:tabular-nums;color:var(--ink2)}
.price{font-family:Unbounded,sans-serif;font-weight:600;font-size:17px;white-space:nowrap}
.price s{display:block;font:13px Onest;color:var(--mut)}
.chipx{display:inline-block;font-size:12px;padding:2px 8px;border-radius:99px;background:var(--soft);color:var(--ink2);margin:2px 4px 0 0}
.prow{display:grid;grid-template-columns:1fr auto;gap:12px;padding:12px 0;border-top:1px solid var(--line);align-items:start}
.prow:first-child{border-top:0}
.login{max-width:360px;margin:12vh auto;padding:0 14px}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:4px 6px;border-bottom:1px solid var(--line)}
@media(prefers-reduced-motion:no-preference){.bulb{transition:transform .15s}}
"""

JS = """
(function(){
var tip=document.createElement('div');tip.className='tip';document.body.appendChild(tip);
function show(e){var t=e.currentTarget.getAttribute('data-t');if(!t)return;tip.textContent=t;tip.style.display='block';
var x=e.clientX||0,y=e.clientY||0;tip.style.left=Math.min(x+12,innerWidth-250)+'px';tip.style.top=Math.max(y-40,8)+'px';}
function hide(){tip.style.display='none';}
document.querySelectorAll('[data-t]').forEach(function(n){n.addEventListener('pointermove',show);n.addEventListener('pointerdown',show);n.addEventListener('pointerleave',hide);});
var st=document.querySelector('.strip');if(st)st.scrollLeft=st.scrollWidth;
document.addEventListener('scroll',hide,{passive:true});
document.querySelectorAll('.chip[data-f]').forEach(function(c){c.addEventListener('click',function(){
document.querySelectorAll('.chip[data-f]').forEach(function(x){x.setAttribute('aria-pressed',x===c)});
var f=c.getAttribute('data-f');document.querySelectorAll('.row[data-o]').forEach(function(r){r.style.display=(f==='all'||r.getAttribute('data-o')===f)?'':'none'});});});
document.querySelectorAll('input[type=range]').forEach(function(r){var o=document.getElementById(r.id+'_o');
function u(){if(o)o.textContent=(+r.value).toFixed(2)};r.addEventListener('input',u);u();});
})();
"""

FONTS = ("<link rel=preconnect href='https://fonts.googleapis.com'><link rel=preconnect href='https://fonts.gstatic.com' crossorigin>"
         "<link rel=stylesheet href='https://fonts.googleapis.com/css2?family=Onest:wght@400;500;600&family=Unbounded:wght@500;600&display=swap'>")

ICONS = {
    "calls": "<path d='M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z'/>",
    "catalog": "<path d='M4 7l8-4 8 4-8 4-8-4zM4 12l8 4 8-4M4 17l8 4 8-4'/>",
    "voice": "<rect x='9' y='3' width='6' height='11' rx='3'/><path d='M5 11a7 7 0 0 0 14 0M12 18v3'/>",
    "prompt": "<path d='M5 4h14v16H5zM9 9h6M9 13h6M9 17h3'/>",
}
NAV = [("/", "Звонки", "calls"), ("/catalog", "Каталог", "catalog"), ("/voice", "Голос", "voice"), ("/prompt", "Промпт", "prompt")]


def page(title: str, body: str, active: str = "/", nav: bool = True):
    from aiohttp import web
    n = ""
    if nav:
        n = "<nav class=nav><div class=brand><i></i>EUROLED</div>" + "".join(
            f"<a href='{h}'{' aria-current=page' if h == active else ''}><svg viewBox='0 0 24 24'>{ICONS[i]}</svg>{l}</a>"
            for h, l, i in NAV) + "</nav>"
    doc = (f"<!doctype html><html lang=ru><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1,viewport-fit=cover'>"
           f"<meta name=color-scheme content='light dark'><title>{E(title)} · EUROLED</title>{FONTS}<style>{CSS}</style>{n}"
           f"<div class=wrap>{body}</div><script>{JS}</script></html>")
    return web.Response(text=doc, content_type="text/html")


# ---------- helpers ----------
def local(ts: int):
    return dt.datetime.fromtimestamp(ts, TZ)


def fmt_dt(ts: int) -> str:
    d = local(ts)
    return f"{d.day} {MON[d.month - 1]}, {d:%H:%M}"


def fmt_dur(sec: int) -> str:
    sec = int(sec or 0)
    return f"{sec // 60}:{sec % 60:02d}"


def outcome(c: dict) -> str:
    s = str(c.get("call_successful") or "").lower()
    return "ok" if s == "success" else "no" if s == "failure" else "na"


OUT_LABEL = {"ok": "Успешно", "no": "Не удалось", "na": "Без оценки"}
OUT_GLYPH = {"ok": "✓", "no": "×", "na": "–"}


def bulbs(convs: list[dict], limit: int = 40) -> str:
    """The signature element: recent calls as a string of lights. Size = length, fill = outcome."""
    items = sorted(convs, key=lambda c: c.get("start_time_unix_secs", 0))[-limit:]
    out = []
    for c in items:
        o = outcome(c)
        dur = c.get("call_duration_secs", 0) or 0
        d = 18 + min(dur, 300) / 300 * 16
        tip = f"{fmt_dt(c.get('start_time_unix_secs', 0))} · {fmt_dur(dur)} · {OUT_LABEL[o]}"
        out.append(f"<a class='bulb {o}' style='--d:{d:.0f}px' href='/call/{E(c['conversation_id'])}' data-t='{E(tip)}' "
                   f"aria-label='{E(tip)}'>{OUT_GLYPH[o]}</a>")
    return "<div class=strip>" + "".join(out) + "</div>"


def bars(days: list[tuple[str, int]], unit: str = "звонков") -> str:
    """days = [(label 'ДД мес', value)]. Thin bars, rounded top, hover tooltip, table fallback."""
    W, H, B = 640, 170, 22
    n = len(days)
    slot = W / n
    bw = max(8, slot * 0.46)
    mx = max([v for _, v in days] + [1])
    top = 14
    ph = H - B - top
    g = (f"<line class=g x1=0 x2={W} y1={top} y2={top}></line><line class=g x1=0 x2={W} y1={top + ph / 2:.1f} y2={top + ph / 2:.1f}></line>"
         f"<line class=g x1=0 x2={W} y1={top + ph} y2={top + ph}></line>"
         f"<text x=0 y={top - 4}>{mx}</text>")
    parts = []
    for i, (lab, v) in enumerate(days):
        x = i * slot + (slot - bw) / 2
        h = ph * v / mx
        y = top + ph - h
        r = min(4, h / 2, bw / 2)
        tip = f"{lab}: {v} {unit}"
        if v:
            p = (f"M{x:.1f},{y + h:.1f}V{y + r:.1f}Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f}H{x + bw - r:.1f}"
                 f"Q{x + bw:.1f},{y:.1f} {x + bw:.1f},{y + r:.1f}V{y + h:.1f}Z")
            parts.append(f"<g><rect class=hit x={i * slot:.1f} y=0 width={slot:.1f} height={H - B} data-t='{E(tip)}'></rect><path class=b d='{p}'></path></g>")
        else:
            parts.append(f"<g><rect class=hit x={i * slot:.1f} y=0 width={slot:.1f} height={H - B} data-t='{E(tip)}'></rect></g>")
        if i % 2 == (n - 1) % 2:
            parts.append(f"<text x={x + bw / 2:.1f} y={H - 6} text-anchor=middle>{E(lab.split()[0])}</text>")
    tbl = "".join(f"<tr><td>{E(l)}<td>{v}" for l, v in days)
    return (f"<svg class=ch viewBox='0 0 {W} {H}' role=img aria-label='{E(unit)} по дням'>{g}{''.join(parts)}</svg>"
            f"<details class=tbl><summary>Таблица</summary><table>{tbl}</table></details>")


def hours(counts: list[int]) -> str:
    mx = max(counts + [1])
    cells = "".join(
        f"<i style='opacity:{0.12 + 0.88 * c / mx:.2f}' data-t='{h}:00–{h + 1}:00 · {c} зв.' role=img aria-label='{h}:00, {c}'></i>"
        for h, c in enumerate(counts))
    return f"<div class=hours>{cells}</div><div class=hours-l><span>0</span><span>6</span><span>12</span><span>18</span><span>24</span></div>"
