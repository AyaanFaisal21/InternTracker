// All backend I/O lives in this module. Pages import only from here.
// Every URL is relative, so the built app works behind any reverse proxy.
// To move to a different backend (e.g. a direct Supabase client), replace
// this file and keep the exported types and function signatures.

export type DegreeLevel = "BS" | "MS" | "PhD";

export type PostingStatus =
  | "pending" // detected, not yet gated
  | "gated" // passed rule checks, awaits agent verdict
  | "verified" // agent approved, safe to publish
  | "rejected" // failed rules or agent verdict
  | "published"; // pushed to the web app

export type PostingSource =
  | "github_list"
  | "greenhouse"
  | "lever"
  | "ashby"
  | "workday"
  | "custom"
  | "suggestion"
  | "opportunity_list";

/** Structured output of the verifier agent (schema.py Verdict). */
export interface Verdict {
  is_swe_internship: boolean;
  is_open: boolean;
  is_legitimate: boolean;
  season: string | null; // e.g. "Summer 2026"
  date_posted: string | null; // ISO date, LLM fallback
  degree_levels: DegreeLevel[];
  confidence: string; // "high" | "medium" | "low"
  reasons: string[];
}

/**
 * One row of GET /api/postings.
 * Shape: schema.py Posting.model_dump(mode="json") plus two server-derived
 * fields, `countries` (countries_of(locations)) and `role`
 * (classify_role(title)).
 */
export interface Posting {
  id: string;
  company: string;
  title: string;
  url: string; // as detected
  canonical_url: string | null; // resolved employer page; prefer over url
  category: string; // internship | program | scholarship | research | event
  audience: string[]; // underclassmen | diversity
  degree_levels: DegreeLevel[]; // heuristic; verdict.degree_levels overrides
  locations: string[];
  sources: PostingSource[];
  date_posted: string | null; // ISO datetime
  date_posted_text: string | null; // raw phrasing when no parseable date
  season: string | null; // heuristic; verdict.season overrides
  qualifications: string | null;
  first_seen: string; // ISO datetime
  status: PostingStatus;
  reject_reason: string | null;
  verdict: Verdict | null;
  countries: string[]; // derived server-side
  role: string; // derived server-side
}

/** One row of GET /api/suggestions (store.py suggestions table). */
export interface Suggestion {
  id: number;
  kind: string; // "url" | "company"
  value: string;
  company: string | null;
  keywords: string | null;
  status: string; // new | matched | no_match | error
  result: string | null;
  created_at: string;
}

/** Body of POST /api/suggest. */
export interface SuggestInput {
  kind: "url" | "company";
  value: string;
  company?: string;
  keywords?: string;
}

const JSON_HEADERS = { "Content-Type": "application/json" };

export async function fetchPostings(): Promise<Posting[]> {
  const r = await fetch("/api/postings");
  if (!r.ok) throw new Error(`GET /api/postings: ${r.status}`);
  return (await r.json()) as Posting[];
}

export async function fetchSuggestions(): Promise<Suggestion[]> {
  const r = await fetch("/api/suggestions");
  if (!r.ok) throw new Error(`GET /api/suggestions: ${r.status}`);
  return (await r.json()) as Suggestion[];
}

/**
 * Queue a suggestion. Returns the new suggestion id, or null when the server
 * refuses the request (rate limit, validation). Network failures throw.
 */
export async function submitSuggestion(
  input: SuggestInput,
): Promise<{ id: number } | null> {
  const r = await fetch("/api/suggest", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify(input),
  });
  if (!r.ok) return null;
  return (await r.json()) as { id: number };
}

/** Fire-and-forget page-open beacon. Never throws. */
export function recordVisit(page: string): void {
  fetch("/api/visit", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ page }),
  }).catch(() => {});
}
