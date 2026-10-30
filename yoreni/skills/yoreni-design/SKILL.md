---
name: yoreni-design
description: Yoreni's visual and interface conventions for widget HTML, generated pages, and user-facing copy.
metadata: {"yoreni":{"emoji":"🎨"}}
---

# Yoreni design

Use this when you write anything the user sees inside yoreni: Home widget HTML, a generated page, or interface copy. The WebUI's template widgets already follow these conventions; when a template can express the content, prefer it over hand-written HTML and the result stays consistent for free.

## Feel

Warm daylight, ink, ceramic, terracotta, sea glass. Surfaces are translucent and calm, typography mixes a quiet serif for headings with a plain sans for data, and color appears as a small accent rather than a background. Light and dark themes are equal citizens; neither is an afterthought.

Avoid loud gradients, neon, heavy borders, drop shadows on text, dashboard-style chrome, and dense grids of boxes. If a screen looks like an admin panel, simplify it.

## Color

Inside a Home widget, use the variables the frame provides. They follow the user's theme automatically, so never hardcode a palette:

| Variable | Use for |
| --- | --- |
| `--widget-foreground` | Primary text and values |
| `--widget-muted-foreground` | Captions, metadata, secondary rows |
| `--widget-border` | Dividers and hairlines |
| `--widget-primary` | The single strongest accent on the card |

The iframe background is transparent by design. Do not paint an opaque background on `html` or `body` unless the widget needs its own contrast surface; if you do, set a matching color per theme with `[data-theme="dark"]`.

The wider product palette, when you need an accent outside the frame:

- Ink `#152232` for strong text on light surfaces.
- Terracotta `#bf5a42` for emphasis, links, and active states.
- Sea glass `#5ba4ad` for secondary accents and calm highlights.

Use at most one accent color per view. Status colors stay semantic: emerald for healthy, amber for attention, red for failure; pair them with a word or icon, never color alone.

## Typography

- Headings use a serif display face at weight 400 with slight negative tracking. In the app: `display-serif` (`Iowan Old Style`, `Palatino`, `Georgia`, CJK serifs). Inside an HTML widget, use `Georgia, "Songti SC", serif` and `letter-spacing: -0.03em`.
- Body text is the system sans stack. Widget body text is 13px with 1.5 line height; captions and metadata are 12px.
- A hero value is roughly 1.9rem in the serif face, with the label below it at 13px.
- Use tabular numerals for times, counts, and metrics that change in place.
- Sentence case everywhere. No all-caps labels, no letter-spaced small caps.

## Layout and shape

- Work on a 4px rhythm. The app's widget card uses 24px horizontal padding and 20px vertical padding; content begins 16px below the title.
- Keep one idea per card. Three to five rows is comfortable; if it needs a scrollbar, it is probably not glanceable.
- Radii: cards 28px, controls 16px, small chips 12px, pills fully rounded. Nested corners step down, never up.
- Separate rows with a 1px hairline at about half the border color instead of boxing every item.
- Align numbers to the right, labels to the left. Truncate long metadata rather than letting it wrap into the value.
- The panel is narrow (about 336px). Design for that width first, then let it breathe on wider screens.

## Motion

Use 200–300ms ease-out with `cubic-bezier(0.22, 1, 0.36, 1)`. Animate opacity and small transforms, not layout. Respect `prefers-reduced-motion: reduce` by removing the animation, and never bounce or overshoot.

## Accessibility

- Use semantic elements: headings, lists, tables, buttons. Do not rebuild them from divs.
- Keep text contrast at 4.5:1 or better in both themes. Muted text is for secondary information, not for the only copy of a value.
- Interactive targets are at least 32px tall and have a visible focus state.
- Do not encode status in color alone; add a label, icon, or shape.
- The widget frame is scripts-only and opaque-origin: no network, storage, cookies, or top navigation. Inline everything and keep the document self-contained.

## Copy

Write like a calm colleague. State the thing, then the number, then the next step.

- Sentence case, no exclamation marks, no filler ("just", "simply", "please note").
- Buttons name the action: "Refresh", "Delete widget", not "OK".
- Labels name the value: "Open tasks", "Next run", "Updated 4 minutes ago".
- Always include units and time zones. Prefer relative times for recency and absolute times for schedules.
- Errors say what failed and what the user can do next. Never expose stack traces, file paths, or internal ids.
- Keep product names and technical terms as they are: yoreni, API, MCP, OAuth, model names.

## HTML widget checklist

Before you finish widget HTML, confirm:

- Colors come from `--widget-*` or the palette above; no hardcoded light-only colors.
- The background is transparent, or explicitly themed for both modes.
- The layout holds at 300px wide and with long values; nothing overlaps.
- Empty, loading, and error states all render something sensible.
- Data re-renders from the `yoreni:data` event and `window.yoreniWidget.data`.
- No fixed viewport height: the frame measures its content and the panel clamps it.
- No network requests, external fonts, or assets that the content policy blocks.

For the data shapes, script contract, and sandbox rules, read the `home-widgets` skill. For labels and descriptions on the card itself, keep the title under 60 characters and the description under 120.
