package de.txtempire.mcwatcher;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.inventory.AbstractContainerScreen;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.inventory.Slot;
import net.minecraft.world.item.ItemStack;

import java.util.concurrent.ConcurrentLinkedQueue;

/**
 * Duel Invsee (Scanner): holt beim Bot die bezahlten Ziele, führt ingame
 * {@code /invsee <Name>} aus, liest das geöffnete Fenster und meldet es an
 * Bot (DM-Bild) und Website. Registriert keinen eigenen Befehl.
 */
public final class DuelInvseeClient {

	private static final long OPEN_TIMEOUT_MS = 4000L;

	private static WatcherConfig config;
	private static ApiClient api;

	private static final ConcurrentLinkedQueue<String[]> scanQueue = new ConcurrentLinkedQueue<>();
	private static volatile String[] scanCurrent = null; // {ign, token}
	private static volatile long scanDeadline = 0L;
	private static volatile long lastFetch = 0L;

	private DuelInvseeClient() {
	}

	public static void init(WatcherConfig cfg, ApiClient apiClient) {
		config = cfg;
		api = apiClient;
		if (config.invseeScanner) {
			ClientTickEvents.END_CLIENT_TICK.register(DuelInvseeClient::scannerTick);
			McWatcher.LOGGER.info("Duel-Invsee-Scanner aktiv (alle {}s)", config.invseeScanIntervalSeconds);
		}
	}

	/** Läuft auf dem Client-Thread (jeder Tick). */
	private static void scannerTick(Minecraft mc) {
		LocalPlayer player = mc.player;
		if (player == null || mc.getConnection() == null) {
			return;
		}
		long now = System.currentTimeMillis();

		if (scanCurrent != null) {
			if (mc.screen instanceof AbstractContainerScreen<?> screen) {
				finishScan(player, screen);
			} else if (now > scanDeadline) {
				if (config.debug) {
					McWatcher.LOGGER.info("Invsee {}: kein Fenster geöffnet (Timeout)", scanCurrent[0]);
				}
				scanCurrent = null;
			}
			return;
		}

		if (!scanQueue.isEmpty()) {
			if (mc.screen != null) {
				return; // Spieler hat gerade selbst ein Fenster/Chat offen — nicht stören
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

	private static void finishScan(LocalPlayer player, AbstractContainerScreen<?> screen) {
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

	/** Zwei unabhängige Ziele, dieselben Item-Daten: Website (Live-Seite per Token) und Bot (Bild per DM). */
	private static void publish(String token, String ign, JsonArray items) {
		JsonObject websitePayload = new JsonObject();
		websitePayload.add("items", items);
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
}
