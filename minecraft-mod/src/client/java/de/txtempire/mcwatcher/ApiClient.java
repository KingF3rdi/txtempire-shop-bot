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
		post("/mc/v1/link", body);
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
		post("/mc/v1/payment", body);
	}

	public void postHeartbeat() {
		// Kein Webhook-Spam — nur HTTP (optional)
		post("/mc/v1/heartbeat", config.basePayload());
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
			if (!ok && shouldRetry(path)) {
				enqueue(path, json);
			} else if (ok) {
				drainRetries();
			}
		});
	}

	private boolean sendNow(String path, String json) {
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
