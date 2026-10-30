import { describe, expect, it } from "vitest";

import { formatMessageGroupTime, startsMessageTimeGroup } from "@/lib/format-time";
import { messageTimeGroups } from "@/lib/message-groups";
import type { UIMessage } from "@/lib/types";

const at = (year: number, month: number, day: number, hour: number, minute = 0) =>
  new Date(year, month, day, hour, minute).getTime();

function message(id: string, createdAt: number): UIMessage {
  return { id, role: "user", content: id, createdAt };
}

describe("message time groups", () => {
  it("breaks a transcript on a long pause or a new day", () => {
    const start = at(2026, 8, 24, 9, 0);
    const groups = messageTimeGroups([
      message("a", start),
      message("b", start + 60_000),
      message("c", start + 11 * 60_000),
      message("d", at(2026, 8, 25, 8, 0)),
    ]);

    expect(groups.map((group) => group.messages.map((entry) => entry.id))).toEqual([
      ["a", "b"],
      ["c"],
      ["d"],
    ]);
    expect(groups.map((group) => group.startedAt)).toEqual([
      start,
      start + 11 * 60_000,
      at(2026, 8, 25, 8, 0),
    ]);
  });

  it("only starts a group for the first message, a pause, or a day change", () => {
    const now = at(2026, 8, 24, 16, 0);

    expect(startsMessageTimeGroup(null, now)).toBe(true);
    expect(startsMessageTimeGroup(now, now + 9 * 60_000)).toBe(false);
    expect(startsMessageTimeGroup(now, now + 10 * 60_000)).toBe(true);
    expect(startsMessageTimeGroup(at(2026, 8, 23, 23, 55), at(2026, 8, 24, 0, 5))).toBe(true);
  });

  it("labels each group by how far back it started", () => {
    const now = new Date(2026, 8, 24, 16, 0);

    expect(formatMessageGroupTime(at(2026, 8, 24, 9, 5), "zh-CN", now)).toBe("09:05");
    expect(formatMessageGroupTime(at(2026, 8, 23, 22, 10), "zh-CN", now)).toBe("昨天 22:10");
    expect(formatMessageGroupTime(at(2026, 8, 20, 14, 0), "zh-CN", now)).toBe("周日 14:00");
    expect(formatMessageGroupTime(at(2026, 2, 3, 14, 0), "zh-CN", now)).toBe("3/3 14:00");
    expect(formatMessageGroupTime(at(2025, 8, 20, 14, 0), "zh-CN", now)).toBe("2025/9/20 14:00");
  });
});
