# TxTEmpire MC Watcher (Fabric)

Client-Mod für den **Shop-Geld-Account (TxTEmpire)**: liest Chat (Messages + Payments)
und meldet Link-Codes sowie Geld-Transfers an die Discord-Bot-API.

Typisches Setup: **Discord-Bot auf einem externen Server**, Minecraft-Client lokal
(oder auf einem anderen PC). Die Mod verbindet sich per HTTP zur Bot-API.

## Server (Discord-Bot)

In der Bot-`.env` auf dem **externen Server**:

```env
MC_API_HOST=0.0.0.0
MC_API_PORT=8765
MC_API_KEY=dein-langes-geheimnis
GUILD_ID=deine-discord-server-id
MC_LINK_IGN=TxTEmpire
```

- Firewall/Security-Group: **TCP 8765** von außen erlauben (oder nur deine IP)
- Bot starten — Log: `[MC-API] Listening on http://0.0.0.0:8765`
- Test vom Heim-PC: `http://DEINE-SERVER-IP:8765/mc/v1/health` → `{"ok":true…}`

## Minecraft-Client (Watcher-Mod)

1. Mod bauen: `cd minecraft-mod` → `gradlew.bat build`  
   JAR: `build/libs/txtempire-mc-watcher-*.jar`
2. JAR in `mods/` des **TxTEmpire**-Fabric-Profils (1.21.11 + Fabric API)
3. Config: `config/txtempire-mc-watcher.json`

```json
{
  "apiUrl": "http://DEINE-SERVER-IP:8765",
  "apiKey": "gleicher-key-wie-MC_API_KEY-auf-dem-Server",
  "guildId": "DEINE_DISCORD_GUILD_ID",
  "enabled": true,
  "debug": true
}
```

**Wichtig:** `apiUrl` = öffentliche Adresse des Bot-Servers, **nicht** `127.0.0.1`
(außer Bot und Minecraft laufen wirklich auf demselben Rechner).

## Ablauf

| Event | Chat | API |
|-------|------|-----|
| Account-Link | `/msg TxTEmpire !link TXTE-XXXXXX` | `POST /mc/v1/link` |
| Zahlung | z.B. `Spieler » TxTEmpire - $500,000` | `POST /mc/v1/payment` |

Discord: `/mclinkpanel` → Code → Ingame `/msg TxTEmpire !link …` → Bestätigung.
Passende Zahlung auf offenes Ticket → Auto-Confirm.
