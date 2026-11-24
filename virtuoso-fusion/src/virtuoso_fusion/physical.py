"""Explicit-deck Calibre execution with separate process and check verdicts."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
from typing import Callable


class PhysicalVerification:
    """Run isolated DRC/LVS/PEX attempts without changing source PDK decks.

    Extensions are scoped to this instance. A runner receives normalized keyword
    arguments including ``run_dir`` and returns an ``execution_ok``/``report``
    mapping. A parser receives the report Path and returns ``checks_passed``.
    Neither a completed extraction nor an LVS probe constitutes chip signoff.
    """

    def __init__(self, calibre: str = "calibre"):
        self.calibre = os.fspath(calibre)
        self._runners: dict[str, Callable] = {}
        self._parsers: dict[str, Callable] = {"drc": parse_drc, "lvs": parse_lvs}

    def register_flow(self, name: str, runner: Callable) -> None:
        _validate_extension(name, runner)
        if name in {"drc", "lvs", "pex", "rcx"}:
            raise ValueError("Built-in flows cannot be replaced")
        self._runners[name] = runner

    def register_parser(self, name: str, parser: Callable) -> None:
        _validate_extension(name, parser)
        if name == "rcx":
            name = "pex"
        self._parsers[name] = parser

    def run(self, kind: str, *, deck, work_dir, layout, primary: str,
            source=None, source_primary: str | None = None, timeout: float = 240,
            netlist: str = "net.dist") -> dict:
        """Return JSON-compatible evidence, also persisted as ``manifest.json``.

        ``execution_ok`` requires all process phases to exit zero. DRC/LVS
        ``checks_passed`` is None when reports are absent or unrecognized.
        PEX ``ok`` means output completeness, with ``checks_passed=None``.
        Only standalone, single-value SVRF IO statements are supported; options
        on report/netlist statements are retained. DRC database output is rebased.
        Known secondary output paths must be local relative literals. This is a
        top-level IO adapter for trusted decks, not a complete SVRF interpreter;
        included rules and custom embedded commands are not rewritten.
        """
        kind, deck, layout, source, root, source_primary = self._validate_inputs(
            kind, deck, layout, source, work_dir, primary, source_primary, timeout, netlist)
        root.mkdir(parents=True, exist_ok=True)
        run_dir = Path(tempfile.mkdtemp(prefix=f"physical-{kind}-", dir=root))
        result = {"kind": kind, "run_dir": str(run_dir), "execution_ok": False,
                  "checks_passed": None, "ok": False, "phase_results": [],
                  "report": None, "netlist": None, "parsed": {},
                  "hashes": {"deck_source": _sha256(deck), "layout": _sha256(layout)}}
        if source:
            result["hashes"]["source"] = _sha256(source)
        if kind in self._runners:
            result.update(self._runners[kind](kind=kind, deck=deck, run_dir=run_dir,
                          layout=layout, primary=primary, source=source,
                          source_primary=source_primary, timeout=timeout,
                          netlist=netlist, calibre=self.calibre))
            result.update(kind=kind, run_dir=str(run_dir))
        else:
            report = run_dir / ("drc.rep" if kind == "drc" else "lvs.rep")
            run_deck = run_dir / "run.svrf"
            run_deck.write_text(_prepare_deck(deck, kind, layout, primary, source,
                                             source_primary, report, run_dir / netlist))
            result.update(deck=str(run_deck), report=str(report))
            result["hashes"]["deck_run"] = _sha256(run_deck)
            if kind == "pex":
                phases = [("phdb", ["-xrc", "-phdb"]),
                          ("pdb", ["-xrc", "-pdb", "-rcc"]),
                          ("fmt", ["-xrc", "-fmt", "-all"])]
                result["netlist"] = str(run_dir / netlist)
            else:
                options = [f"-{kind}", "-hier"]
                if kind == "lvs":
                    options += ["-spice", str(run_dir / "extracted.spice")]
                phases = [(kind, options)]
            for phase, options in phases:
                outcome = _run_phase([self.calibre, *options, str(run_deck)],
                                     run_dir, phase, timeout)
                result["phase_results"].append(outcome)
                if outcome["returncode"] != 0 or outcome["timed_out"]:
                    break
            result["execution_ok"] = (len(result["phase_results"]) == len(phases)
                                      and all(p["returncode"] == 0 and not p["timed_out"]
                                              for p in result["phase_results"]))
        parser = self._parsers.get(kind)
        report_path = Path(result["report"]) if result["report"] else None
        if parser and report_path and report_path.is_file():
            result["parsed"] = parser(report_path)
            result["checks_passed"] = result["parsed"].get("checks_passed")
        if kind == "pex":
            result["parsed"].update(_inspect_netlist(Path(result["netlist"])))
            result["extraction_complete"] = result["parsed"]["complete"]
            result["checks_passed"] = None
            result["ok"] = result["execution_ok"] and result["extraction_complete"]
        else:
            result["ok"] = result["execution_ok"] and result["checks_passed"] is True
        result["status"] = ("COMPLETE" if result["ok"] else "TIMEOUT"
                            if any(p["timed_out"] for p in result["phase_results"])
                            else "FAILED" if not result["execution_ok"]
                            else "CHECKS_FAILED" if result["checks_passed"] is False
                            else "INCOMPLETE")
        result["artifacts"] = {str(p.relative_to(run_dir)): _sha256(p)
                               for p in sorted(run_dir.rglob("*")) if p.is_file()}
        (run_dir / "manifest.json").write_text(json.dumps(result, indent=2) + "\n")
        return result

    def _validate_inputs(self, kind, deck, layout, source, work_dir, primary,
                         source_primary, timeout, netlist):
        if kind == "rcx":
            kind = "pex"
        if kind not in {"drc", "lvs", "pex"} and kind not in self._runners:
            raise ValueError(f"Unknown physical flow: {kind!r}")
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        for value in (primary, source_primary or primary, netlist):
            if not isinstance(value, str) or not value or any(c in value for c in '\r\n"\\'):
                raise ValueError("Names must be nonempty and contain no quotes or newlines")
        if Path(netlist).name != netlist or netlist in {".", ".."}:
            raise ValueError("netlist must be a filename inside the new run directory")
        if kind in {"lvs", "pex"} and source is None:
            raise ValueError("source is required for LVS/PEX")
        deck, layout = Path(deck).resolve(), Path(layout).resolve()
        source = Path(source).resolve() if source is not None else None
        root = Path(work_dir).resolve()
        for path in (deck, layout, source, root):
            if path is not None and any(c in str(path) for c in '\r\n"'):
                raise ValueError("Paths must not contain quotes or newlines")
        for path in (deck, layout, source):
            if path is not None and not path.is_file():
                raise FileNotFoundError(path)
        return kind, deck, layout, source, root, source_primary or primary


def _validate_extension(name, callback):
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise ValueError("Extension name must be a lowercase identifier")
    if not callable(callback):
        raise TypeError("Extension must be callable")


def _prepare_deck(deck, kind, layout, primary, source, source_primary, report, netlist):
    settings = {"LAYOUT PATH": layout, "LAYOUT PRIMARY": primary}
    if kind == "drc":
        settings["DRC SUMMARY REPORT"] = report
    else:
        settings.update({"SOURCE PATH": source, "SOURCE PRIMARY": source_primary,
                         "LVS REPORT": report})
    if kind == "pex":
        settings["PEX NETLIST"] = netlist
    required = set(settings)
    settings["DRC RESULTS DATABASE"] = report.parent / "drc.db"
    found = dict.fromkeys(settings, 0)
    output = []
    token = r'(?P<value>"[^"\r\n]+"|[^\s"\\]+)'
    output_statement = re.compile(
        r"\s*(?P<key>DRC\s+SUMMARY\s+REPORT|LVS\s+REPORT(?!\s+(?:MAXIMUM|OPTION)\b)|"
        r"PEX\s+NETLIST(?:\s+SIMPLE)?|MASK\s+SVDB\s+DIRECTORY|"
        r"PEX\s+REPORT\s+(?:DISTRIBUTED|LUMPED))\s+", re.I)
    for line in deck.read_text().splitlines(keepends=True):
        stripped = line.lstrip()
        if re.match(r"INCLUDE\b", stripped, re.I):
            match = re.fullmatch(r"\s*INCLUDE\s+" + token + r"\s*(?://[^\n]*)?\n?", line, re.I)
            if not match:
                raise ValueError(f"Unsupported INCLUDE syntax: {line.strip()}")
            included = Path(match["value"].strip('"'))
            if not included.is_absolute():
                included = deck.parent / included
            line = line[:match.start("value")] + '"' + str(included.resolve()) + '"' + line[match.end("value"):]
        output_match = output_statement.match(line)
        if output_match and " ".join(output_match["key"].upper().split()) not in settings:
            value_match = re.match(token, line[output_match.end():])
            if not value_match:
                raise ValueError(f"Unsupported output path syntax: {line.strip()}")
            value = value_match["value"].strip('"')
            path = Path(value)
            if path.is_absolute() or ".." in path.parts or any(c in value for c in "$~\\"):
                raise ValueError(f"Unsupported output path outside this run: {line.strip()}")
        for key, value in settings.items():
            prefix = re.match(r"\s*" + key.replace(" ", r"\s+") + r"\s+", line, re.I)
            if not prefix:
                continue
            rest = line[prefix.end():]
            if key == "LVS REPORT" and re.match(r"(?:MAXIMUM|OPTION)\b", rest, re.I):
                continue
            if key == "PEX NETLIST" and re.match(r"SIMPLE\b", rest, re.I):
                continue
            value_match = re.match(token + r"(?P<tail>[^\n]*)(?:\n)?$", rest)
            if not value_match:
                raise ValueError(f"Unsupported {key} syntax: {line.strip()}")
            tail = value_match["tail"]
            if key in {"LAYOUT PATH", "LAYOUT PRIMARY", "SOURCE PATH", "SOURCE PRIMARY"}:
                if tail.strip() and not tail.lstrip().startswith("//"):
                    raise ValueError(f"Unsupported {key} syntax: {line.strip()}")
            elif "\\" in tail or '"' in tail.split("//", 1)[0]:
                raise ValueError(f"Unsupported {key} syntax: {line.strip()}")
            found[key] += 1
            start = prefix.end() + value_match.start("value")
            end = prefix.end() + value_match.end("value")
            line = line[:start] + '"' + str(value) + '"' + line[end:]
            break
        output.append(line)
    for key, count in found.items():
        if count > 1 or (key in required and count != 1):
            raise ValueError(f"Expected exactly one standalone {key} statement; found {count}")
    return "".join(output)


def _run_phase(command, run_dir, phase, timeout):
    log_path = run_dir / f"{phase}.log"
    result = {"phase": phase, "command": command, "returncode": None,
              "timed_out": False, "log": str(log_path)}
    with log_path.open("wb") as log:
        try:
            process = subprocess.Popen(command, cwd=run_dir, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            try:
                result["returncode"] = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                result["timed_out"] = True
                # Calibre may start helper children; stop this invocation's group.
                os.killpg(process.pid, signal.SIGKILL)
                result["returncode"] = process.wait()
        except OSError as error:
            result["error"] = str(error)
            log.write(str(error).encode())
    return result


def parse_drc(report: Path) -> dict:
    text = Path(report).read_text(errors="replace")
    executed = re.search(r"TOTAL DRC RuleChecks Executed:\s*(\d+)", text)
    results = re.search(r"TOTAL DRC Results Generated:\s*(\d+)", text)
    rules = {m[1]: int(m[2]) for m in re.finditer(
        r"RULECHECK\s+(\S+)\s*\.+\s*TOTAL Result Count =\s*(\d+)", text)}
    count = int(results[1]) if results else None
    checks = None if not executed or not results else int(executed[1]) > 0 and count == 0
    return {"checks_passed": checks, "rules_executed": int(executed[1]) if executed else None,
            "results": count, "violations": {name: n for name, n in rules.items() if n}}


def parse_lvs(report: Path) -> dict:
    text = Path(report).read_text(errors="replace")
    section = re.search(r"(?im)^\s*OVERALL COMPARISON RESULTS\s*$", text)
    verdict = "UNKNOWN"
    if section:
        body = text[section.end():]
        body = re.split(r"(?im)^\s*(?:\*{5,}|CELL\s+SUMMARY|LVS PARAMETERS)", body, maxsplit=1)[0]
        match = re.search(r"\b(INCORRECT|NOT COMPARED|CORRECT)\b", body)
        if match:
            verdict = match[1]
    return {"verdict": verdict, "checks_passed": None if verdict == "UNKNOWN" else verdict == "CORRECT",
            "errors": re.findall(r"(?im)^\s*Error:\s*(.+)$", text)}


def _inspect_netlist(netlist):
    pending = [netlist]
    seen = set()
    missing = []
    external = []
    root = netlist.parent.resolve()
    resistors = capacitors = 0
    while pending:
        path = pending.pop().resolve()
        if path in seen:
            continue
        seen.add(path)
        if not path.is_relative_to(root):
            external.append(str(path))
            continue
        if not path.is_file() or path.stat().st_size == 0:
            missing.append(str(path))
            continue
        with path.open(errors="replace") as stream:
            for line in stream:
                include = re.match(r"\s*\.(?:include|inc)\s+(?:\"([^\"]+)\"|'([^']+)'|(\S+))", line, re.I)
                if include:
                    included = next(value for value in include.groups() if value is not None)
                    pending.append(path.parent / included)
                elif re.match(r"\s*[rR][^\s]*\s+\S+\s+\S+\s+\S+", line):
                    resistors += 1
                elif re.match(r"\s*[cC][^\s]*\s+\S+\s+\S+\s+\S+", line):
                    capacitors += 1
    return {"complete": not missing and not external and resistors + capacitors > 0,
            "files": sorted(map(str, seen)), "missing_files": missing, "external_files": external,
            "resistors": resistors, "capacitors": capacitors}


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
