/** Keys a touch keyboard cannot produce, addressed by X11 keysym and DOM code. */
export type RemoteKey = {
  id: string;
  label: string;
  keysym: number;
  code: string;
};

export const REMOTE_KEYS = {
  ctrl: { id: "ctrl", label: "Ctrl", keysym: 0xffe3, code: "ControlLeft" },
  alt: { id: "alt", label: "Alt", keysym: 0xffe9, code: "AltLeft" },
  shift: { id: "shift", label: "Shift", keysym: 0xffe1, code: "ShiftLeft" },
  meta: { id: "meta", label: "Win", keysym: 0xffeb, code: "MetaLeft" },
  tab: { id: "tab", label: "Tab", keysym: 0xff09, code: "Tab" },
  escape: { id: "escape", label: "Esc", keysym: 0xff1b, code: "Escape" },
  enter: { id: "enter", label: "Enter", keysym: 0xff0d, code: "Enter" },
  backspace: { id: "backspace", label: "⌫", keysym: 0xff08, code: "Backspace" },
  delete: { id: "delete", label: "Del", keysym: 0xffff, code: "Delete" },
  left: { id: "left", label: "←", keysym: 0xff51, code: "ArrowLeft" },
  up: { id: "up", label: "↑", keysym: 0xff52, code: "ArrowUp" },
  down: { id: "down", label: "↓", keysym: 0xff54, code: "ArrowDown" },
  right: { id: "right", label: "→", keysym: 0xff53, code: "ArrowRight" },
} as const satisfies Record<string, RemoteKey>;

/** Modifiers stay pressed until the next key uses them. */
export const MODIFIER_IDS = ["ctrl", "alt", "shift", "meta"] as const;

/** Keys the strip sends as one press; the software keyboard supplies the rest. */
export const TAP_KEY_IDS = [
  "tab",
  "escape",
  "enter",
  "backspace",
  "left",
  "up",
  "down",
  "right",
] as const;

/** Physical keydown events the hidden sink forwards instead of typing. */
export const SINK_KEYS: Record<string, RemoteKey> = {
  Backspace: REMOTE_KEYS.backspace,
  Delete: REMOTE_KEYS.delete,
  Tab: REMOTE_KEYS.tab,
  Enter: REMOTE_KEYS.enter,
  Escape: REMOTE_KEYS.escape,
  ArrowLeft: REMOTE_KEYS.left,
  ArrowUp: REMOTE_KEYS.up,
  ArrowDown: REMOTE_KEYS.down,
  ArrowRight: REMOTE_KEYS.right,
};

/** X11 maps Latin-1 characters to their own code point and everything else to
 * the Unicode keysym range. */
export function charKeysym(codePoint: number): number {
  return codePoint >= 0x20 && codePoint <= 0xff
    ? codePoint
    : 0x01000000 | codePoint;
}
