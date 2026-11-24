import pytest
from virtuoso_fusion.policy import Policy


@pytest.mark.parametrize('value', ['false', 'true', 1, [], None])
def test_only_boolean_true_grants_known_write_permission(value):
    assert Policy().check('dbSave', allow_write=value)[0] is False


def test_explicit_true_grants_only_allowlisted_writes():
    assert Policy().check('dbSave', allow_write=True)[0] is True
    assert Policy().check('dbDeleteObject', allow_write=True)[0] is False
