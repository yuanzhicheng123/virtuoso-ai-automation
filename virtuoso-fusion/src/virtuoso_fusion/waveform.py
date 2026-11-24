"""PSF MCP adapter preserving parser failures and requiring a time axis for at.

Adapted from the pinned lite MCP handler (MIT; see THIRD_PARTY_NOTICES.md).
Rendering stays compatible while the vendored snapshot remains unmodified.
"""
from ._upstream import mcp_server as server


def read_waveform(a: dict) -> dict:
    p = server._safe_path(a["path"], must_exist=True)
    wants = a.get("signals") or []
    from virtuoso_bridge.spectre.parsers import (
        parse_spectre_psf_ascii, parse_psf_ascii_directory)
    if p.is_file():
        parsed = parse_spectre_psf_ascii(p)
        if not parsed.ok:
            return server._bridge_result(parsed)
        data = parsed.data
    else:
        data = parse_psf_ascii_directory(p)
    if not data:
        return server._tool_error("No readable PSF ASCII data in: " + str(p))
    keys = sorted(data.keys())
    if wants:
        missing = [s for s in wants if s not in data]
        pick = {s: data[s] for s in wants if s in data}
        if missing:
            return server._tool_error("missing signals: %s (have %d: %s...)"
                               % (missing[:5], len(keys), keys[:10]))
    else:
        pick = {k: data[k] for k in keys[:20]}
    summary = {}
    for k, v in pick.items():
        if isinstance(v, list):
            import math
            count = 0
            minimum = maximum = None
            for x in v:
                if not isinstance(x, (int, float)) or not math.isfinite(x):
                    continue
                count += 1
                minimum = x if minimum is None else min(minimum, x)
                maximum = x if maximum is None else max(maximum, x)
            st = {"n": len(v), "numeric": count,
                  "min": minimum, "max": maximum}
            st["head"] = [x for x in v[:1000]
                           if isinstance(x, (int, float)) and x == x][:5] or v[:5]
            summary[k] = st
        else:
            summary[k] = {"scalar": v}
    txt = "signals=%d keys=%s" % (len(keys), keys[:20])
    payload = {"source": str(p), "available": len(keys),
               "keys": keys[:100], "stats": summary}
    ats = a.get("at") or []
    if ats:
        import bisect
        tvec = data.get("time", data.get("TIME", []))
        if not tvec:
            return server._tool_error("at requires a time axis in seconds; this source has none")
        rows: list[dict] = []
        for tt in list(ats)[:200]:
            try:
                f = float(tt)
            except (TypeError, ValueError):
                continue
            i = bisect.bisect_left(tvec, f)
            if i == len(tvec):
                i -= 1
            elif i > 0 and f - tvec[i - 1] <= tvec[i] - f:
                i -= 1
            row = {"t": tvec[i]}
            for k, v in pick.items():
                if isinstance(v, list) and k.lower() != "time" and i < len(v):
                    x = v[i]
                    row[k] = x
            rows.append(row)
        payload["samples"] = rows
        txt += " samples=%d" % len(rows)
    content: list = []
    vecs = {k: v for k, v in pick.items() if isinstance(v, list)}
    if vecs:
        payload["chart"] = server._append_wave_chart(content, {
            "time": data.get("time", data.get("TIME", [])),
            **{k: v for k, v in vecs.items() if k.lower() != "time"}})
    content.append({"type": "text", "text": "PSF source: %s; %s" % (p, txt)})
    r = server._base_result({"content": content, "structuredContent": payload},
                     cache_public=False)
    return r
