# Phantomline Style Guide

The single source of truth for tokens, scales, and patterns. Reference
this from any prompt that touches CSS — "follow style-guide.md
spacing/radius/type scales" — to stop the drift that produced 25
distinct font-sizes and 14 distinct radii in `phantomline.css`.

The canonical tokens themselves live in `static/phantomline.css`'s
`:root` block (Phase 1 craft pass, 2026-05). This doc explains the
intent.

## Design philosophy

**Industrial-editorial.** Sharp, dense, technical-honest. Ink + paper
+ brand-teal. Hairline rules over heavy borders. Mono for labels and
data, sans for prose. Flat-by-default depth (no big drop shadows);
subtle gradients on hero panels for premium feel.

**Personality (per `anthropic-skills:design` matrix): "Precision &
Density."** Power users live in the tool. Tight spacing, monochrome
foundation, info-forward layout. References: Linear, Raycast, terminal
aesthetics — softened with warm paper text instead of cold pure white.

## Color palette

```
--ink            #0a0a0a    Page background (near-black, not pure)
--ink-2          #141414    Panel background
--paper          #f4f1ea    Foreground text (warm white)
--paper-dim      #d8d4cb    Secondary text
--rule           rgba(244, 241, 234, 0.14)    Hairline divider
--rule-strong    rgba(244, 241, 234, 0.32)    Visible divider
--brand          #1ab8e8    Brand teal — logo color, primary accent
--brand-deep     #0d7ea8    Pressed / hover for brand
--brand-faint    rgba(26, 184, 232, 0.08)     Brand-tinted bg
--signal         #e8a04a    Warnings, secondary accent
--signal-deep    #b8761d    Warning emphasis
```

### Do
- Use `--brand` for primary CTAs and active states only.
- Use `--paper` for headings, `--paper-dim` for body and captions.
- Use `--rule` for dividers; `--rule-strong` only when separation matters.

### Don't
- Don't introduce new accent colors. We're a one-accent product.
- Don't use pure `#fff` or `#000`. Always the warmer ink/paper pair.
- Don't hard-code rgba colors when a token exists.

## Typography

