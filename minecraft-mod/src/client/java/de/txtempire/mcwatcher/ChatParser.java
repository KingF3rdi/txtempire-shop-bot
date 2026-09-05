package de.txtempire.mcwatcher;

import java.util.Locale;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Erkennt !link CODE (inkl. /msg Whisper) und Money-Transfers. */
public final class ChatParser {
	private static final Pattern STRIP = Pattern.compile("§[0-9A-FK-OR]|&#[0-9A-Fa-f]{6}");

	/** Wörter, die nie ein Spieler-IGN sind (Chat-Labels / System). */
	private static final Set<String> RESERVED = Set.of(
		"nachricht", "msg", "whisper", "pn", "pm", "message", "system",
		"money", "geld", "zahlung", "payment", "link", "verify", "code",
		"txtempire", "dir", "you", "dich", "du", "sell", "auktionshaus", "hugosmp"
	);

	private static final Pattern[] WHISPERS = new Pattern[] {
		// Server: "[Nachricht] p9x1 -> Du: !link …"
		Pattern.compile(
			"(?i)^\\[(?:nachricht|msg|whisper|pn|pm|message)\\]\\s*"
				+ "([A-Za-z0-9_]{3,16})\\s*(?:->|→|»|›)\\s*"
				+ "(?:dir|you|dich|du|[A-Za-z0-9_]{3,16})\\s*[:»>]\\s*(.+)$"
		),
		Pattern.compile(
			"(?i)^([A-Za-z0-9_]{3,16})\\s+whispers?(?:\\s+to\\s+you)?\\s*:\\s*(.+)$"
		),
		Pattern.compile(
			"(?i)^([A-Za-z0-9_]{3,16})\\s+fl[uü]ster(?:t|te)(?:\\s+dir)?(?:\\s+zu)?\\s*:\\s*(.+)$"
		),
		Pattern.compile(
			"(?i)^\\[(?:msg|whisper|pn|pm|nachricht|message)\\]\\s*"
				+ "([A-Za-z0-9_]{3,16})\\s*[:»>]\\s*(.+)$"
		),
		// "Steve -> TxTEmpire: !link …" / "Steve » dir: …"
		Pattern.compile(
			"(?i)^([A-Za-z0-9_]{3,16})\\s*(?:->|→|»|›)\\s*"
				+ "(?:dir|you|dich|du|[A-Za-z0-9_]{3,16})?\\s*[:»>]\\s*(.+)$"
		),
		Pattern.compile(
			"(?i)^von\\s+([A-Za-z0-9_]{3,16})\\s*[:»>]\\s*(.+)$"
		),
		Pattern.compile(
			"^[<\\[]\\s*([A-Za-z0-9_]{3,16})\\s*[>\\]]\\s*(.+)$"
		),
		Pattern.compile(
			"(?i)^(?:msg|pn|pm)\\s*[|\\-–]\\s*([A-Za-z0-9_]{3,16})\\s*[|\\-–:]\\s*(.+)$"
		),
	};

	private static final Pattern CODE_ANYWHERE = Pattern.compile(
		"(?i)\\b(TXT[E]?-[A-Z0-9]{4,12})\\b"
	);
	private static final Pattern NAME_BEFORE_CODE = Pattern.compile(
		"(?i)\\b([A-Za-z0-9_]{3,16})\\b[^A-Za-z0-9_]{0,48}?\\b(TXT[E]?-[A-Z0-9]{4,12})\\b"
	);

	private static final Pattern LINK_CMD = Pattern.compile(
		"(?i)(?:^|[\\s\\[\\]<>:])(!?link|!?verify|!verknüpf|!verknuepf)\\s+([A-Za-z0-9\\-]{4,24})\\b"
	);
	private static final Pattern LINK_ONLY = Pattern.compile(
		"(?i)^(?:code[:\\s]*)?(TXT[E]?-[A-Z0-9]{4,12})$"
	);

