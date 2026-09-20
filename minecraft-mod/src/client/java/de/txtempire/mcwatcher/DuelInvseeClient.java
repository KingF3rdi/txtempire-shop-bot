package de.txtempire.mcwatcher;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.fabricmc.fabric.api.client.command.v2.ClientCommandManager;
import net.fabricmc.fabric.api.client.command.v2.ClientCommandRegistrationCallback;
import net.fabricmc.fabric.api.client.message.v1.ClientReceiveMessageEvents;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.inventory.AbstractContainerScreen;
import net.minecraft.world.inventory.Slot;
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
		if (config.invseeScanner) {
			startScanner();
		}
	}

	// -- Scanner: ingame /invsee <Name> ausfuehren und Fenster auslesen ----------

	private static final long OPEN_TIMEOUT_MS = 4000L;
	private static final java.util.concurrent.ConcurrentLinkedQueue<String[]> scanQueue = new java.util.concurrent.ConcurrentLinkedQueue<>();
	private static volatile String[] scanCurrent = null; // {ign, token}
	private static volatile long scanDeadline = 0L;
	private static volatile boolean scanCommandSent = false;
	private static volatile long lastFetch = 0L;

	private static void startScanner() {
		ClientTickEvents.END_CLIENT_TICK.register(DuelInvseeClient::scannerTick);
		McWatcher.LOGGER.info("Duel-Invsee-Scanner aktiv (alle {}s)", config.invseeScanIntervalSeconds);
	}

	/** Laeuft auf dem Client-Thread (jeder Tick). */
	private static void scannerTick(Minecraft mc) {
		LocalPlayer player = mc.player;
		if (player == null || mc.getConnection() == null) {
			return;
		}
		long now = System.currentTimeMillis();

		if (scanCurrent != null) {
			if (mc.screen instanceof AbstractContainerScreen<?> screen) {
				finishScan(mc, player, screen);
			} else if (now > scanDeadline) {
				if (config.debug) {
					McWatcher.LOGGER.info("Invsee {}: kein Fenster geoeffnet (Timeout)", scanCurrent[0]);
				}
				scanCurrent = null;
			}
			return;
		}

		if (!scanQueue.isEmpty()) {
			if (mc.screen != null) {
				return; // Spieler hat gerade selbst ein Fenster/Chat offen - nicht stoeren
			}
			scanCurrent = scanQueue.poll();
			scanDeadline = now + OPEN_TIMEOUT_MS;
			mc.getConnection().sendCommand("invsee " + scanCurrent[0]);
			return;
		}

		if (now - lastFetch >= config.invseeScanIntervalSeconds * 1000L) {
			lastFetch = now;
			api.fetchDuelInvseeTargets(targets -> scanQueue.addAll(targets));
		}
	}

	private static void finishScan(Minecraft mc, LocalPlayer player, AbstractContainerScreen<?> screen) {
		String[] target = scanCurrent;
		scanCurrent = null;
		JsonArray items = new JsonArray();
		Inventory own = player.getInventory();
		for (Slot slot : screen.getMenu().slots) {
			if (slot.container == own) {
				continue; // eigenes Inventar im unteren Teil des Fensters ignorieren
			}
			ItemStack stack = slot.getItem();
			int i = slot.getContainerSlot();
			if (i < 36) {
				addItem(items, "main", i, stack);
			} else if (i < 40) {
				addItem(items, "armor", 39 - i, stack); // Bukkit-Reihenfolge: 39 = Helm
			} else if (i == 40) {
				addItem(items, "offhand", 0, stack);
			}
		}
		if (config.debug) {
			McWatcher.LOGGER.info("Invsee {}: {} Items gelesen", target[0], items.size());
		}
		player.closeContainer();
		publish(target[1], target[0], items);
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
		String ign = player.getGameProfile().name();
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
		String ign = player.getGameProfile().name();
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

		publish(token, selfIgn, items);
	}

	/** Zwei unabhängige Ziele, dieselben Item-Daten: Website (Live-Seite per Token) und Bot (Bild per DM). */
	private static void publish(String token, String ign, JsonArray items) {
		JsonObject websitePayload = new JsonObject();
		websitePayload.add("items", items);
		// buyer_ign/opponent_ign werden von ApiClient.postDuelInvseeSnapshot ergänzt:
		// aus Sicht der Website ist "opponent" immer der, dessen Inventar gemeldet wird (wir selbst).
		api.postDuelInvseeSnapshot(token, ign, "", websitePayload);

		JsonObject reportPayload = new JsonObject();
		reportPayload.add("items", items);
		api.postDuelInvseeReport(ign, reportPayload);
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
		String myName = player.getGameProfile().name();
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
