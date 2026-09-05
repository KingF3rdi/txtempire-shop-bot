package de.txtempire.mcwatcher;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonObject;
import net.fabricmc.loader.api.FabricLoader;

import java.io.IOException;
import java.io.Reader;
import java.io.Writer;
import java.nio.file.Files;
import java.nio.file.Path;

public final class WatcherConfig {
	private static final Gson GSON = new GsonBuilder().setPrettyPrinting().create();

	/** HTTP-API des Discord-Bots (optional, oft blockiert auf Free-Hosting). */
	public String apiUrl = "http://127.0.0.1:8765";
	/** Gleicher Wert wie MC_API_KEY in der Bot-.env */
	public String apiKey = "CHANGE_ME";
	public String guildId = "0";
	/**
	 * Discord Incoming-Webhook (empfohlen bei Bot-Hosting).
	 * Channel → Einstellungen → Integrationen → Webhook.
	 * Beispiel: https://discord.com/api/webhooks/…/…
	 */
	public String discordWebhookUrl = "";
	public boolean enabled = true;
	public boolean debug = false;

	public static Path path() {
		return FabricLoader.getInstance().getConfigDir().resolve("txtempire-mc-watcher.json");
	}

	public static WatcherConfig load() {
		Path file = path();
		if (Files.exists(file)) {
			try (Reader reader = Files.newBufferedReader(file)) {
				WatcherConfig cfg = GSON.fromJson(reader, WatcherConfig.class);
				if (cfg != null) {
					return cfg;
				}
			} catch (IOException e) {
				McWatcher.LOGGER.error("Config lesen fehlgeschlagen", e);
			}
		}
		WatcherConfig fresh = new WatcherConfig();
		fresh.save();
		return fresh;
	}

	public void save() {
		try {
			Files.createDirectories(path().getParent());
			try (Writer writer = Files.newBufferedWriter(path())) {
				GSON.toJson(this, writer);
			}
		} catch (IOException e) {
			McWatcher.LOGGER.error("Config speichern fehlgeschlagen", e);
		}
	}

	public boolean hasWebhook() {
		return discordWebhookUrl != null
			&& !discordWebhookUrl.isBlank()
			&& discordWebhookUrl.contains("discord.com/api/webhooks/");
	}

	public JsonObject basePayload() {
		JsonObject o = new JsonObject();
		if (guildId != null && !guildId.isBlank() && !"0".equals(guildId)) {
			try {
				o.addProperty("guild_id", Long.parseLong(guildId.trim()));
			} catch (NumberFormatException ignored) {
			}
		}
		return o;
	}
}
