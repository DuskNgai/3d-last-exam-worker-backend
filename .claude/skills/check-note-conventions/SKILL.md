---
name: check-note-conventions
description: Use when writing or reviewing documents to verify formatting, style, and writing conventions.
---

# Check Note Conventions

## Overview

Check a document against formatting and style conventions. Dispatches two subagents in parallel:

- **Format Checker** -- shell commands narrow down candidates, LLM judges each one: LaTeX delimiters, non-ASCII chars, notation, cross-references, table cells, heading/body language
- **Style Checker** -- reads document for qualitative judgment: content focus, terminology, math formatting, phrasing, paragraph rhythm, tradeoffs, AI tone

Merge their reports into a single table ordered by severity.

Active skill: call with a document path, e.g. `/check-note-conventions path/to/doc.md`. No document type restrictions -- skip items that don't apply.

## Dispatch

When invoked, dispatch two subagents in parallel.

### Format Checker

```
Given document path {path}. For each check: shell command narrows down candidates,
LLM reviews each candidate and makes the final call.

0. Preflight:
   `test -f "{path}" && test -s "{path}"`
   -> Exit if missing or empty.

1. $$ delimiter balance:
   `grep -noF '$$' "{path}"`
   -> Shell lists $$ occurrences with line numbers. LLM: discard hits inside code blocks, frontmatter, and inline code. Remaining count must be even; odd means unclosed block (error).

2. Non-ASCII arrows:
   `perl -CSD -lne 'print "$.: $_" if /[\x{2190}-\x{21FF}\x{27F0}-\x{27FF}\x{2900}-\x{297F}\x{2B00}-\x{2BFF}]/' "{path}"`
   -> Shell finds lines with arrow characters. Flag as error.

3. Non-ASCII dashes:
   `perl -CSD -lne 'print "$.: $_" if /[\x{2010}-\x{2015}]/' "{path}"`
   -> Shell finds dash variants. Flag as error. Fix by rewriting to eliminate the dash, not by substituting `-`.

4. Unicode math:
   `perl -CSD -lne 'print "$.: $_" if /[\x{0370}-\x{03FF}\x{2100}-\x{214F}\x{1D400}-\x{1D7FF}\x{2200}-\x{22FF}\x{27C0}-\x{27EF}\x{2980}-\x{29FF}\x{2A00}-\x{2AFF}\x{00D7}\x{00B1}]/' "{path}"`
   -> Shell finds all Unicode math/greek characters. Flag as error.

5. Notation inventory:
   `grep -noP '\$\$?\K(?:\\\$|[^$])+(?=\$\$?)' "{path}"` for LaTeX math; `grep -nF -e '\[' -e '\]' -e '\(' -e '\)' "{path}"` for non-standard delimiters.
   -> Shell extracts single-line LaTeX math and non-standard delimiter lines. LLM: supplement from display math blocks, group by nearest ## heading, build symbol-per-section inventory. If document has its own notation section, validate symbols against it; otherwise search for project notation reference. If none exists, ensure consistency within the document itself. Flag same symbol defined differently across sections, or same concept using different symbols across sections (warning). Flag \[...\] / \(...\) as non-standard delimiters that should convert to $$...$$ / $...$ (error).

6. Cross-references:
   `grep -noP '\[[^\]]+\]\(([^)]+)\)' "{path}"; grep -noP '(?<!\w)[\w.-]+/[.\w/-]+' "{path}"`
   -> Shell extracts path candidates. LLM: discard URLs (http/https), fragment-only links (#...), date patterns, and hits inside code blocks. Resolve remaining paths relative to dirname of "{path}", test -f to verify, flag broken links (error).

7. Table review:
   `grep -nP '^\s*\|' "{path}"`
   -> Shell finds table rows. LLM: discard hits inside code blocks.
   a. Split by | (treating \| as escaped literal pipe), skip separator rows, flag cells over 250 chars (warning).
   b. Extract column headers, check names are consistent across tables and match document notation (warning).

8. Heading language:
   `grep -nP '^#{1,6}\s+' "{path}"` for ATX headings; `grep -nP '^(={3,}|-{3,})\s*$' "{path}"` for setext underlines. The line immediately before an underline is a setext heading.
   -> Shell finds heading lines (ATX and setext). LLM: discard hits inside code blocks and frontmatter. Setext headings are not allowed; flag any setext heading as error. For ATX headings, check if heading text is English. Digits and LaTeX within headings are acceptable. Flag non-English headings (error).

9. Body language:
    `perl -CSD -lne 'print "$.: $_" if /[\x{4e00}-\x{9fff}\x{3400}-\x{4DBF}]/' "{path}" | head -1`
    -> Shell checks for CJK presence. LLM: discard CJK inside code blocks and frontmatter. If no CJK found, flag (error).

Output: table with columns Location | Checklist Section | Issue | Severity | Suggestion.
Severity: error for clear violations, warning for style concerns or borderline cases.
```

