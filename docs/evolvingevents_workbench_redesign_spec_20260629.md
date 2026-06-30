# EvolvingEvents workbench redesign spec

## Goal

Redesign `EvolvingEvents 评测` into a lightweight benchmark console in the same product family as LoCoMo, but with fewer modules, less visual stacking, and a clearer single-task workflow.

This is not a marketing page, not a chat homepage, and not a BI dashboard.

Primary user flow:

1. Configure dataset
2. Validate dataset
3. Choose questions / timeline rows
4. Start evaluation
5. Monitor run state
6. Inspect recent results

The page should only keep the modules needed for that flow.

## Visual direction

- Desktop width: about 1440px
- Background: `#F7F5F0`
- Panel background: `#FFFFFF`
- Border: `#E5DED2`
- Primary text: `#111827`
- Secondary text: `#6B7280`
- Muted text: `#9CA3AF`
- Accent blue: `#2563EB`
- Success: `#16A34A`
- Warning: `#D97706`
- Error: `#DC2626`
- Radius: `8px`
- Module gap: `16px`
- Button height: `36px`
- Input/select height: `40px`
- Table row height: `44px`
- No hero
- No illustration
- No gradient
- No purple-blue AI styling
- No large KPI cards
- No card-inside-card composition
- No deep shadow

## Final page structure

The page should be reduced to exactly these six modules:

1. Compact header
2. Run status bar
3. Dataset configuration
4. Run controls
5. Questions and timeline preview
6. Right summary column plus bottom log console

High-level layout:

```text
Header
Run Status Bar
Main Grid
  Left 68%
    Dataset configuration
    Run controls
    Questions and timeline preview
  Right 32%
    Dataset summary
    Run configuration
    Run status
    Recent results
Bottom log console
```

## Existing structure to remove

The current page is visually and structurally overloaded because it composes multiple generic shells and panel systems at once.

The following wrappers should not survive into the redesign as layout primitives:

- `benchmark-console-shell`
- `generic-dataset-shell`
- `dataset-workbench`
- `workflow-stepper`
- `benchmark-stepper`
- `dataset-workflow-stepper`
- `benchmark-status-bar`
- `generic-benchmark-grid`
- `generic-benchmark-bottom`
- `workbench-subsection`
- `section-panel`
- generic `panel` stacking for every subsection

These can remain temporarily during migration, but the target structure should not depend on them.

## Required deletions

Delete or fully suppress these blocks from the EvolvingEvents page:

- workflow stepper
- KPI strip inside the config section (`#evolvingEventsKpis`)
- startup checklist / readiness panel
- history task panel
- repeated subtitle paragraphs that explain obvious actions
- empty-state paragraphs that consume vertical space without helping the next action

## Required keeps

Keep these elements and map them into the new structure:

- `#evolvingEventsData`
- `#evolvingEventsCount`
- `#evolvingEventsMode`
- `.generic-validate[data-benchmark="evolvingevents"]`
- `.generic-run-adapter[data-benchmark="evolvingevents"]`
- `.generic-use-formal-preset[data-benchmark="evolvingevents"]`
- `.generic-use-full-count[data-benchmark="evolvingevents"]`
- `.generic-preview[data-benchmark="evolvingevents"]`
- `.generic-clear-selection[data-benchmark="evolvingevents"]`
- `#evolvingEventsPreview`
- `#evolvingEventsProgressBar`
- `#evolvingEventsProgressText`
- `#evolvingeventsSelectedText`
- `#evolvingEventsRunResult`
- `#evolvingEventsLogBox`

## DOM restructuring target

Target DOM structure:

```html
<section id="evolvingEventsView" class="view-panel ee-page">
  <header class="ee-header">...</header>
  <section class="ee-status-bar">...</section>

  <section class="ee-main-grid">
    <div class="ee-main">
      <section class="ee-panel ee-config">...</section>
      <section class="ee-panel ee-actions">...</section>
      <section class="ee-panel ee-preview">...</section>
    </div>

    <aside class="ee-side">
      <section class="ee-panel ee-dataset-summary">...</section>
      <section class="ee-panel ee-run-config">...</section>
      <section class="ee-panel ee-run-status">...</section>
      <section class="ee-panel ee-recent-result">...</section>
    </aside>
  </section>

  <section class="ee-panel ee-log">...</section>
</section>
```

