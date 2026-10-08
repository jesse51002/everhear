package com.voiceforgaming.vfg.config;

import com.voiceforgaming.vfg.VoiceService;
import com.voiceforgaming.vfg.session.VoiceStatus;
import com.voiceforgaming.vfg.speech.Levels;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.components.EditBox;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.network.chat.CommonComponents;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.Style;
import net.minecraft.util.FormattedCharSequence;

/**
 * Voice settings, from K in the game or the {@code Voice…} button on the Multiplayer
 * screen: server address (and {@code Use this server}), bridge URL, client token (masked),
 * microphone, {@code Test mic}, model, icon corner, status with {@code Retry}, and
 * {@code Test connection}. {@code Done} checks the bridge URL, saves and applies;
 * {@code Cancel} discards.
 */
public final class SettingsScreen extends Screen {
	private static final int WHITE = 0xFFFFFFFF;
	private static final int GREY = 0xFFA0A0A0;
	private static final int RED = 0xFFFF5555;
	private static final int GREEN = 0xFF55FF55;
	private static final int AMBER = 0xFFFFAA00;
	private static final int PANEL_W = 330;
	private static final int LABEL_W = 92;

	private final Screen parent;
	private final VoiceService service;
	private final VfgConfig draft;
	private final List<String> devices = new ArrayList<>();
	private boolean testing;
	private volatile Component connectionResult;
	private volatile int connectionColor = GREY;
	private Component error;

	private int left;
	private int top;
	private int rowH;
	private int widgetH;

	private Button useServerButton;
	private Button micButton;
	private Button testMicButton;
	private Button modelButton;
	private Button cornerButton;
	private Button retryButton;

	public SettingsScreen(Screen parent, VoiceService service) {
		super(Component.translatable("vfg.settings.title"));
		this.parent = parent;
		this.service = service;
		this.draft = service.config().copy();
		// Every OpenAL capture device, re-read each time the screen opens.
		devices.add("");
		for (String d : service.devices()) {
			if (!d.isEmpty() && !devices.contains(d)) {
				devices.add(d);
			}
		}
		if (!devices.contains(draft.micDevice)) {
			devices.add(draft.micDevice); // a saved device that is unplugged right now
		}
	}

	private int row(int i) {
		return top + i * rowH;
	}

	@Override
	protected void init() {
		left = width / 2 - PANEL_W / 2;
		int bottomReserve = 30;
		rowH = Math.max(20, Math.min(24, (height - 26 - bottomReserve - 12) / 9));
		widgetH = Math.min(20, rowH - 2);
		top = 26;
		int fieldX = left + LABEL_W;
		int fieldW = PANEL_W - LABEL_W;

		// 0: server address + Use this server
		EditBox server = new EditBox(font, fieldX, row(0), fieldW - 92, widgetH,
			Component.translatable("vfg.settings.server_address"));
		server.setMaxLength(256);
		server.setValue(draft.serverAddress);
		server.setHint(Component.translatable("vfg.settings.server_address.hint"));
		server.setResponder(v -> draft.serverAddress = v.trim());
		addRenderableWidget(server);
		useServerButton = addRenderableWidget(Button.builder(Component.translatable("vfg.settings.use_this_server"), b -> {
			String joined = service.joinedAddress();
			if (joined != null) {
				server.setValue(joined);
			}
		}).bounds(fieldX + fieldW - 88, row(0), 88, widgetH).build());

		// 1: bridge URL
		EditBox bridge = new EditBox(font, fieldX, row(1), fieldW, widgetH, Component.translatable("vfg.settings.bridge_url"));
		bridge.setMaxLength(256);
		bridge.setValue(draft.bridgeUrl);
		bridge.setHint(Component.translatable("vfg.settings.bridge_url.hint"));
		bridge.setResponder(v -> draft.bridgeUrl = v.trim());
		addRenderableWidget(bridge);

		// 2: client token, masked; pasting works because the box holds the real value
		EditBox token = new EditBox(font, fieldX, row(2), fieldW, widgetH, Component.translatable("vfg.settings.client_token"));
		token.setMaxLength(512);
		token.setValue(draft.clientToken);
		token.addFormatter((text, firstCharIndex) -> FormattedCharSequence.forward("*".repeat(text.length()), Style.EMPTY));
		token.setResponder(v -> draft.clientToken = v.trim());
		addRenderableWidget(token);

		// 3: microphone
		micButton = addRenderableWidget(Button.builder(deviceLabel(), b -> {
			int i = devices.indexOf(draft.micDevice);
			draft.micDevice = devices.get((i + 1) % devices.size());
			b.setMessage(deviceLabel());
			if (testing) {
				service.setTestMic(true, draft.micDevice);
			}
		}).bounds(fieldX, row(3), fieldW, widgetH).build());

		// 4: test mic (meter, speech light and "Heard:" drawn beside and below it)
		testMicButton = addRenderableWidget(Button.builder(testMicLabel(), b -> {
			testing = !testing;
			service.setTestMic(testing, draft.micDevice);
			b.setMessage(testMicLabel());
		}).bounds(fieldX, row(4), 80, widgetH).build());

		// 5: model
		modelButton = addRenderableWidget(Button.builder(modelLabel(), b -> {
			int i = VfgConfig.MODELS.indexOf(draft.model);
			draft.model = VfgConfig.MODELS.get((i + 1) % VfgConfig.MODELS.size());
			b.setMessage(modelLabel());
		}).bounds(fieldX, row(5) + 12, fieldW, widgetH).build());

		// 6: icon corner
		cornerButton = addRenderableWidget(Button.builder(cornerLabel(), b -> {
			int i = VfgConfig.CORNERS.indexOf(draft.iconCorner);
			draft.iconCorner = VfgConfig.CORNERS.get((i + 1) % VfgConfig.CORNERS.size());
			b.setMessage(cornerLabel());
		}).bounds(fieldX, row(6) + 12, fieldW, widgetH).build());

		// 7: status, with Retry when setup failed
		retryButton = addRenderableWidget(Button.builder(Component.translatable("vfg.settings.retry"), b -> service.retrySetup())
			.bounds(fieldX + fieldW - 60, row(7) + 12, 60, widgetH).build());

		// 8: test connection
		addRenderableWidget(Button.builder(Component.translatable("vfg.settings.test_connection"), b -> testConnection())
			.bounds(fieldX, row(8) + 12, 110, widgetH).build());

		// Done / Cancel
		int by = Math.max(row(9) + 16, height - 26);
		by = Math.min(by, height - 22);
		addRenderableWidget(Button.builder(CommonComponents.GUI_DONE, b -> done())
			.bounds(width / 2 - 154, by, 150, 20).build());
		addRenderableWidget(Button.builder(CommonComponents.GUI_CANCEL, b -> onClose())
			.bounds(width / 2 + 4, by, 150, 20).build());

		refresh();
	}

