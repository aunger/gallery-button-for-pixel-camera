// @ts-check
// Custom markdownlint rules enforcing .claude/rules/prose-style.md.
//
// Rule GB001: no-typography-chars
//   Flags em-dash (U+2014), en-dash (U+2013), ellipsis (U+2026), and
//   curly/smart quotes (U+2018 U+2019 U+201C U+201D) in prose.
//   Characters inside fenced code blocks and inline code spans are exempt,
//   because the style guide explicitly excepts characters chosen for their
//   specific meaning in code or markup.
//
// Rule GB002: no-spaced-dash
//   Flags double-hyphens or single hyphens that are surrounded by spaces
//   (" -- " or " - "), since the style guide requires omitting those spaces.
//   Hyphen-ranges like "6-8" (no surrounding spaces) are not flagged.
//   Characters inside code regions are exempt for the same reason as above.
//
// Both rules use the default (markdown-it) parser and the raw lines array.
// Fenced code blocks are identified from the markdown-it token list.
// Inline code spans are erased from each line before checking, using a
// regex that handles single-backtick and multi-backtick spans.

"use strict";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Collect the set of line numbers (1-based) that fall entirely inside a
 * fenced or indented code block, based on the markdown-it token list.
 *
 * @param {import("markdownlint").MarkdownItToken[]} tokens
 * @returns {Set<number>}
 */
function fencedCodeLineNumbers(tokens) {
  const blocked = new Set();
  for (const token of tokens) {
    if ((token.type === "fence" || token.type === "code_block") && token.map) {
      // token.map = [firstLine, lastLine] (0-based, exclusive end).
      for (let ln = token.map[0] + 1; ln <= token.map[1]; ln++) {
        blocked.add(ln); // store as 1-based
      }
    }
  }
  return blocked;
}

/**
 * Erase all inline code spans from a line so that characters inside them
 * are not checked.  The replacement is a run of ASCII spaces of the same
 * byte length, which keeps all column offsets intact.
 *
 * Handles:
 *   - Single-backtick spans:  `code`
 *   - Double-backtick spans: ``code``
 *   - Triple-backtick spans: ```code```
 *
 * A backtick run of N backticks is opened by exactly N backticks and
 * closed by the same run; mismatched lengths do not close each other.
 * This covers the most common cases without a full CommonMark parser.
 *
 * @param {string} line
 * @returns {string}
 */
function eraseInlineCode(line) {
  // Replace `` `...` `` spans from longest backtick run to shortest so
  // that triple-backtick spans are consumed before single-backtick ones.
  // Using a simple loop over run lengths 3..1.
  let result = line;
  for (let n = 3; n >= 1; n--) {
    const fence = "`".repeat(n);
    // Match an opening run of exactly n backticks (not n+1), the content
    // (any chars except newline, non-greedy), and a closing run of exactly n.
    const re = new RegExp(
      "(?<!`)" + fence + "(?!`)" + "([^\\n]*?)" + "(?<!`)" + fence + "(?!`)",
      "g"
    );
    result = result.replace(re, (m) => " ".repeat(m.length));
  }
  return result;
}

// ---------------------------------------------------------------------------
// Rule GB001: no-typography-chars
// ---------------------------------------------------------------------------

const TYPOGRAPHY_CHARS = [
  { char: "—", name: "em-dash",            suggestion: "--" },
  { char: "–", name: "en-dash",            suggestion: "-" },
  { char: "…", name: "ellipsis character", suggestion: "..." },
  { char: "‘", name: "left single quote",  suggestion: "'" },
  { char: "’", name: "right single quote", suggestion: "'" },
  { char: "“", name: "left double quote",  suggestion: '"' },
  { char: "”", name: "right double quote", suggestion: '"' },
];

/** @type {import("markdownlint").Rule} */
const noTypographyChars = {
  names: ["GB001", "no-typography-chars"],
  description:
    "Typography characters not allowed in prose (see .claude/rules/prose-style.md)",
  tags: ["prose-style"],
  // Omitting `parser` gives us params.tokens (markdown-it) for free.
  function: function GB001(params, onError) {
    const blocked = fencedCodeLineNumbers(params.tokens || []);
    const lines = params.lines;

    for (let lineIdx = 0; lineIdx < lines.length; lineIdx++) {
      const lineNumber = lineIdx + 1;
      if (blocked.has(lineNumber)) {
        continue;
      }
      // Erase inline code spans before scanning.
      const line = eraseInlineCode(lines[lineIdx]);

      for (const { char, name, suggestion } of TYPOGRAPHY_CHARS) {
        let idx = line.indexOf(char);
        while (idx !== -1) {
          const cp = char.codePointAt(0);
          onError({
            lineNumber,
            detail:
              "Found " + name +
              " (U+" +
              (cp !== undefined
                ? cp.toString(16).toUpperCase().padStart(4, "0")
                : "????") +
              "); use " + suggestion + " instead",
            context: lines[lineIdx].slice(Math.max(0, idx - 10), idx + 11),
            range: [idx + 1, char.length],
          });
          idx = line.indexOf(char, idx + 1);
        }
      }
    }
  },
};

// ---------------------------------------------------------------------------
// Rule GB002: no-spaced-dash
// ---------------------------------------------------------------------------

// Matches " - " or " -- " (space, one or two hyphens, space), but only when
// preceded by a non-whitespace character so that list-bullet syntax
// ("  - item") and blockquote bullets at the start of a line are not flagged.
// The lookbehind `(?<=\S)` requires a non-space character immediately before
// the leading space that is part of the match.
const SPACED_DASH_RE = /(?<=\S) (--?) /g;

/** @type {import("markdownlint").Rule} */
const noSpacedDash = {
  names: ["GB002", "no-spaced-dash"],
  description:
    "Dash or double-hyphen must not be surrounded by spaces " +
    "(see .claude/rules/prose-style.md)",
  tags: ["prose-style"],
  function: function GB002(params, onError) {
    const blocked = fencedCodeLineNumbers(params.tokens || []);
    const lines = params.lines;

    for (let lineIdx = 0; lineIdx < lines.length; lineIdx++) {
      const lineNumber = lineIdx + 1;
      if (blocked.has(lineNumber)) {
        continue;
      }
      const line = eraseInlineCode(lines[lineIdx]);
      SPACED_DASH_RE.lastIndex = 0;

      let match;
      while ((match = SPACED_DASH_RE.exec(line)) !== null) {
        const dashes = match[1];
        onError({
          lineNumber,
          detail:
            '"' + dashes + '" must not be surrounded by spaces; ' +
            'write "' + dashes + '" with no surrounding spaces',
          context: lines[lineIdx].slice(
            Math.max(0, match.index - 8),
            match.index + match[0].length + 8
          ),
          range: [match.index + 1, match[0].length],
        });
        // Advance by 1 to allow overlapping matches like " - - ".
        SPACED_DASH_RE.lastIndex = match.index + 1;
      }
    }
  },
};

// ---------------------------------------------------------------------------
// Export both rules
// ---------------------------------------------------------------------------

module.exports = [noTypographyChars, noSpacedDash];
