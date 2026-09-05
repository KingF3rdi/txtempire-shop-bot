package de.txtempire.mcwatcher;

import com.mojang.authlib.GameProfile;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.message.v1.ClientReceiveMessageEvents;
import net.minecraft.network.chat.ChatType;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.PlayerChatMessage;

import java.time.Instant;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/**
 * Liest den Chat unten links (Chat-HUD) + Fabric Message-Events.
 */
public class McWatcherClient implements ClientModInitializer {
	private static final long DEDUPE_MS = 2500L;

	private static WatcherConfig config;
	private static ApiClient api;
	private static final Map<String, Long> recent = new ConcurrentHashMap<>();
	private static final ScheduledExecutorService HEARTBEAT =
		Executors.newSingleThreadScheduledExecutor(r -> {
			Thread t = new Thread(r, "txtempire-mc-heartbeat");
			t.setDaemon(true);
			return t;
		});

	@Override
	public void onInitializeClient() {
		config = WatcherConfig.load();
		api = new ApiClient(config);
		McWatcher.LOGGER.info(
			"MC Watcher aktiv — API {} · Webhook {} (config: {})",
			config.apiUrl,
			config.hasWebhook() ? "ja" : "nein",
			WatcherConfig.path()
		);

		ClientReceiveMessageEvents.CHAT.register(this::onChat);
		ClientReceiveMessageEvents.GAME.register(this::onGame);

		// Alle 15s Heartbeat + Retry (Link/Payment wenn Bot kurz offline war)
		api.postHeartbeat();
		HEARTBEAT.scheduleAtFixedRate(
			() -> {
				try {
					api.postHeartbeat();
					api.flushRetryQueue();
				} catch (Exception e) {
					McWatcher.LOGGER.debug("Heartbeat failed: {}", e.toString());
				}
			},
			15L,
			15L,
			TimeUnit.SECONDS
		);
	}

	private void onChat(
		Component message,
		PlayerChatMessage signedMessage,
		GameProfile sender,
		ChatType.Bound parameters,
		Instant receptionTimestamp
	) {
		String name = sender != null ? sender.name() : null;
		onChatLine(message.getString(), name);
	}

	private void onGame(Component message, boolean overlay) {
		if (overlay) {
			return;
		}
		onChatLine(message.getString(), null);
	}

	/** Wird auch vom ChatComponent-Mixin (HUD unten links) aufgerufen. */
	public static void onChatLine(String text, String sender) {
		if (config == null || api == null || !config.enabled) {
			return;
		}
		if (text == null || text.isBlank()) {
			return;
		}

		if (config.debug) {
			McWatcher.LOGGER.info("[chat] {}", text);
		}

		ChatParser.Parsed parsed = ChatParser.parse(text, sender);
		if (parsed == null) {
			return;
		}

		String key = parsed.kind() + ":" + parsed.ign() + ":"
			+ (parsed.code() != null ? parsed.code() : String.valueOf(parsed.amount()))
			+ ":" + text.hashCode();
		long now = System.currentTimeMillis();
		Long prev = recent.put(key, now);
		recent.entrySet().removeIf(e -> now - e.getValue() > DEDUPE_MS);
		if (prev != null && now - prev < DEDUPE_MS) {
			return;
		}

		if (parsed.kind() == ChatParser.Parsed.Kind.LINK) {
			McWatcher.LOGGER.info("Link-Code von {}: {}", parsed.ign(), parsed.code());
			api.postLink(parsed.code(), parsed.ign());
		} else {
			McWatcher.LOGGER.info("Payment von {}: {}", parsed.ign(), parsed.amount());
			api.postPayment(parsed.ign(), parsed.amount(), text);
		}
	}
}
