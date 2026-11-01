import { useTranslation } from "react-i18next";
import { Keyboard } from "lucide-react";

import { SegmentedControl } from "@/components/ui/segmented-control";
import { cn } from "@/lib/utils";
import {
  MODIFIER_IDS,
  REMOTE_KEYS,
  TAP_KEY_IDS,
  type RemoteKey,
} from "@/lib/vnc-keys";

export type PointerMode = "touch" | "trackpad";

type Props = {
  mode: PointerMode;
  onModeChange: (mode: PointerMode) => void;
  keyboardOpen: boolean;
  onKeyboardToggle: () => void;
  activeModifiers: readonly string[];
  onToggleModifier: (key: RemoteKey) => void;
  onTapKey: (key: RemoteKey) => void;
  onCtrlAltDel: () => void;
};

const KEY_CLASS = "flex h-9 min-w-9 shrink-0 items-center justify-center rounded-full px-2.5 text-ui-label font-medium transition-colors duration-150 ease-out motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

/** Keep the hidden keyboard sink focused; a button that takes focus closes
 * the software keyboard mid-combination. */
const keepKeyboardFocus = (event: { preventDefault: () => void }) => event.preventDefault();

/** Touch-only controls for a desktop someone is driving by hand. */
export function ComputerInputBar({
  mode,
  onModeChange,
  keyboardOpen,
  onKeyboardToggle,
  activeModifiers,
  onToggleModifier,
  onTapKey,
  onCtrlAltDel,
}: Props) {
  const { t } = useTranslation();

  return (
    <div className="flex w-full flex-col items-center gap-1.5">
      {keyboardOpen ? (
        <div
          className="flex h-11 w-full max-w-full items-center gap-0.5 overflow-x-auto overscroll-x-contain rounded-full app-surface-glass px-1 [scrollbar-width:none] [touch-action:pan-x] [&::-webkit-scrollbar]:hidden"
          role="toolbar"
          aria-label={t("computer.keyboard")}
        >
          {MODIFIER_IDS.map((id) => {
            const key = REMOTE_KEYS[id];
            const active = activeModifiers.includes(id);
            return (
              <button
                key={id}
                type="button"
                aria-pressed={active}
                aria-label={key.label}
                onMouseDown={keepKeyboardFocus}
                onClick={() => onToggleModifier(key)}
                className={cn(
                  KEY_CLASS,
                  active
                    ? "bg-foreground text-background"
                    : "text-foreground/80 hover:bg-foreground/10",
                )}
              >
                {key.label}
              </button>
            );
          })}
          <span className="mx-0.5 h-5 w-px shrink-0 bg-border/60" aria-hidden />
          {TAP_KEY_IDS.map((id) => {
            const key = REMOTE_KEYS[id];
            return (
              <button
                key={id}
                type="button"
                aria-label={key.id === "backspace" ? "Backspace" : key.label}
                onMouseDown={keepKeyboardFocus}
                onClick={() => onTapKey(key)}
                className={cn(KEY_CLASS, "text-foreground/80 hover:bg-foreground/10")}
              >
                {key.label}
              </button>
            );
          })}
          <button
            type="button"
            aria-label="Ctrl+Alt+Del"
            onMouseDown={keepKeyboardFocus}
            onClick={onCtrlAltDel}
            className={cn(KEY_CLASS, "text-foreground/80 hover:bg-foreground/10")}
          >
            Ctrl+Alt+Del
          </button>
        </div>
      ) : null}

      <div className="flex items-center gap-1 rounded-full app-surface-glass p-1">
        <button
          type="button"
          aria-pressed={keyboardOpen}
          aria-label={t("computer.keyboard")}
          title={t("computer.keyboard")}
          onMouseDown={keepKeyboardFocus}
          onClick={onKeyboardToggle}
          className={cn(
            "flex h-8 w-8 items-center justify-center rounded-full transition-colors duration-150 ease-out motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            keyboardOpen
              ? "bg-foreground text-background"
              : "text-foreground/80 hover:bg-foreground/10",
          )}
        >
          <Keyboard className="h-4 w-4" />
        </button>
        <div onMouseDown={keepKeyboardFocus}>
          <SegmentedControl
            value={mode}
            onChange={onModeChange}
            ariaLabel={t("computer.pointerMode")}
            options={[
              { value: "touch", label: t("computer.touch") },
              { value: "trackpad", label: t("computer.trackpad") },
            ]}
          />
        </div>
      </div>
    </div>
  );
}
