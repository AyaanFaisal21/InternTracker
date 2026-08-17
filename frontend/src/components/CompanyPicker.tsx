// Company typeahead for the subscribe panel: a combobox over the board's
// live company list whose picks become removable chips. Every suggestion and
// every chip carries its posting count, so a reader sees that a name matches
// real postings before submitting. A name the board does not track can still
// be added, but it is marked instead of silently accepted, which is the
// failure this control exists to prevent.

import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import type { Company } from "../api";

/** Whether the list behind the typeahead loaded. */
export type ListState = "loading" | "ready" | "failed";

// A long popup turns into a scroll trap; the query narrows fast enough.
const MAX_ROWS = 8;

interface Row {
  name: string;
  /** Live postings, or null when the name is not on the board's list. */
  count: number | null;
}

/** Reader-facing count for a row or chip. Never claims more than we know. */
function countText(count: number | null, listState: ListState): string {
  if (count !== null) return `${count} open`;
  return listState === "ready" ? "not tracked yet" : "unchecked";
}

export default function CompanyPicker({
  companies,
  listState,
  selected,
  max,
  disabled,
  onChange,
}: {
  companies: Company[];
  listState: ListState;
  selected: string[];
  max: number;
  disabled: boolean;
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

  // Keyed by lowercased name: it resolves both the count and the board's
  // own spelling, so a name typed in another case is stored the way the
  // backend knows it instead of arriving as an unmatchable string.
  const tracked = useMemo(() => {
    const m = new Map<string, Company>();
    companies.forEach((c) => m.set(c.name.toLowerCase(), c));
    return m;
  }, [companies]);

  const full = selected.length >= max;
  const locked = disabled || listState === "loading";

  const rows = useMemo<Row[]>(() => {
    if (full) return [];
    const q = query.trim().toLowerCase();
    const taken = new Set(selected.map((s) => s.toLowerCase()));
    const hits = companies.filter(
      (c) => !taken.has(c.name.toLowerCase()) && c.name.toLowerCase().includes(q),
    );
    if (q)
      hits.sort((a, b) => {
        const lead =
          Number(b.name.toLowerCase().startsWith(q)) -
          Number(a.name.toLowerCase().startsWith(q));
        return lead || b.postings - a.postings;
      });
    const out: Row[] = hits
      .slice(0, MAX_ROWS)
      .map((c) => ({ name: c.name, count: c.postings }));
    // An unmatched name is offered as its own row, so adding it is a
    // deliberate act rather than a typo slipping through unnoticed.
    if (q && !tracked.has(q) && !taken.has(q))
      out.push({ name: query.trim(), count: null });
    return out;
  }, [companies, query, selected, tracked, full]);

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
    if (selected.some((s) => s.toLowerCase() === clean.toLowerCase())) return;
    onChange([...selected, tracked.get(clean.toLowerCase())?.name ?? clean]);
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

  const placeholder =
    listState === "loading"
      ? "loading companies"
      : full
        ? `${max} is the limit`
        : selected.length
          ? "add another"
          : "start typing a company name";

  return (
    <div className="cp">
      <div className="cp-head">
        <label className="cp-lab" htmlFor={inputId}>
          Companies to watch
        </label>
        <span className="cp-tally">
          {selected.length} of {max}
        </span>
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
                const count = tracked.get(name.toLowerCase())?.postings ?? null;
                const unknown = count === null && listState === "ready";
                return (
                  <li
                    key={name}
                    className={unknown ? "cp-chip cp-chip-new" : "cp-chip"}
                  >
                    <span className="cp-chip-name">{name}</span>
                    <span className="cp-dot" aria-hidden="true">
                      ·
                    </span>
                    <span className="cp-n">{countText(count, listState)}</span>
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
            placeholder={placeholder}
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
              <span className="cp-opt-name">{r.name}</span>
              <span className="cp-dot" aria-hidden="true">
                ·
              </span>
              <span className="cp-n">{countText(r.count, listState)}</span>
            </li>
          ))}
        </ul>
      </div>
      <p className="cp-help" id={helpId}>
        Type to search the board. Arrow keys move, Enter adds, Backspace
        removes the last one.
      </p>
    </div>
  );
}
