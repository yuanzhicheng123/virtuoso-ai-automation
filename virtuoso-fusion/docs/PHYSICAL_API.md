# Physical APIs — 0.2.0

The phase3 operations from RFIC-GPT/vl are integrated into the installed Fusion package. OA uses the existing authenticated client; Calibre is an independent local process. No PDK or tool installation path is baked into the public API. Use the tools on the same host as the supplied paths.

## OA geometry and GDS

```python
from virtuoso_fusion import FusionClient

with FusionClient.from_env(evidence_dir='calls') as client:
    copied = client.oa.copy_geometry('sourceLib', 'geometry', 'workLib', 'copy')
    assert copied.ok, copied.errors
    exported = client.oa.export_gds(
        'workLib', 'copy', 'layout', '/work/copy.gds',
        layer_map='/pdk/official.layermap', timeout=120)
    assert exported['ok'], exported
```

`copy_geometry` appends top-level figures (including labels), saves and closes the views, and returns the original `VirtuosoResult`. It rejects sources with instances; it is not hierarchical flattening, PCell parameter cloning or pin/net reconstruction. Source and destination must differ. A copy failure is reported without automatic replay; partial destination figures may remain in memory and require caller review. Parameterized instances remain available through the existing `client.layout` engineering API.

Default export reuses `client.layout.export_gds` and its existing XStream lifecycle. The output dictionary contains `execution_ok`, `ok`, `errors`, `warnings`, `output_path`, `stream_map`, `layer_map_name`, and original metadata. An explicit `oa2strm='/path/to/oa2strm'` plus `libdef='/work/cds.lib'` selects the standalone export route. `env={...}` can supply a run-specific OA environment for that route. Standalone output must be a new path; a timeout raises `subprocess.TimeoutExpired`, and incomplete output must not be reused as evidence. No automatic global OA_HOME shim is installed.

Named mappings are registered per client and actually passed to either exporter:

```python
client.oa.register_layer_map('example', lambda: ('example.map', 'M1 drawing 231 0\n'))
result = client.oa.export_gds('workLib', 'geometry', 'layout', '/work/mapped.gds',
                             layer_map='example')
```

The mapping above is a test example, not an N65 production map. Generated mapping files are retained beside the output. A `Path` always denotes a file; a registered string name denotes its factory. Production use must select the PDK's approved complete map; no automatic OD datatype rewrite is applied.

## Calibre

```python
from virtuoso_fusion import PhysicalVerification

pv = PhysicalVerification(calibre='/tools/calibre')
common = dict(work_dir='/work/verification', layout='/work/dut.gds', primary='dut')
drc = pv.run('drc', deck='/pdk/drc/calibre.drc', **common)
lvs = pv.run('lvs', deck='/pdk/lvs/calibre.lvs', source='/work/dut.cdl', **common)
pex = pv.run('pex', deck='/pdk/rcx/calibre.rcx', source='/work/dut.cdl',
             netlist='extracted.sp', **common)
```

Each call creates a new child directory and retains the adapted deck, process logs, report, artifact hashes and `manifest.json`. Input layout/source/deck hashes are recorded. Original PDK rules are never edited. Deck adaptation accepts standalone SVRF IO statements and resolves top-level include paths; ambiguous or missing settings raise an error instead of guessing. DRC RESULTS DATABASE is relocated when present. Known other top-level outputs (including PEX SIMPLE and SVDB) must use relative literal paths; absolute or parent-directory paths are rejected. Complex TVF/computed IO settings need a caller-supplied compatible runset. Supporting relative dependencies and output paths inside included files or embedded scripts remain the trusted runset author's responsibility; this adapter is not a complete SVRF interpreter or filesystem sandbox.

`rcx` is an alias for `pex`. PEX runs `-xrc -phdb`, then `-xrc -pdb -rcc`, then `-xrc -fmt -all`; a nonzero exit or timeout stops subsequent phases. The main `PEX NETLIST` output name is adapted; `PEX NETLIST SIMPLE` remains unchanged. Main/include netlists must be nonempty, inside this run directory, and contain at least one R or C element to establish extraction completeness. Counts do not establish numeric parasitic accuracy. `timeout` is a per-phase budget, not an overall three-phase deadline.

