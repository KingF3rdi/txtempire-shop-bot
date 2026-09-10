"""Pack AI License-API — läuft auf demselben HTTP-Server wie die MC-API.

Endpoints (kompatibel mit pack-ai-native/license-server + PackAI.exe):
  GET  /health
  GET  /plans
  POST /admin/create   (Header X-PackAI-Secret)
  POST /admin/revoke
  POST /activate
  POST /validate
  POST /consume
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from aiohttp import web

import config

PLANS: dict[str, dict[str, Any]] = {
    "14d": {
        "id": "14d",
        "name": "14 Tage",
        "duration_days": 14,
        "tokens": 50,
        "description": "Starter — 50 Pack-AI-Tokens, 14 Tage",
    },
    "30d": {
        "id": "30d",
        "name": "30 Tage",
        "duration_days": 30,
        "tokens": 200,
        "description": "Standard — 200 Pack-AI-Tokens, 30 Tage",
    },
    "lifetime": {
        "id": "lifetime",
        "name": "Lifetime",
        "duration_days": None,
        "tokens": 2000,
        "description": "Lifetime — 2000 Tokens, kein Ablauf",
    },
}


def _db_path() -> Path:
    return Path(config.DATA_DIR) / "packai_licenses.db"


def _secret() -> str:
    return (getattr(config, "PACKAI_LICENSE_API_SECRET", "") or "").strip()


def _utc_now() -> int:
    return int(time.time())


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS licenses (
              key TEXT PRIMARY KEY,
              plan TEXT NOT NULL,
              tokens_total INTEGER NOT NULL,
              tokens_left INTEGER NOT NULL,
              duration_days INTEGER,
              created_at INTEGER NOT NULL,
              activated_at INTEGER,
              expires_at INTEGER,
              hwid TEXT,
              created_by TEXT,
              note TEXT,
              revoked INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              key TEXT,
              kind TEXT,
              detail TEXT,
              created_at INTEGER
            )
            """
        )
        conn.commit()


def _admin_ok(request: web.Request) -> bool:
    expected = _secret()
    if not expected:
        return False
    auth = (request.headers.get("X-PackAI-Secret") or "").strip()
    return hmac.compare_digest(auth, expected)


def _make_key(plan: str) -> str:
    raw = secrets.token_hex(8).upper()
    chunks = [raw[i : i + 4] for i in range(0, 16, 4)]
    tag = {"14d": "14D", "30d": "30D", "lifetime": "LIFE"}.get(plan, plan.upper()[:4])
    return f"PACKAI-{tag}-{'-'.join(chunks)}"


def _is_active(row: sqlite3.Row) -> bool:
    if row["revoked"]:
        return False
    if row["hwid"] is None:
        return False
    if row["expires_at"] is not None and _utc_now() > int(row["expires_at"]):
        return False
    return True


def _row_public(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "key": row["key"],
        "plan": row["plan"],
        "plan_name": PLANS.get(row["plan"], {}).get("name", row["plan"]),
        "tokens_total": row["tokens_total"],
        "tokens_left": row["tokens_left"],
        "duration_days": row["duration_days"],
        "created_at": row["created_at"],
        "activated_at": row["activated_at"],
        "expires_at": row["expires_at"],
        "hwid": row["hwid"],
        "revoked": bool(row["revoked"]),
        "active": _is_active(row),
    }


