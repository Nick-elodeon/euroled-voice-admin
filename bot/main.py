"""EUROLED voice agent admin: Telegram bot + post-call webhook in one process."""
import asyncio
import io
import json
import logging
import os

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)
from aiohttp import web

from eleven import Eleven, verify_webhook

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("admin")

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED = {int(x) for x in os.environ["ALLOWED_USER_IDS"].split(",") if x.strip()}
WEBHOOK_SECRET = os.environ.get("ELEVEN_WEBHOOK_SECRET", "")
eleven = Eleven(os.environ["ELEVEN_API_KEY"], os.environ["ELEVEN_AGENT_ID"], os.environ.get("ELEVEN_BRANCH_ID"))

bot = Bot(TOKEN)
router = Router()
dp = Dispatcher()
dp.include_router(router)

# pending[user_id] = {"kind": "prompt"|"first"|"kb"|"error", ...}; lost on restart, which is fine
pending: dict[int, dict] = {}
prev_prompt: dict[str, str] = {}  # last prompt before a change, for /rollback


def allowed(m) -> bool:
    return m.from_user and m.from_user.id in ALLOWED


def confirm_kb(action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Применить", callback_data=f"ok:{action}"),
        InlineKeyboardButton(text="✖ Отмена", callback_data="no"),
    ]])


HELP = (
    "Команды:\n"
    "/prompt: текущий промпт файлом\n"
    "/rule текст: добавить правило в конец промпта\n"
    "/setprompt: заменить промпт целиком (пришлёшь текстом или файлом)\n"
    "/rollback: вернуть предыдущий промпт\n"
    "/first: показать первое сообщение, /setfirst текст: заменить\n"
    "/kb: список документов базы знаний\n"
    "/kbadd название: добавить знание (следующим сообщением текст или голос)\n"
    "/kbdel номер: удалить документ по номеру из /kb\n"
    "Под саммари звонка кнопка «Ошибка» превращает замечание в правило."
)


@router.message(Command("start", "help"))
async def start(m: Message):
    if not allowed(m):
        return await m.answer(f"Нет доступа. Ваш ID: {m.from_user.id}")
    await m.answer(HELP)


@router.message(Command("prompt"))
async def show_prompt(m: Message):
    if not allowed(m):
        return
    text = await eleven.get_prompt()
    await m.answer_document(BufferedInputFile(text.encode(), "prompt.txt"), caption=f"{len(text)} символов")


@router.message(Command("rule"))
async def add_rule(m: Message, command: CommandObject):
    if not allowed(m) or not command.args:
        return await m.answer("Использование: /rule текст правила")
    pending[m.from_user.id] = {"kind": "rule", "text": command.args.strip()}
    await m.answer(f"Добавить в конец промпта:\n\n{command.args}", reply_markup=confirm_kb("rule"))


@router.message(Command("setprompt"))
async def set_prompt(m: Message):
    if not allowed(m):
        return
    pending[m.from_user.id] = {"kind": "await_prompt"}
    await m.answer("Пришли новый промпт текстом или .txt файлом.")


@router.message(Command("rollback"))
async def rollback(m: Message):
    if not allowed(m):
        return
    if "p" not in prev_prompt:
        return await m.answer("Предыдущей версии в памяти нет (после перезапуска сервера она сбрасывается). "
                              "Каждая правка присылается файлом в чат, возьми оттуда и сделай /setprompt.")
    pending[m.from_user.id] = {"kind": "full", "text": prev_prompt["p"]}
    await m.answer("Вернуть предыдущий промпт?", reply_markup=confirm_kb("full"))


@router.message(Command("first"))
async def first(m: Message):
    if allowed(m):
        await m.answer(await eleven.get_first_message())


@router.message(Command("setfirst"))
async def setfirst(m: Message, command: CommandObject):
    if not allowed(m) or not command.args:
        return await m.answer("Использование: /setfirst текст")
    pending[m.from_user.id] = {"kind": "first", "text": command.args.strip()}
    await m.answer(f"Новое первое сообщение:\n\n{command.args}", reply_markup=confirm_kb("first"))


