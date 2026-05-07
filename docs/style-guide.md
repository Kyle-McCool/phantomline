# Phantomline Style Guide

**This guide is a summary of `phantomline-brand-bible-markdown.md` v1.0
(May 2026) — the Brand Bible is the source of truth.** This file
explains how the Bible's tokens are wired into the codebase. When the
Bible says one thing and this file says another, **the Bible wins** —
update this file to match.

The canonical CSS tokens live in `static/phantomline.css`'s `:root`
block and `static/landing.css`'s `:root` block (both kept in sync).

## Brand direction

**Cool dark + cyan + Geist.** Vercel-flavored industrial-precision
aesthetic. References: Linear, Vercel, Stripe docs. Tight spacing,
monochrome cool gray foundation, one accent color (cyan), no
gradients on chrome, no shadows for elevation.

## Color (Bible §02)

```
/* Brand cyan ladder (logo) */
--cyan-900   #0A7A85
--cyan-700   #0AA7B5
--cyan-500   #22E7F5  Primary — buttons, links, accents
--cyan-400   #26F3FF  Hover
--cyan-200   #BAFFFF  Ice glow

/* Cool gray scale */
--gray-950   #0B0F11  Deepest bg
--gray-900   #111619
--gray-850   #161B1F
--gray-800   #1C2227
--gray-700   #2A3137
--gray-600   #3A4249
--gray-500   #5C6B77  Muted text
--gray-300   #94A3B0  Secondary text
--gray-100   #E6EAEC  Primary text — never #FFF

/* Surfaces — 4-level via bg-color, NEVER box-shadow */
--bg-base       gray-950
--bg-surface-1  gray-900   Cards, panels, sidebar
--bg-surface-2  gray-850   Nested cards, hover states
--bg-surface-3  gray-800   Active states, dropdowns
--bg-overlay    rgba(0,0,0,0.6)  Modal backdrop only

/* Accent + state */
--accent-default  cyan-500
--accent-hover    cyan-400
--accent-muted    rgba(34, 231, 245, 0.15)
--success         #34D399
--warning         #FBBF24
--error           #F87171
```

### Rules
- Never raw hex in components — always go through a token.
- Never `#FFFFFF` for text — primary text is `#E6EAEC`.
- Never `box-shadow` for elevation — depth = bg-color step.
- Focus rings are always cyan: `outline: 2px solid var(--border-focus); outline-offset: 2px`.
- One accent color. Never introduce a second.

## Typography (Bible §01)

**Geist Sans + Geist Mono.** Self-hosted ideally
(from vercel.com/font); currently via Google Fonts CDN as a fallback
(slight loss of OpenType `tnum`/`zero` features but brand identity
preserved). To upgrade: drop Geist variable woff2 in `static/fonts/` +
replace `@import` with `@font-face`.

| Token | Size | Weight | LH | Tracking | Usage |
|---|---|---|---|---|---|
| `--text-display` | 56px | 700 | 60 | -0.02em | Landing hero only |
| `--text-h1` | 40px | 700 | 44 | -0.02em | Page titles |
| `--text-h2` | 32px | 600 | 36 | -0.015em | Section headings |
| `--text-h3` | 24px | 600 | 32 | -0.01em | Sub-section, card titles |
| `--text-h4` | 20px | 600 | 28 | -0.01em | Minor headings |
| `--text-body` | 16px | 450 | 24 | 0 | Default body |
| `--text-body-sm` | 14px | 450 | 20 | 0 | Secondary body |
| `--text-caption` | 13px | 400 | 20 | 0.01em | Captions, metadata |
| `--text-label` | 12px | 500 | 16 | 0.04em | Uppercase labels, badges |
| `--text-mono` | 14px | 400 | 20 | 0 | Code, technical (Geist Mono) |

### Rules
- **Body weight is 450** (Geist variable axis), not 400.
- **No 15px, 17px, 18px, 22px.** If it's not in the table, it doesn't exist.
- Negative letter-spacing on headings (-0.02 to -0.01em).
- Positive +0.04em on uppercase labels (the only uppercase context).
- Mono ONLY for: code, technical values, metrics, kbd shortcuts. Never body or headings.
- Self-host Geist (Google Fonts CDN strips OpenType features).

### Mobile scaling

| | Desktop | Mobile (≤767px) |
|---|---|---|
| Display | 56px | 36px |
| H1 | 40px | 28px |
| H2 | 32px | 24px |
| H3 | 24px | 20px |
| Body+below | unchanged | unchanged |

## Spacing (Bible §03)

8px base + 4px half-step.

| Token | Value | Usage |
|---|---|---|
| `--space-1` | 4px | icon-to-label, fine adjustments |
| `--space-2` | 8px | tightly related |
| `--space-3` | 12px | small internal gaps |
| `--space-4` | 16px | default component padding |
| `--space-5` | 24px | between groups |
| `--space-6` | 32px | between content blocks |
| `--space-7` | 48px | sections (mobile) |
| `--space-8` | 64px | sections (desktop, smaller) |
| `--space-9` | 96px | major sections (desktop) |
| `--space-10` | 128px | hero |