async def _json(request: web.Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def register_routes(app: web.Application) -> None:
    """Hängt Pack-AI-Routen an die bestehende aiohttp-App."""
    init_db()
    app.router.add_get("/health", health)
    app.router.add_get("/plans", plans)
    app.router.add_post("/admin/create", admin_create)
    app.router.add_post("/admin/revoke", admin_revoke)
    app.router.add_post("/activate", activate)
    app.router.add_post("/validate", validate)
    app.router.add_post("/consume", consume)
    print(
        f"[PackAI-API] Routen aktiv (DB={_db_path()}) · "
        + ("Secret gesetzt" if _secret() else "⚠️ PACKAI_LICENSE_API_SECRET fehlt")
    )


async def health(_: web.Request) -> web.Response:
    return web.json_response(
        {"ok": True, "service": "packai-license", "plans": list(PLANS.keys())}
    )


async def plans(_: web.Request) -> web.Response:
    return web.json_response({"plans": PLANS})


async def admin_create(request: web.Request) -> web.Response:
    if not _admin_ok(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    data = await _json(request)
    plan = str(data.get("plan", "")).lower()
    if plan not in PLANS:
        return web.json_response(
            {"error": "invalid_plan", "allowed": list(PLANS.keys())}, status=400
        )
    count = max(1, min(50, int(data.get("count", 1) or 1)))
    created_by = str(data.get("created_by", "api"))
    note = str(data.get("note", ""))
    p = PLANS[plan]
    keys: list[str] = []
    with _connect() as conn:
        for _ in range(count):
            key = _make_key(plan)
            conn.execute(
                """
                INSERT INTO licenses(
                  key, plan, tokens_total, tokens_left, duration_days,
                  created_at, created_by, note
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    key,
                    plan,
                    int(p["tokens"]),
                    int(p["tokens"]),
                    p["duration_days"],
                    _utc_now(),
                    created_by,
                    note,
                ),
            )
            conn.execute(
                "INSERT INTO events(key, kind, detail, created_at) VALUES(?,?,?,?)",
                (key, "created", f"plan={plan} by={created_by}", _utc_now()),
            )
            keys.append(key)
        conn.commit()
    return web.json_response({"ok": True, "keys": keys, "plan": p})


async def admin_revoke(request: web.Request) -> web.Response:
    if not _admin_ok(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    data = await _json(request)
    key = str(data.get("key", "")).strip().upper()
    with _connect() as conn:
        conn.execute("UPDATE licenses SET revoked=1 WHERE key=?", (key,))
        conn.execute(
            "INSERT INTO events(key, kind, detail, created_at) VALUES(?,?,?,?)",
            (key, "revoked", "admin", _utc_now()),
        )
        conn.commit()
    return web.json_response({"ok": True})


async def activate(request: web.Request) -> web.Response:
    data = await _json(request)
    key = str(data.get("key", "")).strip().upper()
    hwid = str(data.get("hwid", "")).strip()
    if not key or not hwid or len(hwid) < 8:
        return web.json_response({"error": "key_and_hwid_required"}, status=400)
    with _connect() as conn:
        row = conn.execute("SELECT * FROM licenses WHERE key=?", (key,)).fetchone()
        if not row:
            return web.json_response({"error": "invalid_key"}, status=404)
        if row["revoked"]:
            return web.json_response({"error": "revoked"}, status=403)
        if row["hwid"] and row["hwid"] != hwid:
            return web.json_response(
                {
                    "error": "hwid_mismatch",
                    "message": "Key ist an eine andere HWID gebunden",
                },
                status=403,
            )
        expires_at = row["expires_at"]
        activated_at = row["activated_at"]
        if activated_at is None:
            activated_at = _utc_now()
            if row["duration_days"] is None:
                expires_at = None
            else:
                expires_at = activated_at + int(row["duration_days"]) * 86400
            conn.execute(
                "UPDATE licenses SET hwid=?, activated_at=?, expires_at=? WHERE key=?",
                (hwid, activated_at, expires_at, key),
            )
            conn.execute(
                "INSERT INTO events(key, kind, detail, created_at) VALUES(?,?,?,?)",
                (key, "activated", f"hwid={hwid[:12]}…", _utc_now()),
            )
            conn.commit()
        row = conn.execute("SELECT * FROM licenses WHERE key=?", (key,)).fetchone()
        if row["expires_at"] is not None and _utc_now() > int(row["expires_at"]):
            return web.json_response({"error": "expired"}, status=403)
        return web.json_response({"ok": True, "license": _row_public(row)})


async def validate(request: web.Request) -> web.Response:
    data = await _json(request)
    key = str(data.get("key", "")).strip().upper()
    hwid = str(data.get("hwid", "")).strip()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM licenses WHERE key=?", (key,)).fetchone()
        if not row:
            return web.json_response({"ok": False, "error": "invalid_key"}, status=404)
        if row["revoked"]:
            return web.json_response({"ok": False, "error": "revoked"}, status=403)
        if not row["hwid"]:
            return web.json_response({"ok": False, "error": "not_activated"}, status=403)
        if row["hwid"] != hwid:
            return web.json_response({"ok": False, "error": "hwid_mismatch"}, status=403)
        if row["expires_at"] is not None and _utc_now() > int(row["expires_at"]):
            return web.json_response({"ok": False, "error": "expired"}, status=403)
        return web.json_response({"ok": True, "license": _row_public(row)})


async def consume(request: web.Request) -> web.Response:
    data = await _json(request)
    key = str(data.get("key", "")).strip().upper()
    hwid = str(data.get("hwid", "")).strip()
    amount = max(1, int(data.get("amount", 1) or 1))
    reason = str(data.get("reason", "generate"))
    with _connect() as conn:
        row = conn.execute("SELECT * FROM licenses WHERE key=?", (key,)).fetchone()
        if not row:
            return web.json_response({"error": "invalid_key"}, status=404)
        if row["revoked"] or not row["hwid"] or row["hwid"] != hwid:
            return web.json_response({"error": "forbidden"}, status=403)
        if row["expires_at"] is not None and _utc_now() > int(row["expires_at"]):
            return web.json_response({"error": "expired"}, status=403)
        left = int(row["tokens_left"])
        if left < amount:
            return web.json_response(
                {"error": "no_tokens", "tokens_left": left}, status=402
            )
        left -= amount
        conn.execute("UPDATE licenses SET tokens_left=? WHERE key=?", (left, key))
        conn.execute(
            "INSERT INTO events(key, kind, detail, created_at) VALUES(?,?,?,?)",
            (key, "consume", f"-{amount} ({reason}) left={left}", _utc_now()),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM licenses WHERE key=?", (key,)).fetchone()
        return web.json_response({"ok": True, "license": _row_public(row)})
