"""Creates (or updates) the ElevenLabs webhook tool `search_catalog` and attaches it to the agent. Idempotent."""
from __future__ import annotations
import json
import logging
import os

log = logging.getLogger("tools")
STATE = os.environ.get("TOOL_STATE", "/var/lib/euroled-admin/tool.json")
NAME = "search_catalog"

DESCRIPTION = (
    "Поиск товаров в каталоге магазинов euroled.lv (освещение) и euroflizes.lv (плитка, сантехника, декор). "
    "Вызывай ВСЕГДА перед тем, как назвать цену, наличие или характеристики товара. Никогда не называй цену из головы. "
    "В ответе price_eur указана цена с единицей price_unit (за метр, за м², за штуку). Если есть total_eur, называй именно общую сумму. "
    "Поле warning и note обязательно учитывай и честно говори клиенту. "
    "Если ничего не найдено, предложи ближайшую альтернативу или передай запрос менеджеру."
)


def tool_config(host: str, token: str) -> dict:
    def p(desc, typ="string"):
        return {"type": typ, "description": desc}
    return {"tool_config": {
        "type": "webhook", "name": NAME, "description": DESCRIPTION, "response_timeout_secs": 15,
        "api_schema": {
            "url": f"https://{host}/api/search", "method": "GET",
            "query_params_schema": {"properties": {
                "query": p("Ключевые слова на латышском, например: virtene, prozektors, lenta, flizes, spogulis"),
                "length_m": p("Нужная длина в метрах (например 15). Ищет в допуске ±3 м", "number"),
                "max_price": p("Максимальная цена в евро за единицу", "number"),
                "use": p("Где будет использоваться: out (на улице) или in (в помещении)"),
                "christmas": p("1 если ищут новогоднее или рождественское освещение"),
                "shop": p("euroled или euroflizes. Пусто значит оба"),
                "min_ip": p("Минимальная степень защиты IP, например 44 для улицы", "integer"),
                "in_stock": p("1 только в наличии (по умолчанию), 0 включая отсутствующие"),
                "sort": p("price (дешёвые первыми), price_desc, length или discount"),
                "limit": p("Сколько вариантов вернуть, от 1 до 10", "integer"),
            }, "required": []},
            "request_headers": {"X-Tool-Token": token},
        }}}


async def setup(eleven, host: str, token: str) -> str:
    """Create or update the tool, attach to agent. Returns a short report."""
    try:
        saved = json.load(open(STATE))
    except Exception:
        saved = {}
    cfg = tool_config(host, token)
    tool_id = saved.get("tool_id")
    if tool_id:
        try:
            await eleven._req("PATCH", f"/convai/tools/{tool_id}", json=cfg)
        except RuntimeError as e:
            log.warning("patch tool failed, recreating: %s", e)
            tool_id = None
    if not tool_id:
        res = await eleven._req("POST", "/convai/tools", json=cfg)
        tool_id = res["id"]
    a = await eleven.get_agent()
    ids = list(a["conversation_config"]["agent"]["prompt"].get("tool_ids") or [])
    if tool_id not in ids:
        ids.append(tool_id)
        await eleven._patch_agent({"agent": {"prompt": {"tool_ids": ids}}})
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump({"tool_id": tool_id, "host": host}, open(STATE, "w"))
    return f"Инструмент {NAME} подключён к агенту (id {tool_id})."