## Mapping from current HTML to target modules

### 1. Header

Current source:

- `.eval-console-topbar.benchmark-topbar.page-header`

Target:

- `ee-header`

Keep:

- page title
- page subtitle
- actions: sample data, validate, start evaluation, view results

Remove:

- oversized console framing
- extra top spacing inherited from generic shells

### 2. Run status bar

Current source:

- `.status-bar.benchmark-status-bar.run-status-bar`

Target:

- `ee-status-bar`

Keep only 4 items:

- current dataset
- current scope
- current progress
- run state

Remove secondary explanatory lines that duplicate the same status.

### 3. Dataset configuration

Current source:

- `.config-panel`

Target:

- `ee-panel.ee-config`

Keep:

- dataset path input
- validate button
- count input
- mode select
- optional split/subset control if the data model supports it

Move out:

- question search
- KPI block
- long guidance text

### 4. Run controls

Current source:

- action row inside `.live-run-panel`

Target:

- `ee-panel.ee-actions`

This should become a dedicated compact control strip.

Keep:

- start evaluation
- formal 20
- full set
- load questions
- clear selection

Do not keep this mixed with progress messaging and preview copy.

### 5. Questions and timeline preview

Current source:

- preview area inside `.live-run-panel`

Target:

- `ee-panel.ee-preview`

This is the primary left-column visual focus.

Turn the current generic preview box into a fixed-height table or list surface.

Expected columns:

- checkbox
- question id
- timeline summary
- question
- event time
- status

### 6. Right summary column

Current source:

- `.result-summary-panel`
- `.readiness-panel`

Target:

- `ee-dataset-summary`
- `ee-run-config`
- `ee-run-status`
- `ee-recent-result`

The current readiness panel should not survive as an independent panel. Any truly necessary status should be folded into run configuration or run status.

### 7. Bottom log console

Current source:

- `.log-panel`

Target:

- `ee-panel.ee-log`

Keep:

- fixed-height console
- copy / clear actions

Remove:

- history task block above it

## Content rules

Keep the page operational and information-dense.

Preserve:

- current dataset
- current scope
- progress
- run state
- path
- count
- mode
- preview rows
- recent result summary
- run logs

Delete or weaken:

- long descriptive paragraphs
- repeated headings
- large numbers without action value
- decorative empty states
- generic platform explanations unrelated to the next user action

## CSS strategy

Do not keep extending the current generic benchmark layer for this page.

Instead, create a local namespace for EvolvingEvents:

- `.ee-page`
- `.ee-header`
- `.ee-status-bar`
- `.ee-main-grid`
- `.ee-main`
- `.ee-side`
- `.ee-panel`
- `.ee-config`
- `.ee-actions`
- `.ee-preview`
- `.ee-log`

This avoids style coupling with:

- HotpotQA
- LongMemEval
- TauBench
- generic benchmark surfaces

## Minimal implementation order

1. Restructure the EvolvingEvents HTML into the final six modules
2. Delete workflow, KPI, readiness, and history sections
3. Add local `ee-*` classes
4. Write a local CSS layer for `ee-*`
5. Reconnect existing JS hooks to the new containers without changing task logic

Do not start with CSS patching alone. The main problem is structural complexity, not color tuning.

## Acceptance checklist

The redesign is successful only if:

1. The first screen shows header, run status bar, dataset config, run controls, preview, and right summaries
2. Questions and timeline preview is the main left-column visual focus
3. The right column is clearly secondary
4. The page no longer depends on workflow cards or KPI cards for hierarchy
5. The bottom log console has fixed height and never stretches the page
6. The result feels like a purpose-built benchmark console, not a generic admin form
7. The page still belongs to the same product family as LoCoMo, but is visibly lighter and less stacked
