package com.voiceforgaming.vfg;

import com.mojang.blaze3d.platform.InputConstants;
import com.voiceforgaming.vfg.config.SettingsScreen;
import com.voiceforgaming.vfg.hud.ListeningIcon;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientLifecycleEvents;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.keymapping.v1.KeyMappingHelper;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayConnectionEvents;
import net.fabricmc.fabric.api.client.rendering.v1.hud.HudElementRegistry;
import net.fabricmc.fabric.api.client.rendering.v1.hud.VanillaHudElements;
import net.fabricmc.fabric.api.client.screen.v1.ScreenEvents;
import net.fabricmc.fabric.api.client.screen.v1.Screens;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.ChatFormatting;
import net.minecraft.client.KeyMapping;
import net.minecraft.client.Minecraft;
import net.minecraft.client.User;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.gui.screens.multiplayer.JoinMultiplayerScreen;
import net.minecraft.client.multiplayer.ClientPacketListener;
import net.minecraft.client.multiplayer.PlayerInfo;
import net.minecraft.client.multiplayer.ServerData;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.Identifier;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * The entrypoint: wires {@link VoiceService} to Fabric's events — lifecycle, join and
 * leave, the M and K keys, the HUD icon and the Multiplayer screen's {@code Voice…}
 * button; once a second, the names in the tab list, for the hotwords. Nothing here waits on
 * audio, speech or the network.
 */
public final class VfgClient implements ClientModInitializer {
	public static final String MOD_ID = "vfg";
	private static final Logger LOG = LoggerFactory.getLogger("vfg");

	/** How often the tab list is read for the hotwords: every 20 ticks, a second. */
	private static final int PLAYERS_EVERY_TICKS = 20;

	private static volatile VoiceService service;
	private static int ticks;
	private static KeyMapping muteKey;
	private static KeyMapping settingsKey;

	public static Identifier id(String path) {
		return Identifier.fromNamespaceAndPath(MOD_ID, path);
	}

	/** The running service, or {@code null} before the client has started. */
	public static VoiceService service() {
		return service;
	}

	@Override
	public void onInitializeClient() {
		KeyMapping.Category category = KeyMapping.Category.register(id("voice"));
		muteKey = KeyMappingHelper.registerKeyMapping(
			new KeyMapping("key.vfg.mute", InputConstants.Type.KEYBOARD, InputConstants.KEY_M, category));
		settingsKey = KeyMappingHelper.registerKeyMapping(
			new KeyMapping("key.vfg.settings", InputConstants.Type.KEYBOARD, InputConstants.KEY_K, category));

		ClientLifecycleEvents.CLIENT_STARTED.register(VfgClient::onStarted);
		ClientLifecycleEvents.CLIENT_STOPPING.register(mc -> {
			VoiceService s = service;
			if (s != null) {
				s.stop();
			}
		});

		ClientPlayConnectionEvents.JOIN.register((listener, sender, mc) -> {
			VoiceService s = service;
			if (s != null) {
				s.onJoin(joinedAddress(mc));
			}
		});
		ClientPlayConnectionEvents.DISCONNECT.register((listener, mc) -> {
			VoiceService s = service;
			if (s != null) {
				s.onLeave(); // forgets the players too
			}
		});

		ClientTickEvents.END_CLIENT_TICK.register(VfgClient::onTick);

		ListeningIcon icon = new ListeningIcon(VfgClient::service);
		HudElementRegistry.attachElementBefore(VanillaHudElements.CHAT, id("listening"), icon::extract);

		ScreenEvents.AFTER_INIT.register((mc, screen, width, height) -> {
			if (screen instanceof JoinMultiplayerScreen) {
				Screens.getWidgets(screen).add(Button.builder(Component.translatable("vfg.button.voice"),
					b -> openSettings(mc, screen)).bounds(width - 86, 6, 80, 20).build());
			}
		});
	}

	private static void onStarted(Minecraft mc) {
		User user = mc.getUser();
		UUID uuid = user.getProfileId();
		String name = user.getName();
		Path gameDir = FabricLoader.getInstance().getGameDir();
		Path configDir = FabricLoader.getInstance().getConfigDir();
		try {
			VoiceService s = new VoiceService(gameDir, configDir, uuid, name, key -> notice(mc, key));
			service = s;
			s.start();
		} catch (RuntimeException e) {
			LOG.error("voice could not start", e);
		}
	}

	/** The joined server's address as typed in the server list; {@code null} in singleplayer and on LAN. */
	private static String joinedAddress(Minecraft mc) {
		ServerData server = mc.getCurrentServer();
		if (server == null || server.isLan() || mc.hasSingleplayerServer()) {
			return null;
		}
		return server.ip;
	}

	private static void onTick(Minecraft mc) {
		VoiceService s = service;
		while (muteKey.consumeClick()) {
			if (s != null) {
				s.toggleMute();
			}
		}
		while (settingsKey.consumeClick()) {
			if (s != null && mc.gui.screen() == null) {
				openSettings(mc, null);
			}
		}
		if (s != null && ++ticks % PLAYERS_EVERY_TICKS == 0) {
			ClientPacketListener connection = mc.getConnection();
			if (connection != null) {
				s.onPlayers(tabList(connection));
			}
		}
	}

	/** The names in the tab list: the players online, as the server lists them. */
	private static List<String> tabList(ClientPacketListener connection) {
		List<String> names = new ArrayList<>();
		for (PlayerInfo info : connection.getListedOnlinePlayers()) {
			names.add(info.getProfile().name());
		}
		return names;
	}

	private static void openSettings(Minecraft mc, Screen parent) {
		VoiceService s = service;
		if (s != null) {
			mc.gui.setScreen(new SettingsScreen(parent, s));
		}
	}

	/** A grey {@code [Voice] } line in the local chat HUD only; nothing is ever sent as chat. */
	private static void notice(Minecraft mc, String key) {
		mc.execute(() -> {
			Component text = Component.translatable("vfg.chat.prefix")
				.append(Component.translatable(key, muteKey.getTranslatedKeyMessage(),
					settingsKey.getTranslatedKeyMessage()))
				.withStyle(ChatFormatting.GRAY);
			mc.gui.hud.getChat().addClientSystemMessage(text);
		});
	}
}
