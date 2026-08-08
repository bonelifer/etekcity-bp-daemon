# Sample reports

These PDFs show what `etekcity-bp-report` produces for every sane combination
of `[report]` config options, so you can see what a layout looks like before
setting it up. They're all rendered from the same 18-reading, two-profile
fixture dataset (Alice trending down toward her configured goal over six
weeks; Bob with one hypertensive-crisis-range reading, to show category
shading).

## Table layout x unit x date format

| File | Layout | Unit | Date format |
|---|---|---|---|
| [full-mmhg-world.pdf](full-mmhg-world.pdf) | full | mmhg | world |
| [full-mmhg-us.pdf](full-mmhg-us.pdf) | full | mmhg | us |
| [full-kpa-world.pdf](full-kpa-world.pdf) | full | kpa | world |
| [full-kpa-us.pdf](full-kpa-us.pdf) | full | kpa | us |
| [compact-mmhg-world.pdf](compact-mmhg-world.pdf) | compact | mmhg | world |
| [compact-mmhg-us.pdf](compact-mmhg-us.pdf) | compact | mmhg | us |
| [compact-kpa-world.pdf](compact-kpa-world.pdf) | compact | kpa | world |
| [compact-kpa-us.pdf](compact-kpa-us.pdf) | compact | kpa | us |

`full` is one row per reading. `compact` is the same per-reading detail
packed into 2 side-by-side column groups, for a long history without
sprawling across as many pages.

## Rollup layout

| File | Rollup period |
|---|---|
| [rollup-week-mmhg.pdf](rollup-week-mmhg.pdf) | week |
| [rollup-month-mmhg.pdf](rollup-month-mmhg.pdf) | month |

One row per week/month instead of per reading -- avg/min/max systolic and
diastolic, average pulse, reading count, and the worst AHA category seen
that period. Unit and date format have the same effect here as above; only
`rollup_period` is varied since it's the interesting dimension for this
layout.

## Toggle demos

| File | What it shows |
|---|---|
| [full-minimal.pdf](full-minimal.pdf) | `include_address`/`include_profile`/`include_categories`/`include_summary` all `no` -- the bare-minimum table |
| [chart-only.pdf](chart-only.pdf) | `include_table = no` -- trend chart alone, no table |
| [table-only.pdf](table-only.pdf) | `include_chart = no` -- table alone, no chart |

## Per-profile personalization

| File | What it shows |
|---|---|
| [full-with-goal-progress.pdf](full-with-goal-progress.pdf) | `--profile Alice` with `include_goal_progress = yes` -- name/email/notes, restricted to just her readings, and the Goal Progress section |

## Regenerating

```bash
./scripts/generate-samples.py
```

Requires the package installed (`pip install -e .` from a checkout is
enough). Run this after any change to `report.py`'s rendering, so these
stay accurate. See the main [README](../README.md#reports) for the full
list of `[report]` options.
