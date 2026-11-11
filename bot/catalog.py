"""euroled.lv catalog: crawl -> SQLite -> search with facts parsed from product titles."""
from __future__ import annotations
import asyncio
import logging
import os
import re
import sqlite3
import time
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

import taxonomy

log = logging.getLogger("catalog")
SHOPS = {
    "euroled": {"base": "https://euroled.lv", "seeds": ["https://euroled.lv/lv/visas-preces"]},
    "euroflizes": {"base": "https://euroflizes.lv", "seeds": [
        "https://euroflizes.lv/lv/" + p for p in (
            "flizes", "vinila-flizes", "spoguli", "aluminija-enu-profili", "gridlistes", "flizu-profili",
            "kimija-un-piederumi-flizu-ieklasanai", "premium-klases-sienu-paneli", "santehnika-un-apkure",
            "majas-mebeles-un-interjera-preces")]},
}
BASE = SHOPS["euroled"]["base"]
START = SHOPS["euroled"]["seeds"][0]
DB_PATH = os.environ.get("CATALOG_DB", "/var/lib/euroled-admin/catalog.db")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "lv,en;q=0.8",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS products(
  url TEXT PRIMARY KEY, title TEXT, category TEXT, price REAL, price_old REAL, discount INTEGER,
  stock INTEGER, length_m REAL, ip INTEGER, watts REAL, leds INTEGER, kelvin INTEGER,
  color TEXT, battery INTEGER, updated REAL, shop TEXT DEFAULT 'euroled', unit TEXT, size_cm TEXT,
  total REAL, total_src TEXT, piece_m2 REAL, pack_pieces INTEGER, pack_m2 REAL, slug TEXT, cats TEXT);
