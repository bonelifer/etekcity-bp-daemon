# Sample reports

These PDFs show what `etekcity-bp-report` produces for every sane combination
of `[report]` config options, so you can see what a layout looks like before
setting it up. They're all rendered from the same 18-reading, two-profile
fixture dataset (Alice trending down toward her configured goal over six
weeks; Bob with one hypertensive-crisis-range reading, to show category
shading).

Every layout/toggle sample is rendered twice, into two folders:

- **[combined/](combined/)** -- both Alice's and Bob's readings in one
  report, the household view for a device shared by more than one person.
  Since it spans more than one person, the chart gets one colored line pair
  per person with a legend, the summary prints one avg/min/max block per
  person, and the rollup layout adds a "Who" column instead of averaging
  Alice's and Bob's numbers together.
- **[single/](single/)** -- the same report, restricted to `--profile
  Alice`, the report to print and bring to a doctor's appointment. No
  legend, no "Who" column, one summary block -- just Alice's data on its
  own.

`single/` also has [full-with-goal-progress.pdf](single/full-with-goal-progress.pdf),
which only makes sense for one profile at a time (goal progress reads a
single person's configured goals), so it has no `combined/` counterpart.

## Table layout x unit x date format

| File | Layout | Unit | Date format |
|---|---|---|---|
| [combined](combined/full-mmhg-world.pdf) / [single](single/full-mmhg-world.pdf) | full | mmhg | world |
| [combined](combined/full-mmhg-us.pdf) / [single](single/full-mmhg-us.pdf) | full | mmhg | us |
| [combined](combined/full-kpa-world.pdf) / [single](single/full-kpa-world.pdf) | full | kpa | world |
| [combined](combined/full-kpa-us.pdf) / [single](single/full-kpa-us.pdf) | full | kpa | us |
| [combined](combined/compact-mmhg-world.pdf) / [single](single/compact-mmhg-world.pdf) | compact | mmhg | world |
| [combined](combined/compact-mmhg-us.pdf) / [single](single/compact-mmhg-us.pdf) | compact | mmhg | us |
| [combined](combined/compact-kpa-world.pdf) / [single](single/compact-kpa-world.pdf) | compact | kpa | world |
| [combined](combined/compact-kpa-us.pdf) / [single](single/compact-kpa-us.pdf) | compact | kpa | us |

`full` is one row per reading. `compact` is the same per-reading detail
packed into 2 side-by-side column groups, for a long history without
sprawling across as many pages.

## Rollup layout

| File | Rollup period |
|---|---|
| [combined](combined/rollup-week-mmhg.pdf) / [single](single/rollup-week-mmhg.pdf) | week |
| [combined](combined/rollup-month-mmhg.pdf) / [single](single/rollup-month-mmhg.pdf) | month |

One row per week/month instead of per reading -- avg/min/max systolic and
diastolic, average pulse, reading count, and the worst AHA category seen
that period. Unit and date format have the same effect here as above; only
`rollup_period` is varied since it's the interesting dimension for this
layout.

## Toggle demos

| File | What it shows |
|---|---|
| [combined](combined/full-minimal.pdf) / [single](single/full-minimal.pdf) | `include_address`/`include_profile`/`include_categories`/`include_summary` all `no` -- the bare-minimum table |
| [combined](combined/chart-only.pdf) / [single](single/chart-only.pdf) | `include_table = no` -- trend chart alone, no table |
| [combined](combined/table-only.pdf) / [single](single/table-only.pdf) | `include_chart = no` -- table alone, no chart |

## Per-profile personalization

| File | What it shows |
|---|---|
| [single/full-with-goal-progress.pdf](single/full-with-goal-progress.pdf) | `--profile Alice` with `include_goal_progress = yes` -- name/email/notes, restricted to just her readings, and the Goal Progress section |

## Regenerating

```bash
./scripts/generate-samples.py
```

Requires the package installed (`pip install -e .` from a checkout is
enough). Run this after any change to `report.py`'s rendering, so these
stay accurate. See the main [README](../README.md#reports) for the full
list of `[report]` options.
