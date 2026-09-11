package de.txtempire.mcwatcher;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.fabricmc.fabric.api.client.command.v2.ClientCommandManager;
import net.fabricmc.fabric.api.client.command.v2.ClientCommandRegistrationCallback;
import net.fabricmc.fabric.api.client.message.v1.ClientReceiveMessageEvents;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.network.chat.Component;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.item.ItemStack;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Duel Invsee — komplett opt-in: dieser Client meldet NUR das eigene
 * Inventar, und nur wenn der Spieler selbst zuvor per {@code /duelinvsee on}
 * zugestimmt hat. Es gibt keinen Weg, hierüber das Inventar einer anderen
 * Person zu lesen — jede Installation dieses Mods kann ausschließlich für
 * das eigene Spielerkonto berichten.
 *
 * {@code /duelinvsee who} hilft nur beim Ablesen: es schickt {@code /duels}
 * an den Server und zeigt lokal die Zeile an, in der der eigene Name vorkommt
 * (Best-Effort-Parsing, da das exakte Ausgabeformat serverabhängig ist).
 */
public final class DuelInvseeClient {

	private static WatcherConfig config;
	private static ApiClient api;

	private static final ScheduledExecutorService SCHEDULER =
		Executors.newSingleThreadScheduledExecutor(r -> {
			Thread t = new Thread(r, "txtempire-duelinvsee");
			t.setDaemon(true);
			return t;
		});

	private static volatile java.util.concurrent.ScheduledFuture<?> reportTask;

	/** Fenster, in dem nach /duels auf die Antwort gehorcht wird (ms). */
	private static final long DUELS_LISTEN_WINDOW_MS = 4000L;
	private static volatile long duelsListenUntil = 0L;

	private DuelInvseeClient() {
	}

	public static void init(WatcherConfig cfg, ApiClient apiClient) {
		config = cfg;
		api = apiClient;

		ClientCommandRegistrationCallback.EVENT.register((dispatcher, registryAccess) ->
			dispatcher.register(ClientCommandManager.literal("duelinvsee")
				.executes(ctx -> {
					status();
					return 1;
				})
				.then(ClientCommandManager.literal("on").executes(ctx -> {
					setOptIn(true);
					return 1;
				}))
				.then(ClientCommandManager.literal("off").executes(ctx -> {
					setOptIn(false);
					return 1;
				}))
				.then(ClientCommandManager.literal("who").executes(ctx -> {
					lookupOpponent();
					return 1;
				}))));

		ClientReceiveMessageEvents.GAME.register((message, overlay) -> maybeCaptureDuelsLine(message));
		ClientReceiveMessageEvents.CHAT.register((message, signed, sender, params, ts) -> maybeCaptureDuelsLine(message));

		if (config.duelInvseeOptIn) {
			startReporting();
		}
	}

	private static void status() {
		localMessage(config.duelInvseeOptIn
			? "§aDuel Invsee ist §laktiv§r§a — andere können dein Inventar kaufen, solange du das nicht mit §e/duelinvsee off§a wieder ausschaltest."
			: "§7Duel Invsee ist aus. §e/duelinvsee on§7 zum Aktivieren, §e/duelinvsee who§7 um deinen aktuellen Gegner in /duels zu finden.");
	}

	private static void setOptIn(boolean enabled) {
		LocalPlayer player = Minecraft.getInstance().player;
		if (player == null) {
			return;
		}
		config.duelInvseeOptIn = enabled;
		config.save();
		String ign = player.getGameProfile().getName();
		api.postDuelInvseeOptIn(ign, enabled);
		if (enabled) {
			startReporting();
			localMessage("§aDuel Invsee aktiviert. Dein Inventar wird nur sichtbar, wenn jemand es im Discord-Bot für dich kauft.");
		} else {
			stopReporting();
			localMessage("§7Duel Invsee deaktiviert.");
		}
	}

	private static synchronized void startReporting() {
		if (reportTask != null) {
			return;
		}
		reportTask = SCHEDULER.scheduleAtFixedRate(DuelInvseeClient::tick, 0L, 15L, TimeUnit.SECONDS);
	}

	private static synchronized void stopReporting() {
		if (reportTask != null) {
			reportTask.cancel(false);
			reportTask = null;
		}
	}

	private static void tick() {
		if (!config.duelInvseeOptIn) {
			stopReporting();
			return;
		}
		LocalPlayer player = Minecraft.getInstance().player;
		if (player == null) {
			return;
		}
		String ign = player.getGameProfile().getName();
		api.postDuelInvseeHeartbeat(ign, token -> Minecraft.getInstance().execute(() -> pushSnapshot(token, ign)));
	}

