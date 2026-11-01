import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ComputerInputBar, type PointerMode } from "@/components/ComputerInputBar";
import { REMOTE_KEYS, charKeysym } from "@/lib/vnc-keys";

function barProps(overrides: Partial<Parameters<typeof ComputerInputBar>[0]> = {}) {
  return {
    mode: "touch" as PointerMode,
    onModeChange: vi.fn(),
    keyboardOpen: false,
    onKeyboardToggle: vi.fn(),
    activeModifiers: [] as string[],
    onToggleModifier: vi.fn(),
    onTapKey: vi.fn(),
    onCtrlAltDel: vi.fn(),
    ...overrides,
  };
}

describe("ComputerInputBar", () => {
  it("offers the key strip only while the software keyboard is up", () => {
    const { unmount } = render(<ComputerInputBar {...barProps()} />);
    expect(screen.queryByRole("toolbar")).toBeNull();
    unmount();

    render(<ComputerInputBar {...barProps({ keyboardOpen: true })} />);
    const strip = screen.getByRole("toolbar", { name: "Keyboard" });
    expect(within(strip).getByRole("button", { name: "Ctrl" })).toBeInTheDocument();
    expect(within(strip).getByRole("button", { name: "Backspace" })).toBeInTheDocument();
  });

  it("reports a pressed modifier and forwards the tapped key", () => {
    const props = barProps({ keyboardOpen: true, activeModifiers: ["ctrl"] });
    render(<ComputerInputBar {...props} />);
    const strip = screen.getByRole("toolbar");

    const ctrl = within(strip).getByRole("button", { name: "Ctrl" });
    expect(ctrl).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(ctrl);
    expect(props.onToggleModifier).toHaveBeenCalledWith(REMOTE_KEYS.ctrl);

    fireEvent.click(within(strip).getByRole("button", { name: "Esc" }));
    expect(props.onTapKey).toHaveBeenCalledWith(REMOTE_KEYS.escape);
  });

  it("switches between touch and trackpad pointing", () => {
    const props = barProps();
    render(<ComputerInputBar {...props} />);
    expect(screen.getByRole("button", { name: "Touch" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Trackpad" }));
    expect(props.onModeChange).toHaveBeenCalledWith("trackpad");
  });
});

describe("charKeysym", () => {
  it("maps Latin-1 directly and everything else to the Unicode keysym range", () => {
    expect(charKeysym("a".codePointAt(0)!)).toBe(0x61);
    expect(charKeysym("中".codePointAt(0)!)).toBe(0x01004e2d);
  });
});
