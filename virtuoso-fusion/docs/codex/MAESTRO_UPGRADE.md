# Maestro compatibility with lite 672b451

The Fusion adapter now accepts both observed Detail CSV layouts while keeping upstream result fields unchanged. This is a reader compatibility change, not a claim that every Maestro operation or Cadence version has been signed off.

| Export layout | Fusion `point_semantics` | `detail_rows[].point` | Parameters |
|---|---|---|---|
| `Point,Test,Output,Nominal,Spec,Weight,Pass/Fail,...` | `sweep_group` | Explicit CSV Point | Row's preceding `Parameters:` block |
| `Test,Output,Nominal,Spec,Weight,Pass/Fail,...` | `sweep_group` (existing contract) | 1 | Row's preceding `Parameters:` block |
| `Test,Output,Spec,Weight,Pass/Fail,Min,Max,<corner columns>` | `corner_column` | `None`: one row spans multiple columns | Top-level `corner_parameters[corner]`, taken from upstream `points` |

`detail_columns` and each row's `cells` preserve the complete results table. `corner_columns` preserves column order; `corner_values` preserves cell strings, including blank waveform cells. No interpolation or conversion to full-precision simulation values takes place. The new column-oriented format keeps upstream `points` and their parameters unchanged; its integer point numbers are export-column ordinals, not proven native RDB point IDs. A row's `parameters` stays empty for this format, because different columns can have different parameters.

Two tests with the same output name retain separate `detail_rows` entries with their test names. Upstream's name-keyed `points[].outputs` can still overwrite same-named outputs across tests. Consumers requiring all tests must use `detail_rows`, not assume the upstream dictionary is lossless. This adapter does not replace that upstream compatibility API.

The adapter inherits `check_points` and `check_netlist_options` from the pinned upstream facade, including the new point-log/project-directory/RDB resolution logic. `read_results` adds the lossless table fields above. `copy_point_netlist` has the narrowly scoped compatibility override described below. Simulation status must be assessed from point logs (`check_points`) and relevant numeric/design criteria; an exported value, blank waveform cell, overall GUI status, or successful parser run alone is not signoff.

## Fixture provenance and validation

- `tests/fixtures/maestro_corner_detail.csv`: existing real 220 export, including two sweep groups, Nominal and nominal_27 columns. Existing RDB comparison checks its displayed scalar values.
- `tests/fixtures/maestro_ic23_corner_columns.csv`: exact `IC23_CSV` regression sample from lite commit `672b451416b1ef3cc560db4196365075e94c8d4e`, `tests/test_maestro_ic23_results.py`. Upstream describes this as the layout seen live on IC23.1; this copied fixture is not a new Fusion hardware capture.
- Two new format tests failed first with `Detail CSV is missing its results header`, then passed after the adapter change. Further tests cover malformed row width/duplicate corner names and preservation of cross-test same-name outputs.
- Focused regression covers Fusion `test_maestro_corners.py`, `test_client.py`, and upstream `test_maestro_ic23_results.py`, `test_maestro_read_results.py`, `test_maestro_ops.py`: **58 passed** against the pinned 672b451 source.

Only the shown schema variants are recognized. Unknown headers, ambiguous duplicate/empty column names, incorrect data widths, and invalid Point cells raise `ValueError`; this prevents silent interpretation of a different Cadence export. No claim is made for arbitrary localized/reordered formats, GUI fallback reliability, statistical yield, or full Maestro hardware acceptance from these offline tests.

## Real point-directory copy fix

The 220 upgrade run `full2/live-e4_g08nk` completed four RC simulation points but failed while copying a point directory: Cadence left `amps_evaluator.log` as a dangling diagnostic symlink. Upstream `shutil.copytree(..., symlinks=False)` attempted to follow it. The failed run is retained as evidence; successful simulation and successful standalone-netlist preparation remain distinct gates.

Fusion excludes only the exact diagnostic basename `amps_evaluator.log`, whether regular or dangling, when copying. It still follows valid `netlist` and `ihnl` links to materialize portable inputs. Other dangling inputs remain errors. A destination must be new; existing contents are never merged or overwritten. On failure a partial destination may remain for diagnosis, and callers must choose a fresh destination for a retry.

Local copies use standard-library `copytree` with that one ignore pattern. Remote copies use the existing runner to execute Bash with `pipefail`, an exclusive `mkdir`, and a dereferencing tar stream with the same exclusion. Paths are shell-quoted. The remote path requires Bash and tar on the Virtuoso Linux host; this does not add a Python package dependency or a copy framework.

`tests/test_maestro_copy.py` exercises real temporary directories and symlinks for both local and remote paths; the latter executes the actual shell command locally instead of mocking copy behavior. Cases include missing/dangling/regular diagnostic files, real `netlist`/`ihnl` inputs surviving removal of their original targets, preservation of other logs, rejection of unrelated dangling inputs, existing-target protection, and shell metacharacters in pathnames. Five cases failed against inherited upstream behavior; all ten pass after the fix. Together with the fifteen CSV tests: **25 passed**. Real 220 acceptance of the rebuilt package is recorded separately by the release runner.
