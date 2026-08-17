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
 * Shape: schema.py Posting.model_dump(mode="json") plus three
 * server-derived fields: `countries` (countries_of(locations)), `remote`
 * (is_remote(locations)), and `role` (classify_role(title)).
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
  countries: string[]; // derived server-side; empty = region unknown
  remote: boolean; // derived server-side
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

/** One row of GET /api/companies: a company that has live postings. */
export interface Company {
  name: string;
  postings: number;
}

/** Body of POST /api/subscribe. Only email exists as a channel today. */
export interface SubscribeInput {
  channel: "email";
  target: string;
  filters: { companies: string[] };
}

/**
 * Success body of POST /api/subscribe. `matches` maps every submitted
 * company to its live posting count, so 0 means "not tracked yet". `token`
 * is the confirmation secret from the emailed link: never render it.
 * `pending_verification` is true while the row waits for a click;
 * `verification_sent` is false when the server saved the row but mailed
 * nothing, so callers must not tell the reader to check their inbox.
 */
export interface Subscription {
  id: number;
  token: string;
  pending_verification: boolean;
  verification_sent: boolean;
  matches: Record<string, number>;
}

/** Why the server refused a subscribe request. */
export type SubscribeRefusal = "invalid" | "rate_limited" | "server";

export type SubscribeResult =
  | { ok: true; subscription: Subscription }
  | { ok: false; reason: SubscribeRefusal };

const JSON_HEADERS = { "Content-Type": "application/json" };

export async function fetchPostings(signal?: AbortSignal): Promise<Posting[]> {
  const r = await fetch("/api/postings", { signal });
  if (!r.ok) throw new Error(`GET /api/postings: ${r.status}`);
  return (await r.json()) as Posting[];
}

export async function fetchSuggestions(
  signal?: AbortSignal,
): Promise<Suggestion[]> {
  const r = await fetch("/api/suggestions", { signal });
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

export async function fetchCompanies(signal?: AbortSignal): Promise<Company[]> {
  const r = await fetch("/api/companies", { signal });
  if (!r.ok) throw new Error(`GET /api/companies: ${r.status}`);
  return (await r.json()) as Company[];
}

/**
 * Request an email subscription. The subscription stays inactive until the
 * confirmation link is opened, and `verification_sent` on the result says
 * whether that link was actually mailed. A refusal comes back as a typed
 * result so callers can tell validation from rate limiting; only network
 * failures throw.
 */
export async function subscribe(
  input: SubscribeInput,
  signal?: AbortSignal,
): Promise<SubscribeResult> {
  const r = await fetch("/api/subscribe", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify(input),
    signal,
  });
  if (!r.ok) {
    const reason: SubscribeRefusal =
      r.status === 400
        ? "invalid"
        : r.status === 429
          ? "rate_limited"
          : "server";
    return { ok: false, reason };
  }
  return { ok: true, subscription: (await r.json()) as Subscription };
}

/** Fire-and-forget page-open beacon. Never throws. */
export function recordVisit(page: string): void {
  fetch("/api/visit", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ page }),
  }).catch(() => {});
}
