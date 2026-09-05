package de.txtempire.mcwatcher;

import com.google.gson.JsonObject;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class ApiClient {
	private final WatcherConfig config;
	private final HttpClient http;
	private final ExecutorService pool = Executors.newSingleThreadExecutor(r -> {
		Thread t = new Thread(r, "txtempire-mc-api");
		t.setDaemon(true);
		return t;
	});

	public ApiClient(WatcherConfig config) {
		this.config = config;
		this.http = HttpClient.newBuilder()
			.connectTimeout(Duration.ofSeconds(5))
			.executor(pool)
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
		post("/mc/v1/link", body);
	}

	public void postPayment(String ign, double amount, String raw) {
		JsonObject body = config.basePayload();
		body.addProperty("ign", ign);
		body.addProperty("amount", amount);
		body.addProperty("raw", raw);
		post("/mc/v1/payment", body);
	}

	public void postHeartbeat() {
		post("/mc/v1/heartbeat", config.basePayload());
	}

	private void post(String path, JsonObject body) {
		if (!config.enabled) {
			return;
		}
		String base = config.apiUrl.endsWith("/")
			? config.apiUrl.substring(0, config.apiUrl.length() - 1)
			: config.apiUrl;
		URI uri = URI.create(base + path);
		String json = body.toString();
		pool.execute(() -> {
			try {
				HttpRequest.Builder b = HttpRequest.newBuilder(uri)
					.timeout(Duration.ofSeconds(10))
					.header("Content-Type", "application/json")
					.header("Authorization", "Bearer " + config.apiKey)
					.POST(HttpRequest.BodyPublishers.ofString(json));
				HttpResponse<String> resp = http.send(b.build(), HttpResponse.BodyHandlers.ofString());
				if (config.debug || resp.statusCode() >= 400) {
					McWatcher.LOGGER.info(
						"API {} → {} {}", path, resp.statusCode(), resp.body()
					);
				}
			} catch (Exception e) {
				McWatcher.LOGGER.warn("API-Call fehlgeschlagen ({}): {}", path, e.toString());
			}
		});
	}
}
