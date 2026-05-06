# Frontend design skill

This is the working text of the `frontend-design` skill we apply when
building or rebuilding pages under `app/` (and any other frontend in
this repo). It's kept in-tree so the conventions outlive any one
session.

```
---
name: frontend-design
description: Create distinctive, production-grade frontend interfaces with high design quality. Use this skill when the user asks to build web components, pages, or applications. Generates creative, polished code that avoids generic AI aesthetics.
license: Complete terms in LICENSE.txt
---
```

## When to use

- Any visual rebuild of `app/` pages (clubs / club / player / dashboard).
- Any new HTML surface in this repo (e.g. a new analytics page, a
  shared component, an embedded widget).
- When the report PNGs need a redesign — the same aesthetic
  vocabulary should carry across `reports/` and `app/`.

Before reaching for the existing CSS in `_app_lib.CSS`, re-read the
"Design Thinking" section below and pick a direction. Don't copy the
last page's tokens reflexively — the point is intentionality.

## Design Thinking

Before coding, understand the context and commit to a BOLD aesthetic
direction:

- **Purpose**: What problem does this interface solve? Who uses it?
- **Tone**: Pick an extreme: brutally minimal, maximalist chaos,
  retro-futuristic, organic/natural, luxury/refined, playful/toy-like,
  editorial/magazine, brutalist/raw, art deco/geometric, soft/pastel,
  industrial/utilitarian, etc. There are so many flavors to choose
  from. Use these for inspiration but design one that is true to the
  aesthetic direction.
- **Constraints**: Technical requirements (framework, performance,
  accessibility).
- **Differentiation**: What makes this UNFORGETTABLE? What's the one
  thing someone will remember?

**CRITICAL**: Choose a clear conceptual direction and execute it with
precision. Bold maximalism and refined minimalism both work — the key
is intentionality, not intensity.

Then implement working code (HTML/CSS/JS, React, Vue, etc.) that is:

- Production-grade and functional
- Visually striking and memorable
- Cohesive with a clear aesthetic point-of-view
- Meticulously refined in every detail

## Frontend Aesthetics Guidelines

Focus on:

- **Typography**: Choose fonts that are beautiful, unique, and
  interesting. Avoid generic fonts like Arial and Inter; opt instead
  for distinctive choices that elevate the frontend's aesthetics;
  unexpected, characterful font choices. Pair a distinctive display
  font with a refined body font.
- **Color & Theme**: Commit to a cohesive aesthetic. Use CSS variables
  for consistency. Dominant colors with sharp accents outperform
  timid, evenly-distributed palettes.
- **Motion**: Use animations for effects and micro-interactions.
  Prioritize CSS-only solutions for HTML. Use Motion library for React
  when available. Focus on high-impact moments: one well-orchestrated
  page load with staggered reveals (animation-delay) creates more
  delight than scattered micro-interactions. Use scroll-triggering and
  hover states that surprise.
- **Spatial Composition**: Unexpected layouts. Asymmetry. Overlap.
  Diagonal flow. Grid-breaking elements. Generous negative space OR
  controlled density.
- **Backgrounds & Visual Details**: Create atmosphere and depth rather
  than defaulting to solid colors. Add contextual effects and textures
  that match the overall aesthetic. Apply creative forms like gradient
  meshes, noise textures, geometric patterns, layered transparencies,
  dramatic shadows, decorative borders, custom cursors, and grain
  overlays.

NEVER use generic AI-generated aesthetics like overused font families
(Inter, Roboto, Arial, system fonts), cliched color schemes
(particularly purple gradients on white backgrounds), predictable
layouts and component patterns, and cookie-cutter design that lacks
context-specific character.

Interpret creatively and make unexpected choices that feel genuinely
designed for the context. No design should be the same. Vary between
light and dark themes, different fonts, different aesthetics. NEVER
converge on common choices (Space Grotesk, for example) across
generations.

**IMPORTANT**: Match implementation complexity to the aesthetic
vision. Maximalist designs need elaborate code with extensive
animations and effects. Minimalist or refined designs need restraint,
precision, and careful attention to spacing, typography, and subtle
details. Elegance comes from executing the vision well.

Remember: extraordinary creative work is the goal. Don't hold back —
show what can truly be created when thinking outside the box and
committing fully to a distinctive vision.

## Project-specific constraints (Rainham CC)

These hard constraints survive any aesthetic choice:

- **Mobile-first**, `max-width: 540px`. The user reads everything on
  his phone. Don't desktop-ify.
- **No build step.** Pages are static HTML written by Python builders.
  Inline `<style>` is fine; web fonts via `<link>` are fine; no Tailwind,
  no React, no PostCSS.
- **No tracking / no third-party JS** beyond the font CDN
  (fonts.googleapis.com / fonts.bunny.net) and an optional analytics
  snippet if explicitly requested.
- **Self-contained HTML** for any artefact that gets shared (the
  scout-report PNG pipeline depends on a single HTML file with all
  styles inlined — see `scout.py:render_html`).
- **Tabular numerics everywhere** — cricket is a numbers game, so
  `font-variant-numeric: tabular-nums` is non-negotiable on stats.
- **Accessibility floor**: minimum 4.5:1 contrast on body text; tap
  targets ≥ 36 px high; respect `prefers-reduced-motion`.

Within those, anything goes — pick a direction and commit.
