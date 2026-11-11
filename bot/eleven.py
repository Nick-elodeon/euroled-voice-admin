"""Thin async client for the ElevenLabs Agents REST API (source of truth for prompt and KB)."""
from __future__ import annotations
import hashlib
import hmac
import time

import aiohttp

BASE = "https://api.elevenlabs.io/v1"


class Eleven:
    def __init__(self, api_key: str, agent_id: str, branch_id: str | None = None):
        self.key = api_key
        self.agent_id = agent_id
        self.branch_id = branch_id

    async def _req(self, method: str, path: str, **kw):
        headers = {"xi-api-key": self.key}
        params = kw.pop("params", {})
        async with aiohttp.ClientSession(headers=headers) as s:
            async with s.request(method, BASE + path, params=params, **kw) as r:
                if r.status >= 400:
                    raise RuntimeError(f"ElevenLabs {r.status}: {(await r.text())[:300]}")
                if r.content_type == "application/json":
                    return await r.json()
                return await r.text()

    def _branch(self):
        return {"branch_id": self.branch_id} if self.branch_id else {}

    # ---- agent ----
    async def get_agent(self):
        return await self._req("GET", f"/convai/agents/{self.agent_id}", params=self._branch())

    async def get_prompt(self) -> str:
        a = await self.get_agent()
        return a["conversation_config"]["agent"]["prompt"]["prompt"]

    async def get_first_message(self) -> str:
        a = await self.get_agent()
        return a["conversation_config"]["agent"]["first_message"]

    async def _patch_agent(self, conversation_config: dict):
        return await self._req(
            "PATCH", f"/convai/agents/{self.agent_id}",
            params=self._branch(), json={"conversation_config": conversation_config},
        )

    async def set_prompt(self, text: str):
        return await self._patch_agent({"agent": {"prompt": {"prompt": text}}})

    async def set_first_message(self, text: str):
        return await self._patch_agent({"agent": {"first_message": text}})

    # ---- knowledge base ----
    async def kb_attached(self) -> list[dict]:
        a = await self.get_agent()
        return a["conversation_config"]["agent"]["prompt"].get("knowledge_base", [])

    async def kb_add_text(self, name: str, text: str) -> str:
        doc = await self._req("POST", "/convai/knowledge-base/text", json={"name": name, "text": text})
        doc_id = doc["id"]
        await self._req(
            "POST", f"/convai/knowledge-base/{doc_id}/rag-index",
            json={"model": "multilingual_e5_large_instruct"},
        )
        kb = await self.kb_attached()
        kb.append({"type": "text", "name": name, "id": doc_id, "usage_mode": "auto"})
        await self._patch_agent({"agent": {"prompt": {"knowledge_base": kb}}})
        return doc_id

    async def kb_delete(self, doc_id: str):
        kb = [d for d in await self.kb_attached() if d["id"] != doc_id]
        await self._patch_agent({"agent": {"prompt": {"knowledge_base": kb}}})
        await self._req("DELETE", f"/convai/knowledge-base/{doc_id}", params={"force": "true"})

    # ---- conversations / speech ----
    async def conversation(self, conversation_id: str):
        return await self._req("GET", f"/convai/conversations/{conversation_id}")

    async def conversations(self, page_size: int = 30, cursor: str | None = None):
        params = {"agent_id": self.agent_id, "page_size": page_size}
        if cursor:
            params["cursor"] = cursor
        return await self._req("GET", "/convai/conversations", params=params)

    async def audio_url_bytes(self, conversation_id: str) -> bytes:
        async with aiohttp.ClientSession(headers={"xi-api-key": self.key}) as s:
            async with s.get(f"{BASE}/convai/conversations/{conversation_id}/audio") as r:
                if r.status >= 400:
                    raise RuntimeError(f"audio {r.status}")
                return await r.read()

    async def get_voice(self) -> dict:
        a = await self.get_agent()
        return a["conversation_config"].get("tts", {})

    async def set_voice(self, tts: dict):
        return await self._patch_agent({"tts": tts})

    async def transcribe(self, audio: bytes, filename: str = "voice.ogg") -> str:
        form = aiohttp.FormData()
        form.add_field("model_id", "scribe_v1")
        form.add_field("file", audio, filename=filename)
        res = await self._req("POST", "/speech-to-text", data=form)
        return res["text"]


def verify_webhook(secret: str, body: bytes, header: str, tolerance: int = 1800) -> bool:
    """ElevenLabs signs webhooks: header `t=<ts>,v0=<hmac_sha256(secret, f"{ts}.{body}")>`."""
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        ts, sig = parts["t"], parts["v0"]
    except Exception:
        return False
    if abs(time.time() - int(ts)) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)
