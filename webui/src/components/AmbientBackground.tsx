import type { CSSProperties } from "react";

import { useThemeValue } from "@/hooks/useTheme";

type SkyPalette = {
  base: string;
  top: string;
  mid: string;
  bottom: string;
  glowA: string;
  glowB: string;
  glowC: string;
};

const PALETTES: Record<"light" | "dark", SkyPalette> = {
  light: {
    base: "#eef2f6",
    top: "#dbe8f8",
    mid: "#f3f5f6",
    bottom: "#f7efe3",
    glowA: "#cfe3fb",
    glowB: "#f7e6cd",
    glowC: "#d8e7f5",
  },
  dark: {
    base: "#0f141d",
    top: "#132131",
    mid: "#1a2733",
    bottom: "#242a2e",
    glowA: "#1c3856",
    glowB: "#46372a",
    glowC: "#1f3342",
  },
};

/** The sky follows the theme only; its glow drifts slowly to keep the surface alive. */
export function AmbientBackground() {
  const theme = useThemeValue();

  const palette = PALETTES[theme];
  const style = {
    "--sky-base": palette.base,
    "--sky-top": palette.top,
    "--sky-mid": palette.mid,
    "--sky-bottom": palette.bottom,
    "--sky-glow-a": palette.glowA,
    "--sky-glow-b": palette.glowB,
    "--sky-glow-c": palette.glowC,
  } as CSSProperties;

  return (
    <div className="app-ambient" aria-hidden="true" style={style}>
      <div className="app-ambient__wash" />
      <div className="app-ambient__drift app-ambient__drift--one" />
      <div className="app-ambient__drift app-ambient__drift--two" />
      <div className="app-ambient__grain" />
    </div>
  );
}
