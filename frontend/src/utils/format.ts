// Small formatting helpers shared by History (per-form latency) and Metrics
// (aggregate averages) — Phase 6.

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const totalSeconds = ms / 1000;
  if (totalSeconds < 60) return `${totalSeconds.toFixed(1)} s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = Math.round(totalSeconds % 60);
  return `${minutes}m ${seconds}s`;
}

export function formatRatio(value: number | null | undefined): string {
  if (value === null || value === undefined) return "n/a";
  return `${Math.round(value * 100)}%`;
}

export function formatSeconds(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  return formatDuration(seconds * 1000);
}
