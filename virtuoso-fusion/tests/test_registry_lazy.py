"""The optional large inventory must not burden engineering-only clients."""
from concurrent.futures import ThreadPoolExecutor
import gzip
import json
from pathlib import Path
from threading import Barrier

from virtuoso_fusion import FusionClient
from virtuoso_fusion.registry import ApiRegistry


def test_client_creation_does_not_read_packaged_registry(monkeypatch):
    def reject_read(*args, **kwargs):
        raise AssertionError('default client eagerly loaded the registry')
    monkeypatch.setattr(gzip, 'open', reject_read)
    with FusionClient() as client:
        assert isinstance(client.registry, ApiRegistry)
        assert client.allow_write is False
        assert callable(client.maestro.read_results)


def test_first_concurrent_read_loads_once_and_keeps_instance_isolation(monkeypatch):
    original = gzip.open
    reads = []
    def counted(path, *args, **kwargs):
        reads.append(Path(path))
        return original(path, *args, **kwargs)
    monkeypatch.setattr(gzip, 'open', counted)
    first, second = ApiRegistry(), ApiRegistry()
    assert not reads
    start = Barrier(8)
    def get_shared(_):
        start.wait()
        return first.raw
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(get_shared, range(8)))
    assert len(reads) == 1 and all(v is values[0] for v in values)
    first.raw['_fusion_context_probe'] = {'arglist_raw':'(context_a)'}
    assert '_fusion_context_probe' not in second.raw
    assert len(reads) == 2


def test_custom_registry_still_validates_at_constructor(tmp_path):
    path = tmp_path/'bad.json'
    path.write_text(json.dumps({'f':{'params':[{'name':1}]}}))
    try:
        ApiRegistry(path)
    except ValueError:
        pass
    else:
        raise AssertionError('custom data must be validated at entry')
