"""Skill：对 skillbridge.Workspace 的最小封装（连接 / 调用 / 关闭）。"""
import os


class Skill(object):
    def __init__(self, workspace_id=None, workspace=None, timeout=30):
        self.workspace_id = workspace_id or os.environ.get("VB_WORKSPACE", "default")
        self.timeout = timeout
        self._ws = workspace

    # -- 连接 -------------------------------------------------------- #
    @property
    def ws(self):
        if self._ws is None:
            from skillbridge import Workspace
            self._ws = Workspace.open(self.workspace_id)
        return self._ws

    def call(self, fn, *args):
        return self.ws[fn](*args)

    def eval(self, fn, *args):
        """兼容别名。"""
        return self.call(fn, *args)

    def __getattr__(self, item):
        if item.startswith("_"):
            raise AttributeError(item)
        return lambda *a, **k: self.ws[item](*a)

    def close(self):
        if self._ws is not None:
            try:
                self._ws.close()
            finally:
                self._ws = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def connect(workspace_id=None, **kw):
    s = Skill(workspace_id=workspace_id, **kw)
    _ = s.ws          # 触发连接，失败立即抛出
    return s