@router.message(Command("kb"))
async def kb_list(m: Message):
    if not allowed(m):
        return
    docs = await eleven.kb_attached()
    pending[m.from_user.id] = {"kind": "kblist", "docs": docs}
    await m.answer("\n".join(f"{i + 1}. {d['name']}" for i, d in enumerate(docs)) or "База пуста")


@router.message(Command("kbdel"))
async def kb_del(m: Message, command: CommandObject):
    if not allowed(m):
        return
    docs = pending.get(m.from_user.id, {}).get("docs")
    if not docs or not command.args or not command.args.strip().isdigit():
        return await m.answer("Сначала /kb, потом /kbdel номер")
    i = int(command.args) - 1
    if not 0 <= i < len(docs):
        return await m.answer("Нет такого номера")
    pending[m.from_user.id] = {"kind": "kbdel", "doc": docs[i]}
    await m.answer(f"Удалить «{docs[i]['name']}»?", reply_markup=confirm_kb("kbdel"))


@router.message(Command("kbadd"))
async def kb_add(m: Message, command: CommandObject):
    if not allowed(m) or not command.args:
        return await m.answer("Использование: /kbadd название")
    pending[m.from_user.id] = {"kind": "await_kb", "name": command.args.strip()}
    await m.answer("Пришли текст знания или голосовое сообщение.")


async def extract_text(m: Message) -> str | None:
    if m.voice or m.audio:
        f = await bot.download(m.voice or m.audio)
        return await eleven.transcribe(f.read())
    if m.document:
        f = await bot.download(m.document)
        return f.read().decode("utf-8", "ignore")
    return m.text


@router.message(F.text | F.voice | F.audio | F.document)
async def free_input(m: Message):
    if not allowed(m):
        return
    st = pending.get(m.from_user.id)
    if not st:
        return await m.answer(HELP)
    kind = st["kind"]
    if kind == "await_prompt":
        text = await extract_text(m)
        pending[m.from_user.id] = {"kind": "full", "text": text}
        return await m.answer(f"Новый промпт, {len(text)} символов. Применить?", reply_markup=confirm_kb("full"))
    if kind == "await_kb":
        text = await extract_text(m)
        pending[m.from_user.id] = {"kind": "kb", "name": st["name"], "text": text}
        return await m.answer(f"Добавить в базу «{st['name']}»:\n\n{text[:1500]}", reply_markup=confirm_kb("kb"))
    if kind == "await_error":
        text = await extract_text(m)
        pending[m.from_user.id] = {"kind": "rule", "text": f"Исправление по реальному звонку: {text}"}
        return await m.answer(f"Превратить в правило:\n\n{text}", reply_markup=confirm_kb("rule"))


@router.callback_query(F.data == "no")
async def cancel(c: CallbackQuery):
    pending.pop(c.from_user.id, None)
    await c.message.edit_text("Отменено")


@router.callback_query(F.data.startswith("ok:"))
async def apply(c: CallbackQuery):
    if c.from_user.id not in ALLOWED:
        return await c.answer("Нет доступа", show_alert=True)
    st = pending.pop(c.from_user.id, None)
    if not st:
        return await c.answer("Устарело, повтори команду", show_alert=True)
    try:
        if st["kind"] in ("rule", "full"):
            old = await eleven.get_prompt()
            await c.message.answer_document(BufferedInputFile(old.encode(), "prompt_before.txt"),
                                            caption="Бэкап промпта до правки")
            prev_prompt["p"] = old
            new = old.rstrip() + "\n\n# Правило\n" + st["text"] if st["kind"] == "rule" else st["text"]
            await eleven.set_prompt(new)
        elif st["kind"] == "first":
            await eleven.set_first_message(st["text"])
        elif st["kind"] == "kb":
            await eleven.kb_add_text(st["name"], st["text"])
        elif st["kind"] == "kbdel":
            await eleven.kb_delete(st["doc"]["id"])
        await c.message.edit_text("Готово ✅ Изменение сохранено в агенте (черновик/ветка опубликуйте при необходимости).")
    except Exception as e:  # surface API errors to the admin
        log.exception("apply failed")
        await c.message.edit_text(f"Ошибка: {e}")


