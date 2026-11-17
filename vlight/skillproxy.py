"""skillproxy —— 把没实现的 SKILL 函数转发给真实 Virtuoso（并可录制回放）。

协议：一行一个 JSON
    请求  {"seq":1,"op":"call","fn":"dbOpenCellViewByType","args":[...]}
    响应  {"seq":1,"ok":true,"result":...} / {"seq":1,"ok":false,"error":"..."}

句柄：结果里的远端对象编码成 {"$h":{"type":"cellview","id":"..."}}，本地变 RemoteHandle，
属性访问/写入再按 op=getq/setq 回源。

三种传输：
    SocketTransport(host, port)     真机（skillbridge/自建 server）
    RecordingTransport(inner, path) 把请求/响应写成 jsonl，供离线回放
    ReplayTransport(path)           离线按 (fn,args) 回放
"""
from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

from .registry import RemoteHandle


def encode(value: Any):
    if isinstance(value, RemoteHandle):
        return {"$h": {"type": value.type, "id": value.id}}
    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    return value


def decode(value: Any):
    if isinstance(value, dict):
        if "$h" in value:
            h = value["$h"]
            return RemoteHandle(h.get("type", "object"), str(h.get("id", "")), None)
        return {k: decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode(v) for v in value]
    return value


class Transport:
    def request(self, payload: dict) -> dict:
        raise NotImplementedError


class SocketTransport(Transport):
    def __init__(self, host: str = "127.0.0.1", port: int = 7000,
                 timeout: float = 30.0):
        self.addr = (host, port)
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._file = None

    def _connect(self):
        if self._sock is None:
            self._sock = socket.create_connection(self.addr, self.timeout)
            self._file = self._sock.makefile("rwb")

    def request(self, payload: dict) -> dict:
        self._connect()
        self._file.write((json.dumps(payload) + "\n").encode())
        self._file.flush()
        line = self._file.readline()
        if not line:
            raise RuntimeError("代理连接被关闭")
        return json.loads(line)

    def close(self) -> None:
        if self._file:
            self._file.close()
        if self._sock:
            self._sock.close()
        self._sock = self._file = None


class RecordingTransport(Transport):
    """包一层，把交互记录成 jsonl。"""

    def __init__(self, inner: Transport, path: str | Path):
        self.inner = inner
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = self.path.open("a", encoding="utf-8")

    def request(self, payload: dict) -> dict:
        resp = self.inner.request(payload)
        self.fh.write(json.dumps({"req": payload, "resp": resp},
                                 ensure_ascii=False) + "\n")
        self.fh.flush()
        return resp

    def close(self) -> None:
        self.fh.close()
        if hasattr(self.inner, "close"):
            self.inner.close()


class ReplayTransport(Transport):
    """离线回放：按 (fn, args) 找记录。"""

    def __init__(self, path: str | Path):
        self.table: dict[str, dict] = {}
        for line in Path(path).read_text().splitlines():
            rec = json.loads(line)
            key = self._key(rec["req"])
            self.table.setdefault(key, rec["resp"])

    @staticmethod
    def _key(req: dict) -> str:
        return json.dumps([req.get("op"), req.get("fn"), req.get("args"),
                           req.get("handle"), req.get("prop")],
                          sort_keys=True, ensure_ascii=False)

    def request(self, payload: dict) -> dict:
        key = self._key(payload)
        if key not in self.table:
            raise KeyError(f"回放记录里没有 {payload.get('fn')}")
        return self.table[key]


class Proxy:
    def __init__(self, transport: Transport):
        self.transport = transport
        self.seq = 0

    def _rpc(self, **payload) -> Any:
        self.seq += 1
        payload["seq"] = self.seq
        resp = self.transport.request(payload)
        if not resp.get("ok", False):
            raise RuntimeError(f"远端错误: {resp.get('error')}")
        out = decode(resp.get("result"))
        self._attach(out)
        return out

    def _attach(self, value) -> None:
        if isinstance(value, RemoteHandle):
            value.backend = self
        elif isinstance(value, list):
            for v in value:
                self._attach(v)

    # -- 调用与属性 -------------------------------------------------------- #
    def call(self, name: str, args) -> Any:
        return self._rpc(op="call", fn=name, args=[encode(a) for a in args])

    def get_attr(self, handle: RemoteHandle, prop: str) -> Any:
        return self._rpc(op="getq", handle=encode(handle), prop=prop)

    def set_attr(self, handle: RemoteHandle, prop: str, value: Any) -> Any:
        return self._rpc(op="setq", handle=encode(handle), prop=prop,
                         value=encode(value))

    def close(self) -> None:
        if hasattr(self.transport, "close"):
            self.transport.close()


class FakeBackend(Transport):
    """测试用的本地后端：实现少量函数 + 句柄属性。"""

    def __init__(self, funcs: dict | None = None):
        self.funcs = funcs or {}
        self.objects: dict[str, dict] = {}
        self.calls: list[tuple] = []

    def add_object(self, otype: str, oid: str, props: dict) -> None:
        self.objects[oid] = {"type": otype, **props}

    def request(self, payload: dict) -> dict:
        op = payload.get("op")
        self.calls.append((op, payload.get("fn"), payload.get("prop")))
        try:
            if op == "call":
                fn = payload["fn"]
                if fn not in self.funcs:
                    return {"seq": payload["seq"], "ok": False,
                            "error": f"unknown function {fn}"}
                result = self.funcs[fn](*[decode(a) for a in payload.get("args", [])])
                return {"seq": payload["seq"], "ok": True, "result": encode(result)}
            if op == "getq":
                h = decode(payload["handle"])
                obj = self.objects.get(h.id, {})
                return {"seq": payload["seq"], "ok": True,
                        "result": encode(obj.get(payload["prop"]))}
            if op == "setq":
                h = decode(payload["handle"])
                self.objects.setdefault(h.id, {})[payload["prop"]] = \
                    decode(payload.get("value"))
                return {"seq": payload["seq"], "ok": True, "result": True}
            return {"seq": payload["seq"], "ok": False, "error": "bad op"}
        except Exception as exc:                       # noqa: BLE001
            return {"seq": payload["seq"], "ok": False, "error": str(exc)}


__all__ = ["Transport", "SocketTransport", "RecordingTransport", "ReplayTransport",
           "Proxy", "FakeBackend", "encode", "decode"]
