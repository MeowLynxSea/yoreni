import { describe, expect, it } from "vitest";

import { emojiLocale, loadEmojis, searchEmojis } from "@/components/projects/emoji-catalog";

describe("project emoji catalogue", () => {
  it("loads the whole Unicode set rather than a hand-picked list", async () => {
    const options = await loadEmojis("en");
    expect(options.length).toBeGreaterThan(1500);
    expect(new Set(options.map((option) => option.hexcode)).size).toBe(options.length);
  });

  it("searches labels and keywords in the interface language", async () => {
    const english = await loadEmojis("en");
    expect(searchEmojis(english, "rocket").map((option) => option.emoji)).toContain("🚀");

    const chinese = await loadEmojis("zh-CN");
    expect(searchEmojis(chinese, "火箭").map((option) => option.emoji)).toContain("🚀");
    expect(searchEmojis(chinese, "发射").length).toBeGreaterThan(0);
  });

  it("returns everything for an empty query and nothing for a miss", async () => {
    const options = await loadEmojis("en");
    expect(searchEmojis(options, "")).toHaveLength(options.length);
    expect(searchEmojis(options, "zzzz no such emoji")).toEqual([]);
  });

  it("maps an interface locale to the closest data set", () => {
    expect(emojiLocale("zh-TW")).toBe("zh-hant");
    expect(emojiLocale("zh-CN")).toBe("zh");
    expect(emojiLocale("id")).toBe("en");
  });
});
