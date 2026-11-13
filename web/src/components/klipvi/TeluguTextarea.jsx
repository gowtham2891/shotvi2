import React, { useEffect, useRef, useState } from "react";
import { client } from "@/api/client";

/** Telugu spellings for a word typed in English letters (POST /transliterate; [] when the service is down). */
async function suggest(word) {
  try {
    const { data } = await client.post("/transliterate", { text: word }, { timeout: 5000 });
    return Array.isArray(data?.suggestions) ? data.suggestions : [];
  } catch {
    return [];
  }
}

/** The English-letters word just before the caret: { word, start, end } or null. */
function wordAtCaret(value, caret) {
  const before = value.slice(0, caret);
  const m = /[A-Za-z]+$/.exec(before);
  if (!m) return null;
  return { word: m[0], start: caret - m[0].length, end: caret };
}

/**
 * A caption line you can type Telugu into with an English keyboard, like Google's Telugu input: type "enti", pick
 * ఏంటి from the list (Space or Enter takes the first, arrows choose, Esc keeps the English letters).
 * `telugu` off: a plain text box. `onCommit(text)` fires when the line is left (blur) or Enter is pressed with no list.
 */
export default function TeluguTextarea({ value, onCommit, telugu = true, className = "", ...rest }) {
  const ref = useRef(null);
  const [list, setList] = useState([]); // suggestions for the current word
  const [pick, setPick] = useState(0);
  const [cur, setCur] = useState(null); // { word, start, end }
  const req = useRef(0);

  const grow = () => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  };
  useEffect(grow, [value]);

  const close = () => {
    setList([]);
    setCur(null);
    setPick(0);
  };

  const lookUp = () => {
    const el = ref.current;
    if (!telugu || !el) return;
    const w = wordAtCaret(el.value, el.selectionStart);
    if (!w) return close();
    const id = ++req.current;
    setCur(w);
    suggest(w.word).then((s) => {
      if (id !== req.current) return; // a newer keystroke won
      setList(s);
      setPick(0);
    });
  };

  const accept = (text, addSpace) => {
    const el = ref.current;
    if (!el || !cur) return;
    const v = el.value;
    const next = v.slice(0, cur.start) + text + (addSpace ? " " : "") + v.slice(cur.end);
    el.value = next;
    const caret = cur.start + text.length + (addSpace ? 1 : 0);
    el.setSelectionRange(caret, caret);
    close();
    grow();
  };

  const onKeyDown = (e) => {
    if (list.length && cur) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        setPick((p) => (p + (e.key === "ArrowDown" ? 1 : -1) + list.length) % list.length);
        return;
      }
      if (e.key === " " || e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        accept(list[pick], e.key === " ");
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        close();
        return;
      }
    }
    if (e.key === "Enter") {
      e.preventDefault();
      e.currentTarget.blur();
    }
  };

  return (
    <div className="relative flex-1 min-w-0">
      <textarea
        ref={ref}
        defaultValue={value}
        rows={1}
        onInput={() => {
          grow();
          lookUp();
        }}
        onKeyDown={onKeyDown}
        onBlur={(e) => {
          // a click on a suggestion lands after blur: let it apply first
          setTimeout(() => {
            close();
            onCommit?.(e.target.value);
          }, 120);
        }}
        className={`w-full resize-none overflow-hidden ${className}`}
        aria-autocomplete={telugu ? "list" : undefined}
        aria-expanded={telugu ? list.length > 0 : undefined}
        {...rest}
      />
      {list.length > 0 && cur && (
        <ul role="listbox" aria-label={`Telugu for ${cur.word}`} className="absolute left-0 top-full z-20 mt-1 min-w-[10rem] rounded-lg border border-k-line2 bg-k-raised shadow-xl py-1">
          {list.map((s, i) => (
            <li key={s}>
              <button
                type="button"
                role="option"
                aria-selected={i === pick}
                onMouseDown={(e) => {
                  e.preventDefault(); // keep the focus in the line
                  accept(s, true);
                }}
                className={`w-full text-left px-3 py-1 text-[0.95rem] ${i === pick ? "bg-k-accent text-k-ink" : "text-k-text hover:bg-k-line"}`}
              >
                {s}
              </button>
            </li>
          ))}
          <li className="px-3 pt-1 pb-0.5 text-[0.7rem] text-k-subtle border-t border-k-line mt-1">Space picks the first, Esc keeps “{cur.word}”</li>
        </ul>
      )}
    </div>
  );
}
