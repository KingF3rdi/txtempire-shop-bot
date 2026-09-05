# TxTEmpire MC Watcher (Fabric)

Client-Mod für den **Shop-Bot-Account**: liest Chat (Messages + Payments) und meldet
Link-Codes sowie Geld-Transfers an die Discord-Bot API.

## Setup

1. Discord-Bot `.env`:
   ```env
   MC_API_KEY=dein-secret
   MC_API_PORT=8765
   GUILD_ID=deine-server-id
   ```
2. Bot starten (API lauscht auf Port 8765).
3. Mod bauen:
   ```bash
   cd minecraft-mod
   ./gradlew.bat build
   ```
   JAR liegt unter `build/libs/txtempire-mc-watcher-1.0.0.jar`.
4. JAR in `mods/` des **Bot-Minecraft-Clients** legen (Fabric 1.21.11 + Fabric API).
5. Config nach `.minecraft/config/txtempire-mc-watcher.json` kopieren
   (Vorlage: `minecraft-mod/txtempire-mc-watcher.json` im Repo-Ordner — lokal mit API-Key).
   ```json
   {
     "apiUrl": "http://127.0.0.1:8765",
     "apiKey": "gleicher-key-wie-MC_API_KEY",
     "guildId": "DEINE_DISCORD_GUILD_ID",
     "enabled": true,
     "debug": false
   }
   ```
6. Mit dem Shop-Geld-Account (**TxTEmpire**) einloggen und online bleiben.

## Ablauf

| Event | Chat | API |
|-------|------|-----|
| Account-Link | `/msg TxTEmpire !link TXTE-XXXXXX` | `POST /mc/v1/link` |
| Zahlung | `Steve hat dir 500000$ gegeben.` | `POST /mc/v1/payment` |

Discord: `/mclinkpanel` → User verlinkt IGN → Code → Ingame
`/msg TxTEmpire !link …` → verknüpft.
Passende Zahlung auf offenes Ticket → **Auto-Confirm**.