CREATE INDEX IF NOT EXISTS i_cat ON products(category);
"""


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def parse_facts(title: str) -> dict:
    """Facts live in the title: '... 10m, daudzkrāsaina, USB 5V, IP44, 100LED'."""
    t = title
    f: dict = {}
    m = re.search(r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s?m(?![\w])", t)
    f["length_m"] = _num(m.group(1)) if m else None
    m = re.search(r"IP\s?(\d{2})", t)
    f["ip"] = int(m.group(1)) if m else None
    m = re.search(r"(\d+(?:[.,]\d+)?)\s?W\b", t)
    f["watts"] = _num(m.group(1)) if m else None
    m = re.search(r"(\d+)\s?LED", t, re.I)
    f["leds"] = int(m.group(1)) if m else None
    m = re.search(r"(\d{4})\s?K\b", t)
    f["kelvin"] = int(m.group(1)) if m else None
    m = re.search(r"(\d{2,3})\s?[xх×]\s?(\d{2,3})(?:\s?[xх×]\s?(\d{1,3}))?\s?(?:cm|mm)?", t)
    f["size_cm"] = "x".join(g for g in m.groups() if g) if m else None
    low = t.lower()
    color = None
    for key, val in (("daudzkrāsain", "multicolor"), ("rgb", "rgb"), ("silti balt", "warm_white"),
                     ("auksti balt", "cold_white"), ("neitr", "neutral_white"), ("balt", "white")):
        if key in low:
            color = val
            break
    f["color"] = color
    f["battery"] = 1 if re.search(r"baterij|\bAA\b|3xAA", t, re.I) else 0
    return f


def parse_listing(html: str, page_url: str, shop: str = "euroled") -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.select("a.product"):
        href = a.get("href") or ""
        if not href.startswith(("http", "/")):
            continue
        url = urljoin(page_url, href).split("?")[0]
        title_el = a.select_one(".product__title")
        price_el = a.select_one(".product__price-value")
        if not title_el or not price_el:
            continue
        title = title_el.get_text(" ", strip=True)
        try:
            price = _num(price_el.get_text(strip=True))
        except ValueError:
            continue
        stock_el = a.select_one(".product__stock-badge")
        stock = None
        if stock_el:
            m = re.search(r"(\d+(?:[.,]\d+)?)", stock_el.get_text())
            stock = _num(m.group(1)) if m else 0
        lab = a.select_one(".product__label")
        disc = None
        if lab:
            m = re.search(r"(\d+)\s*%", lab.get_text())
            disc = int(m.group(1)) if m else None
        segs = [p for p in urlparse(url).path.split("/") if p and p not in ("lv", "visas-preces")]
        category = "/".join(segs[:-1])  # without product slug
        cur_el = a.select_one(".product__price-currency")
        cur = cur_el.get_text(strip=True).lower() if cur_el else ""
        if "m²" in cur or "m2" in cur:
            unit = "m2"
        elif re.search(r"/\s?m\b", cur):
            unit = "m"  # price per metre of cable/strip
        else:
            unit = "gab"
        row = {"url": url, "title": title, "category": category, "price": price,
               "price_old": None, "discount": disc, "stock": stock, "shop": shop, "unit": unit,
               "slug": urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]}
        row.update(parse_facts(title))
        out.append(row)
    return out


def category_links(html: str, page_url: str, shop: str = "euroled") -> set[str]:
    seeds = SHOPS[shop]["seeds"]
    soup = BeautifulSoup(html, "html.parser")
    found = set()
    for a in soup.find_all("a", href=True):
        u = urljoin(page_url, a["href"]).split("?")[0].split("#")[0].rstrip("/")
        if any(u == sd or u.startswith(sd + "/") for sd in seeds) and "javascript" not in u:
            found.add(u)
    return found


def db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    cols = {r[1] for r in c.execute("PRAGMA table_info(products)")}
    for name, typ in (("shop", "TEXT DEFAULT 'euroled'"), ("unit", "TEXT"), ("size_cm", "TEXT"), ("total", "REAL"),
                      ("total_src", "TEXT"), ("piece_m2", "REAL"), ("pack_pieces", "INTEGER"), ("pack_m2", "REAL"),
                      ("slug", "TEXT"), ("cats", "TEXT")):
        if name not in cols:
            c.execute(f"ALTER TABLE products ADD COLUMN {name} {typ}")
    for (u,) in c.execute("SELECT url FROM products WHERE slug IS NULL").fetchall():
        c.execute("UPDATE products SET slug = ? WHERE url = ?", (u.rstrip("/").rsplit("/", 1)[-1], u))
    c.execute("UPDATE products SET cats = category WHERE cats IS NULL")
    c.execute("CREATE INDEX IF NOT EXISTS i_slug ON products(shop, slug)")
    c.commit()
    return c


CRAWL_VERSION = 5  # bump when parsing changes: forces a fresh crawl on next start
MARKER = os.path.join(os.path.dirname(DB_PATH) or ".", "crawl_done.json")


def crawl_due(max_age_h: float = 20) -> bool:
    import json
    try:
        m = json.load(open(MARKER))
        return m.get("v") != CRAWL_VERSION or time.time() - m.get("t", 0) > max_age_h * 3600
    except Exception:
        return True


PROGRESS: dict = {"running": False, "pages": 0, "products": 0, "failed": 0, "last_error": "", "last_url": "", "started": 0, "finished": 0}


def _save(rows, now):
    c = db()
    with c:
        for r in rows:
            r = dict(r, updated=now)
            if c.execute("SELECT 1 FROM products WHERE shop = ? AND slug = ? AND url != ? AND updated = ?",
                         (r["shop"], r["slug"], r["url"], now)).fetchone():
                c.execute("UPDATE products SET cats = cats || '|' || ? WHERE shop = ? AND slug = ? AND updated = ? "
                          "AND instr('|' || cats || '|', '|' || ? || '|') = 0",
                          (r["category"], r["shop"], r["slug"], now, r["category"]))
                continue  # same product listed under another category: keep one row
            c.execute(
                "INSERT INTO products(url,title,category,price,price_old,discount,stock,"
                "length_m,ip,watts,leds,kelvin,color,battery,updated,shop,unit,size_cm,slug,cats) VALUES(:url,:title,:category,:price,"
                ":price_old,:discount,:stock,:length_m,:ip,:watts,:leds,:kelvin,:color,:battery,:updated,:shop,:unit,:size_cm,:slug,:category) "
                "ON CONFLICT(url) DO UPDATE SET title=excluded.title, category=excluded.category, price_old=NULL, "
                "discount=excluded.discount, stock=excluded.stock, length_m=excluded.length_m, ip=excluded.ip, "
                "watts=excluded.watts, leds=excluded.leds, kelvin=excluded.kelvin, color=excluded.color, "
                "battery=excluded.battery, updated=excluded.updated, shop=excluded.shop, unit=excluded.unit, "
                "size_cm=excluded.size_cm, slug=excluded.slug, cats=CASE WHEN products.updated = excluded.updated THEN products.cats ELSE excluded.category END, "
                "total=CASE WHEN products.price = excluded.price AND products.length_m IS excluded.length_m THEN products.total ELSE NULL END, "
                "total_src=CASE WHEN products.price = excluded.price AND products.length_m IS excluded.length_m THEN products.total_src ELSE NULL END, "
                "price=excluded.price", r)
    c.close()


def parse_detail(html: str, row: dict) -> dict:
    """Facts only the product page has: the real order total (per-metre goods) and the pack size (tiles)."""
    soup = BeautifulSoup(html, "html.parser")
    out: dict = {}
    if row["unit"] == "m" and row.get("length_m"):
        blk = soup.select_one(".card__price")
        total = None
        if blk:
            el = blk.select_one(".card__pice-sale-value")
            m = re.search(r"\d+(?:[.,]\d+)?", (el or blk).get_text(" ", strip=True))
            total = _num(m.group(0)) if m else None
        if total is None:
            m = re.search(r'card__pice-sale-value[^>]*>\s*(\d+(?:[.,]\d+)?)', html)
            total = _num(m.group(1)) if m else None
        calc = row["price"] * row["length_m"]
        if total is not None and abs(total - calc) <= max(0.5, 0.03 * calc):
            out.update(total=round(total, 2), total_src="site")
        else:  # the site disagrees with the title: never guess
            log.warning("total check: %s site=%s calc=%.2f", row.get("url"), total, calc)
            out.update(total=None, total_src="check")
    elif row["unit"] == "m2":
        el = soup.select_one("#piecesCart")
        try:
            piece = float(el["data-area-of-piece"])
            n = int(el["data-pieces-in-package"])
            out.update(piece_m2=piece, pack_pieces=n, pack_m2=round(piece * n, 4))
        except (TypeError, KeyError, ValueError):
            pass
    return out


async def enrich(s: aiohttp.ClientSession, shop: str, delay: float = 0.4) -> dict:
    """Open product pages that need a real total or a pack size; only those that changed."""
    c = db()
    todo = [dict(r) for r in c.execute(
        "SELECT * FROM products WHERE shop = ? AND ((unit = 'm' AND length_m IS NOT NULL AND total_src IS NULL) "
        "OR (unit = 'm2' AND pack_m2 IS NULL))", (shop,))]
    c.close()
    done = check = 0
    for r in todo:
        PROGRESS["last_url"] = r["url"]
        html = await _get(s, r["url"])
        await asyncio.sleep(delay)
        if html is None:
            continue
        d = parse_detail(html, r)
        if not d:
            continue
        if d.get("total_src") == "check":
            check += 1
        c = db()
        with c:
            sets = ", ".join(f"{k} = ?" for k in d)
            c.execute(f"UPDATE products SET {sets} WHERE url = ?", [*d.values(), r["url"]])
        c.close()
        done += 1
    return {"enriched": done, "check": check}


async def _get(s: aiohttp.ClientSession, url: str) -> str | None:
    for attempt in range(3):
        try:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=40)) as r:
                if r.status == 200:
                    return await r.text()
                log.warning("GET %s -> %s", url, r.status)
                PROGRESS["last_error"] = f"HTTP {r.status} на {url}"
                if r.status in (403, 429, 503):
                    await asyncio.sleep(5 * (attempt + 1))
                else:
                    return None
        except Exception as e:  # network hiccup
            log.warning("GET %s failed: %s", url, e)
            PROGRESS["last_error"] = f"{type(e).__name__}: {e} ({url})"
            await asyncio.sleep(3)
    return None


async def crawl(max_pages: int = 4000, delay: float = 0.6) -> dict:
    """BFS over each shop's categories; paginate listings with ?page=N. Saves while crawling."""
    t0 = time.time()
    PROGRESS.update(running=True, pages=0, products=0, failed=0, last_error="", started=t0, finished=0, shop="")
    total = {"products": 0, "pages": 0, "failed": 0}
    per_shop: dict[str, int] = {}
    checks: dict[str, int] = {}
    async with aiohttp.ClientSession(headers=HEADERS) as s:
        for shop, cfg in SHOPS.items():
            PROGRESS["shop"] = shop
            seen_pages: set[str] = set()
            products: dict[str, dict] = {}
            slugs: set[str] = set()
            queue = list(cfg["seeds"])
            pages = blocked = 0
            while queue and pages < max_pages:
                url = queue.pop(0)
                if url in seen_pages or url in products:
                    continue
                seen_pages.add(url)
                PROGRESS["last_url"] = url
                html = await _get(s, url)
                pages += 1
                if html is None:
                    blocked += 1
                    PROGRESS.update(failed=total["failed"] + blocked)
                    continue
                rows = parse_listing(html, url, shop)
                fresh = [r for r in rows if r["url"] not in products]
                for r in rows:
                    products[r["url"]] = r
                    slugs.add(r["slug"])
                if rows:
                    _save(rows, t0)
                    n = 2
                    while n < 200:  # walk the following pages of this listing
                        h = await _get(s, f"{url}?page={n}")
                        pages += 1
                        await asyncio.sleep(delay)
                        if h is None:
                            break
                        more = [r for r in parse_listing(h, url, shop) if r["url"] not in products]
                        if not more:
                            break
                        for r in more:
                            products[r["url"]] = r
                            slugs.add(r["slug"])
                        _save(more, t0)
                        PROGRESS.update(pages=total["pages"] + pages, products=total["products"] + len(slugs))
                        n += 1
                for link in category_links(html, url, shop):
                    if link not in seen_pages and link not in products:
                        queue.append(link)
                PROGRESS.update(pages=total["pages"] + pages, products=total["products"] + len(slugs))
                await asyncio.sleep(delay)
            if products:
                c = db()
                with c:  # gone from the site (only this shop)
                    c.execute("DELETE FROM products WHERE shop = ? AND updated < ?", (shop, t0))
                c.close()
            PROGRESS["phase"] = "цены на страницах товаров"
            en = await enrich(s, shop)
            checks[shop] = en["check"]
            PROGRESS["phase"] = ""
            per_shop[shop] = len(slugs)
            total["products"] += len(slugs)
            total["pages"] += pages
            total["failed"] += blocked
    try:
        import json
        json.dump({"v": CRAWL_VERSION, "t": time.time(), "products": total["products"]}, open(MARKER, "w"))
    except Exception:
        log.warning("cannot write crawl marker")
    PROGRESS.update(running=False, finished=time.time(), shop="", pages=total["pages"], products=total["products"], failed=total["failed"])
    return {**total, "per_shop": per_shop, "check": checks, "secs": round(time.time() - t0)}


