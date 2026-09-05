# Discord Shop Bot (shop_bot_py)

Python Discord-Shop mit Kategorien, Warenkorb, Tickets, Pack-Versand und Vouch — **der offizielle TxTEmpire Discord-Bot**.

## Setup

1. Bot im [Discord Developer Portal](https://discord.com/developers/applications) anlegen
2. Privileged Intents: **Server Members Intent** (optional: Message Content nicht nötig)
3. Bot einladen mit: Manage Channels, Manage Roles, Send Messages, Attach Files, Embed Links, **applications.commands**
4. Abhängigkeiten:

```bash
cd discord-bot
pip install -r requirements.txt
cp .env.example .env
```

5. `.env` ausfüllen:

```env
DISCORD_TOKEN=dein_token
GUILD_ID=deine_server_id

# Optional — Vouches auf der Website + Kategorien/Produkte automatisch übernehmen
SHOP_API_URL=https://shop.deinedomain.de
BOT_API_KEY=gleicher-key-wie-backend
```

**Wichtig:** `DISCORD_TOKEN` muss identisch mit `DISCORD_BOT_TOKEN` in `backend/.env` sein, damit die Website Discord-Tickets erstellen kann.

6. Start:

```bash
python bot.py
```

## Befehle

| Command | Beschreibung |
|---------|--------------|
| `/setup` | Staff-, Customer-Rolle, Ticket-Kategorie, Vouch-Channel, Ticket-Limit |
| `/payees` | 50/50 Zahlungsempfänger A + B |
| `/adminpanel` | Interaktives Admin-Panel |
| `/category add/list/delete` | Kategorien |
| `/item add/list/delete` | Items (Preis, Pack-DM, Pack-Link, Autorole) |
| `/ticketlimit` | Max. offene Kauf-Tickets |
| `/shoppanel` | Shop-Panel posten |
| `/buypanel` | Buy Panel 1 oder 2 posten (**slot Pflicht**) |
| `/buypanelboth` | Buy Panel 1 und 2 posten/aktualisieren |
| `/buypanelconfig` | Kategorien für Panel 1/2 einstellen (interaktive Auswahl) |
| `/buypanelstatus` | Panel-Konfiguration anzeigen |
| `/buypanelrefresh` | Gespeicherte Panels aktualisieren |
| `/panelsetup` | **Empfohlen:** Beide Panels posten + Status |
| `/syncshop` | Kategorien und Produkte manuell von der Website synchronisieren |
| `/dailydeal post` | Daily Deal posten (Rabatt %/Betrag + Direkt-Kauf-Button) |
| `/dailydeal end` | Aktiven Daily Deal beenden |
| `/dailydeal list` | Aktive Daily Deals anzeigen |
| `/cart` | Warenkorb öffnen |
| `/vouch` | Einmalig pro bestätigtem Kauf |

## Kauf-Flow

1. User wählt Kategorie → Item → Warenkorb → **Kaufen**
2. Privates Ticket mit Gesamtpreis und **50/50**-Aufteilung
3. User: **Payment beweisen** (IGN + Bild-Anhang)
4. Staff: **Payment bestätigen** → Pack per DM/Link, Rollen, `/vouch` freigeschaltet

## Website-Anbindung

Wenn `SHOP_API_URL` und `BOT_API_KEY` gesetzt sind:

- **Kategorien und Produkte** werden beim Bot-Start und vor `/buypanels` automatisch von der Website übernommen
- Manueller Sync: `/syncshop`
- **Vouches** werden an die Website gesendet

| Komponente | Rolle |
|------------|--------|
| **discord-bot/** | Discord-Shop (Slash-Commands, Tickets, Warenkorb) |
| **backend/** | Website-Shop, OAuth, MC-Bot-API |
| **minecraft-bot/** | Ingame-Zahlungen + Link-Codes |

Vouches werden optional an `POST /api/bot/vouches/sync` gesendet, wenn `SHOP_API_URL` gesetzt ist.

## Dateien

- `bot.py` — Einstieg
- `cogs/` — Slash-Commands
- `views/` — Buttons, Selects, Panels
- `db/database.py` — SQLite (eigene Bot-Datenbank in `data/shop.db`)
- `integrations/shop_api.py` — Website-API Bridge
