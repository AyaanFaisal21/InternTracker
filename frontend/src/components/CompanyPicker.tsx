// Company typeahead: a combobox over a candidate list whose picks become
// removable chips. Two callers share it. The subscribe panel feeds it
// /api/companies and lets an untracked name through, marked rather than
// silently accepted, which is the failure that control exists to prevent.
// The board's sidebar feeds it the companies of the postings already
// loaded and refuses anything else, since filtering to a company with no
// postings only empties the board. Everything else, the filtering, the key
// handling, the chips and the combobox roles, is the same control.

import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import "../styles/picker.css";

/** Whether the list behind the typeahead is available yet. */
export type ListState = "loading" | "ready" | "failed";

/** One candidate name and the postings standing behind it. */
export interface CompanyOption {
  name: string;
  count: number;
}

// A long popup turns into a scroll trap; the query narrows fast enough.
const MAX_ROWS = 8;

/**
 * Comparison key for a company name. Case, padding and repeated spaces are
 * not differences, so "  nvidia" and "NVIDIA" are one company here and in
 * every caller that filters on the picked names.
 */
export function companyKey(name: string): string {
  return name.trim().toLowerCase().replace(/\s+/g, " ");
}

interface Row {
  name: string;
  /** Postings behind the name, or null when it is not on the list. */
  count: number | null;
}

/** Reader-facing count for a row or chip. Never claims more than we know. */
function countText(count: number | null, listState: ListState, unit: string): string {
  if (count !== null) return unit ? `${count} ${unit}` : String(count);
  return listState === "ready" ? "not tracked yet" : "unchecked";
}

