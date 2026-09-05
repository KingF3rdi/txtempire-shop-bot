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
| `/new item` | Item anlegen + Buy-Panel posten (+ optional Rabattcode, **5 Uses**) |
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
| `/mclinkpanel` | Panel: Minecraft-Account verlinken / unverifizieren |
| `/bot status` | Bot-/Watcher-Status aufs Link-Panel schreiben |
| `/link` · `/unlink` · `/mcstatus` | Account verknüpfen, lösen, Status |
| `/mcsetup` | Auto-Confirm + Payment-Log-Channel |

## Kauf-Flow

1. User wählt Kategorie → Item → Warenkorb → **Kaufen**
2. Privates Ticket mit Gesamtpreis
3. **Mit verknüpftem MC-Account:** Ingame zahlen → Ticket wird **automatisch bestätigt**
4. **Ohne Link:** User **Payment beweisen** (IGN + Bild) → Staff **Payment bestätigen**
5. Pack per DM/Link, Rollen, `/vouch` freigeschaltet

## Minecraft Account-Link

1. `/mclinkpanel` posten
2. User: **Account verlinken** → IGN → Code `TXTE-…`
3. Ingame: `/msg TxTEmpire !link TXTE-…` (privat)
4. Fabric-Mod `minecraft-mod/` meldet den Code an die Bot-API
5. Discord ↔ IGN verknüpft · **Unverifizieren** löst die Bindung
6. Bei passender Zahlung zum offenen Ticket → Auto-Confirm

Voraussetzungen: Discord-Bot mit `MC_API_KEY` (meist auf **externem Server**),
Fabric-Mod auf dem TxTEmpire-Client mit `apiUrl` = `http://SERVER-IP:8765`
(siehe `minecraft-mod/README.md`). Firewall-Port **8765** muss erreichbar sein.
`MC_LINK_IGN` steuert den Empfänger (Standard: `TxTEmpire`).

## Website-Anbindung

Wenn `SHOP_API_URL` und `BOT_API_KEY` gesetzt sind:

- **Kategorien und Produkte** werden beim Bot-Start und vor `/buypanels` automatisch von der Website übernommen
- Manueller Sync: `/syncshop`
- **Vouches** werden an die Website gesendet

| Komponente | Rolle |
|------------|--------|
| **/** (dieses Repo) | Discord-Shop (Slash-Commands, Tickets, Warenkorb, MC-Link) |
| **minecraft-mod/** | Fabric Chat-Watcher (Link-Codes + Payments → Bot-API) |
| **backend/** | Optional: Website-Shop, OAuth |

Vouches werden optional an `POST /api/bot/vouches/sync` gesendet, wenn `SHOP_API_URL` gesetzt ist.

## Dateien

- `bot.py` — Einstieg
- `cogs/` — Slash-Commands (inkl. `mc_link`)
- `views/` — Buttons, Selects, Panels (inkl. MC-Link)
- `db/database.py` — SQLite (eigene Bot-Datenbank in `data/shop.db`)
- `integrations/shop_api.py` — Website-API Bridge
- `integrations/mc_api.py` — HTTP-API für die Fabric-Mod
- `utils/mc_confirm.py` — Auto-Bestätigung nach Ingame-Zahlung
- `minecraft-mod/` — Fabric Client-Mod