RESERVE = 0.10  # tiles: +10% for cuts, so the order surely suffices


def tile_plan(price_m2: float, pack_m2: float, area_m2: float, reserve: float = RESERVE) -> dict:
    """Whole boxes only: area + reserve, rounded up to boxes. Totals in cents via integers."""
    import math
    need = area_m2 * (1 + reserve)
    boxes = max(1, math.ceil(round(need / pack_m2, 6)))
    box_cents = round(price_m2 * pack_m2 * 100)
    return {"need_m2": round(need, 2), "boxes": boxes, "boxes_m2": round(boxes * pack_m2, 2),
            "box_price_eur": box_cents / 100, "total_eur": boxes * box_cents / 100}


def stats() -> dict:
    c = db()
    n = c.execute("SELECT COUNT(*), MAX(updated) FROM products").fetchone()
    per = {r[0] or "euroled": r[1] for r in c.execute("SELECT shop, COUNT(*) FROM products GROUP BY shop")}
    c.close()
    return {"products": n[0], "updated": n[1], "per_shop": per}


# real order price: per-metre goods are compared by the total for their length
EFF = "(CASE WHEN unit = 'm' AND length_m IS NOT NULL THEN COALESCE(total, price * length_m) ELSE price END)"


# Russian/English words callers use -> stems that appear in Latvian product titles.
_SYN = {
    "плитка": "flīz", "плитки": "flīz", "плитку": "flīz", "tiles": "flīz", "tile": "flīz", "flīzes": "flīz",
    "гирлянда": "virten", "гирлянду": "virten", "гирлянды": "virten", "garland": "virten", "virtene": "virten", "virtenes": "virten",
    "серая": "pelēk", "серую": "pelēk", "серый": "pelēk", "grey": "pelēk", "gray": "pelēk", "pelēkas": "pelēk",
    "белая": "balt", "белую": "balt", "белый": "balt", "white": "balt", "baltas": "balt",
    "черная": "meln", "чёрная": "meln", "black": "meln",
    "зеркало": "spoguļ", "зеркала": "spoguļ", "mirror": "spoguļ",
    "матовая": "matēt", "матовую": "matēt", "глянцевая": "spīdīg", "глянцевую": "spīdīg",
    "светильник": "gaismek", "светильники": "gaismek", "лампа": "spuldz", "лампочка": "spuldz",
    "прожектор": "prožektor", "лента": "lent", "ленту": "lent",
}
_STOP = {"для", "на", "и", "в", "с", "по", "the", "for", "a", "of"}


