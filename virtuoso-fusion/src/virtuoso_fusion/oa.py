"""Top-level OA geometry and explicit layer-map export on the existing client."""
from __future__ import annotations

from dataclasses import asdict
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile

from .values import to_skill


class FusionOAOps:
    def __init__(self, client):
        self._client = client
        self._layer_maps = {}

    def copy_geometry(self, src_lib, src_cell, dst_lib, dst_cell, view='layout'):
        """Append top-level figures, including labels, to a destination view.

        Hierarchical sources are rejected: this does not flatten PCells or
        instances. A failed copy may leave partial destination figures in memory;
        the returned VirtuosoResult retains the original failure and recovery data.
        """
        _check_names(src_lib, src_cell, dst_lib, dst_cell, view)
        if (src_lib, src_cell) == (dst_lib, dst_cell):
            raise ValueError('source and destination must be different cellviews')
        source = ' '.join(to_skill(v) for v in (src_lib, src_cell, view))
        destination = ' '.join(to_skill(v) for v in (dst_lib, dst_cell, view))
        code = f'''let((src dst copied sourceCount destinationCount)
  unwindProtect(
    progn(
      src = dbOpenCellViewByType({source} "maskLayout" "r")
      unless(src error("Cannot open source cellview"))
      when(src~>instances error("copy_geometry requires a source without instances"))
      sourceCount = length(src~>shapes)
      dst = dbOpenCellViewByType({destination} "maskLayout" "a")
      unless(dst error("Cannot open destination cellview"))
      copied = 0
      foreach(fig src~>shapes
        unless(dbCopyFig(fig dst) error("dbCopyFig failed"))
        copied = copied + 1
      )
      destinationCount = length(dst~>shapes)
      unless(dbSave(dst) error("Cannot save destination cellview"))
      list("copied" copied "source_shapes" sourceCount "destination_shapes" destinationCount)
    )
    progn(
      when(dst dbClose(dst))
      when(src dbClose(src))
    )
  )
)'''
        return self._client.execute_skill(code)

    def register_layer_map(self, name, factory):
        """Register a local factory returning ``(filename, mapping_text)``."""
        _check_names(name)
        if not callable(factory):
            raise TypeError('layer map factory must be callable')
        self._layer_maps[name] = factory

    def export_gds(self, lib, cell, view, gds, libdef=None, *, oa2strm=None,
                   layer_map=None, timeout=300, env=None):
        """Export through existing XStream, or an explicitly supplied oa2strm.

        ``layer_map`` is a file path or a registered factory name. Generated maps
        are retained beside the output for provenance. XStream requires a map;
        standalone oa2strm requires libdef and a new output path. ``env`` applies
        only to the standalone process and never alters the current session.
        """
        _check_names(lib, cell, view)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('timeout must be a positive finite number of seconds')
        if oa2strm is None and env is not None:
            raise ValueError('env is only supported with explicit oa2strm')
        if oa2strm is None and layer_map is None:
            raise ValueError('XStream export requires layer_map')
        output = Path(gds).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        mapping, map_name = self._resolve_layer_map(layer_map, output.parent)
        if oa2strm is None:
            raw = self._client.layout.export_gds(lib, cell, output, view=view,
                                                  stream_map=mapping, timeout=timeout)
            return {'execution_ok': raw.ok, 'ok': raw.ok, 'errors': list(raw.errors),
                    'warnings': list(raw.warnings), 'output_path': str(output),
                    'stream_map': str(mapping), 'layer_map_name': map_name,
                    'metadata': asdict(raw)}
        if libdef is None:
            raise ValueError('standalone oa2strm requires libdef')
        libdef_path = Path(libdef).expanduser().resolve(strict=True)
        if output.exists():
            raise FileExistsError(f'Use a new GDS output path: {output}')
        command = [str(oa2strm), '-lib', lib, '-cell', cell, '-view', view,
                   '-gds', str(output), '-libDefFile', str(libdef_path)]
        if mapping is not None:
            command += ['-layerMap', str(mapping)]
        completed = subprocess.run(command, env={**os.environ, **(env or {})},
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, errors='replace', timeout=timeout, check=False)
        diagnostic_prefix = r'^[ \t]*(?:\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}[ \t]+)?'
        errors = re.findall(diagnostic_prefix + r'ERROR(?:[ \t]|:).*$', completed.stdout, re.MULTILINE | re.IGNORECASE)
        warnings = re.findall(diagnostic_prefix + r'WARNING(?:[ \t]|:).*$', completed.stdout, re.MULTILINE | re.IGNORECASE)
        size = output.stat().st_size if output.is_file() else 0
        if not size:
            errors.append('oa2strm did not produce a nonempty GDS file')
        return {'execution_ok': completed.returncode == 0,
                'ok': completed.returncode == 0 and not errors, 'errors': errors,
                'warnings': warnings, 'output_path': str(output),
                'stream_map': str(mapping) if mapping is not None else None,
                'layer_map_name': map_name,
                'metadata': {'rc': completed.returncode, 'size': size,
                             'stdout': completed.stdout, 'command': command}}

    def _resolve_layer_map(self, value, output_dir):
        if value is None:
            return None, None
        if isinstance(value, str) and value in self._layer_maps:
            filename, content = self._layer_maps[value]()
            if not isinstance(filename, str) or Path(filename).name != filename or filename in ('', '.', '..'):
                raise ValueError('layer map factory must return a filename without directories')
            if not isinstance(content, str):
                raise TypeError('layer map factory content must be text')
            folder = Path(tempfile.mkdtemp(prefix='.fusion-layer-map-', dir=output_dir))
            path = folder / filename
            path.write_text(content, encoding='utf-8')
            return path, value
        return Path(value).expanduser().resolve(strict=True), None


def _check_names(*names):
    if any(not isinstance(name, str) or not name.strip() for name in names):
        raise ValueError('library, cell, view and map names must be nonempty strings')
