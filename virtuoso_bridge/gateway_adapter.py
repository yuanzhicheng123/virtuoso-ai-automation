"""把 GenericSkill 适配成平台 gateway 期望的 session（.call(fn, *args)）。"""
from .generic import GenericSkill
from .policy import Policy
from .availability import availability


class Denied(RuntimeError):
    pass


class GatewaySession(object):
    """受策略约束的会话：读操作放行，写操作必须在白名单内且显式授权。"""

    def __init__(self, skill=None, registry=None, allow_write=False, log_path=None):
        self.gs = GenericSkill(skill=skill, registry=registry, allow_write=allow_write)
        self.policy = Policy()
        self.allow_write = allow_write
        self.log_path = log_path

    # gateway/tools 只需要这个接口
    def call(self, fn, *args, **kwargs):
        allowed, reason = self.policy.check(fn, allow_write=self.allow_write)
        if not allowed:
            self._log(fn, False, reason)
            raise Denied("%s: %s" % (reason, fn))
        try:
            v = self.gs.call(fn, *args, **kwargs)
        except Exception as e:
            self._log(fn, False, "%s: %s" % (type(e).__name__, str(e)[:80]))
            raise
        self._log(fn, True, "OK")
        return v

    def reason(self, fn):
        return availability(self.gs.reg, fn)

    def _log(self, fn, ok, msg):
        if not self.log_path:
            return
        import time
        with open(self.log_path, "a") as f:
            f.write("%.3f|%s|%s|%s\n" % (time.time(), fn, "ok" if ok else "err", msg))
