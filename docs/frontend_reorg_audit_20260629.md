# LoCoMo frontend reorg audit

## What actually drives the app
- `web/static/index.html`
- `web/static/app-state.js`
- `web/static/app-core.js`
- `web/static/app-format.js`
- `web/static/app.js`
- `web/static/styles.css`
- `web/static/boot.js`

`static/` is the legacy mirror and compatibility surface, not the primary
authoring target.

## Split files that exist but are not yet the primary authoring surface
- `static/index.html`
- `static/app-state.js`
- `static/app-core.js`
- `static/app-format.js`
- `static/app.js`
- `static/styles.css`
- `static/boot.js`
- `web/static/styles/base.css`
- `web/static/styles/components.css`
- `web/static/styles/layout.css`
- `web/static/styles/sidebar.css`
- `web/static/styles/account-topbar.css`
- `web/static/styles/views/*.css`

## Why the UI is hard to change
1. One giant JS bundle owns multiple pages.
2. One giant CSS bundle owns shared components and page overrides.
3. Several classes are defined globally and then overridden per view, sometimes more than once.
4. The report/eval/judge views all reuse path rows, task tables, logs, KPIs, and status chips, but those are not isolated as stable shared components.

## Files most worth changing first
- `web/static/app.js`
- `web/static/styles.css`
- `web/static/index.html`
- `web/static/app-core.js`
- `web/static/app-format.js`
- `web/static/app-state.js`

## Concrete hotspots
- `renderRunsSelectionState`
- `renderRunCard`
- `renderRunCompareSummary`
- `renderRunAudit`
- `runAuditMetric`
- `path-row` / `report-artifact-row`
- `run-audit-grid`
- `report-kv-grid`
- `task-table`
- `log-console`

## Recommended refactor order
1. Extract shared visual primitives from `web/static/styles.css` into a real component layer.
2. Split `runsView` and `evalView` renderers out of `web/static/app.js`.
3. Stop adding more page-specific overrides to the same selectors.
4. Only then sync to `static/*` and remove dead legacy rules.
