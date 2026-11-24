"""Copy real point trees, including real links, through both transport paths."""
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from virtuoso_fusion.maestro import FusionMaestroOps


class ShellRunner:
    def run_command(self, command):
        return subprocess.run(command, shell=True, text=True, capture_output=True)


@pytest.fixture(params=[False, True], ids=["local", "remote-shell"])
def copier(request):
    owner = SimpleNamespace(ssh_runner=ShellRunner() if request.param else None)
    return FusionMaestroOps(owner).copy_point_netlist


@pytest.fixture
def point_tree(tmp_path):
    point = tmp_path / "point ' $(literal) `literal`"
    point.mkdir()
    actual = tmp_path / "actual"
    (actual / "netlist").mkdir(parents=True)
    (actual / "ihnl").mkdir()
    (actual / "netlist" / "input.scs").write_text("simulator lang=spectre\n")
    (actual / "ihnl" / "devices.scs").write_text("R0 (a b) resistor r=1000\n")
    (point / "netlist").symlink_to("../actual/netlist", target_is_directory=True)
    (point / "ihnl").symlink_to("../actual/ihnl", target_is_directory=True)
    (point / "keep.log").write_text("retain this diagnostic\n")
    return point, actual, tmp_path / "copy ' $(literal) `literal`"


@pytest.mark.parametrize("diagnostic", ["missing", "dangling", "regular"])
def test_copy_materializes_input_links_and_excludes_only_known_diagnostic(copier, point_tree, diagnostic):
    point, actual, destination = point_tree
    if diagnostic == "dangling":
        (point / "amps_evaluator.log").symlink_to("missing-diagnostic.log")
    elif diagnostic == "regular":
        (point / "amps_evaluator.log").write_text("not an input\n")
    copier(str(point), str(destination))
    shutil.rmtree(actual)
    assert not (destination / "netlist").is_symlink()
    assert not (destination / "ihnl").is_symlink()
    assert (destination / "netlist" / "input.scs").read_text() == "simulator lang=spectre\n"
    assert (destination / "ihnl" / "devices.scs").read_text() == "R0 (a b) resistor r=1000\n"
    assert (destination / "keep.log").read_text() == "retain this diagnostic\n"
    assert not (destination / "amps_evaluator.log").exists()
    assert not (destination / "amps_evaluator.log").is_symlink()


def test_other_dangling_inputs_fail(copier, point_tree):
    point, _, destination = point_tree
    (point / "model.scs").symlink_to("missing-input.scs")
    with pytest.raises((shutil.Error, RuntimeError)):
        copier(str(point), str(destination))


def test_existing_destination_is_not_modified(copier, point_tree):
    point, _, destination = point_tree
    destination.mkdir()
    (destination / "keep").write_text("original")
    with pytest.raises((FileExistsError, RuntimeError)):
        copier(str(point), str(destination))
    assert [p.name for p in destination.iterdir()] == ["keep"]
    assert (destination / "keep").read_text() == "original"
