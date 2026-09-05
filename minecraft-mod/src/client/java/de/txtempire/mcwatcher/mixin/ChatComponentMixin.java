package de.txtempire.mcwatcher.mixin;

import de.txtempire.mcwatcher.McWatcherClient;
import net.minecraft.client.GuiMessageTag;
import net.minecraft.client.gui.components.ChatComponent;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.MessageSignature;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * Fängt jede Zeile ab, die unten links im Chat-HUD erscheint
 * (inkl. /msg und Server-Systemnachrichten).
 */
@Mixin(ChatComponent.class)
public abstract class ChatComponentMixin {

	@Inject(method = "addMessage(Lnet/minecraft/network/chat/Component;)V", at = @At("HEAD"))
	private void txtempire$onAddMessage(Component message, CallbackInfo ci) {
		if (message != null) {
			McWatcherClient.onChatLine(message.getString(), null);
		}
	}

	@Inject(
		method = "addMessage(Lnet/minecraft/network/chat/Component;Lnet/minecraft/network/chat/MessageSignature;Lnet/minecraft/client/GuiMessageTag;)V",
		at = @At("HEAD"),
		require = 0
	)
	private void txtempire$onAddMessageSigned(
		Component message,
		MessageSignature signature,
		GuiMessageTag tag,
		CallbackInfo ci
	) {
		if (message != null) {
			McWatcherClient.onChatLine(message.getString(), null);
		}
	}
}
