# Connected OA / parasitic / post-layout acceptance

This is a small, explicit circuit acceptance fixture, not foundry or full-chip signoff. It closes the previously missing **named circuit ports → extracted resistors/capacitors → Spectre post-layout simulation** path. Its DRC violations and tool warnings remain visible.

## Verified run (2026-11-24, installed Fusion 0.2.1)

`attempt5/live-66j9o50q` passed **1/1 complete-chain test, zero skipped, 28.01s**. Owned Virtuoso/RAMIC/Xvfb cleanup flags are all true.

| Check | Actual result |
|---|---|
| OA schematic → native Spectre | M0 `(D G S B) nch l=60n w=600n`; CDF/check-save/readback completed |
| OA layout → GDS → LVS | Four named ports; one MOS; CORRECT against independent expected CDL |
| Original DRC | 1737 checks, **52 violations; not clean** |
| xRC | 27 positive resistors, 32 positive capacitors; all three phases completed |
| Extracted R range | 0.000954997Ω–207.825Ω |
| Extracted C range | 7.34573e-20F–8.34417e-15F |
| Pre / post drain DC | 141.218554µA / 137.319693µA; relative change 2.76087% |
| 1kHz output magnitude change | 3.37413% |
| 100GHz post/pre output magnitude | 0.187084 |
| Parasitic reference readback | 0.0V in both pre and post |
| Spectre warnings | Pre: 1 native-option deprecation. Post: same 1 + 4 VSS formal/global collisions. No other coded warnings. |

The final numerical gate is PASS **within this circuit's stated criteria**. DRC and ERC manufacturing gates remain unclosed. Full machine-readable results are `connected-report.json`, `simulation-report.json`, `acceptance-criteria.json`, `result.json` and each physical sub-run's `manifest.json`. Native netlist SHA256: `40f1121b93c752f3434624f942b6fb5cb1eb55e6c578965ae8c6b7462585c672`; criteria SHA256: `3a47fe27ace32a878e7139ec6a53a100ac4af5c322036c70fdf9284a4456f69d`.

## Circuit and independence of evidence

- N65 `nch`, W=600nm, L=60nm; four independent ports `D G S B`.
- Native OA layout uses the real PDK MOS master, `M1_PO/via` gate contact and `M1_PODc/layout` body contact. Gate/drain M1 runs are approximately 100um, source M1 approximately 10um.
- The MOS D/S M1 pin boxes, G PO boxes and gate contact geometry were read from the actual PDK masters in `probe2`/`probe4` before connection coordinates were selected. OA geometry, master parameters and terminal names are read back after save/reopen.
- The reference CDL is an independently declared one-MOS, four-port topology. It is **not called a native schematic export**. Calibre compares the actual OA-exported GDS against this separate reference.
- The final test additionally builds a matching OA schematic, applies CDF W/L, checks/saves, exports the native Spectre netlist and verifies its device connectivity and dimensions. The final pre-layout deck contains that unmodified exported netlist plus the external testbench/analyses. The post-layout deck reuses the same native model-include sections and simulator options; it does not add a second umbrella TT include. The post-layout deck includes the untouched Calibre extracted netlist and its included R/C files.
- No PDK rules are changed. PhysicalVerification rewrites only the supported run input/output paths; each run retains original/run deck hashes. PDK files and private daemon credentials stay on 220.

## Frozen numerical criteria

`acceptance-criteria.json` and `test-source.py` are written before each run. Final criteria revision 4 uses 1.2V supply, 0.6V gate bias, a 1kohm drain load, TT/27C and AC 1kHz–100GHz (41 points):

1. Native source has M0 D/G/S/B, W=600nm and L=60nm; LVS is CORRECT with four named ports.
2. Three-phase xRC completes with positive R and C values, and all generated include files are present.
3. Both drain currents are between 0 and 10mA; relative pre/post DC difference <20%.
4. Low-frequency output-voltage magnitude difference <20%; 100GHz post/pre magnitude <0.99.
5. Calibre's `VSS` reference is explicitly declared global and driven to zero in the testbench. Spectre may eliminate the ideal zero-voltage VSS node from PSF, so an unloaded behavioral voltage reader `BREF v=V(VSS)+1` saves `vss_probe`; its readback minus 1V must be within 1pV of zero. Every extracted parasitic subcircuit's final reference pin is checked to connect to VSS.
6. Both decks retain the unmodified native simulator options and permit the one SPECTRE-17101 warning that `checklimitdest=psf` will be deprecated in future releases. Post-layout additionally permits four SFE-2453 warnings caused by VSS being both a global node and a formal subcircuit terminal; Spectre explicitly retains the formal terminal. Every warning is recorded and no other coded warning is accepted. No extracted or native netlist is edited to hide warnings.

The high-frequency point is a parasitic-sensitivity check, **not a declaration that this PDK/model/circuit is qualified for a 100GHz product**. Twenty-percent windows are functional fixture acceptance, not circuit specification limits. DC shifts include layout-dependent MOS parameters as well as series resistance; they must not all be attributed to wiring resistance.

