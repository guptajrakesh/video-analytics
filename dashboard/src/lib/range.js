export const RANGE_PRESETS = [
  { id: "1h", label: "Last hour", ms: 60 * 60 * 1000 },
  { id: "24h", label: "Last 24 hours", ms: 24 * 60 * 60 * 1000 },
  { id: "7d", label: "Last 7 days", ms: 7 * 24 * 60 * 60 * 1000 },
  { id: "all", label: "All time", ms: null },
];

export const BUCKETS = [
  { minutes: 5, label: "5 min" },
  { minutes: 15, label: "15 min" },
  { minutes: 60, label: "1 hour" },
  { minutes: 1440, label: "1 day" },
];

// Resolved when a request is made, so an auto-refreshing "Last hour" keeps sliding.
export function rangeParams(presetId) {
  const preset = RANGE_PRESETS.find((p) => p.id === presetId);
  if (!preset || preset.ms === null) return {};
  return { start: new Date(Date.now() - preset.ms).toISOString() };
}

export function slugify(text) {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
}

export function newId(prefix, label = "") {
  const rand = Math.random().toString(36).slice(2, 7);
  const slug = slugify(label);
  return slug ? `${prefix}-${slug}-${rand}` : `${prefix}-${rand}`;
}

export function formatBucket(isoString, bucketMinutes) {
  const d = new Date(isoString);
  if (bucketMinutes >= 1440) return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
