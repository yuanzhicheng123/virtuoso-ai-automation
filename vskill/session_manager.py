"""SessionManager：常驻会话的串行队列、对象锁、请求 ID、故障恢复。

位于 gateway 之下（gateway 不变）。只负责"怎么安全地发一条 SKILL 请求"。
"""
import itertools
import threading
import time
from dataclasses import dataclass, field

FREEZE = "FROZEN"


class Busy(RuntimeError):
    """另一个进程已持有会话独占权。"""


class StaleHandle(RuntimeError):
    """跨 epoch 复用对象句柄时抛出。"""


class Handle(object):
    """带 epoch 的对象句柄：epoch 不匹配就拒绝，不做隐式回读。"""
    __slots__ = ("obj", "epoch")

    def __init__(self, obj, epoch):
        self.obj = obj
        self.epoch = epoch

    def __repr__(self):
        return "<Handle epoch=%s %r>" % (self.epoch, self.obj)


@dataclass
class ReqLog:
    rid: int
    epoch: int
    fn: str
    ok: bool
    ms: float
    error: str = ""


class SingleEntry(object):
    """跨进程独占：flock 当前会话锁文件；第二个入口直接 BUSY。"""

    def __init__(self, path):
        self.path = path
        self.fh = None

    def acquire(self):
        import fcntl
        self.fh = open(self.path, "a+")
        try:
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, OSError):
            self.fh.close()
            self.fh = None
            raise Busy("BUSY: 另一个入口已持有 %s" % self.path)
        return self

    def release(self):
        import fcntl
        if self.fh:
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
            self.fh.close()
            self.fh = None


class SessionManager:
    def __init__(self, connect, timeout=10.0, log_path=None):
        self._connect = connect
        self.timeout = timeout
        self.log_path = log_path
        self._lock = threading.Lock()          # 会话级串行
        self._obj_locks = {}
        self._rid = itertools.count(1)
        self.epoch = 1
        self.state = "HEALTHY"
        self.ws = connect()
        self.log = []
        self._fh = open(log_path, "a", encoding="utf-8") if log_path else None

    # -- 生命周期 --------------------------------------------------------- #
    def _new_ws(self):
        try:
            self.ws.close()
        except Exception:                       # noqa: BLE001
            pass
        self.ws = self._connect()
        self.epoch += 1
        self.state = "HEALTHY"

    def reconnect(self, verify="plus(1 1)"):
        """恢复通信并核对服务可用；不重放任何写操作。"""
        self._new_ws()
        try:
            v = self.ws["car"]([1, 2])
            ok = int(v) == 1
        except Exception as exc:                # noqa: BLE001
            ok = False
            v = f"{type(exc).__name__}: {exc}"
        return {"epoch": self.epoch, "state": self.state, "probe": str(v), "ok": ok}

    def freeze(self, reason):
        self.state = FREEZE
        self._record("FREEZE", False, 0.0, reason)

    def unfreeze(self):
        self.state = "HEALTHY"

    # -- 调用 ------------------------------------------------------------- #
    def _record(self, fn, ok, ms, err="", rid=None, epoch=None):
        e = ReqLog(rid or 0, epoch or self.epoch, fn, ok, round(ms, 1), err[:160])
        self.log.append(e)
        if self._fh:
            self._fh.write(f"{time.time():.3f}|{e.epoch}|{e.rid}|{e.fn}|"
                           f"{'ok' if ok else 'err'}|{e.ms}|{e.error}\n")
            self._fh.flush()

    def call(self, fn, *args, timeout=None):
        rid = next(self._rid)
        t0 = time.time()
        args = tuple(self._unwrap(a) for a in args)
        with self._lock:
            if self.state == FREEZE:
                self._record(fn, False, 0, "FROZEN", rid)
                raise RuntimeError("会话已冻结：先 reconnect 并核对状态")
            try:
                if timeout is None:
                    v = self.ws[fn](*args)
                else:
                    v = self._call_with_timeout(fn, args, timeout)
                self._record(fn, True, (time.time() - t0) * 1000, "", rid)
                return v
            except TimeoutError:
                self.freeze("timeout")
                self._record(fn, False, (time.time() - t0) * 1000, "TIMEOUT", rid)
                raise
            except Exception as exc:            # noqa: BLE001
                self._record(fn, False, (time.time() - t0) * 1000,
                             f"{type(exc).__name__}: {exc}", rid)
                raise

    def guard(self, obj):
        """把对象包成带 epoch 的句柄。"""
        return Handle(obj, self.epoch)

    def _unwrap(self, a):
        if isinstance(a, Handle):
            if a.epoch != self.epoch:
                raise StaleHandle(
                    "句柄来自 epoch %s，当前 epoch %s；请按库路径/Cell/View 重新定位"
                    % (a.epoch, self.epoch))
            return a.obj
        return a

    def _call_with_timeout(self, fn, args, timeout):
        box = {}

        def worker():
            try:
                box["v"] = self.ws[fn](*args)
            except Exception as exc:            # noqa: BLE001
                box["e"] = exc

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            raise TimeoutError(f"{fn} 超时 {timeout}s")
        if "e" in box:
            raise box["e"]
        return box["v"]

    def write(self, key, fn, *args, **kwargs):
        """同一 (lib,cell,view) 串行；超时后冻结，禁止盲重试。"""
        lock = self._obj_locks.setdefault(key, threading.Lock())
        with lock:
            return self.call(fn, *args, **kwargs)

    def close(self):
        try:
            self.ws.close()
        finally:
            if self._fh:
                self._fh.close()