export default function CompanyPicker({
  label,
  help,
  placeholder,
  unit = "",
  options,
  listState,
  selected,
  max,
  allowUnknown,
  disabled = false,
  onChange,
}: {
  label: string;
  help: string;
  /** Empty-state prompt. The loading and full states word themselves. */
  placeholder: string;
  /** Noun after a count; empty renders the bare number. */
  unit?: string;
  options: CompanyOption[];
  listState: ListState;
  selected: string[];
  /** Selection cap. Infinity means uncapped, which also drops the tally. */
  max: number;
  /** Whether a name absent from `options` may be picked. */
  allowUnknown: boolean;
  disabled?: boolean;
  onChange: (next: string[]) => void;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const inputRef = useRef<HTMLInputElement>(null);

  const uid = useId();
  const inputId = `${uid}-input`;
  const listId = `${uid}-list`;
  const helpId = `${uid}-help`;
  const optionId = (i: number) => `${uid}-opt-${i}`;

  // Keyed by comparison key: it resolves both the count and the list's own
  // spelling, so a name typed in another case is stored the way the source
  // knows it instead of arriving as an unmatchable string.
  const known = useMemo(() => {
    const m = new Map<string, CompanyOption>();
    options.forEach((c) => m.set(companyKey(c.name), c));
    return m;
  }, [options]);

  const full = selected.length >= max;
  const locked = disabled || listState === "loading";

  const rows = useMemo<Row[]>(() => {
    if (full) return [];
    const q = companyKey(query);
    const taken = new Set(selected.map(companyKey));
    const hits = options.filter(
      (c) => !taken.has(companyKey(c.name)) && companyKey(c.name).includes(q),
    );
    if (q)
      hits.sort((a, b) => {
        const lead =
          Number(companyKey(b.name).startsWith(q)) -
          Number(companyKey(a.name).startsWith(q));
        return lead || b.count - a.count;
      });
    const out: Row[] = hits
      .slice(0, MAX_ROWS)
      .map((c) => ({ name: c.name, count: c.count }));
    // An unmatched name is offered as its own row, so adding it is a
    // deliberate act rather than a typo slipping through unnoticed.
    if (allowUnknown && q && !known.has(q) && !taken.has(q))
      out.push({ name: query.trim(), count: null });
    return out;
  }, [options, query, selected, known, full, allowUnknown]);

  const activeValid = open && active >= 0 && active < rows.length;

  // Keep the highlighted row inside the popup's scroll window.
  useEffect(() => {
    if (!activeValid) return;
    document
      .getElementById(`${uid}-opt-${active}`)
      ?.scrollIntoView({ block: "nearest" });
  }, [activeValid, active, uid]);

  function close() {
    setOpen(false);
    setActive(-1);
  }

  function add(name: string) {
    const clean = name.trim();
    close();
    setQuery("");
    inputRef.current?.focus();
    if (!clean || full) return;
    const hit = known.get(companyKey(clean));
    if (!hit && !allowUnknown) return;
    if (selected.some((s) => companyKey(s) === companyKey(clean))) return;
    onChange([...selected, hit?.name ?? clean]);
  }

  function remove(name: string) {
    onChange(selected.filter((s) => s !== name));
    // The chip's own button is unmounting, so move focus somewhere real.
    inputRef.current?.focus();
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!rows.length) return;
      const step = e.key === "ArrowDown" ? 1 : -1;
      if (!open) {
        setOpen(true);
        setActive(step === 1 ? 0 : rows.length - 1);
        return;
      }
      setActive((i) =>
        i < 0
          ? step === 1
            ? 0
            : rows.length - 1
          : (i + step + rows.length) % rows.length,
      );
      return;
    }
    if (e.key === "Enter") {
      // Always swallowed: this field sits in a form, and Enter here means
      // "take this company", never "submit the subscription".
      e.preventDefault();
      if (activeValid) add(rows[active].name);
      else if (query.trim()) add(query);
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      if (open) close();
      else setQuery("");
      return;
    }
    if (e.key === "Backspace" && !query && selected.length) {
      e.preventDefault();
      remove(selected[selected.length - 1]);
      return;
    }
    if (e.key === "Tab" && open) close();
  }

  const prompt =
    listState === "loading"
      ? "loading companies"
      : full
        ? `${max} is the limit`
        : selected.length
          ? "add another"
          : placeholder;

  return (
    <div className="cp">
      <div className="cp-head">
        <label className="cp-lab" htmlFor={inputId}>
          {label}
        </label>
        {Number.isFinite(max) && (
          <span className="cp-tally">
            {selected.length} of {max}
          </span>
        )}
      </div>
      <div className="cp-anchor">
        <div
          className="cp-box"
          onMouseDown={(e) => {
            // Clicking the padding focuses the field without stealing the
            // press from a chip's remove button.
            if (e.target === e.currentTarget) {
              e.preventDefault();
              inputRef.current?.focus();
              if (!locked) setOpen(true);
            }
          }}
        >
          {selected.length > 0 && (
            <ul className="cp-chips">
              {selected.map((name) => {
                const count = known.get(companyKey(name))?.count ?? null;
                const unlisted = count === null && listState === "ready";
                return (
                  <li
                    key={name}
                    className={unlisted ? "cp-chip cp-chip-new" : "cp-chip"}
                  >
                    <span className="cp-chip-name" title={name}>
                      {name}
                    </span>
                    <span className="cp-dot" aria-hidden="true">
                      ·
                    </span>
                    <span className="cp-n">{countText(count, listState, unit)}</span>
                    <button
                      type="button"
                      className="cp-x"
                      aria-label={`Remove ${name}`}
                      disabled={disabled}
                      onClick={() => remove(name)}
                    >
                      ×
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          <input
            ref={inputRef}
            id={inputId}
            className="cp-input"
            type="text"
            autoComplete="off"
            spellCheck={false}
            disabled={locked}
            placeholder={prompt}
            value={query}
            role="combobox"
            aria-expanded={open}
            aria-controls={listId}
            aria-autocomplete="list"
            aria-describedby={helpId}
            aria-activedescendant={activeValid ? optionId(active) : undefined}
            onChange={(e) => {
              setQuery(e.target.value);
              setActive(-1);
              setOpen(true);
            }}
            onClick={() => {
              if (!locked) setOpen(true);
            }}
            onBlur={close}
            onKeyDown={onKeyDown}
          />
        </div>
        <ul
          className="cp-pop"
          id={listId}
          role="listbox"
          aria-label="Company suggestions"
          hidden={!open || rows.length === 0}
        >
          {rows.map((r, i) => (
            <li
              key={`${r.name}-${r.count === null ? "new" : "known"}`}
              id={optionId(i)}
              role="option"
              aria-selected={i === active}
              className={
                (r.count === null ? "cp-opt cp-opt-new" : "cp-opt") +
                (i === active ? " on" : "")
              }
              // Keep the press from blurring the input before the click lands.
              onMouseDown={(e) => e.preventDefault()}
              onMouseEnter={() => setActive(i)}
              onClick={() => add(r.name)}
            >
              <span className="cp-opt-name" title={r.name}>
                {r.name}
              </span>
              <span className="cp-dot" aria-hidden="true">
                ·
              </span>
              <span className="cp-n">{countText(r.count, listState, unit)}</span>
            </li>
          ))}
        </ul>
      </div>
      <p className="cp-help" id={helpId}>
        {help}
      </p>
    </div>
  );
}
