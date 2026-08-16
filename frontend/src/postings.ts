// Shared posting helpers. Direct ports of the inline JS in web.py.

import type { Posting } from "./api";

/** Statuses shown under the "Open" tab and counted on the landing page. */
export const OPEN_STATUSES: string[] = [
  "pending",
  "gated",
  "verified",
  "published",
];

export function isOpen(p: Posting): boolean {
  return OPEN_STATUSES.includes(p.status);
}

/** Effective degree levels: verdict overrides the heuristic when non-empty. */
export function degreesOf(p: Posting): string[] {
  return p.verdict && (p.verdict.degree_levels || []).length
    ? p.verdict.degree_levels
    : p.degree_levels || [];
}

/** Effective season: verdict overrides the heuristic. */
export function seasonOf(p: Posting): string | null {
  return (p.verdict && p.verdict.season) || p.season || null;
}

/** Open to undergraduates (unstated degree levels allow everyone). */
export function allowsBS(p: Posting): boolean {
  const d = degreesOf(p);
  return !d.length || d.includes("BS");
}

/** Open to graduate students (unstated degree levels allow everyone). */
export function allowsGrad(p: Posting): boolean {
  const d = degreesOf(p);
  return !d.length || d.includes("MS") || d.includes("PhD");
}

export function hoursAgo(p: Posting): number | null {
  return p.date_posted
    ? (Date.now() - new Date(p.date_posted).getTime()) / 3.6e6
    : null;
}

export function postedLabel(p: Posting): string {
  const h = hoursAgo(p);
  if (h === null) return p.date_posted_text || "date unknown";
  if (h < 1) return "just now";
  if (h < 24) return Math.floor(h) + "h ago";
  if (h < 24 * 14) return Math.floor(h / 24) + "d ago";
  return (p.date_posted as string).slice(0, 10);
}