### Section spacing
- Section vertical padding: 96px desktop / 64px tablet / 48px mobile
- Content max-width: 1200px centered
- Side padding: 48 / 32 / 24px

### Golden rule: internal ≤ external
Padding inside a component must never exceed margin separating it from
its neighbors. If a card has 16px internal padding, gap between cards
must be ≥16px (24px recommended).

### Rules
- Never spacing values outside scale (no 13px, 17px, 22px).
- Never `--space-1` for non-icon adjustments.
- Never 0px gap between interactive elements (min 8px).

## Border radius (Bible §04)

| Token | Value | Usage |
|---|---|---|
| `--radius-sm` | 4px | badges, tags, small |
| `--radius-md` | 8px | buttons, inputs, cards, dropdowns |
| `--radius-lg` | 12px | modals, hero containers |

**NO PILLS, EVER.** Bible §04: pills are the #1 vibe-coded tell.
Avatars and status dots use `border-radius: 50%` (circles, not pills).

## Components (Bible §04)

### Buttons — 3 variants only

| Variant | Bg | Text | Border | Use |
|---|---|---|---|---|
| Primary | `--accent-default` | `--text-on-accent` | none | Main CTA |
| Secondary | transparent | `--text-primary` | 1px `--border-strong` | Supporting |
| Ghost | transparent | `--text-secondary` | none | Tertiary, nav |

- Padding: 12px 24px
- Radius: `--radius-md` (8px)
- Font: 14px / 500
- Transition: `all 200ms ease`
- Hover Primary: bg → `--accent-hover`
- Hover Secondary: bg → `--bg-surface-2`
- Hover Ghost: bg → `--bg-surface-1`
- Active: `transform: scale(0.98)`
- Focus: 2px cyan outline, 2px offset

### Cards
- Bg `--bg-surface-1`, border 1px `--border-default`, radius `--radius-md`, padding 24px, **NO shadow**
- Hover (interactive): border → `--border-strong`, bg → `--bg-surface-2`

### Inputs
- Bg `--bg-surface-1`, border 1px `--border-strong`, radius `--radius-md`, padding 12px 16px
- Text `--text-primary`, placeholder `--text-muted`
- Focus: border → `--accent-default` + focus ring
- Error: border → `--error`

### Badges / Tags
- Radius `--radius-sm` (4px) — NOT pill
- Padding 4px 8px
- Font 12px/500/uppercase/+0.04em
- Bg `--accent-muted` (cyan) or `--bg-surface-2` (neutral)

## Logo (Bible §05)

- **Only in nav and footer.** Never in hero.
- Nav: ghost icon + "phantomline.xyz" wordmark, left-aligned, 28px height in 60px nav
- Footer: same, in `--text-muted`, 24-28px height
- Mobile <768px: ghost icon only, drop wordmark
- Clear space: minimum height of ghost icon on all sides
- Min digital size: 24px

## Footer (Bible §06)

**Max 3 columns. 4-5 links per column. Stay 3-wide on mobile.**

Current: Product / Resources / Company. Brand block sits ABOVE the
grid (full-width) for clean column rhythm.

- No newsletter signup
- No social icon bars (text links only)
- No back-to-top
- No gradients / decorative bg

## Mobile (Bible §07)

Mobile-first CSS. Base = mobile, `min-width` queries scale up.

| Breakpoint | Width | Layout |
|---|---|---|
| Mobile | ≤767px | Single column, stacked |
| Tablet | 768-1023px | 2-col where appropriate |
| Desktop | ≥1024px | Full multi-column |

### Touch targets
- Min 44×44px tap area on every interactive
- Min 8px between adjacent tap targets
- No hover-only interactions

### Non-negotiable
- No horizontal scroll on any viewport ≥320px
- No fixed/sticky except nav bar
- No carousels
- Use `srcset`/`sizes` on all images

## Anti-patterns (forbidden)

- ❌ `#FFFFFF` for text
- ❌ `box-shadow` for elevation on resting components (modals/overlays OK)
- ❌ `border-radius: 999px` (pills) on anything
- ❌ `linear-gradient` on buttons, cards, inputs
- ❌ Border width > 1px (except focus rings)
- ❌ Spring/bouncy animations
- ❌ More than 1 primary CTA per viewport
- ❌ Icon-only button without `aria-label`
- ❌ Spacing outside scale (13/17/22px)
- ❌ Font size outside scale (15/17/18/22px)
- ❌ Two display fonts on one surface
- ❌ Multiple accent colors

## Living document

Update this file when:
- A Bible decision is implemented and the codebase converges on a token
- A new component pattern emerges
- A token's intended use changes

Always: update this file BEFORE building, never after. Otherwise the
guide rots and the next session re-litigates the same choices.

## Cross-reference

- Brand Bible (source of truth): `phantomline-brand-bible-markdown.md`
- Brand brief (audience + voice): `docs/brand-brief.md`
- Design process (Define→Build→Review→Refine loop): `docs/design-process.md`
