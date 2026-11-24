# Third-party notices

The runtime dependency and vendored MCP server/skills snapshot come from
https://github.com/Arcadia-1/virtuoso-bridge-lite at commit
`672b451416b1ef3cc560db4196365075e94c8d4e`.

The snapshot under `src/virtuoso_fusion/_upstream` is copied without source
modifications by `tools/prepare_resources.py`. The integration lives outside
that directory. Resource hashes and local registry provenance are recorded in
`src/virtuoso_fusion/_data/provenance.json`. Local registry data comes from the
user-provided engineering-source 0.8.1 repair and is not covered by the upstream
MIT license.

## virtuoso-bridge-lite license (complete)

MIT License

Copyright (c) 2025-2026 Zhishuai Zhang, Xintian Li, Nan Sun, Lu Jie

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Local phase3 integration

The physical-verification interface design is adapted from the user's local
`/home/lihong/projects/RFIC-GPT/vl/vskill/phase3` at commit
`8bfb0e8` (OA geometry copy/export, Calibre IO adaptation, report parsing and
extension registries). Fusion implements these outside the vendored upstream
namespace, with same-client OA, explicit decks, wired callbacks and scoped
acceptance. No Cadence executables, PDK decks or device models are included.