| Field | Meaning |
|---|---|
| `execution_ok` | All required process phases exited zero |
| `checks_passed` | DRC summary clean / overall LVS verdict; `None` for missing or unrecognized reports and for PEX |
| `extraction_complete` | PEX output and includes are present with R/C elements; not full-chip acceptance |
| `ok` | DRC/LVS execution plus explicit passing check; PEX execution plus extraction completeness |
| `status` | COMPLETE, CHECKS_FAILED, INCOMPLETE, FAILED or TIMEOUT |
| `phase_results` | Actual commands, return codes, timeout flags and log paths |

DRC exit zero with violations has `execution_ok=True, checks_passed=False, ok=False`. LVS considers the overall result section, not a child-cell CORRECT string. A successful single-device LVS or extraction does not prove complete circuit, routing, post-layout or tapeout signoff. Review retained warnings and the actual report scope.

## Extensions

Callbacks are per `PhysicalVerification` instance and are invoked by `run`:

```python
from pathlib import Path

def custom_runner(*, run_dir, **inputs):
    report = run_dir / 'check.txt'
    # Execute the actual custom tool here using normalized input paths.
    report.write_text('INCOMPLETE')
    return {'execution_ok': True, 'report': str(report)}

def custom_parser(report):
    return {'checks_passed': Path(report).read_text().strip() == 'PASS'}

pv.register_flow('custom_check', custom_runner)
pv.register_parser('custom_check', custom_parser)
```

`run` still requires explicit `deck`, `layout`, `primary`, and `work_dir`; callback keyword arguments include `kind`, absolute `deck/layout/source`, `primary/source_primary`, `run_dir`, `timeout`, `netlist`, and `calibre`. The custom runner owns its tool execution and timeout handling. Without an explicit parser pass, the custom check remains incomplete. Registering an EM/IR/ESD name does not implement that tool or grant signoff. No dynamic plugin loading framework is added.

## Discovery and CLI

```sh
virtuoso-fusion discover --deck /pdk/drc/calibre.drc
virtuoso-fusion physical drc --deck /pdk/drc/calibre.drc \
  --layout /work/dut.gds --primary dut --work-dir /work/checks
virtuoso-fusion physical pex --deck /pdk/rcx/calibre.rcx \
  --layout /work/dut.gds --primary dut --source /work/dut.cdl \
  --work-dir /work/checks --netlist extracted.sp
```

The Python `discover_tools(executables={...}, decks=[...])` and CLI report local executable/deck presence and whether license environment variables are set. They do not check out a license or claim a tool is licensed/runnable. After argument parsing, the physical CLI emits one JSON result: exit 0 for the scoped `ok`, 1 for execution/API input error, and 2 for failed or incomplete checks after successful execution. Argument parsing errors retain argparse stderr and exit 2 without a JSON result. Compose phases explicitly; no pipeline silently skips missing source netlists or treats unexecuted stages as passed.

Acceptance records are in `docs/codex/STATE.md`. Existing 0.1.1 verification evidence remains historical; new API acceptance is tracked separately.

## MCP (0.3.0)

`discover_tools` and `run_physical` expose the same local-host Python APIs through MCP (31 tools total). `run_physical` takes explicit `kind`, `deck`, `layout`, `primary`, `work_dir` and optional source/netlist/calibre/timeout arguments. Results retain `execution_ok`, `checks_passed`, `extraction_complete`, phase logs and artifact hashes in `structuredContent`. DRC violations or incomplete extraction set `isError=true`; successful PEX still has `checks_passed=null`.

This is a synchronous stdio operation: a long Calibre run also delays ping/discovery on that connection. `timeout` is per phase; three-phase PEX can approach three times that budget. MCP cancellation notifications do not cancel the process. Preserve the run directory and reconcile existing artifacts after caller timeout; do not blindly repeat. A persistent job/status/recovery service is not implemented in this release. For long optimization/physical campaigns use explicit Python/CLI job ownership; the JSON interface is not itself a workflow scheduler.
