import type { Quadrant, StopStatus } from "./types";

// Party-neutral palette: no saffron, green, blue or red, so no quadrant reads as a party colour.
export const Q: Record<Quadrant, { name: string; action: string; color: string; soft: string }> = {
  PERSUADE: { name: "Persuadable, likely to vote", action: "Persuade first", color: "#5B2E91", soft: "#EEE7F7" },
  GOTV: { name: "Supporter, unlikely to vote", action: "Get them to the booth", color: "#0F766E", soft: "#E1F2EF" },
  LOW: { name: "Neutral, low turnout", action: "Low priority", color: "#A8A3B8", soft: "#F1F0F4" },
  OPPOSITION: { name: "Opposition core", action: "Skip", color: "#5E5A6B", soft: "#E9E8EC" },
};
export const Q_ORDER: Quadrant[] = ["PERSUADE", "GOTV", "LOW", "OPPOSITION"];

export const STATUS: Record<StopStatus, { label: string; tone: string }> = {
  PENDING: { label: "Not visited", tone: "text-ink-3" },
  VISITED: { label: "Visited", tone: "text-gotv" },
  NOT_HOME: { label: "Not home", tone: "text-amber" },
  REFUSED: { label: "Refused", tone: "text-ink-3" },
  REVISIT: { label: "Revisit", tone: "text-ink" },
};

export const fmt = (n: number | null | undefined) => (n ?? 0).toLocaleString("en-IN");
