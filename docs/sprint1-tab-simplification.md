# Studio nav simplification — proposal

**Status:** proposal, not built. Review before I touch the UI.

## The problem

Phantomline's pitch is *"faceless videos without the tool chaos."* The studio at `/app` has 12 top-level tabs in `templates/index.html:56-116`:

```
Workflow:    Launch Setup | Create Video | Publish | Library | Settings
Intelligence: SEO Finder | Analytics | Optimize
Advanced:    Long Story | Short Script | Narrate Text | Music & Mix | Video Studio
```

Plus an "Advanced Tools" disclosure toggle. That's the same tool-chaos the marketing copy promises to escape. The collapsible Advanced section helps but doesn't fix the fundamental shape: 12 disconnected modes.

## Proposed shape — 3 primary modes

```
[ Create ]  [ Optimize ]  [ Library ]            <-- 3 tabs, always visible
```

Everything else is contextual. No persistent secondary nav.

### What goes where

**Create** (replaces Launch Setup + Create Video + Long Story + Short Script + Narrate Text + Music & Mix + Video Studio):
- Single linear flow: Idea → Script → Voice → Music → Render → Publish-Draft.
- "Mode" picker at the top of Create switches between Long Story / Short / Narrate Existing Text / Source-Video Overlay. These were 4 tabs; now they're a single dropdown that re-shapes the same 6-step flow.
- Music & Mix and Video Studio collapse into in-flow steps, not standalone tabs. The user opens Music inside Create when they hit step 4, not as a separate destination.
- Settings becomes a gear icon top-right, not a tab.

**Optimize** (replaces SEO Finder + Optimize tab):
- Tabs inside Optimize: "By keyword" (the existing SEO Finder) | "By video" (the existing Optimize Library).
- Analytics CSV ingestion lives here under a "Channel data" disclosure — it feeds both sub-tabs.

**Library** (unchanged in spirit, but now the only destination for finished work):
- All bundles + artifacts, with filters: All / Bundles / Narrations / Renders / Drafts.
- Replaces the implicit "Library" tab + the Publish queue (which becomes a Library filter).

**Publish** disappears as a tab. Publishing is:
- A step at the end of Create's flow (post-render → schedule/upload screen)
- A row action on every Library item ("publish this bundle")

## Why this works

1. **Tabs are for parallel modes you context-switch between.** Phantomline's flows are sequential (idea → render). A tabs UI implies the user wants to bounce between Music & Mix and SEO Finder mid-task. They don't — they want to finish the script.
2. **Mobile gets fixed for free.** 12 tabs on a phone is a hamburger-menu disaster. 3 tabs fit the bottom-nav pattern users expect.
3. **Settings/Launch/Analytics are infrastructure, not workflow.** Demoting them to icons (gear, "?", chart icon) clears space.
4. **The Advanced Tools disclosure stops being needed.** Each "advanced" feature lives inside the relevant primary tab.

## Reference tab → new home

| Old tab           | New home                                          |
|-------------------|---------------------------------------------------|
| Launch Setup      | First-run banner inside Create. Dismissible.      |
| Create Video      | Create (default mode)                             |
| Publish           | End-of-Create step + Library row action           |
| Library           | Library (unchanged label)                         |
| Settings          | Gear icon (top-right)                             |
| SEO Finder        | Optimize → "By keyword" sub-tab                   |
| Analytics         | Optimize → "Channel data" disclosure              |
| Optimize          | Optimize → "By video" sub-tab                     |
| Long Story        | Create → mode dropdown                            |
| Short Script      | Create → mode dropdown                            |
| Narrate Text      | Create → mode dropdown                            |
| Music & Mix       | Create → step 4 (post-script)                     |
| Video Studio      | Create → step 5 (post-music)                      |

## Implementation sketch (next session)

1. New top nav component with 3 buttons. Old tabs hidden behind a `data-legacy-nav` flag for one release so we can revert if it tanks usage.
2. Create flow becomes a state-machine component: each step renders one of the existing panels. We're not rewriting the panels, just changing how the user *navigates* between them.
3. Optimize becomes a 2-tab sub-shell containing the existing SEO Finder and Optimize panels untouched.
4. Library gets a filter dropdown that surfaces the Publish queue as one filter option.
5. Settings/gear opens the existing Settings panel as a modal.

**Estimated effort:** 1 day for nav + Create state machine, 0.5 day for Optimize sub-tabs + Library filters, 0.5 day for Settings modal. ~2 days total.

## Risks

- **Existing users have muscle memory** for current tab positions. Mitigation: ship a one-time toast ("We simplified the studio — here's where each tool moved") on first post-deploy load.
- **The Create wizard could feel constraining** for power users who want to jump to step 4 directly. Mitigation: each step exposes a "skip to" link to the next, and the top-of-flow "Mode" dropdown can be set to "Custom" which surfaces every step as a tab inside Create.
- **Mobile bottom-nav vs current top-tab pattern** — switching nav location for mobile only could fragment the codebase. Mitigation: keep top-nav on both; the 3-item shape works fine at the top on mobile too.

## Open questions for you

1. Are you OK with **Publish disappearing as a top-level tab**? It's the most-used by paying users; demoting it might surprise them.
2. Is the **Settings-as-modal** pattern acceptable, or do you want to keep it as a screen?
3. **Launch Setup as dismissible banner** — should it also stay accessible from a "?" icon, or is it strictly first-run?

Answer these and I'll build it next session.
