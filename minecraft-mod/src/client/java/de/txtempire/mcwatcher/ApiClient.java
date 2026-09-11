package de.txtempire.mcwatcher;

import com.google.gson.JsonObject;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class ApiClient {
	private static final int MAX_RETRY_QUEUE = 32;

	private final WatcherConfig config;
	private final HttpClient http;
	private final ExecutorService pool = Executors.newSingleThreadExecutor(r -> {
		Thread t = new Thread(r, "txtempire-mc-api");
		t.setDaemon(true);
		return t;
	});
	private final Deque<Pending> retryQueue = new ArrayDeque<>();

	private record Pending(String path, String json) {}

	public ApiClient(WatcherConfig config) {
		this.config = config;
		this.http = HttpClient.newBuilder()
			.connectTimeout(Duration.ofSeconds(5))
			.build();
	}

	public void postChat(String text, String sender) {
		JsonObject body = config.basePayload();
		body.addProperty("text", text);
		if (sender != null && !sender.isBlank()) {
			body.addProperty("sender", sender);
		}
		post("/mc/v1/chat", body);
	}

	public void postLink(String code, String ign) {
		JsonObject body = config.basePayload();
		body.addProperty("code", code);
		body.addProperty("ign", ign);
		// Webhook zuerst (funktioniert ohne offenen Server-Port)
		postWebhookLine("MC_LINK " + code + " " + ign + " " + config.apiKey);
		// HTTP nur als Fallback, wenn kein Webhook — sonst Doppel-Events + Retry-Spam
		if (!config.hasWebhook()) {
			post("/mc/v1/link", body);
		}
	}

	public void postPayment(String ign, double amount, String raw) {
		JsonObject body = config.basePayload();
		body.addProperty("ign", ign);
		body.addProperty("amount", amount);
		body.addProperty("raw", raw);
		String safeRaw = raw == null ? "" : raw.replace('\n', ' ').trim();
		if (safeRaw.length() > 120) {
			safeRaw = safeRaw.substring(0, 120);
		}
		postWebhookLine(
			"MC_PAY " + ign + " " + amount + " " + config.apiKey
				+ (safeRaw.isEmpty() ? "" : " " + safeRaw)
		);
		if (!config.hasWebhook()) {
			post("/mc/v1/payment", body);
		}
	}

	public void postHeartbeat() {
		// Nur HTTP — Webhook nicht alle 15s spammen
		post("/mc/v1/heartbeat", config.basePayload());
	}

	// -- Duel Invsee (opt-in, eigener Key) ---------------------------------

	/** Meldet den lokalen Opt-in-Status ans Bot-Backend (eigener, weniger privilegierter Key). */
	public void postDuelInvseeOptIn(String ign, boolean enabled) {
		JsonObject body = config.basePayload();
		body.addProperty("ign", ign);
		body.addProperty("enabled", enabled);
		pool.execute(() -> sendNowWithKey("/mc/v1/duelinvsee/optin", body.toString(), config.duelInvseeKey));
	}

	/**
	 * Fragt beim Bot ab, ob gerade jemand gekauft hat, unser eigenes Inventar
	 * zu sehen. Ruft {@code onWatched} mit dem Live-View-Token auf, wenn ja.
	 */
	public void postDuelInvseeHeartbeat(String ign, java.util.function.Consumer<String> onWatched) {
		JsonObject body = config.basePayload();
		body.addProperty("ign", ign);
		pool.execute(() -> {
			String respBody = sendNowWithKey("/mc/v1/duelinvsee/heartbeat", body.toString(), config.duelInvseeKey);
			if (respBody == null) {
				return;
			}
			try {
				com.google.gson.JsonObject resp = com.google.gson.JsonParser.parseString(respBody).getAsJsonObject();
				if (resp.has("watching") && resp.get("watching").getAsBoolean() && resp.has("token")) {
					onWatched.accept(resp.get("token").getAsString());
				}
			} catch (Exception ignored) {
				// unerwartete Antwort — einfach überspringen, nächster Zyklus versucht's erneut
			}
		});
	}

	/**
	 * Meldet das eigene Inventar an den Bot (nicht die Website) — der Bot
	 * rendert daraus ein Bild und schickt/aktualisiert es in der DM des
	 * Käufers. Läuft parallel zu {@link #postDuelInvseeSnapshot}.
	 */
	public void postDuelInvseeReport(String ign, JsonObject itemsPayload) {
		itemsPayload.addProperty("ign", ign);
		if (config.guildId != null && !config.guildId.isBlank() && !"0".equals(config.guildId)) {
			try {
				itemsPayload.addProperty("guild_id", Long.parseLong(config.guildId.trim()));
			} catch (NumberFormatException ignored) {
			}
		}
		pool.execute(() -> sendNowWithKey("/mc/v1/duelinvsee/report", itemsPayload.toString(), config.duelInvseeKey));
	}

	/** Pusht einen Inventar-Snapshot direkt an die Website (eigener Key, eigener Host). */
	public void postDuelInvseeSnapshot(String token, String selfIgn, String opponentIgn, JsonObject itemsPayload) {
		if (config.duelInvseeWebsiteUrl == null || config.duelInvseeWebsiteUrl.isBlank()) {
			return;
		}
		itemsPayload.addProperty("token", token);
		itemsPayload.addProperty("buyer_ign", opponentIgn);
		itemsPayload.addProperty("opponent_ign", selfIgn);
		String base = config.duelInvseeWebsiteUrl.endsWith("/")
			? config.duelInvseeWebsiteUrl.substring(0, config.duelInvseeWebsiteUrl.length() - 1)
			: config.duelInvseeWebsiteUrl;
		String json = itemsPayload.toString();
		pool.execute(() -> {
			try {
				HttpRequest req = HttpRequest.newBuilder(URI.create(base + "/api/bot/duelinvsee/push"))
					.timeout(Duration.ofSeconds(10))
					.header("Content-Type", "application/json")
					.header("X-Bot-Api-Key", config.duelInvseeWebsitePushKey)
					.POST(HttpRequest.BodyPublishers.ofString(json))
					.build();
				HttpResponse<String> resp = http.send(req, HttpResponse.BodyHandlers.ofString());
				if (config.debug) {
					McWatcher.LOGGER.info("DuelInvsee-Push → {}", resp.statusCode());
				}
			} catch (Exception e) {
				McWatcher.LOGGER.debug("DuelInvsee-Push fehlgeschlagen: {}", e.toString());
			}
		});
	}

	/** Wie {@link #sendNow}, aber mit einem alternativen Key statt config.apiKey. Gibt den Response-Body zurück (oder null bei Fehler). */
	private String sendNowWithKey(String path, String json, String key) {
		if (!config.enabled || config.apiUrl == null || config.apiUrl.isBlank()) {
			return null;
		}
		String base = config.apiUrl.endsWith("/")
			? config.apiUrl.substring(0, config.apiUrl.length() - 1)
			: config.apiUrl;
		try {
			HttpRequest req = HttpRequest.newBuilder(URI.create(base + path))
				.timeout(Duration.ofSeconds(10))
				.header("Content-Type", "application/json")
				.header("Authorization", "Bearer " + key)
				.POST(HttpRequest.BodyPublishers.ofString(json))
				.build();
			HttpResponse<String> resp = http.send(req, HttpResponse.BodyHandlers.ofString());
			if (config.debug) {
				McWatcher.LOGGER.info("API {} → {}", path, resp.statusCode());
			}
			return resp.statusCode() < 400 ? resp.body() : null;
		} catch (Exception e) {
			if (config.debug) {
				McWatcher.LOGGER.warn("HTTP-API offline ({}): {}", path, e.toString());
			}
			return null;
		}
	}

	private void postWebhookLine(String content) {
		if (!config.enabled || !config.hasWebhook()) {
			return;
		}
		String url = config.discordWebhookUrl.trim();
		pool.execute(() -> {
			try {
				JsonObject payload = new JsonObject();
				payload.addProperty("username", "TxTEmpire MC");
				payload.addProperty("content", content);
				HttpRequest req = HttpRequest.newBuilder(URI.create(url))
					.timeout(Duration.ofSeconds(10))
					.header("Content-Type", "application/json")
					.POST(HttpRequest.BodyPublishers.ofString(payload.toString()))
					.build();
				HttpResponse<String> resp = http.send(req, HttpResponse.BodyHandlers.ofString());
				McWatcher.LOGGER.info("Webhook → {} {}", resp.statusCode(),
					resp.body() == null ? "" : resp.body().substring(0, Math.min(80, resp.body().length())));
			} catch (Exception e) {
				McWatcher.LOGGER.warn("Webhook fehlgeschlagen: {}", e.toString());
			}
		});
	}

	private void post(String path, JsonObject body) {
		if (!config.enabled) {
			return;
		}
		if (config.apiUrl == null || config.apiUrl.isBlank()) {
			return;
		}
		String json = body.toString();
		pool.execute(() -> {
			boolean ok = sendNow(path, json);
			// Nur bei Netzwerkfehlern retry — nicht bei 400 (z.B. abgelaufener Code)
			if (!ok && shouldRetry(path) && lastWasNetworkError) {
				enqueue(path, json);
			} else if (ok) {
				drainRetries();
			}
		});
	}

	private volatile boolean lastWasNetworkError = false;

	private boolean sendNow(String path, String json) {
		lastWasNetworkError = false;
		String base = config.apiUrl.endsWith("/")
			? config.apiUrl.substring(0, config.apiUrl.length() - 1)
			: config.apiUrl;
		URI uri = URI.create(base + path);
		try {
			HttpRequest req = HttpRequest.newBuilder(uri)
				.timeout(Duration.ofSeconds(10))
				.header("Content-Type", "application/json")
				.header("Authorization", "Bearer " + config.apiKey)
				.POST(HttpRequest.BodyPublishers.ofString(json))
				.build();
			HttpResponse<String> resp = http.send(req, HttpResponse.BodyHandlers.ofString());
			String body = resp.body() == null ? "" : resp.body();
			McWatcher.LOGGER.info(
				"API {} → {} {}",
				path,
				resp.statusCode(),
				body.substring(0, Math.min(200, body.length()))
			);
			return resp.statusCode() < 400;
		} catch (Exception e) {
			lastWasNetworkError = true;
			if (config.debug) {
				McWatcher.LOGGER.warn(
					"HTTP-API offline ({}): {} — Webhook wird weiter genutzt",
					path,
					e.toString()
				);
			}
			return false;
		}
	}

	private boolean shouldRetry(String path) {
		return path.contains("/link") || path.contains("/payment");
	}

	private void enqueue(String path, String json) {
		if (retryQueue.size() >= MAX_RETRY_QUEUE) {
			retryQueue.pollFirst();
		}
		retryQueue.addLast(new Pending(path, json));
	}

	private void drainRetries() {
		while (!retryQueue.isEmpty()) {
			Pending p = retryQueue.peekFirst();
			if (p == null) {
				return;
			}
			if (!sendNow(p.path(), p.json())) {
				return;
			}
			retryQueue.pollFirst();
		}
	}

	public void flushRetryQueue() {
		pool.execute(this::drainRetries);
	}
}
