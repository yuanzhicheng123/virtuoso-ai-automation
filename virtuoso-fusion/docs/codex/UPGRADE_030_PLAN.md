# 0.3.0 integration and performance plan

Scope approved by user: absorb lite's three commits through 672b451, merge both Maestro CSV layouts, close a connected OA/LVS/PEX/post-layout probe, and deeply assess bloat/Python suitability for AI-assisted analog/RF design. Digital SoC is a boundary discussion, not a new implementation target.

1. Pin upstream and regenerate vendored resources from the pinned clean worktree; test both CSV formats without replacing upstream point semantics.
2. Wire existing physical API into MCP with explicit typed inputs and preserved execution/check/extraction verdicts. No new workflow framework.
3. Measure 0.2.1 vs 0.3.0 using the same independent-process benchmark; lazy-load the large packaged registry while preserving per-instance live-signature isolation and immediate custom-input validation. First generic load remains an explicit cost.
4. Build a connected named-port OA fixture, compare independent intended/native schematic and layout, run unchanged-rule DRC/LVS/xRC, then run pre/post-layout Spectre with criteria frozen before each attempt. Keep failed attempts and known warnings; no DRC-clean or chip-signoff claim without evidence.
5. Measure real RPC serial versus batched work using the same total operations on the same session; record logging and cold/warm effects separately. Avoid attributing simulation wait to Python.
6. Run complete Fusion regression, relevant new upstream regressions, installed-package tests and 220 live acceptance. Rebuild wheel from sdist, verify hashes and record exact source revisions.
7. Publish the engineering review in docs/codex/ARCHITECTURE_PERFORMANCE_REVIEW.md: language vs implementation vs workflow limits, supported workloads, missing closure mechanisms, and ranked next work. No speculative safety/concurrency framework or solver rewrite.
