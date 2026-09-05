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
	/** Eigenes HttpClient — kein shared Executor mit dem Submit-Pool (sonst Deadlock). */
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

	/** @return true bei HTTP &lt; 400 */
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
			McWatcher.LOGGER.warn(
				"API-Call fehlgeschlagen ({}): {} — Discord-Bot muss laufen (API Port {})",
				path,
				e.toString(),
				portHint()
			);
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
		McWatcher.LOGGER.info("API-Retry vorgemerkt ({} in Queue)", retryQueue.size());
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

	/** Vom Heartbeat: offene Link/Payment-Requests erneut senden. */
	public void flushRetryQueue() {
		pool.execute(this::drainRetries);
	}

	private String portHint() {
		try {
			URI u = URI.create(config.apiUrl);
			int port = u.getPort();
			return port > 0 ? String.valueOf(port) : "8765";
		} catch (Exception e) {
			return "8765";
		}
	}
}