	private Component deviceLabel() {
		return draft.micDevice.isEmpty() ? Component.translatable("vfg.settings.mic.default")
			: Component.literal(draft.micDevice);
	}

	private Component testMicLabel() {
		return Component.translatable(testing ? "vfg.settings.test_mic.on" : "vfg.settings.test_mic.off");
	}

	private Component modelLabel() {
		return Component.translatable(switch (draft.model) {
			case VfgConfig.MODEL_BASE -> "vfg.settings.model.base";
			case VfgConfig.MODEL_TINY -> "vfg.settings.model.tiny";
			default -> "vfg.settings.model.parakeet";
		});
	}

	private Component cornerLabel() {
		return Component.translatable("vfg.settings.corner." + draft.iconCorner);
	}

	private void testConnection() {
		connectionResult = Component.translatable("vfg.settings.connection.testing");
		connectionColor = GREY;
		service.testConnection(draft.effectiveBridgeUrl(), draft.effectiveServerAddress(), draft.effectiveClientToken())
			.thenAccept(r -> {
				switch (r.check()) {
					case TOKEN_ACCEPTED -> {
						connectionResult = Component.translatable("vfg.settings.connection.ok");
						connectionColor = GREEN;
					}
					case TOKEN_REJECTED -> {
						connectionResult = Component.translatable("vfg.settings.connection.rejected");
						connectionColor = RED;
					}
					case UNREACHABLE -> {
						connectionResult = Component.translatable("vfg.settings.connection.unreachable");
						connectionColor = RED;
					}
					case UNEXPECTED -> {
						connectionResult = Component.translatable("vfg.settings.connection.unexpected", r.status());
						connectionColor = AMBER;
					}
				}
			});
	}

	private void done() {
		if (!VfgConfig.isValidBridgeUrl(draft.bridgeUrl)) {
			error = Component.translatable("vfg.settings.error.bridge_url");
			return;
		}
		stopTest();
		try {
			service.applySettings(draft.copy());
		} catch (IOException e) {
			error = Component.translatable("vfg.settings.error.save", e.getMessage());
			return;
		}
		minecraft.gui.setScreen(parent);
	}

	private void stopTest() {
		if (testing) {
			testing = false;
			service.setTestMic(false, draft.micDevice);
		}
	}

	@Override
	public void onClose() {
		stopTest();
		minecraft.gui.setScreen(parent);
	}

	@Override
	public void removed() {
		stopTest();
		super.removed();
	}

	@Override
	public void tick() {
		super.tick();
		refresh();
	}

