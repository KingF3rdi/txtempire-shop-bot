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

public class McWatcherClient implements ClientModInitializer {
	private static final long DEDUPE_MS = 2500L;

	private WatcherConfig config;
	private ApiClient api;
	private final Map<String, Long> recent = new ConcurrentHashMap<>();

	@Override
	public void onInitializeClient() {
		config = WatcherConfig.load();
		api = new ApiClient(config);
		McWatcher.LOGGER.info(
			"MC Watcher aktiv — API {} (config: {})",
			config.apiUrl,
			WatcherConfig.path()
		);

		ClientReceiveMessageEvents.CHAT.register(this::onChat);
		ClientReceiveMessageEvents.GAME.register(this::onGame);
	}

	private void onChat(
		Component message,
		PlayerChatMessage signedMessage,
		GameProfile sender,
		ChatType.Bound parameters,
		Instant receptionTimestamp
	) {
		String name = sender != null ? sender.name() : null;
		handle(message.getString(), name);
	}

	private void onGame(Component message, boolean overlay) {
		if (overlay) {
			return;
		}
		handle(message.getString(), null);
	}

	private void handle(String text, String sender) {
		if (!config.enabled || text == null || text.isBlank()) {
			return;
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
