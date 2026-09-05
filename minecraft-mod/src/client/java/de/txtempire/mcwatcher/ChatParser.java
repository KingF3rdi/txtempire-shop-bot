package de.txtempire.mcwatcher;

import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Erkennt !link CODE (inkl. /msg Whisper) und Money-Transfers. */
public final class ChatParser {
	private static final Pattern STRIP = Pattern.compile("§[0-9A-FK-OR]|&#[0-9A-Fa-f]{6}");

	private static final Pattern[] WHISPERS = new Pattern[] {
		Pattern.compile(
			"(?i)^([A-Za-z0-9_]{3,16})\\s+whispers?(?:\\s+to\\s+you)?\\s*:\\s*(.+)$"
		),
		Pattern.compile(
			"(?i)^([A-Za-z0-9_]{3,16})\\s+fl[uü]ster(?:t|te)(?:\\s+dir)?(?:\\s+zu)?\\s*:\\s*(.+)$"
		),
		Pattern.compile(
			"(?i)^\\[(?:msg|whisper|pn|pm|nachricht)\\]\\s*([A-Za-z0-9_]{3,16})\\s*:\\s*(.+)$"
		),
		Pattern.compile(
			"(?i)^([A-Za-z0-9_]{3,16})\\s*(?:->|→|»)\\s*(?:dir|you|dich)?\\s*:\\s*(.+)$"
		),
		Pattern.compile(
			"(?i)^von\\s+([A-Za-z0-9_]{3,16})\\s*:\\s*(.+)$"
		),
		Pattern.compile(
			"^[<\\[]\\s*([A-Za-z0-9_]{3,16})\\s*[>\\]]\\s*(.+)$"
		),
	};

	private static final Pattern LINK_CMD = Pattern.compile(
		"(?i)(?:^|[\\s\\[\\]<>:])(!?link|!?verify|!verknüpf|!verknuepf)\\s+([A-Za-z0-9\\-]{4,24})\\b"
	);
	private static final Pattern LINK_ONLY = Pattern.compile(
		"(?i)^(?:code[:\\s]*)?(TXT[E]?-[A-Z0-9]{4,12})$"
	);

	private static final Pattern[] PAYMENTS = new Pattern[] {
		Pattern.compile(
			"(?i)([A-Za-z0-9_]{3,16})\\s+hat\\s+dir\\s+([\\d][\\d.,]*)\\s*(?:\\$|€|euro|geld|coins?)?\\s+gegeben"
		),
		Pattern.compile(
			"(?i)du\\s+hast\\s+([\\d][\\d.,]*)\\s*(?:\\$|€|euro)?\\s+von\\s+([A-Za-z0-9_]{3,16})\\s+erhalten"
		),
		Pattern.compile(
			"(?i)([A-Za-z0-9_]{3,16})\\s+(?:paid|sent|gave)\\s+(?:you\\s+)?([\\d][\\d.,]*)"
		),
		Pattern.compile(
			"(?i)([A-Za-z0-9_]{3,16})\\s*(?:->|→|»|>)\\s*(?:you|dir|dich)?\\s*:?\\s*([\\d][\\d.,]*)\\s*(?:\\$|€)?"
		),
		Pattern.compile(
			"(?i)(?:zahlung|payment|überweisung|ueberweisung)\\s+(?:von\\s+)?([A-Za-z0-9_]{3,16})\\s*:?\\s*([\\d][\\d.,]*)"
		),
	};

	private ChatParser() {}

	public static String strip(String text) {
		if (text == null) {
			return "";
		}
		return STRIP.matcher(text).replaceAll("").replaceAll("\\s+", " ").trim();
	}

	public static Parsed parse(String raw, String senderHint) {
		String clean = strip(raw);
		if (clean.isEmpty()) {
			return null;
		}

		String sender = senderHint;
		String body = clean;
		for (Pattern wp : WHISPERS) {
			Matcher wm = wp.matcher(clean);
			if (wm.matches()) {
				if (sender == null || sender.isBlank()) {
					sender = wm.group(1);
				}
				body = wm.group(2).trim();
				break;
			}
		}

		Matcher lm = LINK_CMD.matcher(body);
		boolean linkFound = lm.find();
		if (!linkFound) {
			lm = LINK_CMD.matcher(clean);
			linkFound = lm.find();
		}
		if (linkFound && sender != null && !sender.isBlank()) {
			return Parsed.link(lm.group(2).toUpperCase(Locale.ROOT), sender.trim());
		}
		Matcher only = LINK_ONLY.matcher(body);
		if (only.matches() && sender != null && !sender.isBlank()) {
			return Parsed.link(only.group(1).toUpperCase(Locale.ROOT), sender.trim());
		}

		for (int i = 0; i < PAYMENTS.length; i++) {
			Matcher pm = PAYMENTS[i].matcher(clean);
			if (!pm.find()) {
				continue;
			}
			String ign;
			String amountRaw;
			if (i == 1) {
				amountRaw = pm.group(1);
				ign = pm.group(2);
			} else {
				ign = pm.group(1);
				amountRaw = pm.group(2);
			}
			Double amount = parseAmount(amountRaw);
			if (amount != null && amount > 0 && ign != null) {
				return Parsed.payment(ign, amount);
			}
		}
		return null;
	}

	static Double parseAmount(String raw) {
		if (raw == null) {
			return null;
		}
		String s = raw.trim().replace(" ", "");
		if (s.isEmpty()) {
			return null;
		}
		if (s.contains(",") && s.contains(".")) {
			if (s.lastIndexOf(',') > s.lastIndexOf('.')) {
				s = s.replace(".", "").replace(",", ".");
			} else {
				s = s.replace(",", "");
			}
		} else if (s.contains(",")) {
			String[] parts = s.split(",");
			if (parts.length > 1 && parts[parts.length - 1].length() == 3) {
				s = s.replace(",", "");
			} else {
				s = s.replace(",", ".");
			}
		} else if (s.chars().filter(ch -> ch == '.').count() > 1) {
			s = s.replace(".", "");
		}
		try {
			return Double.parseDouble(s);
		} catch (NumberFormatException e) {
			return null;
		}
	}

	public record Parsed(Kind kind, String code, String ign, double amount) {
		enum Kind { LINK, PAYMENT }

		static Parsed link(String code, String ign) {
			return new Parsed(Kind.LINK, code, ign, 0);
		}

		static Parsed payment(String ign, double amount) {
			return new Parsed(Kind.PAYMENT, null, ign, amount);
		}
	}
}
