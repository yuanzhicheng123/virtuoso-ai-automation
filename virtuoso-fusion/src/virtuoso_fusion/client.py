"""One upstream client for both engineering domains and registered calls."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from threading import RLock
import time
import uuid

from virtuoso_bridge import VirtuosoClient, VirtuosoResult

from .generic import GenericSkill
from .maestro import FusionMaestroOps
from .oa import FusionOAOps
from .registry import ApiRegistry


class FusionClient(VirtuosoClient):
    """VirtuosoClient with registry calls, serialized execution and evidence.

    Engineering facades retain upstream contracts and authorization semantics.
    ``allow_write`` applies to registered calls, not arbitrary SKILL or facades.
    """

    def __init__(self, *args, registry=None, allow_write=False, evidence_dir=None, **kwargs):
        self._fusion_lock = RLock()
        self._fusion_closed = False
        self._fusion_id = uuid.uuid4().hex
        self._fusion_sequence = 0
        super().__init__(*args, **kwargs)
        self.maestro = FusionMaestroOps(self)
        self.oa = FusionOAOps(self)
        self.registry = registry if isinstance(registry, ApiRegistry) else ApiRegistry(registry)
        self.generic = GenericSkill(self.execute_skill, registry=self.registry, allow_write=allow_write)
        self.evidence_dir = Path(evidence_dir) if evidence_dir is not None else None

    @classmethod
    def from_env(cls, *, timeout=30, log_to_ciw=True, profile=None,
                 registry=None, allow_write=False, evidence_dir=None):
        client = super().from_env(timeout=timeout, log_to_ciw=log_to_ciw, profile=profile)
        if registry is not None:
            client.registry = registry if isinstance(registry, ApiRegistry) else ApiRegistry(registry)
            client.generic.reg = client.registry
        client.allow_write = allow_write
        client.evidence_dir = Path(evidence_dir) if evidence_dir is not None else None
        return client

    @property
    def allow_write(self):
        return self.generic.allow_write

    @allow_write.setter
    def allow_write(self, value):
        self.generic.allow_write = value

    def call(self, fn, *args, timeout=None, **kwargs) -> VirtuosoResult:
        return self.generic.call(fn, *args, timeout=timeout, **kwargs)

    def execute_skill(self, skill_code, timeout=None, *, retry_connect=False) -> VirtuosoResult:
        budget = self._timeout if timeout is None else timeout
        if isinstance(budget, bool) or not isinstance(budget, (int, float)) or not math.isfinite(budget) or budget <= 0:
            raise ValueError("timeout must be a positive finite number of seconds")
        with self._fusion_lock:
            if self._fusion_closed:
                raise RuntimeError("FusionClient is closed; create a new client")
            # The same path is used by every inherited engineering facade.
            result = super().execute_skill(skill_code, timeout=timeout, retry_connect=False)
            self._record(skill_code, result)
            return result

    def close(self):
        if self._fusion_closed:
            return
        with self._fusion_lock:
            if not self._fusion_closed:
                self._fusion_closed = True
                super().close()

    def _record(self, code, result):
        if self.evidence_dir is None:
            return
        self._fusion_sequence += 1
        event = {
            "client_id": self._fusion_id,
            "sequence": self._fusion_sequence,
            "time_ns": time.time_ns(),
            "skill": code,
            "sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
            "result": result.model_dump(mode="json"),
        }
        try:
            self.evidence_dir.mkdir(parents=True, exist_ok=True)
            with (self.evidence_dir / (self._fusion_id + ".jsonl")).open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError as exc:
            # Execution has already finished; a logging failure must not look
            # like a transport failure that invites the caller to replay it.
            result.metadata["fusion_evidence_error"] = str(exc)
            result.warnings.append("Execution completed but evidence could not be saved: " + str(exc))
