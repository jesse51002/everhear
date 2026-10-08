package com.voiceforgaming.vfg.hud;

import com.voiceforgaming.vfg.VoiceService;
import com.voiceforgaming.vfg.session.VoiceStatus;
import com.voiceforgaming.vfg.speech.Levels;
import java.util.function.Supplier;
import net.minecraft.client.DeltaTracker;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.GuiGraphicsExtractor;

/**
 * The corner icon: a 9×13 GUI-pixel mic drawn with {@code fill} from a bitmask, 4 px in
 * from the configured corner, drawn only while connected to the matching server and while
 * the HUD is shown. Pixel art rather than a sprite, so nothing breaks when the sprite API
 * moves.
 */
public final class ListeningIcon {
	public static final int WIDTH = 9;
	public static final int HEIGHT = 13;
	private static final int INSET = 4;

	private static final String[] MIC = {
		"...XXX...",
		"..XXXXX..",
		"..XXXXX..",
		"..XXXXX..",
		"..XXXXX..",
		"X.XXXXX.X",
		"X.XXXXX.X",
		"X..XXX..X",
		".X.....X.",
		"..XXXXX..",
		"....X....",
		"....X....",
		"..XXXXX..",
	};

	private static final int SHADOW = 0x80000000;
	private static final int AMBER = 0xFFFFAA00;
	private static final int RED = 0xFFE03030;
	private static final int RED_SLASH = 0xFFFF9090;
	private static final int GREY = 0xFF8A8A8A;
	private static final int GREY_SLASH = 0xFFE0E0E0;
	private static final int WHITE = 0xFFFFFFFF;
	private static final int GREEN = 0xFF55FF55;
	private static final int DIM_GREEN = 0xFF2E8B2E;
	private static final int BAR_BACK = 0x90000000;

	private final Supplier<VoiceService> service;

	public ListeningIcon(Supplier<VoiceService> service) {
		this.service = service;
	}

	/** {@code HudElement#extractRenderState}. */
	public void extract(GuiGraphicsExtractor g, DeltaTracker deltaTracker) {
		VoiceService s = service.get();
		if (s == null) {
			return;
		}
		VoiceStatus st = s.status();
		VoiceStatus.Icon icon = st.icon();
		Minecraft mc = Minecraft.getInstance();
		if (icon == VoiceStatus.Icon.HIDDEN || mc.gui.hud.isHidden()) {
			return;
		}
		String corner = s.config().iconCorner;
		boolean left = corner.endsWith("left");
		boolean top = corner.startsWith("top");
		int x = left ? INSET : g.guiWidth() - INSET - WIDTH;
		int y = top ? INSET : g.guiHeight() - INSET - HEIGHT - 3;

		switch (icon) {
			case SETUP -> {
				mic(g, x, y, AMBER);
				label(g, mc, x, y, left, st.setupPercent() + "%", AMBER);
			}
			case ERROR -> {
				mic(g, x, y, RED);
				slash(g, x, y, RED_SLASH);
			}
			case MUTED -> {
				mic(g, x, y, GREY);
				slash(g, x, y, GREY_SLASH);
			}
			case NO_TOKEN -> {
				mic(g, x, y, GREY);
				label(g, mc, x, y, left, "?", GREY_SLASH);
			}
			case DELIVERY_PROBLEM -> {
				mic(g, x, y, WHITE);
				g.fill(x + WIDTH - 2, y - 1, x + WIDTH + 1, y + 2, AMBER);
			}
			case HEARING -> {
				mic(g, x, y, GREEN);
				levelBar(g, x, y + HEIGHT + 1, st.livePeak());
			}
			case LISTENING -> mic(g, x, y, DIM_GREEN);
			default -> {
			}
		}
	}

	private static void mic(GuiGraphicsExtractor g, int x, int y, int color) {
		for (int pass = 0; pass < 2; pass++) {
			int dx = pass == 0 ? 1 : 0;
			int c = pass == 0 ? SHADOW : color;
			for (int r = 0; r < MIC.length; r++) {
				String row = MIC[r];
				int c0 = -1;
				for (int col = 0; col <= row.length(); col++) {
					boolean on = col < row.length() && row.charAt(col) == 'X';
					if (on && c0 < 0) {
						c0 = col;
					} else if (!on && c0 >= 0) {
						g.fill(x + c0 + dx, y + r + dx, x + col + dx, y + r + 1 + dx, c);
						c0 = -1;
					}
				}
			}
		}
	}

	private static void slash(GuiGraphicsExtractor g, int x, int y, int color) {
		for (int r = -1; r <= HEIGHT; r++) {
			int col = WIDTH - 1 - Math.round((r + 1) * (WIDTH - 1) / (float) (HEIGHT + 1));
			g.fill(x + col + 1, y + r, x + col + 2, y + r + 1, SHADOW);
			g.fill(x + col, y + r, x + col + 1, y + r + 1, color);
		}
	}

	private static void levelBar(GuiGraphicsExtractor g, int x, int y, float peak) {
		double db = Levels.toDbfs(peak);
		int filled = (int) Math.round(Math.max(0, Math.min(1, (db + 60.0) / 60.0)) * WIDTH);
		g.fill(x, y, x + WIDTH, y + 2, BAR_BACK);
		if (filled > 0) {
			g.fill(x, y, x + filled, y + 2, GREEN);
		}
	}

	private static void label(GuiGraphicsExtractor g, Minecraft mc, int x, int y, boolean left, String text, int color) {
		int w = mc.font.width(text);
		int tx = left ? x + WIDTH + 3 : x - 3 - w;
		g.text(mc.font, text, tx, y + 3, color);
	}
}