### Style Checker

```
Given document path {path}. Read the document and evaluate qualitatively:

1. Content focus (§1) -- does the document focus on physical principles, mathematical derivations, algorithmic tradeoffs? Or does it describe code/API calls/implementation steps?
2. Terminology (§1) -- are domain-specific terms kept untranslated?
3. Math formatting (§2) -- any plain-text math left unconverted? Bare Greek letter names, math functions, superscript/subscript notation like `x^2` or `a_i`, inline equations? (Delimiter issues `\[...\]` / `\(...\)` are covered by Format Checker as errors; this check focuses on unconverted math content, which is warning.)
4. Phrasing (§4) -- scan for filler: "值得注意的是", "综上所述", "另外", "此外". Check for mechanical structures: "一方面...另一方面...", "不仅...而且...".
5. Paragraph rhythm (§5) -- varied paragraph length? Short sentences add variety (two-word sentence is fine)? Natural Chinese transitions ("但是", "所以", "因为", "换句话说") used where appropriate?
6. Judgment (§6) -- tradeoffs stated clearly with resolution ("X is better than Y because...")? Concrete examples with real numbers ("inflates metric X by ~0.05", not "can bias the metric")?
7. AI tone (§7) -- hedge words ("可能", "通常", "往往", "一定程度上") not packed every sentence? Claims not all balanced with counterpoints? Sentence structure varied? Active voice preferred? Concrete over abstract ("certain", "various", "relevant")? Far-fetched analogies? (suggestion only)

Reference location by line number where possible, otherwise by section heading.

Output: table with columns Location | Checklist Section | Issue | Severity | Suggestion.
Severity: warning (style concern) or suggestion (optional polish).
```

### Merge

Combine both tables. If one subagent fails or returns empty, use the other's results and note the omission. If both fail, report the error to the user.

Order rows by severity: error, then warning, then suggestion. Within each severity, preserve original order. If both agents reference the same location for different reasons, keep both rows -- Location format may differ between agents (line numbers vs section references).

## Checklist

Check items applicable to the document under review; skip items that don't apply. Style and voice items are soft guidelines -- reasonable deviations are acceptable.

### 1. Content

- [ ] Focus on physical principles, mathematical derivations, algorithmic tradeoffs -- not code or API calls or implementation steps
- [ ] Headings are English (ATX only; setext `===`/`---` underlines not allowed), body is Chinese
- [ ] Domain-specific terminology stays untranslated

### 2. Math, Tables & Consistency

- [ ] All math uses LaTeX: `$...$` inline, `$$...$$` block -- never plain-text math. `\[...\]` / `\(...\)` are non-standard, must convert to `$$...$$` / `$...$`
- [ ] `$$` delimiters balanced -- display math blocks properly opened and closed
- [ ] Notation and symbols used consistently throughout the document -- same symbol for same concept across sections. Search for the project's notation reference; if none exists, ensure consistency within the document itself.
- [ ] Table column names are consistent and match the document's own notation
- [ ] Cross-document reference paths are correct
- [ ] Table cells concise -- flag cells over 250 characters

### 3. Characters & Symbols

- [ ] No non-ASCII arrows. Use `->`, prose alternatives, or standard syntax
- [ ] No non-ASCII dashes (en dash, em dash). Rewrite to eliminate the dash -- don't substitute `-`
- [ ] Math symbols must use LaTeX syntax (`$...$` or `$$...$$`) -- never use non-ASCII Unicode math symbols

