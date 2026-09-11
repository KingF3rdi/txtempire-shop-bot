"""HTTP-API für die Minecraft Chat-Watcher-Mod."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from aiohttp import web

import config
from utils.duel_invsee_store import find_active_watch_token, set_opt_in
from utils.mc_confirm import handle_mc_link_redeem, handle_mc_payment

if TYPE_CHECKING:
    from bot import ShopBot

IGN_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")
CODE_RE = re.compile(r"^[A-Z0-9\-]{4,24}$", re.I)


def _bearer_or_key(request: web.Request) -> str:
    auth = request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (request.headers.get("X-Api-Key") or "").strip()


def _auth_ok(request: web.Request) -> bool:
    expected = (config.MC_API_KEY or "").strip()
    if not expected:
        return False
    token = _bearer_or_key(request)
    return bool(token) and token == expected


def _duelinvsee_auth_ok(request: web.Request) -> bool:
    """Eigener, schwächer privilegierter Key für die Duel-Invsee-Endpunkte —
    getrennt von MC_API_KEY, da dieser an alle Spieler weitergegeben wird,
    die selbst Duel Invsee nutzen wollen (siehe minecraft-mod/)."""
    expected = (config.DUEL_INVSEE_PUBLIC_KEY or "").strip()
    if not expected:
        return False
    token = _bearer_or_key(request)
    return bool(token) and token == expected


async def _read_json(request: web.Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except (json.JSONDecodeError, ValueError):
        raise web.HTTPBadRequest(text='{"ok":false,"reason":"invalid_json"}')
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(text='{"ok":false,"reason":"invalid_json"}')
    return data


def _guild_id(data: dict[str, Any]) -> int:
    raw = data.get("guild_id")
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    return int(config.GUILD_ID or 0)


class McApiServer:
    def __init__(self, bot: ShopBot) -> None:
        self.bot = bot
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self.last_watcher_at: str | None = None
        self.last_watcher_event: str | None = None
        self.watcher_hits: int = 0

    @property
    def listening(self) -> bool:
        return self._site is not None and bool(config.MC_API_KEY)

    def _touch_watcher(self, event: str) -> None:
        from datetime import datetime, timezone

        self.last_watcher_at = datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
        self.last_watcher_event = event
        self.watcher_hits += 1

    def _app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/mc/v1/health", self.health)
        app.router.add_post("/mc/v1/heartbeat", self.heartbeat)
        app.router.add_post("/mc/v1/link", self.link)
        app.router.add_post("/mc/v1/payment", self.payment)
        app.router.add_post("/mc/v1/chat", self.chat)
        app.router.add_post("/mc/v1/duelinvsee/optin", self.duelinvsee_optin)
        app.router.add_post("/mc/v1/duelinvsee/heartbeat", self.duelinvsee_heartbeat)
        # Pack AI License-API auf demselben Port (für Bot + PackAI.exe)
        from integrations import packai_license_api

        packai_license_api.register_routes(app)
        return app

    async def start(self) -> None:
        if not config.MC_API_KEY:
            print("[MC-API] Deaktiviert — MC_API_KEY nicht gesetzt.")
            return
        self._runner = web.AppRunner(self._app())
        await self._runner.setup()
        self._site = web.TCPSite(
            self._runner, config.MC_API_HOST, int(config.MC_API_PORT)
        )
        await self._site.start()
        print(
            f"[MC-API] Listening on http://{config.MC_API_HOST}:{config.MC_API_PORT}"
        )
        print(
            f"[PackAI-API] Bot lokal: http://127.0.0.1:{int(config.MC_API_PORT)} "
            f"(PACKAI_LICENSE_API_URL) · PackAI.exe: öffentliche IP:{int(config.MC_API_PORT)}"
        )

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None

    async def health(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "ok": True,
                "service": "txtempire-mc-api",
                "listening": self.listening,
                "last_watcher_at": self.last_watcher_at,
            }
        )

    async def heartbeat(self, request: web.Request) -> web.Response:
        if not _auth_ok(request):
            raise web.HTTPUnauthorized(text='{"ok":false,"reason":"unauthorized"}')
        self._touch_watcher("heartbeat")
        return web.json_response({"ok": True})

    async def link(self, request: web.Request) -> web.Response:
        if not _auth_ok(request):
            raise web.HTTPUnauthorized(text='{"ok":false,"reason":"unauthorized"}')
        self._touch_watcher("link")
        data = await _read_json(request)
        code = str(data.get("code") or "").strip().upper()
        ign = str(data.get("ign") or "").strip()
        if not CODE_RE.match(code):
            return web.json_response(
                {"ok": False, "reason": "bad_code"}, status=400
            )
        if not IGN_RE.match(ign):
            return web.json_response(
                {"ok": False, "reason": "bad_ign"}, status=400
            )
        result = await handle_mc_link_redeem(self.bot, code=code, ign=ign)
        status = 200 if result.get("ok") else 400
        return web.json_response(result, status=status)

    async def payment(self, request: web.Request) -> web.Response:
        if not _auth_ok(request):
            raise web.HTTPUnauthorized(text='{"ok":false,"reason":"unauthorized"}')
        self._touch_watcher("payment")
        data = await _read_json(request)
        ign = str(data.get("ign") or "").strip()
        raw = str(data.get("raw") or data.get("raw_text") or "")[:500]
        guild_id = _guild_id(data)
        if not guild_id:
            return web.json_response(
                {"ok": False, "reason": "guild_id_required"}, status=400
            )
        if not IGN_RE.match(ign):
            return web.json_response(
                {"ok": False, "reason": "bad_ign"}, status=400
            )
        try:
            amount = float(data.get("amount"))
        except (TypeError, ValueError):
            return web.json_response(
                {"ok": False, "reason": "bad_amount"}, status=400
            )
        if amount <= 0:
            return web.json_response(
                {"ok": False, "reason": "bad_amount"}, status=400
            )
        result = await handle_mc_payment(
            self.bot,
            guild_id=guild_id,
            ign=ign,
            amount=amount,
            raw_text=raw,
        )
        return web.json_response(result)

    async def duelinvsee_optin(self, request: web.Request) -> web.Response:
        """Vom Ingame-Mod aufgerufen, wenn ein Spieler SELBST `/duelinvsee on`
        oder `/duelinvsee off` ausführt. Rein deklarativ — jeder meldet nur
        seinen eigenen Opt-in-Status für seinen eigenen IGN."""
        if not _duelinvsee_auth_ok(request):
            raise web.HTTPUnauthorized(text='{"ok":false,"reason":"unauthorized"}')
        self._touch_watcher("duelinvsee_optin")
        data = await _read_json(request)
        ign = str(data.get("ign") or "").strip()
        guild_id = _guild_id(data)
        if not IGN_RE.match(ign):
            return web.json_response({"ok": False, "reason": "bad_ign"}, status=400)
        if not guild_id:
            return web.json_response(
                {"ok": False, "reason": "guild_id_required"}, status=400
            )
        enabled = bool(data.get("enabled"))
        await set_opt_in(self.bot, guild_id, ign, enabled)
        return web.json_response({"ok": True, "ign": ign, "enabled": enabled})

    async def duelinvsee_heartbeat(self, request: web.Request) -> web.Response:
        """Vom Ingame-Mod alle ~10s aufgerufen (nur solange der Spieler selbst
        opted-in ist): meldet zurück, ob GERADE jemand für diesen IGN bezahlt
        hat zuzusehen, und mit welchem Token der Mod sein Inventar dann direkt
        an die Website pushen soll."""
        if not _duelinvsee_auth_ok(request):
            raise web.HTTPUnauthorized(text='{"ok":false,"reason":"unauthorized"}')
        self._touch_watcher("duelinvsee_heartbeat")
        data = await _read_json(request)
        ign = str(data.get("ign") or "").strip()
        guild_id = _guild_id(data)
        if not IGN_RE.match(ign):
            return web.json_response({"ok": False, "reason": "bad_ign"}, status=400)
        if not guild_id:
            return web.json_response(
                {"ok": False, "reason": "guild_id_required"}, status=400
            )
        token = await find_active_watch_token(self.bot, guild_id, ign)
        if token:
            return web.json_response({"ok": True, "watching": True, "token": token})
        return web.json_response({"ok": True, "watching": False})

    async def chat(self, request: web.Request) -> web.Response:
        """Generischer Chat-Event: Mod schickt Klartext, Bot parst Link/Payment."""
        if not _auth_ok(request):
            raise web.HTTPUnauthorized(text='{"ok":false,"reason":"unauthorized"}')
        self._touch_watcher("chat")
        data = await _read_json(request)
        text = str(data.get("text") or data.get("message") or "").strip()
        sender = str(data.get("sender") or data.get("ign") or "").strip()
        guild_id = _guild_id(data)
        if not text:
            return web.json_response(
                {"ok": False, "reason": "empty_text"}, status=400
            )

        from utils.mc_chat_parse import parse_chat_event

        parsed = parse_chat_event(text, sender=sender or None)
        if parsed is None:
            return web.json_response({"ok": True, "handled": False, "reason": "no_match"})

        if parsed["type"] == "link":
            result = await handle_mc_link_redeem(
                self.bot, code=parsed["code"], ign=parsed["ign"]
            )
            return web.json_response({**result, "handled": True, "type": "link"})

        if parsed["type"] == "payment":
            if not guild_id:
                return web.json_response(
                    {"ok": False, "reason": "guild_id_required"}, status=400
                )
            result = await handle_mc_payment(
                self.bot,
                guild_id=guild_id,
                ign=parsed["ign"],
                amount=float(parsed["amount"]),
                raw_text=text[:500],
            )
            return web.json_response({**result, "handled": True, "type": "payment"})

        return web.json_response({"ok": True, "handled": False})