@router.callback_query(F.data.startswith("err:"))
async def mark_error(c: CallbackQuery):
    if c.from_user.id not in ALLOWED:
        return await c.answer("Нет доступа", show_alert=True)
    pending[c.from_user.id] = {"kind": "await_error", "conv": c.data[4:]}
    await c.message.answer("Что бот сделал не так? Напиши или надиктуй, как должно быть.")
    await c.answer()


# ---------------- post-call webhook ----------------
async def webhook(request: web.Request):
    body = await request.read()
    if WEBHOOK_SECRET and not verify_webhook(WEBHOOK_SECRET, body, request.headers.get("ElevenLabs-Signature", "")):
        return web.Response(status=401)
    asyncio.create_task(handle_call(json.loads(body)))  # answer fast, work in background
    return web.Response(text="ok")


async def handle_call(payload: dict):
    try:
        d = payload.get("data", payload)
        conv_id = d.get("conversation_id", "")
        analysis = d.get("analysis", {}) or {}
        summary = analysis.get("transcript_summary") or "(без саммари)"
        meta = d.get("metadata", {}) or {}
        dur = meta.get("call_duration_secs", "?")
        collected = analysis.get("data_collection_results") or {}
        contacts = "\n".join(f"• {k}: {v.get('value')}" for k, v in collected.items() if v.get("value"))
        text = f"📞 Звонок {dur} с\n\n{summary}" + (f"\n\n{contacts}" if contacts else "")
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⚠ Ошибка бота", callback_data=f"err:{conv_id}")]])
        for uid in ALLOWED:
            await bot.send_message(uid, text[:4000], reply_markup=kb)
            lines = [f"{t.get('role')}: {t.get('message')}" for t in d.get("transcript", []) if t.get("message")]
            if lines:
                await bot.send_document(uid, BufferedInputFile("\n".join(lines).encode(), f"call_{conv_id[:8]}.txt"))
    except Exception:
        log.exception("post-call handling failed")


async def health(_):
    return web.Response(text="ok")


# ---------------- polling (no inbound port or HTTPS needed) ----------------
STATE_FILE = os.environ.get("STATE_FILE", "state.json")
POLL_SECS = int(os.environ.get("POLL_SECS", "60"))


def load_state() -> dict:
    try:
        return json.load(open(STATE_FILE))
    except Exception:
        return {}


def save_state(st: dict):
    os.makedirs(os.path.dirname(os.path.abspath(STATE_FILE)), exist_ok=True)
    json.dump(st, open(STATE_FILE, "w"))


async def poll_calls():
    """Every POLL_SECS look for finished calls newer than the last seen one and report them."""
    import time
    st = load_state()
    if "since" not in st:  # first start: do not replay history
        st["since"] = int(time.time())
        save_state(st)
    while True:
        try:
            res = await eleven._req("GET", "/convai/conversations",
                                    params={"agent_id": eleven.agent_id, "page_size": 20})
            convs = [c for c in res.get("conversations", [])
                     if c.get("start_time_unix_secs", 0) > st["since"] and c.get("status") == "done"]
            for c in sorted(convs, key=lambda c: c["start_time_unix_secs"]):
                full = await eleven.conversation(c["conversation_id"])
                await handle_call(full)
                st["since"] = c["start_time_unix_secs"]
                save_state(st)
        except Exception:
            log.exception("poll failed")
        await asyncio.sleep(POLL_SECS)


async def main():
    if os.environ.get("ELEVEN_WEBHOOK_SECRET"):  # optional webhook mode if HTTPS is available
        app = web.Application()
        app.router.add_post("/elevenlabs/post-call", webhook)
        app.router.add_get("/", health)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", "8080"))).start()
    else:
        asyncio.create_task(poll_calls())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
