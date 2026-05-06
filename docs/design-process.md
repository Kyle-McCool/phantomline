# Phantomline Design Process

The "how we work on design changes" doc. Adapted from
`anthropic-skills:premium-saas-design`'s Define → Build → Review →
Refine loop, scaled down for a Flask + vanilla CSS/JS codebase (no
React, no Tailwind, no shadcn).

## The loop

```
DEFINE → BUILD → REVIEW → REFINE
   ↑                        │
   └────────────────────────┘
```

Every design change touches all four phases. Skipping `DEFINE`
(jumping straight to "make this prettier") is what produced 25
distinct font-sizes and 14 distinct radii in `phantomline.css` before
the Phase 1 craft pass cleaned them up.

## DEFINE phase — context artifacts

Before changing visuals, the chat needs three docs in front of it:

1. **`docs/brand-brief.md`** — What we're building, who for, what
   personality. Stable; rarely edited.
2. **`docs/style-guide.md`** — Tokens, scales, anti-patterns. Living
   doc; update when a decision is made that contradicts it.
3. **The PR / commit message** — What this specific change is doing
   and why. Captures the local context.

For meaningful redesigns (new section, new page), also include a
**section spec** in the prompt:

```
# [Section name] Spec

## Layout
- [Where on page, grid behavior, responsive breakpoints]

## Reference (paste screenshots)
- Inspiration site: [URL + what to copy]

## Components
- Primary CTA: [style, text, action]
- [Other components]

## Motion
- [What animates, when, why]

## States
- Empty / loading / error / populated
```

## BUILD phase — implementation rules

- **Section isolation.** Do one section per chat. Don't ask the model
  to redesign the studio AND the landing in a single session — it'll
  cross-contaminate decisions.
- **Commit per section.** After each section is done. If iteration
  goes wrong, revert just that commit.
- **Never write inline styles in HTML** for new code. CSP forbids
  inline scripts; we have a softer rule on inline styles but they
  spread fast — extract to the appropriate stylesheet.
- **Always reference tokens from `phantomline.css :root`** (or
  `landing.css :root` for marketing). Hardcoded colors / sizes are a
  red flag in code review.

## REVIEW phase — what to check

Before merging:

- [ ] All new spacing values are on the 4px grid (4/8/12/16/24/32)
- [ ] All new radii are from the scale (4/8/14/22/999px/50%)
- [ ] All new font-sizes are integers from the scale
      (11/12/13/14/16/18/24/32 or `clamp()` for hero)
- [ ] No new accent colors introduced
- [ ] Motion respects `@media (prefers-reduced-motion: reduce)`
- [ ] Touch targets ≥44px on mobile
- [ ] `:focus-visible` styles present on every interactive element
- [ ] Skip-to-main link still works
- [ ] CSS still parses (no unbalanced braces): `node -e "..."` check
- [ ] JS still parses: `node --check static/phantomline.js`
- [ ] Page renders at 1280 / 768 / 375px without horizontal scroll
- [ ] Dark mode (the only mode we ship) — no contrast issues

## REFINE phase — when to update the style guide

Update `docs/style-guide.md` if you:

- Introduce a new component pattern
- Change a token's intended use
- Discover a new anti-pattern
- Pick a personality direction for a new surface

Update `docs/brand-brief.md` if you:

- Change the target audience
- Reframe the primary goal
- Change the brand voice or tone

## Tooling

- **`anthropic-skills:design`** — Run for craft passes. Detects
  spacing / radius / typography drift.
- **`anthropic-skills:modern-web-design`** — Run for landing /
  marketing pass. Detects fluid-type, motion, a11y, perf opportunities.
- **`anthropic-skills:ux-audit`** — Run for "is the workflow
  comprehensible?" review. Persona-driven dogfooding.
- **`anthropic-skills:ui-audit`** — Run for component-level a11y +
  state-coverage check.

## When this loop fails

If a design change feels off after shipping:

1. Re-read `brand-brief.md` — does the change still serve the
   target audience?
2. Re-read `style-guide.md` — did the change introduce a token that
   should have been added to the guide?
3. Spawn a `ux-audit` skill on the surface — is there a workflow
   problem that the visual change masked but didn't fix?

## History

This process was formalized 2026-05-06 after a multi-session redesign
spree (landing rebuild, studio Phase A+D refresh, color sweep,
workflow audit) produced organic drift. The Phase 1 / 2 / 3 craft
pass on the same date documented the existing tokens and cleaned
the worst violations.