**Fonts:**
- Headings + body: `IBM Plex Sans` (var: `--font-sans`)
- Labels, data, pills, eyebrows, code: `IBM Plex Mono` (var: `--font-mono`)
- One display font, one body. **Never mix two display fonts** (the
  pillar pages used Cormorant Garamond — that's been removed).

**Scale (phantomline.css `:root` tokens):**

| Token | Size | Usage |
|---|---|---|
| `--t-xs` | 11px | eyebrows, badge labels |
| `--t-sm` | 12px | secondary, tag chips, mono labels |
| `--t-base` | 13px | body in dense contexts |
| `--t-md` | 14px | primary body, button text |
| `--t-lg` | 16px | lede, prominent body |
| `--t-xl` | 18px | h3 |
| `--t-2xl` | 24px | h2 in compact contexts |
| `--t-3xl` | 32px | h2 in display contexts |

**Hero scale (fluid):**
- Landing h1: `clamp(56px, 9vw, 124px)`
- Section eyebrows + heroes: use `clamp()` for fluidity at desktop widths

**Weights:** 400 body, 500 active body, 600 headings/buttons, 700 brand. No 800/900.

**Tracking:** -0.005em on body, -0.02em on display headlines, +0.04em on
uppercase labels.

### Fractional sizes are forbidden
The Phase 1 pass eliminated 11.5 / 12.5 / 13.5 / 9.5 / 10.5px. **Never
re-introduce them.** Round to the nearest scale token.

## Spacing scale

4px-grid based. **Phantomline.css** tolerates 6/10/14/18/22 historically
but new code should reference these tokens:

| Token | Value | Use case |
|---|---|---|
| `--space-1` | 4px | hairline, icon insets |
| `--space-2` | 8px | tight gaps |
| `--space-3` | 12px | standard pad / row gap |
| `--space-4` | 16px | comfortable pad |
| `--space-6` | 24px | generous pad / section gap |
| `--space-8` | 32px | major section spacing |
| `--space-12` | 48px | hero spacing |

**Symmetrical padding rule:** TLBR sides match. If top is 16px, all
sides are 16px. The only exception is when content naturally creates
asymmetry (e.g. card with leading icon).

## Border radius

Sharp + flat depth strategy. Industrial-editorial wants chips and
buttons to feel cut-out, not pillowy.

| Token | Value | Use case |
|---|---|---|
| `--r-1` | 4px | tiny accents (focus rings, status dots) |
| `--r-2` | 8px | chips, buttons, inputs |
| `--r-3` | 14px | cards, workflow steps |
| `--r-4` | 22px | hero cards, big panels |
| `--r-pill` | 999px | pills, tags |
| `--r-circle` | 50% | avatars, status dots |

Phase 1 collapsed one-offs (13/24/26/28/34px) to nearest scale value.
**Don't introduce new radii.** Pick from the ladder.

## Depth strategy

**Flat by default; subtle gradients for emphasis.** Match Linear /
Raycast, not Stripe.

- Plain panels: 1px hairline border (`--rule`), no shadow
- Cards on focus: 1px brighter border (`--rule-strong`)
- Hero cards: subtle linear-gradient + inset 1px highlight at top
- Modals / popovers: heavier shadow `--shadow` (the only big drop shadow)

**No** dramatic drop shadows on resting state. **No** layered shadows
unless on a true overlay.

## Motion

| Token | Value | Use case |
|---|---|---|
| `--motion-fast` | 150ms | micro (hover, tap, focus ring) |
| `--motion-base` | 200ms | tab switches, card flips |
| `--motion-slow` | 300ms | route transitions, large reveals |
| `--ease-out` | `cubic-bezier(0.16, 1, 0.3, 1)` | entrances |
| `--ease-in-out` | `cubic-bezier(0.65, 0, 0.35, 1)` | state changes |

**Always** wrap motion in `@media (prefers-reduced-motion: reduce)` to
disable for users who opt out.

**Don't** use spring physics, bouncy overshoots, or parallax. Motion is
communication, not decoration.

## Component patterns

### Buttons

```css
.btn {
  font-family: var(--font-mono);
  font-size: var(--t-md);
  font-weight: 600;
  letter-spacing: 0.02em;
  text-transform: lowercase;
  padding: 12px 18px;
  border-radius: var(--r-2);
  border: 1px solid var(--rule-strong);
  background: var(--brand-faint);
  color: var(--paper);
  transition: background var(--motion-fast) var(--ease-out),
              border-color var(--motion-fast) var(--ease-out);
}
.btn:hover { background: var(--brand-faint), brighter; }
.btn:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
.btn.secondary { background: transparent; border-color: var(--rule); }
```

### Inputs

Custom only — no native `<select>` styling. Use `--rule` borders, paper
text, `--ink-2` background. Focus ring = `--brand` 2px outline.

### Cards

- Panel: 1px `--rule` border, `--ink-2` background, `--r-3` radius
- Hero card: `--r-4` radius, brand-tinted gradient, inset top highlight

### Eyebrows

Mono, 11px, uppercase, brand-teal, with a brand-teal dot prefix.

```css
.make-eyebrow::before {
  content: "";
  width: 8px; height: 8px;
  border-radius: var(--r-circle);
  background: var(--brand);
  box-shadow: 0 0 16px rgba(26, 184, 232, 0.7);
}
```

## Anti-patterns (forbidden)

- ❌ Pure black or pure white (use `--ink` / `--paper`)
- ❌ Big drop shadows on resting state (`0 25px 50px` etc.)
- ❌ `border-radius` larger than `--r-4` on small elements
- ❌ Asymmetric padding without a content reason
- ❌ Multiple accent colors (one + one signal max)
- ❌ Spring or bouncy animations
- ❌ Two display fonts on one surface
- ❌ Fractional font-sizes (no 11.5/12.5/etc — round to scale)
- ❌ Native `<select>` rendering — build custom
- ❌ Animations without `prefers-reduced-motion` guard

## Per-surface notes

### Landing (`templates/landing.html` + `static/landing.css`)
- Bold-minimalism direction with fluid `clamp()` hero type
- 5 fluid-type clauses, `prefers-reduced-motion` honored, `:focus-visible` present
- Brand mark + favicon are the only `<img>` tags — both have `width`/`height`/`decoding="async"`

### Studio (`templates/index.html` + `static/phantomline.css`)
- Precision-and-density direction; sidebar tabs + main + right rail
- Workflow grid: `align-items: stretch` so left + right panels match heights
- Tab default = "Create Video" for returning users; auto-routes to "Launch Setup" when readiness has blockers
- `body.is-returning` hides first-run hero on every visit after first

### Pillar / alternative pages
- Use `landing.css` tokens + `article.css` for typography
- Headings = `--font-sans` (IBM Plex Sans), no Cormorant Garamond

## Living document

This doc is updated as design decisions emerge. **When you make a
decision that contradicts this guide, update the guide first, then
build.** Otherwise the guide rots and the next session re-litigates
the same choices.
