# Physical API integration plan — 0.2.0

Goal: integrate the useful phase3 interfaces from RFIC-GPT/vl commit 8bfb0e8 into the installed Fusion library, repair observed integration gaps, and validate on 220.

Architecture: FusionClient.oa shares the authenticated client and existing XStream exporter. PhysicalVerification is a local Calibre runner with explicit PDK decks and fresh run directories. Per-instance registries invoke custom runners, parsers and layer-map factories. No second session service, global PDK mutation or automatic chip-signoff claim.

## Implementation and acceptance

1. `physical.py` + `test_physical.py`: failing cases for DRC violations, missing reports, overall LVS verdict, actual custom callbacks, PEX phase order/artifacts, timeout and aliases; implement and pass with real temporary subprocesses. Deck transformations affect IO settings and include resolution only; preserve originals and record hashes.
2. `oa.py` + `test_oa.py`: same-client top-level geometry copy, named map passed to actual exporter, explicit standalone oa2strm compatibility. Test failures before implementation. Reject hierarchical copy rather than silently discarding instances. Uniform export dict; original errors/warnings retained.
3. `environment.py`, CLI and package/client wiring: local discovery reports presence only; `physical` CLI distinguishes execution, failed checks and unknown acceptance. Explicit PDK inputs. Public symbols and version 0.2.0. CLI failure first, then real temporary executable tests.
4. Installed 220 live test: new isolated N65 OA library; copy and reopen simple geometry; named-map GDS export; 600nm/60nm nch fixture exported via official PDK map; DRC retains violations, single-device LVS matches, three-stage PEX yields actual capacitances. No production files changed. Separate process success from checks_passed and full_chip_signoff.
5. Full offline regression, fresh wheel installation, CLI/import/resource checks, source distribution rebuild, independent review. Update docs/codex/STATE.md and scoped capability matrix; preserve old releases and all live attempts.

Tech stack: existing Python/pytest/setuptools, fixed upstream Virtuoso bridge, RAMIC/XStream, installed Calibre. No new runtime dependencies.
