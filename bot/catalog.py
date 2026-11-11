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
  color TEXT, battery INTEGER, updated REAL, shop TEXT DEFAULT 'euroled', unit TEXT, size_cm TEXT);
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
        old_el = a.select_one(".product__price-old")
        old = None
        if old_el:
            m = re.search(r"\d+(?:[.,]\d+)?", old_el.get_text())
            old = _num(m.group(0)) if m else None
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
        unit = "m2" if ("m²" in cur or "m2" in cur) else "gab"
        row = {"url": url, "title": title, "category": category, "price": price,
               "price_old": old, "discount": disc, "stock": stock, "shop": shop, "unit": unit}
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
    for name, typ in (("shop", "TEXT DEFAULT 'euroled'"), ("unit", "TEXT"), ("size_cm", "TEXT")):
        if name not in cols:
            c.execute(f"ALTER TABLE products ADD COLUMN {name} {typ}")
    c.commit()
    return c


PROGRESS: dict = {"running": False, "pages": 0, "products": 0, "failed": 0, "last_error": "", "last_url": "", "started": 0, "finished": 0}


def _save(rows, now):
    c = db()
    with c:
        for r in rows:
            r = dict(r, updated=now)
            c.execute(
                "INSERT OR REPLACE INTO products(url,title,category,price,price_old,discount,stock,"
                "length_m,ip,watts,leds,kelvin,color,battery,updated,shop,unit,size_cm) VALUES(:url,:title,:category,:price,"
                ":price_old,:discount,:stock,:length_m,:ip,:watts,:leds,:kelvin,:color,:battery,:updated,:shop,:unit,:size_cm)", r)
    c.close()


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
    async with aiohttp.ClientSession(headers=HEADERS) as s:
        for shop, cfg in SHOPS.items():
            PROGRESS["shop"] = shop
            seen_pages: set[str] = set()
            products: dict[str, dict] = {}
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
                        _save(more, t0)
                        PROGRESS.update(pages=total["pages"] + pages, products=total["products"] + len(products))
                        n += 1
                for link in category_links(html, url, shop):
                    if link not in seen_pages and link not in products:
                        queue.append(link)
                PROGRESS.update(pages=total["pages"] + pages, products=total["products"] + len(products))
                await asyncio.sleep(delay)
            if products:
                c = db()
                with c:  # gone from the site (only this shop)
                    c.execute("DELETE FROM products WHERE shop = ? AND updated < ?", (shop, t0))
                c.close()
            per_shop[shop] = len(products)
            total["products"] += len(products)
            total["pages"] += pages
            total["failed"] += blocked
    PROGRESS.update(running=False, finished=time.time(), shop="", pages=total["pages"], products=total["products"], failed=total["failed"])
    return {**total, "per_shop": per_shop, "secs": round(time.time() - t0)}


def stats() -> dict:
    c = db()
    n = c.execute("SELECT COUNT(*), MAX(updated) FROM products").fetchone()
    per = {r[0] or "euroled": r[1] for r in c.execute("SELECT shop, COUNT(*) FROM products GROUP BY shop")}
    c.close()
    return {"products": n[0], "updated": n[1], "per_shop": per}


def search(query: str = "", category: str = "", length_m: float | None = None, length_tol: float = 0.0,
           min_length: float | None = None, max_price: float | None = None, min_ip: int | None = None,
           in_stock: bool = True, sort: str = "price", limit: int = 5, shop: str = "") -> list[dict]:
    """Filter by facts; length_m + length_tol gives a range. sort: price | price_desc | length | discount."""
    where, args = ["1=1"], []
    for w in (query or "").lower().split():
        where.append("(lower(title) LIKE ? OR category LIKE ?)")
        args += [f"%{w}%", f"%{w}%"]
    if category:
        where.append("category LIKE ?")
        args.append(f"%{category}%")
    if length_m is not None:
        where.append("length_m BETWEEN ? AND ?")
        args += [length_m - length_tol, length_m + length_tol]
    if min_length is not None:
        where.append("length_m >= ?")
        args.append(min_length)
    if max_price is not None:
        where.append("price <= ?")
        args.append(max_price)
    if min_ip is not None:
        where.append("ip >= ?")
        args.append(min_ip)
    if shop:
        where.append("shop = ?")
        args.append(shop)
    if in_stock:
        where.append("stock > 0")
    order = {"price": "price ASC", "price_desc": "price DESC", "length": "length_m ASC",
             "discount": "discount DESC"}.get(sort, "price ASC")
    c = db()
    rows = c.execute(f"SELECT * FROM products WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ?",
                     args + [limit]).fetchall()
    c.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(asyncio.run(crawl()))
