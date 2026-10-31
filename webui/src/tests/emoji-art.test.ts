import { describe, expect, it } from "vitest";

import { hexcodeOf, splitEmojiRuns } from "@/lib/emoji-art";

describe("emoji artwork mapping", () => {
  it("names artwork files by codepoint", () => {
    expect(hexcodeOf("🚀")).toBe("1f680");
    expect(hexcodeOf("❤️")).toBe("2764-fe0f");
  });

  it("splits text, pictographs, flags and keycaps", () => {
    const runs = splitEmojiRuns("ready 🚀 and 🇯🇵 then 1️⃣");
    expect(runs.map((run) => run.kind)).toEqual([
      "text", "emoji", "text", "emoji", "text", "emoji",
    ]);
    expect(runs.filter((run) => run.kind === "emoji").map((run) => run.kind === "emoji" && run.emoji))
      .toEqual(["🚀", "🇯🇵", "1️⃣"]);
  });

  it("keeps joined sequences together", () => {
    const runs = splitEmojiRuns("team 👨‍👩‍👧‍👦 done");
    expect(runs.filter((run) => run.kind === "emoji")).toHaveLength(1);
    expect(splitEmojiRuns("plain text")).toEqual([{ kind: "text", text: "plain text" }]);
  });
});
