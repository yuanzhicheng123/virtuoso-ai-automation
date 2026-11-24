# Maestro / native-netlist live acceptance — 2026-11-23

裁决：放行（限本文已执行的软件能力范围）。0.1.1 的真实 OA→原生网表→Spectre DC/AC 与 Maestro sweep/corner 结果读取已通过独立真机复验；不授予芯片全流程或全 SKILL 函数签核。

## Environment and scope

- Host: `eda-220`; final installed wheel `virtuoso-fusion 0.1.1`, Python 3.13.12, IC2310, Spectre 20.1.
- Every attempt owns a unique Xvfb display, authenticated RAMIC endpoint, Virtuoso PID and isolated OA library. Runner confirms all three owned processes stopped.
- Only analogLib primitive RC is used. This validates software/tool orchestration, not transistor performance, PDK corner accuracy, RF/PLL metrics, or tapeout readiness.
- Fixtures: `tests/live/test_maestro_signoff.py`. No production cell or PDK modifications.

## Evidence

Remote root: `/home/lihong/virtuoso-fusion-20261123/signoff/`.

| Attempt | Result | Meaning |
| --- | --- | --- |
| `maestro-attempt1/live-i0ervddr` | 1 failed, 1 passed, 37.50 s | Native export was correct; fixture expected lowercase `1k`, while Cadence normalized `1K`. Maestro actually completed. Evidence preserved. |
| `maestro-attempt2/live-qb9h8n8v` | 2 passed, 34.71 s | Corrected equivalent-unit assertion; native Spectre and Maestro numeric assertions passed. Broader result audit found dropped corner columns, so pytest alone does not grant complete API acceptance. |
| `maestro-attempt3/live-d9wg4mjp` | 2 passed, 42.06 s; exit 0 | 0.1.1 final: full corner columns numerically asserted; all four RDB points done and no result errors; four Spectre logs zero errors; MC config survives save/close/reopen. |

Each contains `result.json`, `live.xml`, `pytest.log`, authenticated call evidence, saved OA/CDF evidence, exported netlist, raw Spectre outputs, Maestro SDB/RDB, snapshots and `artifact-manifest.json`. `tools/maestro_evidence.py` records SHA-256 after process shutdown and exports relevant RDB tables without modifying the RDB. Authentication material is excluded from the manifest. Final attempt also preserves the executed `test-source.py`; it is included in the full artifact manifest.

## Actual verified behavior

1. Create RC schematic with explicit `VIN`, `OUT`, ground topology; set CDF `vdc=1`, `acm=1`, `r=1k`, `c=1p`; Check & Save returns zero schematic errors.
2. Native Cadence `export_netlist` emits `V0 (VIN 0)`, `R0 (VIN OUT)`, `C0 (OUT 0)` with expected parameter values. Standalone Spectre consumes this native DUT unchanged with only DC/AC analyses appended.
3. Native Spectre process exit 0. DC `OUT=1 V`; AC at 158.489319 MHz yields magnitude 0.708586896, matching the RC cutoff criterion (analytic cutoff 159.154943 MHz).
4. Maestro test/AC analysis/output/spec/design variable and `nominal_27` corner save, close, reopen; `c_val=1p,100f` readback succeeds. New history `Interactive.0` is checked against empty pre-run history list.
5. Actual Maestro sweep results are 158.8 MHz and 1.588 GHz; the deliberately chosen `>1 GHz` spec gives fail/pass, as expected.
6. Raw RDB proves four executed simulation points: 2 capacitor values × (Nominal + nominal_27). All four `testStatus` rows map to `done`; every result's `errorID` is NULL. Precise BW values are 158781749.06068113 Hz and 1587817490.606811 Hz for both corners.
7. Monte Carlo options `points=3`, `seed=20261123` are applied, saved, the GUI session closed and reopened, then read back through the structured API. **CONFIG_ONLY**: no Monte Carlo statistical simulation was performed; this primitive RC has no statistical model.

Every Spectre corner point reported 0 errors and 1 `SPECTRE-17101` warning: generated `checklimitdest=psf` is deprecated for future releases. The warning does not invalidate present numeric results, and is retained.

## Fixed and live-verified defect: corner information loss

Real CSV fixture: `tests/fixtures/maestro_corner_detail.csv`.

Cadence exports header `Point,Test,Output,Nominal,Spec,Weight,Pass/Fail,Min,Max,nominal_27` with a valid `nominal_27` value on every BW row. Upstream `reader/runs.py::_parse_detail_csv` only consumes the first seven columns. Returned dictionaries lose all corner values and Min/Max silently. They cannot substantiate all-corner signoff. Fusion 0.1.1 now preserves `detail_columns`, `corner_columns`, and `detail_rows` with each row’s `test` and `corner_values`, while retaining upstream fields. `point_semantics="sweep_group"` distinguishes exported sweep groups from actual corner simulation points. Attempt 3 asserts all four BW values and checks the raw RDB/status/logs independently.

Supporting read-only RDB table export with source hash: `tests/fixtures/maestro_corner_rdb_evidence.json`.

`maeGetOverallYield` returned `(nil Yield 50 PassedPoints 1 ErrorPoints 2)`. This summary is not used to claim simulation errors or complete yield signoff: raw RDB has all four points done and no result errors; `/OUT` is an invisible waveform output (type 1) with empty scalar entries, not a failed scalar measurement. The exact API aggregation semantics remain unverified. Individual intended BW/spec assertions and raw point status are the acceptance evidence.

## Remaining scope

Not executed through this Maestro fixture: PDK-based process/voltage/temperature corners, actual Monte Carlo/mismatch distribution, PSS/PNoise/PAC/PXF, transient noise, oscillator convergence/jitter, EM/IR/reliability and post-extraction simulations. Separate standalone Spectre probes cover 27 PVT combinations, three process-only Monte Carlo samples and RF analyses; see `RF_PVT_FINDINGS.md`. Those do not establish equivalent Maestro orchestration or chip-level signoff.