### 4. Style -- Phrasing to Avoid

- [ ] Avoid "值得注意的是", "综上所述", "另外", "此外" as filler
- [ ] Avoid "一方面...另一方面...", "不仅...而且..." as mechanical structure

### 5. Style -- Paragraph Rhythm

- [ ] Vary paragraph length, avoid mechanical cadence. Very short (1-2 sentences) or very long (8+ sentences) paragraphs are fine in moderation.
- [ ] Short sentences add variety. A two-word sentence is fine.
- [ ] Use natural transitions: "但是", "所以", "因为", "换句话说"

### 6. Style -- Judgment

- [ ] Tradeoffs and pros/cons stated clearly -- don't hedge every conclusion, say which approach works better and why
- [ ] Use concrete examples with real numbers -- "a loose mask on noisy data inflates metric X by ~0.05", not "noise can bias the metric"

### 7. AI Tone -- Human Voice

- [ ] No hedge words packing every sentence -- "可能", "通常", "往往", "一定程度上" everywhere is a giveaway. Use only when uncertainty is real.
- [ ] Do not sand off every sharp edge -- if every claim has a counterpoint, it reads like LLM hedging. Sometimes X is just better than Y, say it.
- [ ] Mix of simple and complex sentences -- if every sentence has the same structure (all "Subject verb object" or all "If X, then Y"), paragraph reads as mechanical
- [ ] Active voice preferred over passive -- "the kernel computes X" not "X is computed by the kernel". Reserve passive for when the actor is unknown or irrelevant.
- [ ] Concrete over abstract -- prefer specific objects and quantities over generic descriptors like "certain", "various", "relevant"
- [ ] No far-fetched analogies -- avoid stretched comparisons that don't add insight (suggestion)

## Common Mistakes

| Mistake | Fix |
|---------|------|
| Describing code flow or API calls | Focus on physical principles and tradeoffs instead |
| English body text | Write body in Chinese, headings in English |
| Non-English heading | Write headings in English |
| Setext heading (`===` or `---` underline) | Use ATX `#` headings instead |
| Domain-specific terms translated | Keep domain terminology in original language |
| Plain-text math like `sigma = sqrt(N)` | Use LaTeX: `$\sigma = \sqrt{N}$` |
| Table cell over 250 characters | Split large cells into prose |
| Table column names inconsistent | Use consistent column names that match document notation |
| `\[...\]` or `\(...\)` instead of `$$...$$` or `$...$` | Use standard LaTeX delimiters |
| Notation inconsistency (same symbol, different meaning) | Keep same meaning for same symbol across sections |
| Unbalanced `$$` delimiters | Ensure every `$$` has a matching `$$` to close the block |
| Broken cross-reference link | Verify target file exists relative to the document |
| Non-ASCII arrow (e.g. right-arrow) | Use `->` or prose |
| Non-ASCII dash (e.g. em-dash) in text | Rewrite to eliminate the dash |
| Bare Unicode math symbol (e.g. α, ∑) | Use LaTeX: `$\alpha$`, `$\sum$` |
| "值得注意的是", "综上所述", "另外", "此外" as filler | Drop them -- structure should make the point obvious |
| "一方面...另一方面...", "不仅...而且..." as structure | Drop them -- explain the relationship directly |
| All paragraphs same length | Vary paragraph length, avoid mechanical cadence |
| No transitions between ideas | Use natural transitions like "但是", "所以", "因为", "换句话说" |
| Vague claim without real numbers | Add concrete example with real values |
| Hedge words in every sentence | Use only when uncertainty is real, not as default posture |
| Every claim has a counterpoint | Let some claims stand without qualification |
| Far-fetched analogies (suggestion) | Drop analogies that don't add insight; use direct explanation |

## Reporting

Present findings as a table:

| Location | Checklist Section | Issue | Severity | Suggestion |
|----------|-------------------|-------|----------|------------|
| §1       | Content           | ...   | error    | ...        |

Severity: **error** (hard rule violation), **warning** (style concern), **suggestion** (optional polish).
