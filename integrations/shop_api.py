"""Optionale Anbindung an die TxTEmpire Website-Shop-API.

Der Bot-Host (bot-hosting.net) blockiert ausgehende Verbindungen zu
*.workers.dev (bestätigt per /nettest), Discord selbst ist aber erreichbar.
Deshalb laufen fast alle Bot->Website-Ereignisse (Vouches, Verkäufe,
Umsatz, Produkte, Login-/Zahlungs-Codes) als Discord-Nachricht
"WSAUTH|<json>" über einen Webhook-Relay-Channel statt per direktem HTTP-
Call — der Worker liest den Channel per Cron über die Discord-Bot-API aus
(siehe backend/index.js, processRelayMessages/handleRelayEvent).

Nur fetch_catalog/fetch_pending_vouches/submit_vouch brauchen weiterhin
eine synchrone Antwort und gehen (aktuell noch nicht funktionsfähig) über
direktes HTTP an SHOP_API_URL.
"""

from __future__ import annotations

import json

import httpx

import config


class ShopApiClient:
    def __init__(self) -> None:
        self.api_url = (config.SHOP_API_URL or "").rstrip("/")
        self.api_key = config.BOT_API_KEY or ""
        self.relay_webhook_url = config.SHOP_RELAY_WEBHOOK_URL or ""
        self.enabled = bool(self.api_url and self.api_key)

    async def _post_relay_event(self, event_type: str, **fields) -> bool:
        """Postet ein Ereignis in den Discord-Relay-Webhook statt direkt an
        die Website-API. Fire-and-forget: kein Rückgabewert vom Worker,
        nur ob der Post rausging."""
        if not self.relay_webhook_url:
            return False
        try:
            content = "WSAUTH|" + json.dumps({"type": event_type, **fields})
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    self.relay_webhook_url, json={"content": content}
                )
                return resp.status_code < 400
        except Exception as exc:
            print(f"[Shop API] Relay-Event ({event_type}) fehlgeschlagen: {exc}")
            return False

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
        rating: int | None = None,
        source: str = "ticket",
    ) -> bool:
        return await self._post_relay_event(
            "vouch",
            giver_name=giver_name,
            message=message,
            is_positive=is_positive,
            external_id=external_id,
            rating=rating,
            source=source,
        )

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

    async def sync_sale(self, amount: float = 0) -> bool:
        """Zählt einen abgeschlossenen Discord-Shop-Kauf (+1 verkauft) und
        rechnet den Betrag zum Umsatz dazu (Discord-Shop-Währung 1:1 als
        Euro übernommen, auf ausdrücklichen Wunsch trotz fehlendem Kurs)."""
        return await self._post_relay_event("sale", amount=amount)

    async def sync_revenue(self, amount: float) -> bool:
        """Zählt zusätzlichen Umsatz ohne Pack-Zählung dazu (z.B. Lizenzkey-
        Verkäufe - Discord-Shop-Währung 1:1 als Euro übernommen)."""
        if amount <= 0:
            return False
        return await self._post_relay_event("revenue", amount=amount)

    async def upsert_product(
        self,
        *,
        category_name: str,
        product_name: str,
        price: float = 0,
        sales_count: int = 0,
        description: str = "",
    ) -> bool:
        """Legt ein Discord-natives Produkt (Name+Kategorie als Identität)
        auf der Website an oder aktualisiert seine Verkaufszahl."""
        return await self._post_relay_event(
            "product_upsert",
            category_name=category_name,
            product_name=product_name,
            price=price,
            sales_count=sales_count,
            description=description,
        )

    async def sync_purchase(
        self,
        *,
        discord_id: str,
        product_id: int,
        download_url: str | None = None,
    ) -> bool:
        """Schaltet einen Website-Download frei, nachdem der Bot einen Kauf
        (Rollen-/Pack-Lieferung) abgeschlossen hat."""
        return await self._post_relay_event(
            "purchase",
            discord_id=discord_id,
            product_id=product_id,
            download_url=download_url,
        )

    async def confirm_ingame_login(self, code: str) -> bool:
        """Bestätigt einen Website-Ingame-Login-Code (Nutzer hat ihn per PN
        an den Bot ingame geschickt, die Mod hat den Whisper per Discord-
        Webhook gemeldet)."""
        return await self._post_relay_event("ingame_login", code=code)

    async def confirm_order_by_amount(
        self,
        minecraft_username: str,
        amount: float,
    ) -> dict | None:
        """Meldet eine Ingame-Zahlung, die zu keinem Discord-Ticket passt,
        als möglichen Website-Bestellungs-Treffer (fire-and-forget, kein
        sofortiges Match-Ergebnis mehr)."""
        ok = await self._post_relay_event(
            "payment_confirm", ign=minecraft_username, amount=amount
        )
        return {"ok": ok, "relayed": ok} if ok else None


shop_api = ShopApiClient()
