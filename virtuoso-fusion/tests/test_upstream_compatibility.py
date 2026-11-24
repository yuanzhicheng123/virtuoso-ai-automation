"""A same-version upstream downgrade must fail before any tool connection."""
from types import SimpleNamespace

import pytest
from virtuoso_bridge.virtuoso.maestro.ops import MaestroOps
from virtuoso_fusion import maestro


def test_pinned_upstream_has_required_result_capabilities():
    maestro._require_upstream_capabilities(MaestroOps)


@pytest.mark.parametrize('missing', ['check_points', 'check_netlist_options', 'copy_point_netlist'])
def test_old_same_version_upstream_has_actionable_error(missing):
    methods = {name: object() for name in ['check_points', 'check_netlist_options', 'copy_point_netlist'] if name != missing}
    with pytest.raises(ImportError, match='force-reinstall') as exc:
        maestro._require_upstream_capabilities(SimpleNamespace(**methods))
    assert missing in str(exc.value)
    assert '672b451' in str(exc.value)