	private static final Pattern[] PAYMENTS = new Pattern[] {
		// HugoSMP / ähnlich: "[HugoSMP] Du hast $400000 von 0Moos erhalten."
		Pattern.compile(
			"(?i)du\\s+hast\\s+\\$?\\s*([\\d][\\d.,]*\\s*[kmb]?)\\s*(?:\\$|€|euro)?\\s+"
				+ "von\\s+([A-Za-z0-9_]{3,16})\\s+erhalten"
		),
		// "Steve hat dir 1.000.000$ gegeben" / "Steve hat dir $500k gegeben"
		Pattern.compile(
			"(?i)([A-Za-z0-9_]{3,16})\\s+hat\\s+dir\\s+\\$?\\s*([\\d][\\d.,]*\\s*[kmb]?)\\s*"
				+ "(?:\\$|€|euro|geld|coins?)?\\s+gegeben"
		),
		// "Gamerleo15 » TxTEmpire - $450,000"
		Pattern.compile(
			"(?i)([A-Za-z0-9_]{3,16})\\s*[»›→>]\\s*"
				+ "(?:you|dir|dich|du|txtempire|[A-Za-z0-9_]{3,16})\\s*[-–:]\\s*"
				+ "\\$?\\s*([\\d][\\d.,]*\\s*[kmb]?)"
		),
		Pattern.compile(
			"(?i)([A-Za-z0-9_]{3,16})\\s+(?:paid|sent|gave)\\s+(?:you\\s+)?"
				+ "\\$?\\s*([\\d][\\d.,]*\\s*[kmb]?)"
		),
		Pattern.compile(
			"(?i)(?:zahlung|payment|überweisung|ueberweisung)\\s+(?:von\\s+)?"
				+ "([A-Za-z0-9_]{3,16})\\s*:?\\s*\\$?\\s*([\\d][\\d.,]*\\s*[kmb]?)"
		),
	};

	/** Patterns bei denen group1=amount, group2=ign */
	private static final int AMOUNT_FIRST_COUNT = 1;

	private ChatParser() {}

	public static String strip(String text) {
		if (text == null) {
			return "";
		}
		return STRIP.matcher(text).replaceAll("").replaceAll("\\s+", " ").trim();
	}

	private static boolean isIgn(String name) {
		if (name == null || name.isBlank()) {
			return false;
		}
		String n = name.trim();
		if (n.length() < 3 || n.length() > 16) {
			return false;
		}
		return !RESERVED.contains(n.toLowerCase(Locale.ROOT));
	}

	public static Parsed parse(String raw, String senderHint) {
		String clean = strip(raw);
		if (clean.isEmpty()) {
			return null;
		}

		String sender = isIgn(senderHint) ? senderHint.trim() : null;
		String body = clean;
		for (Pattern wp : WHISPERS) {
			Matcher wm = wp.matcher(clean);
			if (!wm.matches()) {
				continue;
			}
			String from = wm.group(1);
			if (isIgn(from)) {
				sender = from.trim();
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
		if (linkFound && isIgn(sender)) {
			return Parsed.link(lm.group(2).toUpperCase(Locale.ROOT), sender);
		}
		Matcher only = LINK_ONLY.matcher(body);
		if (only.matches() && isIgn(sender)) {
			return Parsed.link(only.group(1).toUpperCase(Locale.ROOT), sender);
		}

		Matcher named = NAME_BEFORE_CODE.matcher(clean);
		while (named.find()) {
			String ign = named.group(1);
			String code = named.group(2).toUpperCase(Locale.ROOT);
			if (isIgn(ign)) {
				return Parsed.link(code, ign);
			}
		}
		if (isIgn(sender)) {
			Matcher anywhere = CODE_ANYWHERE.matcher(clean);
			if (anywhere.find()) {
				return Parsed.link(anywhere.group(1).toUpperCase(Locale.ROOT), sender);
			}
		}

		// Index 0 = "du hast … von … erhalten" → amount dann ign
		for (int i = 0; i < PAYMENTS.length; i++) {
			Matcher pm = PAYMENTS[i].matcher(clean);
			if (!pm.find()) {
				continue;
			}
			String ign;
			String amountRaw;
			if (i < AMOUNT_FIRST_COUNT) {
				amountRaw = pm.group(1);
				ign = pm.group(2);
			} else {
				ign = pm.group(1);
				amountRaw = pm.group(2);
			}
			Double amount = parseAmount(amountRaw);
			if (amount != null && amount > 0 && isIgn(ign)) {
				return Parsed.payment(ign, amount);
			}
		}
		return null;
	}

	static Double parseAmount(String raw) {
		if (raw == null) {
			return null;
		}
		String s = raw.trim().replace(" ", "").replace("$", "").replace("€", "");
		if (s.isEmpty()) {
			return null;
		}
		double mult = 1;
		char last = Character.toLowerCase(s.charAt(s.length() - 1));
		if (last == 'k' || last == 'm' || last == 'b') {
			mult = last == 'k' ? 1_000d : last == 'm' ? 1_000_000d : 1_000_000_000d;
			s = s.substring(0, s.length() - 1);
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
			return Double.parseDouble(s) * mult;
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
