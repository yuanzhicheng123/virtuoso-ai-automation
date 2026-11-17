"""vskill —— 把 SKILL 函数表变成 Python API（适配 virtuoso-bridge / skillbridge）。

    import vskill
    vskill.set_bridge(vskill.MockBridge())          # 离线
    vskill.dbCreateInst(cv, master, "M1", (0, 0), "R0")

    vskill.set_bridge(vskill.VirtuosoBridgeAdapter(host="eda-220"))
    vskill.hiGetCurrentWindow()                     # 真机
"""
from . import api
from .api import FUNCTIONS, SkillFunction, SkillUnavailable, get
from .bridge import (Bridge, MockBridge, Point, SkillHandle, SkillbridgeAdapter,
                     SubprocessSkillBridge, Symbol, VirtuosoBridgeAdapter,
                     get_bridge, set_bridge, to_skill)
from .session import Batch, Ref, Session

__version__ = "0.1.0"

__all__ = ["api", "FUNCTIONS", "SkillFunction", "SkillUnavailable", "get",
           "Bridge", "MockBridge", "SkillHandle", "SkillbridgeAdapter",
           "SubprocessSkillBridge", "Symbol", "Point", "VirtuosoBridgeAdapter",
           "get_bridge", "set_bridge", "to_skill", "Session", "Batch", "Ref",
           "__version__"]


def __getattr__(name):
    return getattr(api, name)
