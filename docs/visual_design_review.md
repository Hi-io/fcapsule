# Visual Design Review

## Scope

Refine the complete operations interface without changing collection, correlation,
retention, model selection or incident analysis. Preserve the investigation structure
validated in the previous UX pass. All interface copy remains in English.

## Baseline

Screenshots are retained locally in `local_reports/visual-refresh/`.

1. Operations: useful content, but almost every level uses the same thin border and
   weight. Native disclosure markers accumulate on the left and obscure nesting.
2. Targets: a large configuration form pushes day-to-day coverage below the fold.
   Namespace titles and their tables lack a strong parent/child distinction.
3. Settings: the asymmetric two-column arrangement gives a small retention control
   most of the space while squeezing the longer model form into the right column.

## Direction

Use a restrained workspace layout: persistent light navigation, neutral surfaces,
blue interactive accents, green brand and health indicators, and amber/red fault
states. Section identity comes from spacing, indentation and typography rather
than stacking framed panels. No decorative imagery, gradients or marketing blocks.

- Operations keeps the inline episode and its Overview/Evidence/Timeline tabs.
  An inset accent and a tinted header identify the active investigation.
- Disclosure chevrons sit on the trailing edge and rotate on expansion. Domain
  icons identify first-level evidence; indented contents distinguish deeper levels.
- Targets places source health and discovery before coverage. Namespace headings
  contain the cluster context, with application rows visibly inset beneath them.
  Connection configuration remains available in a disclosure after coverage.
- Settings uses stacked sections with a narrow heading column and a consistently
  sized form column. Numerical fields retain sensible widths.
- Responsive navigation becomes a horizontal bar; forms and investigation columns
  stack at smaller breakpoints. Wide coverage tables scroll within their section.
- Native semantics, keyboard interaction, visible focus and reduced-motion support
  remain in place. Icons supplement rather than replace meaningful labels.

Layout hierarchy is informed by [Material's canonical layouts](https://m3.material.io/foundations/layout/canonical-examples/overview),
without introducing a framework migration. Icons are vendored from
[Lucide 0.468.0](https://github.com/lucide-icons/lucide/tree/0.468.0/icons), with the
upstream license included. No CDN or external font request is needed at runtime.

## Implementation

Shared presentation tokens and component refinements live in `visual.css`, loaded
after the existing structural stylesheet. The Python server serves the local icon
allowlist. Neither package builds nor Kubernetes installations require Node.js;
Node is only used for the optional UI-helper regression tests.
