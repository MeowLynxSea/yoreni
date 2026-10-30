import { expect, test } from "@playwright/test";

for (const width of [412, 1440]) {
  test.describe(`${width}px document startup skeleton`, () => {
    test.use({ viewport: { width, height: 922 }, reducedMotion: "reduce" });
    for (const surface of ["settings", "chat"]) {
      test(`${surface} paints before the application and stylesheet download`, async ({ page }) => {
        await page.addInitScript(() => localStorage.setItem("yoreni.locale", "en"));
        // Neither React nor the app stylesheet may supply this first paint.
        await page.route(/\/(?:src\/main\.tsx|assets\/index-[^/]+\.(?:js|css))$/, (route) => route.abort());
        await page.goto(`/#/${surface}`, { waitUntil: "domcontentloaded" });
        const shell = page.locator(".startup-shell");
        await expect(shell).toHaveAttribute("data-startup-surface", surface);
        await expect(shell).toHaveAttribute("aria-busy", "true");
        await expect(shell.locator(".startup-header")).toBeVisible();
        await expect(shell.locator(surface === "chat" ? ".startup-composer" : ".startup-settings-index")).toBeVisible();
        const blocks = shell.locator(".startup-block:visible");
        expect(await blocks.count()).toBeGreaterThan(5);
        const box = await blocks.first().boundingBox();
        expect(box!.width).toBeGreaterThan(0);
        expect(box!.height).toBeGreaterThan(0);
        expect(await blocks.first().evaluate((element) => getComputedStyle(element).backgroundColor)).not.toBe("rgba(0, 0, 0, 0)");
        expect(await shell.getByRole("button").count()).toBe(0);
        expect(await shell.getByRole("textbox").count()).toBe(0);
        expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
        const status = shell.getByRole("status");
        await expect(status).toHaveText("Loading yoreni…");
        expect(await status.evaluate((element) => element.getBoundingClientRect().width)).toBe(1);
      });
    }

    test("restores the stored theme and locale before React", async ({ page }) => {
      await page.addInitScript(() => {
        localStorage.setItem("yoreni-webui.theme", "dark");
        localStorage.setItem("yoreni.locale", "zh-CN");
      });
      await page.route("**/src/main.tsx", (route) => route.abort());
      await page.goto("/#/settings");
      const shell = page.locator(".startup-shell");
      await expect(shell).toHaveAttribute("data-startup-surface", "settings");
      await expect(shell.getByRole("status")).toHaveText("正在加载 yoreni…");
      await expect(page.locator("html")).toHaveClass(/dark/);
      await expect(shell).toHaveAttribute("data-startup-settings", "index");
    });

    test("settings sub-page paints a narrow section skeleton", async ({ page }) => {
      await page.addInitScript(() => localStorage.setItem("yoreni.locale", "en"));
      await page.route(/\/(?:src\/main\.tsx|assets\/index-[^/]+\.(?:js|css))$/, (route) => route.abort());
      await page.goto("/#/settings/models", { waitUntil: "domcontentloaded" });
      const shell = page.locator(".startup-shell");
      await expect(shell).toHaveAttribute("data-startup-surface", "settings");
      await expect(shell).toHaveAttribute("data-startup-settings", "section");
      const section = shell.locator(".startup-settings-section");
      await expect(section).toBeVisible();
      const box = await section.boundingBox();
      expect(box!.width).toBeLessThanOrEqual(704);
      expect(await shell.locator(".startup-settings-index").isVisible()).toBe(false);
    });
  });
}