## Attempts and known limitations

All attempts are under `eda-220:/home/lihong/virtuoso-fusion-20261123/signoff/connected-030/`.

- `probe1`–`probe4`: read-only inspection and fixture construction development. Contact cells have different actual OA view names; `M1_PO` uses `via`, `M1_PODc` uses `layout`. Empty-instance probe outputs were not accepted as connection evidence.
- `attempt1/live-qef_s5xb`: genuine LVS CORRECT, 27R/32C, 1737 DRC checks / 52 results. The test failed because scalar DC PSF data was incorrectly passed to the vector reader. Physical and simulation outputs remain intact.
- `simulation2`: original voltage-clamped drain-current criterion failed (100GHz post/pre=3.7502). Total terminal current includes displacement current, so expecting low-pass attenuation was an unsuitable observable. Revision 1 remains failed.
- `simulation3`: independent revision-2 loaded-output bench still failed with a floating parasitic VSS reference (ratio 1.3064).
- `simulation4`: explicit VSS reference connection produced the expected loaded-output attenuation (ratio 0.181386). This was diagnostic numerical evidence, not final full-chain acceptance.
- `attempt2/live-gbe1_5w7`: full chain rerun failed the deliberately strict zero-warning check because of four SFE-2453 name-collision warnings. Revision 3 explicitly classifies that precise warning and adds reference-node assertions before a new run.
- `attempt3/live-0p6tffcz`: new schematic setup failed before simulation: upstream CDF API rejects specifying both total width `w` and finger width `wf`. Final script uses total width only and verifies the exported value.

- `attempt4/live-tm9thh0c`: OA schematic/CDF/native export passed, but the initial pre-layout wrapper duplicated the PDK umbrella TT include already represented by native model sections. Spectre correctly rejected duplicate parameters. No PDK/model edits were made; the wrapper was fixed to use the original native include set exactly once.
- `simulation5`: strict warning gate exposed the native `checklimitdest=psf` deprecation; criterion revision 4 was recorded before another run.
- `simulation6`: scalar VSS lookup failed because Spectre elided an ideal zero node from PSF; no failed lookup was treated as a zero.
- `simulation7`: explicit unloaded VSS readback and the native pre-layout deck passed all revision-4 numerical/reference/warning checks.

This fixture has 52 DRC results, including local shape/contact/implant restrictions, density/dummy/ESD checks. It is not DRC-clean. Calibre also retains layer-map, absent predefined power-net and CONNECT/IDEAL-XCELL warnings. Power/ground ERC PATHCHK operations report missing recognized power/ground names; **ERC closure is not established**, even though the four circuit ports and electrical testbench connections are valid. Do not equate a correct LVS comparison, completed extraction or a passing software acceptance test with a manufacturing signoff.

Remaining scope: multi-device/multi-level routing, coupling/aggressor analysis, DRC-clean product layout, actual supply-net ERC, corner extraction/post-simulation, statistical mismatch/yield, EMIR, ESD, DFM and system-level specification closure.

## Reproduction

Run the installed Fusion wheel under the existing owned-session runner, with the known IC23.1 / Spectre20.1 / Calibre2022.2 executables on PATH:

```bash
VF_LIVE_CONNECTED=1 venv/bin/python source/tools/run_live.py \
  --root signoff/connected-030/final-installed \
  --cds-lib cds.lib --gui --test-timeout 800 \
  --tests source/tests/live/test_connected_signoff.py
```

The runner chooses a new isolated directory and owns/cleans its Virtuoso, RAMIC daemon and Xvfb processes. A new release must be tested using its installed wheel; earlier 0.2.1 evidence is not silently relabeled as 0.3.0 evidence.

## 0.3.0 final installed-code rerun (2026-11-24)

The full suite `signoff/upgrade-030/full4/live-geyaqkmk` uses Fusion 0.3.0 with
lite `672b451`; all 24 tests passed in 116.86s with zero skips. The runner confirms
owned Virtuoso/RAMIC/Xvfb cleanup. This includes the import compatibility guard
and the point-netlist copy fix; the original 0.2.1 attempt5 above remains historical.
The final distribution differs from this run's wheel only in README/license metadata;
all 53 installed package payload files are checked against the delivered wheel.

The connected fixture again gives four-port LVS CORRECT, 27R/32C,
DC difference 2.76087%, low-frequency amplitude difference 3.37413%, and
high-frequency post/pre amplitude 0.187084. DRC remains 1737 checks / 52 results;
ERC and product-level qualification remain unclosed. See
[final results](../../evidence/upgrade-030/final-live/result.json),
[physical report](../../evidence/upgrade-030/final-live/connected-report.json),
[simulation report](../../evidence/upgrade-030/final-live/simulation-report.json).
Use `venv-030/bin/python` for new reproductions.
