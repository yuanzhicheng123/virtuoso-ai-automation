"""Registry-aware Python engineering APIs for Cadence Virtuoso."""
from virtuoso_bridge import (
    ExecutionStatus,
    SpectrePool,
    SpectreSimulator,
    VirtuosoResult,
    SimulationResult,
)

from .client import FusionClient
from .generic import GenericSkill
from .registry import ApiRegistry
from .values import Symbol
from .physical import PhysicalVerification
from .environment import discover_tools

__version__ = "0.3.2"
__all__ = [
    "FusionClient", "GenericSkill", "ApiRegistry", "Symbol",
    "ExecutionStatus", "VirtuosoResult", "SimulationResult",
    "SpectreSimulator", "SpectrePool", "PhysicalVerification", "discover_tools",
]
