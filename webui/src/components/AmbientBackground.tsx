import { useEffect, useState, type CSSProperties } from "react";

import { useThemeValue } from "@/hooks/useTheme";

type Sky = "dawn" | "day" | "dusk" | "night";
type SkyPalette = {
  base: string;
  top: string;
  mid: string;
  bottom: string;
  glowA: string;
  glowB: string;
  glowC: string;
};

const PALETTES: Record<"light" | "dark", Record<Sky, SkyPalette>> = {
  light: {
    dawn: {
      base: "#f3ede6",
      top: "#dfe8f5",
      mid: "#f6f0e9",
      bottom: "#f1dfd2",
      glowA: "#cfe0f7",
      glowB: "#f6d9c6",
      glowC: "#e7d8ef",
    },
    day: {
      base: "#eef2f6",
      top: "#dbe8f8",
      mid: "#f3f5f6",
      bottom: "#f7efe3",
      glowA: "#cfe3fb",
      glowB: "#f7e6cd",
      glowC: "#d8e7f5",
    },
    dusk: {
      base: "#f0e9e8",
      top: "#e7ddf2",
      mid: "#f5ece6",
      bottom: "#efd4c8",
      glowA: "#d9c9ee",
      glowB: "#f3c4a8",
      glowC: "#e9d5d0",
    },
    night: {
      base: "#e9ebf2",
      top: "#dce3f2",
      mid: "#eef0f4",
      bottom: "#e7e4e7",
      glowA: "#ccd7ee",
      glowB: "#dfd8ea",
      glowC: "#d8e0ea",
    },
  },
  dark: {
    dawn: {
      base: "#11151f",
      top: "#172033",
      mid: "#241f2c",
      bottom: "#2f2427",
      glowA: "#27466e",
      glowB: "#6a4433",
      glowC: "#3b2f4a",
    },
    day: {
      base: "#0f141d",
      top: "#132131",
      mid: "#1a2733",
      bottom: "#242a2e",
      glowA: "#1c3856",
      glowB: "#46372a",
      glowC: "#1f3342",
    },
    dusk: {
      base: "#17121f",
      top: "#241a35",
      mid: "#30202d",
      bottom: "#382426",
      glowA: "#4a2f66",
      glowB: "#7a4434",
      glowC: "#32234a",
    },
    night: {
      base: "#090c14",
      top: "#0d1322",
      mid: "#121827",
      bottom: "#171927",
      glowA: "#1b2b50",
      glowB: "#2a2340",
      glowC: "#12243a",
    },
  },
};

function skyAt(hour: number): Sky {
  if (hour >= 5 && hour < 9) return "dawn";
  if (hour >= 9 && hour < 17) return "day";
  if (hour >= 17 && hour < 20) return "dusk";
  return "night";
}

/** The sky changes with the viewer's local time, like daylight through a window. */
export function AmbientBackground() {
  const theme = useThemeValue();
  const [hour, setHour] = useState(() => new Date().getHours());

  useEffect(() => {
    const timer = window.setInterval(() => setHour(new Date().getHours()), 60_000);
    return () => window.clearInterval(timer);
  }, []);

  const palette = PALETTES[theme][skyAt(hour)];
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
