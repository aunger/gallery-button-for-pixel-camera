---
paths:
  - "**/*.{md,markdown}"
  - "**/*.{kt,java,sh,py}"
  - "**/*.*"
---
# Prose style and typographical standards

The following rules apply to prose in documents, code comments, and text output.

## Word wrap in Markdown, etc

Split prose at sentence breaks.
This balances line-diff stability with moderate line lengths.

This rule applies only when word wrap is a matter of preference or style, such as in
Markdown and other auto-flowed source text.

## Avoid angle quote, em-dash, and ellipsis characters

Prefer widely compatible characters, such as those in ASCII, when the option exists.

Since this rule refers only to prose, characters chosen for their specific meaning in
code or markup (such as pairs of back-ticks) are exempt.

- Replace dashes with commas, semicolons, or parentheses when grammatically possible,
  and double-hyphens otherwise.
- Replace ellipses with triple periods.
- Replace angled, directional, or smart quotes (single quote or double quote) with
  straight quote characters.

## Do not surround dashes or double-hyphens with spaces

Omit the spaces--like this.