	/** Muss auf dem Client-Thread laufen (Inventarzugriff). */
	private static void pushSnapshot(String token, String selfIgn) {
		LocalPlayer player = Minecraft.getInstance().player;
		if (player == null) {
			return;
		}
		JsonArray items = new JsonArray();
		Inventory inv = player.getInventory();
		for (int slot = 0; slot < inv.getContainerSize(); slot++) {
			addItem(items, "main", slot, inv.getItem(slot));
		}
		addItem(items, "armor", 0, player.getItemBySlot(EquipmentSlot.HEAD));
		addItem(items, "armor", 1, player.getItemBySlot(EquipmentSlot.CHEST));
		addItem(items, "armor", 2, player.getItemBySlot(EquipmentSlot.LEGS));
		addItem(items, "armor", 3, player.getItemBySlot(EquipmentSlot.FEET));
		addItem(items, "offhand", 0, player.getItemBySlot(EquipmentSlot.OFFHAND));

		// Zwei unabhängige Ziele, dieselben Item-Daten: die Website (Live-Seite,
		// per Token) und der Bot (rendert ein Bild, schickt/aktualisiert es per
		// DM beim Käufer). Jede JsonObject-Hülle bekommt ihre eigenen
		// Zusatzfelder, das gemeinsame JsonArray wird nicht verändert.
		JsonObject websitePayload = new JsonObject();
		websitePayload.add("items", items);
		// buyer_ign/opponent_ign werden von ApiClient.postDuelInvseeSnapshot ergänzt:
		// aus Sicht der Website ist "opponent" immer der, dessen Inventar gemeldet wird (wir selbst).
		api.postDuelInvseeSnapshot(token, selfIgn, "", websitePayload);

		JsonObject reportPayload = new JsonObject();
		reportPayload.add("items", items);
		api.postDuelInvseeReport(selfIgn, reportPayload);
	}

	private static void addItem(JsonArray out, String group, int slot, ItemStack stack) {
		if (stack == null || stack.isEmpty()) {
			return;
		}
		JsonObject o = new JsonObject();
		o.addProperty("slot", slot);
		o.addProperty("group", group);
		o.addProperty("type", String.valueOf(net.minecraft.core.registries.BuiltInRegistries.ITEM.getKey(stack.getItem())));
		o.addProperty("amount", stack.getCount());
		out.add(o);
	}

	// -- /duels Best-Effort-Lookup (rein lokal, kein Server-seitiger Zugriff) --

	private static void lookupOpponent() {
		LocalPlayer player = Minecraft.getInstance().player;
		if (player == null || Minecraft.getInstance().getConnection() == null) {
			return;
		}
		localMessage("§7Suche deinen Gegner in /duels ...");
		duelsListenUntil = System.currentTimeMillis() + DUELS_LISTEN_WINDOW_MS;
		Minecraft.getInstance().getConnection().sendCommand("duels");
	}

	private static void maybeCaptureDuelsLine(Component message) {
		if (System.currentTimeMillis() > duelsListenUntil) {
			return;
		}
		LocalPlayer player = Minecraft.getInstance().player;
		if (player == null || message == null) {
			return;
		}
		String line = ChatParser.strip(message.getString());
		String myName = player.getGameProfile().getName();
		if (line.isBlank() || !line.toLowerCase(java.util.Locale.ROOT).contains(myName.toLowerCase(java.util.Locale.ROOT))) {
			return;
		}
		duelsListenUntil = 0L; // erste Treffer-Zeile reicht, nicht mehrfach anschlagen

		String opponent = extractOpponent(line, myName);
		if (opponent != null) {
			localMessage("§aDein aktueller Gegner: §e" + opponent
				+ "§a. Kauf im Discord mit §e/duelinvsee " + opponent + "§a.");
		} else {
			localMessage("§7Zeile in /duels gefunden: §f" + line
				+ "\n§7Lies den Gegnernamen ab und nutze im Discord §e/duelinvsee <Gegner>§7.");
		}
	}

	/** Best-Effort: versucht gängige Trenner ("vs", "gegen", "-", "⚔") zu erkennen. Serverabhängig, ggf. Anpassung nötig. */
	private static String extractOpponent(String line, String myName) {
		String[] separators = { " vs ", " vs. ", " gegen ", " ⚔ ", " - ", " – " };
		String lower = line.toLowerCase(java.util.Locale.ROOT);
		for (String sep : separators) {
			int idx = lower.indexOf(sep.toLowerCase(java.util.Locale.ROOT));
			if (idx < 0) {
				continue;
			}
			String left = line.substring(0, idx).trim();
			String right = line.substring(idx + sep.length()).trim();
			String leftName = lastToken(left);
			String rightName = firstToken(right);
			if (leftName.equalsIgnoreCase(myName) && !rightName.isBlank()) {
				return rightName;
			}
			if (rightName.equalsIgnoreCase(myName) && !leftName.isBlank()) {
				return leftName;
			}
		}
		return null;
	}

	private static String lastToken(String s) {
		String[] parts = s.trim().split("\\s+");
		return parts.length == 0 ? "" : parts[parts.length - 1].replaceAll("[^A-Za-z0-9_]", "");
	}

	private static String firstToken(String s) {
		String[] parts = s.trim().split("\\s+");
		return parts.length == 0 ? "" : parts[0].replaceAll("[^A-Za-z0-9_]", "");
	}

	private static void localMessage(String text) {
		LocalPlayer player = Minecraft.getInstance().player;
		if (player != null) {
			player.displayClientMessage(Component.literal(text), false);
		}
	}
}
