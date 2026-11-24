# Physical API and external Calibre acceptance — 2026-11-23

> 本页记录0.1.1历史验收。0.2.0已新增正式物理验证API；当前接口能力和验收以 [STATE.md](STATE.md) 为准，历史测试结论不扩展为芯片签核。
裁决：修改后放行。Layout/OA/PCell/GDS 的下列已执行接口可按限定范围使用；芯片工艺签核不放行：真实 DRC 有 29 个结果，LVS/PEX 仅覆盖浮空单晶体管测试单元，且库未提供 DRC/LVS/PEX 工程接口。

## Accepted native-library scope

220 root: `/home/lihong/virtuoso-fusion-20261123/signoff`.
Current controlled-size run: `physical-layout-attempt3/live-qdxnn3wg`.
`tests/live/test_layout_signoff.py`: **4 passed in 4.57 s**, installed wheel in authenticated private RAMIC/GUI session; runner reports owned Virtuoso, daemon and Xvfb stopped.

- `library.create(..., technology_library="tsmcN65")`: independent OA library attached to real N65 technology.
- `layout.create`/`layout.modify`: M1 rectangle grouped, rotated R90 and translated; saved, closed and reopened. Exact final bounding box `(4, 6)-(5, 8)` verified with library geometry reader.
- `layout_create_param_inst`: real `tsmcN65/nch/layout` PCell instantiated and saved. `layout.modify` with native `dbReplaceProp` sets `l=60n`, `w=wf=600n`. Reopened master parameters were read, and **independent Calibre extraction verifies actual geometry at L=6e-08, W=6e-07**, not merely instance property text.
- `layout.export_gds`: current-run XStream export for both geometry and transistor, 0 errors each; one `XSTRM-333` warning each because the N65 layer map has no coloring entry. Fresh files are 2048 bytes each. Binary GDS records are validated through ENDLIB, with only legal zero padding thereafter. Top cells and mapped layers are checked; transistor uses layers 6,17,26,30,31,63,75,127.

Retained attempts: attempt1 had three test-harness errors (parameter db objects needed name/value decoding; ENDLIB zero padding was rejected). Library generation/export succeeded. Attempt2 passed all 4 tests with default PCell parameters. Attempt3 adds controlled size and is the acceptance evidence. No source library/PDK was modified.

## Real foundry-deck execution

`tools/physical_calibre.py` is an **external acceptance runner**, not a packaged library capability. It derives run-local deck copies by substituting only top/input paths and resolving relative include paths to read-only originals. No rules, define switches, exclusions or waivers are altered. Original deck hashes and run-deck hashes are in each manifest. Calibre 2022.2 successfully checked out DRC/LVS/xRC licenses. Exit code 0 is recorded as `EXECUTED_REVIEW_REPORTS`, never automatically as signoff.

| Stage | Fresh attempt | Actual result | Acceptance limit |
|---|---|---|---|
| DRC | `physical-drc-uw_yvs9s` | 1737 rules, **29 results**, exit 0 | **FAIL** for DRC-clean signoff. Full-chip rule switches retained. |
| LVS | `physical-lvs-bdbiw06_` | `lvs.rep`: **CORRECT**; 1 four-pin MN instance / 4 nets / 0 ports on each side; extracted 600nm/60nm device | PASS only as a one-device extraction/comparison probe. Warning: ambiguity points resolved arbitrarily. No top-port, routing, hierarchy or circuit-equivalence signoff. |
| ERC | same LVS attempt | 6 checks, 0 results | Only this isolated fixture and deck's enabled checks. |
| PEX | `physical-pex-9rmgw5ev` | PHDB → PDB `-rcc` → formatter `-all`, all exit 0; nonempty netlist, one 600nm/60nm device, 6 positive capacitors | Extraction probe passes; no resistor survives in this small fixture. RC-network resistance accuracy, port mapping and post-layout simulation are **NOT VERIFIED**. |

DRC results: DOD.R.1=1, PO.DN.1L=1, DPO.R.1=1, M9.DN.1L=1, CSR.R.1.NPi=1, CSR.R.1.COi=6, CSR.R.1.M1i=2, CSR.R.1.M1_real=2, CSR.R.1.ODi=1, CSR.R.1.POi=1, DM1.R.1 through DM9.R.1=1 each, LUP.6=2, ESD.WARN.1=1. These are retained violations, not waived as a passing chip.

PEX output `net.dist` contains ground capacitors 0.00805588 fF, 0.00805588 fF, 0.0294304 fF. `net.dist.NCH_PROBE.pxi` contains coupling capacitors 0.053972 fF, 0.0599803 fF, 0.0599803 fF. `net.dist.pex` is an included metadata file. PDB reports 8 warnings / 0 errors; formatter 1 warning / 0 errors. Warnings include ignored nplug/pplug contact CONNECT statements, absent excluded power net names in this floating fixture, and PEX IDEAL XCELL applying only to gate-level extraction. PHDB also retains layer-map warnings. These are not silently suppressed or adjudicated as foundry signoff.

The independently intended reference CDL is generated from 600nm/60nm test parameters before extraction, not copied from extracted output. Floating drain/source ambiguity and absent ports make this a deliberately bounded tool probe.

## Reproduction

Run from the 220 environment (fresh roots are required):

```sh
venv/bin/python source/tools/run_live.py --root signoff/physical-layout-new --cds-lib cds.lib --gui --tests source/tests/live/test_layout_signoff.py --test-timeout 360
venv/bin/python source/tools/physical_calibre.py --root signoff --gds <new-live-run>/nch_probe.gds --cell nch_probe --mode drc
venv/bin/python source/tools/physical_calibre.py --root signoff --gds <new-live-run>/nch_probe.gds --cell nch_probe --source <new-live-run>/nch_probe.cdl --mode lvs
venv/bin/python source/tools/physical_calibre.py --root signoff --gds <new-live-run>/nch_probe.gds --cell nch_probe --source <new-live-run>/nch_probe.cdl --mode pex
```

Exact executable command, GDS/source/deck SHA-256, return codes and raw artifact hashes are in `manifest.json`; GUI identity and acceptance are in `result.json`, `live.xml`, `calls/*.jsonl`, geometry/PCell readbacks and XStream logs. Source PDK decks remain on 220 and should not be bundled in a public library release.

## Remaining full-flow gaps

- No packaged DRC/LVS/ERC/PEX runset interface, structured report adjudication or PDK option contract; external Calibre executability does not close this gap.
- Typed `layout_create_param_inst` exposes no parameter list. Controlled sizing here uses explicitly supplied native SKILL in the batch editor.
- No proven connected transistor layout with named ports, complete OA schematic ↔ layout equivalence, hierarchy, contact/well/tap construction or full-chip DRC-clean target.
- No signed-off PEX resistance network or extracted-view binding back to ADE, no pre/post-layout correlation or multicorner post-layout result.
- No verified antenna closure, density/fill insertion, EM/IR, latch-up/ESD signoff, RF/EM extraction, package/IO signoff or foundry tapeout checks.
- Only the exercised IC23.1/N65/Calibre2022.2 combination is evidenced. Broad symbol registration and arbitrary SKILL transport do not establish support for every Virtuoso function, GUI context, product option or license.

Next acceptance requires a deliberately designed connected multi-terminal layout and matching OA schematic, independent geometric/device expectations, an approved process/deck option contract, DRC violation review, LVS with named ports and no unresolved mismatches, and nontrivial extracted R/C followed by post-layout simulation. A user-supplied chip target and tapeout criteria are needed for actual chip signoff.
