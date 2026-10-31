import { describe, expect, it } from "vitest";

import { emojiGridWindow } from "@/components/projects/EmojiGrid";

const STRIDE = 34;

describe("emoji grid window", () => {
  it("renders only the rows around the viewport", () => {
    const window = emojiGridWindow(0, 240, 200);
    expect(window.start).toBe(0);
    expect(window.end).toBeLessThan(12);
  });

  it("follows the scroll position without exceeding the grid", () => {
    const window = emojiGridWindow(STRIDE * 50, 240, 60);
    expect(window.start).toBe(48);
    expect(window.end).toBe(60);
  });

  it("keeps at least one row when the grid is shorter than the viewport", () => {
    expect(emojiGridWindow(0, 240, 1)).toEqual({ start: 0, end: 1 });
    expect(emojiGridWindow(0, 240, 0)).toEqual({ start: 0, end: 1 });
  });
});
