"""virtuoso_bridge —— 修复版 + 通用扩展（registry 驱动）。

修复：原 0.7.0 安装只剩 dist-info、无 package 目录，import 即失败。
扩展：在 skillbridge 之上提供 registry 驱动的通用调用、签名挖掘与覆盖率统计。
"""
from .skill import Skill, connect  # noqa: F401
from .registry import ApiRegistry, DEFAULT_REGISTRY  # noqa: F401
from .generic import GenericSkill  # noqa: F401
from .mine import mine_arglists, parse_arglists  # noqa: F401

__version__ = "0.8.0"
__all__ = ["Skill", "connect", "GenericSkill", "ApiRegistry", "DEFAULT_REGISTRY",
           "mine_arglists", "parse_arglists"]
