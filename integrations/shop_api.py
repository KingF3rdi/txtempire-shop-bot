"""Optionale Anbindung an die TxTEmpire Website-Shop-API."""

from __future__ import annotations

import httpx

import config


class ShopApiClient:
    def __init__(self) -> None:
        self.api_url = (config.SHOP_API_URL or "").rstrip("/")
        self.api_key = config.BOT_API_KEY or ""
        self.enabled = bool(self.api_url and self.api_key)

    async def fetch_catalog(self) -> dict | None:
        if not self.enabled:
            return None
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(
                    f"{self.api_url}/api/bot/catalog",
                    headers={"X-Bot-Api-Key": self.api_key},
                )
                if resp.status_code >= 400:
                    print(
                        f"[Shop API] Catalog fetch fehlgeschlagen: HTTP {resp.status_code}"
                    )
                    return None
                return resp.json()
        except Exception as exc:
            print(f"[Shop API] Catalog fetch fehlgeschlagen: {exc}")
            return None

    async def sync_vouch(
        self,
        *,
        giver_name: str,
        message: str,
        is_positive: bool,
        external_id: int | None = None,
    ) -> bool:
        if not self.enabled:
            return False
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{self.api_url}/api/bot/vouches/sync",
                    headers={
                        "X-Bot-Api-Key": self.api_key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "giver_name": giver_name,
                        "message": message,
                        "is_positive": is_positive,
                        "external_id": external_id,
                    },
                )
                return resp.status_code < 400
        except Exception as exc:
            print(f"[Shop API] Vouch sync fehlgeschlagen: {exc}")
            return False

    async def fetch_pending_vouches(self, discord_id: str) -> list[dict] | None:
        if not self.enabled:
            return None
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{self.api_url}/api/bot/vouches/pending",
                    headers={"X-Bot-Api-Key": self.api_key},
                    params={"discord_id": discord_id},
                )
                if resp.status_code >= 400:
                    return None
                data = resp.json()
                return data if isinstance(data, list) else []
        except Exception as exc:
            print(f"[Shop API] Vouch pending fetch fehlgeschlagen: {exc}")
            return None

    async def submit_vouch(
        self,
        *,
        discord_id: str,
        order_id: int,
        rating: int,
        message: str,
        giver_name: str,
    ) -> dict | None:
        if not self.enabled:
            return None
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{self.api_url}/api/bot/vouches/submit",
                    headers={
                        "X-Bot-Api-Key": self.api_key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "discord_id": discord_id,
                        "order_id": order_id,
                        "rating": rating,
                        "message": message,
                        "giver_name": giver_name,
                    },
                )
                if resp.status_code >= 400:
                    return None
                return resp.json()
        except Exception as exc:
            print(f"[Shop API] Vouch submit fehlgeschlagen: {exc}")
            return None

    async def confirm_order_by_amount(
        self,
        minecraft_username: str,
        amount: float,
    ) -> dict | None:
        """Prüft, ob eine offene Website-Bestellung (Ingame-Zahlung) zu
        IGN + Betrag passt, und bestätigt sie ggf. automatisch."""
        if not self.enabled:
            return None
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{self.api_url}/api/bot/orders/confirm_by_amount",
                    headers={
                        "X-Bot-Api-Key": self.api_key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "minecraft_username": minecraft_username,
                        "amount": amount,
                    },
                )
                if resp.status_code >= 400:
                    return None
                return resp.json()
        except Exception as exc:
            print(f"[Shop API] Website-Order-Confirm fehlgeschlagen: {exc}")
            return None


shop_api = ShopApiClient()
