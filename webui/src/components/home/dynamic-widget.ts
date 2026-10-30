/** Widget HTML is untrusted content: scripts only, no same-origin, no network. */
export const WIDGET_FRAME_SANDBOX = "allow-scripts";

export const MIN_WIDGET_HEIGHT = 40;
export const MAX_WIDGET_HEIGHT = 600;
export const DEFAULT_WIDGET_HEIGHT = 168;

export const WIDGET_DATA_MESSAGE = "yoreni:widget:data";
export const WIDGET_HEIGHT_MESSAGE = "yoreni:widget:height";
export const WIDGET_REFRESH_MESSAGE = "yoreni:widget:refresh";
export const WIDGET_DATA_EVENT = "yoreni:data";

const WIDGET_CSP = [
  "default-src 'none'",
  "img-src data: https:",
  "style-src 'unsafe-inline'",
  "script-src 'unsafe-inline'",
  "connect-src 'none'",
].join("; ");

export interface WidgetTheme {
  scheme: "light" | "dark";
  foreground: string;
  mutedForeground: string;
  border: string;
  primary: string;
  fontFamily: string;
  fontSize: string;
}

const FALLBACK_COLORS = {
  light: {
    foreground: "hsl(240 6% 12%)",
    mutedForeground: "hsl(240 4% 44%)",
    border: "hsl(240 6% 89%)",
    primary: "hsl(240 6% 12%)",
  },
  dark: {
    foreground: "hsl(240 7% 96%)",
    mutedForeground: "hsl(240 4% 64%)",
    border: "hsl(240 4% 24%)",
    primary: "hsl(240 7% 96%)",
  },
} as const;

/** The app stores palette tokens as bare `H S% L%` triples; anything else is already a color. */
function cssColor(value: string, fallback: string): string {
  const token = value.trim();
  if (!token) return fallback;
  return /^\d+(?:\.\d+)? \d+(?:\.\d+)?% \d+(?:\.\d+)?%$/.test(token) ? `hsl(${token})` : token;
}

export function readWidgetTheme(element: HTMLElement | null, scheme: "light" | "dark"): WidgetTheme {
  const fallback = FALLBACK_COLORS[scheme];
  const styles = element ? window.getComputedStyle(element) : null;
  const token = (name: string) => styles?.getPropertyValue(name) ?? "";
  return {
    scheme,
    foreground: cssColor(token("--foreground"), fallback.foreground),
    mutedForeground: cssColor(token("--muted-foreground"), fallback.mutedForeground),
    border: cssColor(token("--border"), fallback.border),
    primary: cssColor(token("--primary"), fallback.primary),
    fontFamily: token("font-family").trim() || "-apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif",
    fontSize: token("font-size").trim() || "13px",
  };
}

interface WidgetState {
  data: unknown;
  updatedAt: number | null;
  theme: WidgetTheme;
}

/** JSON for an inline script: `<` escapes keep `</script>` and `<!--` from ending the block. */
function scriptJson(value: unknown): string {
  return JSON.stringify(value)
    .replace(/</g, "\\u003c")
    .replace(/\u2028/g, "\\u2028")
    .replace(/\u2029/g, "\\u2029");
}

function bootstrap(state: WidgetState): string {
  return `(function () {
  var state = ${scriptJson(state)};
  function post(type, payload) {
    try { window.parent.postMessage(Object.assign({ type: type }, payload), "*"); } catch (error) {}
  }
  function publish() {
    window.yoreniWidget = {
      data: state.data,
      updatedAt: state.updatedAt,
      theme: state.theme,
      refresh: function () { post(${JSON.stringify(WIDGET_REFRESH_MESSAGE)}, {}); }
    };
  }
  function measure() {
    var root = document.documentElement;
    var body = document.body;
    // A collapsed panel has no width; its reflowed height says nothing about the widget.
    if (!body || root.clientWidth < 1) return;
    post(${JSON.stringify(WIDGET_HEIGHT_MESSAGE)}, { height: body.scrollHeight });
  }
  window.addEventListener("resize", measure);
  window.addEventListener("message", function (event) {
    var message = event.data;
    if (!message || typeof message !== "object" || message.type !== ${JSON.stringify(WIDGET_DATA_MESSAGE)}) return;
    state.data = message.data;
    state.updatedAt = message.updatedAt;
    publish();
    try { window.dispatchEvent(new CustomEvent(${JSON.stringify(WIDGET_DATA_EVENT)}, { detail: state.data })); } catch (error) {}
    measure();
  });
  document.addEventListener("DOMContentLoaded", function () {
    var body = document.body;
    if (!body) return;
    body.style.fontFamily = state.theme.fontFamily;
    body.style.fontSize = state.theme.fontSize;
    publish();
    measure();
    if (typeof ResizeObserver === "function") new ResizeObserver(measure).observe(body);
  });
  publish();
})();`;
}

export interface WidgetDocumentOptions {
  html: string;
  data: unknown;
  updatedAt: number | null;
  theme: WidgetTheme;
}

/** Compose the iframe document: policy, theme variables, the `window.yoreniWidget` bootstrap, then the fragment. */
export function widgetDocument({ html, data, updatedAt, theme }: WidgetDocumentOptions): string {
  const variables = [
    `--widget-foreground: ${theme.foreground}`,
    `--widget-muted-foreground: ${theme.mutedForeground}`,
    `--widget-border: ${theme.border}`,
    `--widget-primary: ${theme.primary}`,
  ].join("; ");
  return `<!doctype html>
<html lang="en" data-theme="${theme.scheme}">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="${WIDGET_CSP}">
<style>
:root { color-scheme: ${theme.scheme}; ${variables} }
html, body { margin: 0; padding: 0; background: transparent; }
body { color: var(--widget-foreground); line-height: 1.5; overflow-wrap: anywhere; }
</style>
<script>${bootstrap({ data, updatedAt, theme })}</script>
</head>
<body>${html}</body>
</html>`;
}

export function clampWidgetHeight(height: unknown): number {
  if (typeof height !== "number" || !Number.isFinite(height)) return DEFAULT_WIDGET_HEIGHT;
  return Math.min(MAX_WIDGET_HEIGHT, Math.max(MIN_WIDGET_HEIGHT, Math.round(height)));
}