def _query_words(query: str) -> list[str]:
    """Lower-case words, mapped to stems; words found in no product are dropped (so 'pelēkas' or a
    stray word cannot empty the result)."""
    words = []
    for w in (query or "").lower().replace('"', " ").split():
        if w in _STOP:
            continue
        words.append(_SYN.get(w, w))
    if len(words) < 2:
        return words
    c = db()
    keep = []
    for w in words:
        n = c.execute("SELECT 1 FROM products WHERE lower(title) LIKE ? OR cats LIKE ? LIMIT 1",
                      (f"%{w}%", f"%{w}%")).fetchone()
        if n:
            keep.append(w)
    c.close()
    return keep or words


def search(query: str = "", category: str = "", length_m: float | None = None, length_tol: float = 0.0,
           min_length: float | None = None, max_price: float | None = None, min_ip: int | None = None,
           in_stock: bool = True, sort: str = "price", limit: int = 5, shop: str = "",
           use: str = "", christmas: bool = False) -> list[dict]:
    """Filter by facts; length_m + length_tol gives a range. sort: price | price_desc | length | discount."""
    where, args = ["1=1"], []
    for w in _query_words(query):
        where.append("(lower(title) LIKE ? OR cats LIKE ?)")
        args += [f"%{w}%", f"%{w}%"]
    if category:
        where.append("cats LIKE ?")
        args.append(f"%{category}%")
    if length_m is not None:
        where.append("length_m BETWEEN ? AND ?")
        args += [length_m - length_tol, length_m + length_tol]
    if min_length is not None:
        where.append("length_m >= ?")
        args.append(min_length)
    if max_price is not None:
        where.append(EFF + " <= ?")
        args.append(max_price)
    if min_ip is not None:
        where.append("ip >= ?")
        args.append(min_ip)
    if christmas:
        where.append("(('|' || cats || '|') LIKE ? OR ('|' || cats) LIKE ?)")
        args += [f"%|{taxonomy.CHRISTMAS}|%", f"%|{taxonomy.CHRISTMAS}/%"]
    if use in ("in", "out"):
        sl = taxonomy.slugs_for_use(use)
        where.append("(" + " OR ".join(["('|' || cats || '|') LIKE ? OR ('|' || cats) LIKE ?"] * len(sl)) + ")")
        for x in sl:
            args += [f"%|{x}|%", f"%|{x}/%"]
    if shop:
        where.append("shop = ?")
        args.append(shop)
    if in_stock:
        where.append("stock > 0")
    order = {"price": EFF + " ASC", "price_desc": EFF + " DESC", "length": "length_m ASC",
             "discount": "discount DESC"}.get(sort, "price ASC")
    c = db()
    rows = c.execute(f"SELECT * FROM products WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ?",
                     args + [limit * 4]).fetchall()
    c.close()
    out, seen = [], set()
    for r in rows:
        key = (r["shop"], r["title"], r["price"], r["length_m"])
        if key in seen:
            continue
        seen.add(key)
        if len(out) >= limit:
            break
        d = dict(r)
        d.update(taxonomy.info(d.get("category", "")))
        d["warning"] = "Без лампочек: лампы E27 покупаются отдельно" if "bez spuldz" in d["title"].lower() else None
        d["total_eur"] = d["total"] if d.get("unit") == "m" and d.get("total_src") == "site" else None
        d["box_price_eur"] = round(d["price"] * d["pack_m2"], 2) if d.get("unit") == "m2" and d.get("pack_m2") else None
        out.append(d)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(asyncio.run(crawl()))
