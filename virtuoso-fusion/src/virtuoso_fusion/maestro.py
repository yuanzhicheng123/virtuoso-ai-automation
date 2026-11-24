"""Lossless corner columns on top of the upstream Maestro result export."""
from __future__ import annotations

import csv
import io
import shlex
import shutil

from virtuoso_bridge.virtuoso.maestro.ops import MaestroOps


def _require_upstream_capabilities(ops):
    required = ('check_points', 'check_netlist_options', 'copy_point_netlist')
    missing = [name for name in required if not hasattr(ops, name)]
    if missing:
        raise ImportError(
            'Fusion 0.3 requires virtuoso-bridge-lite 672b451; missing: ' + ', '.join(missing)
            + '. pip may retain an older 1.0.0 dependency. Use a fresh environment or '
              'python -m pip install --force-reinstall --no-deps <the delivered upstream wheel>.')


_require_upstream_capabilities(MaestroOps)


class FusionMaestroOps(MaestroOps):
    def copy_point_netlist(self, source: str, destination: str) -> None:
        """Materialize a point's inputs in a new directory, excluding its log.

        Cadence can leave ``amps_evaluator.log`` dangling after a successful
        run. Only that diagnostic basename is excluded; broken input links
        still fail. A failed copy can leave a partial destination for diagnosis.
        """
        runner = self._owner.ssh_runner
        if runner is None:
            shutil.copytree(source, destination, symlinks=False,
                            ignore=shutil.ignore_patterns("amps_evaluator.log"))
            return
        command = (
            f"mkdir -- {shlex.quote(destination)} && "
            f"tar --dereference --exclude=amps_evaluator.log -cf - -C {shlex.quote(source)} . "
            f"| tar -xf - -C {shlex.quote(destination)}"
        )
        result = runner.run_command(f"bash -o pipefail -c {shlex.quote(command)}")
        if result.returncode != 0:
            raise RuntimeError(f"copy_point_netlist failed: {result.stderr.strip()}")

    def read_results(self, session: str, lib: str = "", cell: str = "",
                     history: str = "", *, include_raw: bool = False) -> dict:
        """Read results with explicit sweep-group or corner-column dimensions.

        Upstream fields retain their original values. ``detail_rows`` preserves
        test identity and every exported cell; ``corner_values`` contains raw
        strings, including empty/non-scalar cells. A Detail export's ``Point``
        identifies a sweep group, not an RDB point × corner simulation ID.
        Exports without Nominal use ``point_semantics="corner_column"``:
        upstream ``points`` identify columns, each detail row spans those
        columns with ``point=None``, and ``corner_parameters`` maps their
        names to the parameters parsed by upstream. Cross-test duplicate
        output names remain separate in ``detail_rows``.
        CSV values retain Cadence's display precision; use RDB/waveforms when
        full numeric precision or simulation completion status is needed.
        """
        result = super().read_results(session, lib=lib, cell=cell,
                                      history=history, include_raw=True)
        if not result:
            return result
        result.update(_read_corner_columns(result["raw_csv"]))
        if result["point_semantics"] == "corner_column":
            result["corner_parameters"] = {
                point["corner"]: point["parameters"].copy()
                for point in result["points"]
            }
        if not include_raw:
            result.pop("raw_csv")
        return result


def _read_corner_columns(text: str) -> dict:
    """Validate the export boundary and preserve the full Detail table."""
    base_columns = ["Test", "Output", "Nominal", "Spec", "Weight", "Pass/Fail"]
    per_corner_columns = ["Test", "Output", "Spec", "Weight", "Pass/Fail", "Min", "Max"]
    columns = None
    rows = []
    parameters = {}
    corner_columns = []
    has_point = False
    column_points = False
    reader = csv.reader(io.StringIO(text), strict=True)
    try:
        for cells in reader:
            if not any(cell.strip() for cell in cells):
                continue
            header = [cell.strip() for cell in cells]
            if columns is None:
                has_point = header[0] == "Point"
                offset = int(has_point)
                column_points = header[:7] == per_corner_columns and len(header) > 7
                if not column_points and header[offset:offset + 6] != base_columns:
                    continue
                if len(set(header)) != len(header) or any(not name for name in header):
                    raise ValueError("Detail CSV has empty or duplicate column names")
                columns = header
                corner_columns = header[7:] if column_points else ["Nominal"] + [
                    name for name in columns[offset + 6:] if name not in ("Min", "Max")
                ]
                continue
            if cells[0].strip().startswith("Parameters:"):
                parameters = {}
                for item in cells[0].split(":", 1)[1].split(","):
                    if "=" in item:
                        name, value = item.split("=", 1)
                        parameters[name.strip()] = value.strip()
                continue
            if len(cells) != len(columns):
                raise ValueError(f"Detail CSV row {reader.line_num}: expected {len(columns)} columns, got {len(cells)}")
            record = dict(zip(columns, cells))
            if has_point and not record["Point"].strip().isdigit():
                raise ValueError(f"Detail CSV row {reader.line_num}: invalid Point {record['Point']!r}")
            rows.append({
                "point": int(record["Point"]) if has_point else (None if column_points else 1),
                "test": record["Test"].strip(),
                "name": record["Output"].strip(),
                "parameters": parameters.copy(),
                "cells": cells,
                "corner_values": {name: record[name] for name in corner_columns},
            })
    except csv.Error as exc:
        raise ValueError(f"Detail CSV row {reader.line_num}: {exc}") from exc
    if columns is None:
        raise ValueError("Detail CSV is missing its results header")
    return {
        "point_semantics": "corner_column" if column_points else "sweep_group",
        "detail_columns": columns,
        "corner_columns": corner_columns,
        "detail_rows": rows,
    }
