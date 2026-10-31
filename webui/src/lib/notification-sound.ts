// The bundled message chime is opt-in and plays in both foreground and
// background tabs, independently of browser notification permissions.

import { readLocalPreferences } from "@/lib/local-preferences";

export const MESSAGE_SOUND_PATH = "/notification.wav";

let audio: HTMLAudioElement | null = null;

export function playMessageSound(): void {
  try {
    if (!readLocalPreferences().notificationSound) return;
    if (audio === null) audio = new Audio(MESSAGE_SOUND_PATH);
    audio.currentTime = 0;
    void audio.play().catch(() => {
      // Autoplay policies or a missing asset must never surface as an error.
    });
  } catch {
    // A notification chime must never break the stream.
  }
}