	private void refresh() {
		VoiceStatus st = service.status();
		if (useServerButton != null) {
			useServerButton.active = service.joinedAddress() != null;
		}
		if (retryButton != null) {
			retryButton.visible = st.setup() == VoiceStatus.Setup.SETUP_FAILED || st.setup() == VoiceStatus.Setup.ENGINE_FAILED;
		}
		if (testMicButton != null) {
			testMicButton.setMessage(testMicLabel());
		}
	}

	@Override
	public void extractRenderState(GuiGraphicsExtractor g, int mouseX, int mouseY, float partialTick) {
		super.extractRenderState(g, mouseX, mouseY, partialTick);
		g.centeredText(font, title, width / 2, 8, WHITE);
		if (draft.hasEnvOverrides()) {
			g.centeredText(font, Component.translatable("vfg.settings.env_override"), width / 2, 17, AMBER);
		}
		int textDy = (widgetH - 8) / 2;
		label(g, "vfg.settings.server_address", row(0) + textDy);
		label(g, "vfg.settings.bridge_url", row(1) + textDy);
		label(g, "vfg.settings.client_token", row(2) + textDy);
		label(g, "vfg.settings.microphone", row(3) + textDy);
		label(g, "vfg.settings.test_mic", row(4) + textDy);
		label(g, "vfg.settings.model", row(5) + 12 + textDy);
		label(g, "vfg.settings.icon_corner", row(6) + 12 + textDy);
		label(g, "vfg.settings.status", row(7) + 12 + textDy);
		label(g, "vfg.settings.connection", row(8) + 12 + textDy);

		VoiceStatus st = service.status();
		int fieldX = left + LABEL_W;
		int fieldW = PANEL_W - LABEL_W;

		// Mic test: peak meter, speech light, last text heard.
		boolean live = testing || service.listening();
		int meterX = fieldX + 86;
		int meterY = row(4) + widgetH / 2 - 3;
		int meterW = fieldW - 86 - 14;
		g.fill(meterX, meterY, meterX + meterW, meterY + 6, 0xFF202020);
		if (live) {
			double db = Levels.toDbfs(st.livePeak());
			int filled = (int) Math.round(Math.max(0, Math.min(1, (db + 60.0) / 60.0)) * meterW);
			int color = db > -3 ? RED : db > -12 ? AMBER : GREEN;
			if (filled > 0) {
				g.fill(meterX, meterY, meterX + filled, meterY + 6, color);
			}
		}
		int lightX = fieldX + fieldW - 8;
		g.fill(lightX, meterY, lightX + 8, meterY + 6, live && st.speaking() ? GREEN : 0xFF404040);
		if (live) {
			String heard = Component.translatable("vfg.settings.heard", st.lastHeard()).getString();
			g.text(font, font.plainSubstrByWidth(heard, fieldW), fieldX, row(4) + widgetH + 2, GREY);
		}

		// Status line.
		int statusW = retryButton != null && retryButton.visible ? fieldW - 64 : fieldW;
		Component status = statusText(st);
		int statusColor = st.setupFailed() || st.micError() != null || st.tokenRejected() ? RED
			: st.setup() == VoiceStatus.Setup.RUNNING ? AMBER : GREEN;
		g.text(font, font.plainSubstrByWidth(status.getString(), statusW), fieldX, row(7) + 12 + textDy, statusColor);

		Component result = connectionResult;
		if (result != null) {
			g.text(font, font.plainSubstrByWidth(result.getString(), fieldW - 116), fieldX + 116,
				row(8) + 12 + textDy, connectionColor);
		}
		if (error != null) {
			g.centeredText(font, error, width / 2, row(9) + 4, RED);
		}
	}

	private Component statusText(VoiceStatus st) {
		return switch (st.setup()) {
			case RUNNING -> Component.translatable("vfg.status.setup_running", st.setupPercent());
			case SETUP_FAILED -> Component.translatable("vfg.status.setup_failed", nullToEmpty(st.setupError()));
			case ENGINE_FAILED -> Component.translatable("vfg.status.engine_failed", nullToEmpty(st.setupError()));
			case UNSUPPORTED -> Component.translatable("vfg.status.unsupported");
			case READY -> {
				if (st.micError() != null) {
					yield Component.literal(st.micError());
				}
				if (st.tokenRejected()) {
					yield Component.translatable("vfg.status.token_rejected");
				}
				if (st.deliveryProblem()) {
					yield Component.translatable("vfg.status.delivery_problem");
				}
				yield Component.translatable(service.listening() ? "vfg.status.listening" : "vfg.status.ready");
			}
		};
	}

	private void label(GuiGraphicsExtractor g, String key, int y) {
		g.text(font, Component.translatable(key), left, y, WHITE);
	}

	private static String nullToEmpty(String s) {
		return s == null ? "" : s;
	}
}
