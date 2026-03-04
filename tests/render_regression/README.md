# Render Regression Harness

This harness runs deterministic render scenarios and compares output signatures.

## Fixture Source

Default fixture directory:

`D:\processing\test_files_trimmed`

Override with env var:

`REELTUG_TEST_FIXTURE_DIR`

GitHub Actions also reads this from repository variable:

`REELTUG_TEST_FIXTURE_DIR`

Current workflow is configured for a self-hosted Windows runner with labels:

`self-hosted`, `windows`, `reeltug`

Or override with CLI:

`--fixtures-root <path>`

## Cases

Case definitions live in:

`tests/render_regression/cases.json`

## Baseline Lifecycle

Create/update baseline signatures:

```powershell
py -3.13 tests\render_regression\run_render_regression.py --create-baseline
```

Run comparison against baseline:

```powershell
py -3.13 tests\render_regression\run_render_regression.py
```

Run one case only:

```powershell
py -3.13 tests\render_regression\run_render_regression.py --case split_concat_reverse
```
