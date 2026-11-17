"""协议层 —— 从 ``libalchemy.so`` 里抠出来的 ``evaluation.proto``。

``_ZL44descriptor_table_protodef_evaluation_2eproto``（``.rodata 0x5a5620``）里是一段
``FileDescriptorProto``，解出来就是下面这套消息（``package proto``，proto3）。
这里用一个极小的手写编解码器复刻它：varint / fixed64 / length-delimited，
足够和真实的 DSO 服务端互通（HTTP/WebSocket 只是载体，负载就是这个 protobuf）。

消息全貌（字段号与二进制一致）：

    DSOAny{1 double value_dbl, 2 int64 value_int, 3 string value_str, 4 bool value_bool}
    Goal{2 map<string,Response> responses}
      Response{1 Objective objective, 2 Constraint constraint, 3 Attributes attributes}
        Constraint{1 Range, 2 GreaterOrLessThan, 3 Equality, 4 Type}
    Domain{1 map<string,ParamAttr> symbolParams, 2 DomainType allParamTypes, 3 int64 batchSize}
    EvaluationInitialRequest{1 OptimizerType, 2 Domain, 3 double budget, 4 int64 batchSize,
                             5 bool maximize, 6 HyperParameters, 7 repeated FidelityLevel,
                             8 int32 optimizerId, 9 Goal, 10 ProjectSettings}
    OptimizationResult{1 Cancellation, 2 OptimizerInfoResponse, 3 GetPointsResult}
    EvaluationLoopRequest{1 Cancellation, 2 EvaluationResult, 3 int32 optimizerId}
    EvaluationRequest{1 EvaluationInitialRequest, 2 EvaluationLoopRequest, 3 bool requestOptimizers}
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# 线格式
# --------------------------------------------------------------------------- #

WT_VARINT, WT_I64, WT_LEN, WT_I32 = 0, 1, 2, 5


def put_varint(out: bytearray, v: int) -> None:
    if v < 0:
        v += 1 << 64
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return


def get_varint(buf: bytes, i: int) -> tuple[int, int]:
    r = s = 0
    while True:
        b = buf[i]
        i += 1
        r |= (b & 0x7F) << s
        s += 7
        if not b & 0x80:
            return r, i


def tag(field_no: int, wt: int) -> int:
    return (field_no << 3) | wt


class Writer:
    def __init__(self):
        self.buf = bytearray()

    def varint(self, no: int, v: int):
        put_varint(self.buf, tag(no, WT_VARINT))
        put_varint(self.buf, int(v))
        return self

    def double(self, no: int, v: float):
        put_varint(self.buf, tag(no, WT_I64))
        self.buf += struct.pack("<d", float(v))
        return self

    def string(self, no: int, v: str):
        data = v.encode()
        put_varint(self.buf, tag(no, WT_LEN))
        put_varint(self.buf, len(data))
        self.buf += data
        return self

    def message(self, no: int, data: bytes):
        put_varint(self.buf, tag(no, WT_LEN))
        put_varint(self.buf, len(data))
        self.buf += data
        return self

    def bytes(self) -> bytes:
        return bytes(self.buf)


def read_fields(buf: bytes):
    """产出 (field_no, wire_type, value)。"""
    i = 0
    n = len(buf)
    while i < n:
        t, i = get_varint(buf, i)
        no, wt = t >> 3, t & 7
        if wt == WT_VARINT:
            v, i = get_varint(buf, i)
        elif wt == WT_I64:
            v = struct.unpack("<d", buf[i:i + 8])[0]
            i += 8
        elif wt == WT_LEN:
            ln, i = get_varint(buf, i)
            v = buf[i:i + ln]
            i += ln
        elif wt == WT_I32:
            v = struct.unpack("<f", buf[i:i + 4])[0]
            i += 4
        else:
            raise ValueError(f"bad wire type {wt}")
        yield no, wt, v


# --------------------------------------------------------------------------- #
# 消息
# --------------------------------------------------------------------------- #

@dataclass
class FidelityLevel:
    label: str = ""
    cost_per_eval: float = 1.0

    def encode(self) -> bytes:
        return Writer().string(1, self.label).double(2, self.cost_per_eval).bytes()


@dataclass
class ParamAttr:
    """continuous / discrete(values|step)，对应 ``Domain.ParamAttr``。"""

    kind: str = "continuous"          # continuous | values | step
    lo: float = 0.0
    hi: float = 1.0
    step: float = 0.0
    values: list = field(default_factory=list)
    nominal: float = 0.0

    def encode(self) -> bytes:
        def anyd(v: float) -> bytes:          # AnyTypeValue{1: double}
            return Writer().double(1, float(v)).bytes()

        inner = Writer()
        if self.kind == "continuous":
            c = (Writer().message(1, anyd(self.lo))
                 .message(2, anyd(self.hi))
                 .message(3, anyd(self.nominal)).bytes())
            inner.message(1, c)
        else:
            d = Writer()
            if self.kind == "values":
                vals = Writer()
                for v in self.values:
                    vals.message(1, Writer().double(1, float(v)).bytes())
                d.message(1, Writer().message(1, vals.bytes())
                          .message(2, anyd(self.nominal)).bytes())
            else:
                sp = (Writer().message(1, anyd(self.lo)).message(2, anyd(self.hi))
                      .message(3, anyd(self.step)).message(4, anyd(self.nominal)).bytes())
                d.message(2, sp)
            inner.message(2, d.bytes())
        return inner.bytes()


@dataclass
class DomainMsg:
    params: dict = field(default_factory=dict)     # name -> ParamAttr
    batch_size: int = 1
    discrete: bool = True

    def encode(self) -> bytes:
        w = Writer()
        for name, attr in self.params.items():
            entry = Writer().string(1, name).message(2, attr.encode()).bytes()
            w.message(1, entry)
        w.varint(2, 2 if self.discrete else 1)
        w.varint(3, self.batch_size)
        return w.bytes()


@dataclass
class ResponseMsg:
    objective: int = 0                 # 0 none, 1 maximize, 2 minimize
    constraint_type: int = -1          # 0 range, 1 lt, 2 gt, 3 equality
    lower: float = 0.0
    upper: float = 0.0
    weight: float = 1.0

    def encode(self) -> bytes:
        r = Writer()
        if self.objective:
            r.varint(1, self.objective)
        if self.constraint_type >= 0:
            c = Writer().varint(4, self.constraint_type)
            if self.constraint_type == 0:
                c.message(1, Writer().double(1, self.lower).double(2, self.upper).bytes())
            elif self.constraint_type in (1, 2):
                c.message(2, Writer().double(1, self.upper).varint(2, 1).bytes())
            else:
                c.message(3, Writer().double(1, self.upper).bytes())
            r.message(2, c.bytes())
        return r.bytes()


@dataclass
class GoalMsg:
    responses: dict = field(default_factory=dict)   # name -> ResponseMsg

    def encode(self) -> bytes:
        w = Writer()
        for name, resp in self.responses.items():
            w.message(2, Writer().string(1, name).message(2, resp.encode()).bytes())
        return w.bytes()


@dataclass
class InitialRequest:
    optimizer: str = "Optimizer"
    domain: DomainMsg = field(default_factory=DomainMsg)
    budget: float = 100.0
    batch_size: int = 1
    maximize: bool = False
    hyper: dict = field(default_factory=dict)
    fidelity: list = field(default_factory=list)
    optimizer_id: int = 1
    goal: GoalMsg = field(default_factory=GoalMsg)

    def encode(self) -> bytes:
        w = Writer().message(1, Writer().string(1, self.optimizer).bytes())
        w.message(2, self.domain.encode())
        w.double(3, self.budget)
        w.varint(4, self.batch_size)
        w.varint(5, 1 if self.maximize else 0)
        if self.hyper:
            hp = Writer()
            for k, v in self.hyper.items():
                gv = Writer()
                if isinstance(v, bool):
                    gv.varint(4, int(v))
                elif isinstance(v, float):
                    gv.double(1, v)
                elif isinstance(v, int):
                    gv.varint(2, v)
                else:
                    gv.string(3, str(v))
                hp.message(1, Writer().string(1, k).message(2, gv.bytes()).bytes())
            w.message(6, hp.bytes())
        for fl in self.fidelity:
            w.message(7, fl.encode())
        w.varint(8, self.optimizer_id)
        w.message(9, self.goal.encode())
        return w.bytes()


@dataclass
class PointMeta:
    point: dict = field(default_factory=dict)
    fidelity: str = ""
    batch_id: int = 0
    observation_id: int = 0


@dataclass
class PointsResult:
    points: list = field(default_factory=list)     # list[PointMeta]

    def encode(self) -> bytes:
        w = Writer()
        for pm in self.points:
            inner = Writer()
            for k, v in pm.point.items():
                gv = Writer()
                if isinstance(v, bool):
                    gv.varint(4, int(v))
                elif isinstance(v, int):
                    gv.varint(2, v)
                elif isinstance(v, float):
                    gv.double(1, v)
                else:
                    gv.string(3, str(v))
                inner.message(1, Writer().string(1, k).message(2, gv.bytes()).bytes())
            inner.string(3, pm.fidelity)
            inner.varint(4, pm.batch_id)
            inner.varint(5, pm.observation_id)
            w.message(1, inner.bytes())
        return w.bytes()


@dataclass
class ResultMsg:
    points: PointsResult | None = None
    cancelled: bool = False
    message: str = ""

    def encode(self) -> bytes:
        w = Writer()
        if self.cancelled:
            w.message(1, Writer().varint(1, 1).string(2, self.message).bytes())
        if self.points is not None:
            w.message(3, self.points.encode())
        return w.bytes()


def decode_result(buf: bytes) -> dict:
    """最小解码器：够客户端读回点列表。"""
    out = {"points": [], "cancelled": None}
    for no, wt, v in read_fields(buf):
        if no == 1 and wt == WT_LEN:
            out["cancelled"] = dict((n, val) for n, _, val in read_fields(v))
        elif no == 3 and wt == WT_LEN:
            for n2, w2, v2 in read_fields(v):
                if n2 == 1 and w2 == WT_LEN:
                    pm = {"point": {}, "fidelity": "", "batch_id": 0, "observation_id": 0}
                    for n3, w3, v3 in read_fields(v2):
                        if n3 == 1 and w3 == WT_LEN:
                            k = None
                            for n4, w4, v4 in read_fields(v3):
                                if n4 == 1:
                                    k = v4.decode()
                            pm["point"][k] = None
                        elif n3 == 4:
                            pm["batch_id"] = v3
                        elif n3 == 5:
                            pm["observation_id"] = v3
                    out["points"].append(pm)
    return out


# --------------------------------------------------------------------------- #
# 解码（与上面的 encode 成对，用于进程内/HTTP 往返）
# --------------------------------------------------------------------------- #

def decode_any(buf: bytes):
    v = None
    for no, wt, val in read_fields(buf):
        if no == 1 and wt == WT_I64:
            v = val
        elif no == 2 and wt == WT_VARINT:
            v = val
        elif no == 3 and wt == WT_LEN:
            v = val.decode()
        elif no == 4 and wt == WT_VARINT:
            v = bool(val)
    return v


def decode_initial(buf: bytes) -> "InitialRequest":
    req = InitialRequest()
    for no, wt, v in read_fields(buf):
        if no == 1 and wt == WT_LEN:
            for n2, _, v2 in read_fields(v):
                if n2 == 1:
                    req.optimizer = v2.decode()
        elif no == 2 and wt == WT_LEN:
            req.domain = decode_domain(v)
        elif no == 3 and wt == WT_I64:
            req.budget = float(v)
        elif no == 4 and wt == WT_VARINT:
            req.batch_size = int(v)
        elif no == 5 and wt == WT_VARINT:
            req.maximize = bool(v)
        elif no == 6 and wt == WT_LEN:
            for n2, _, v2 in read_fields(v):
                if n2 != 1:
                    continue
                key, gv = None, None
                for n3, _, v3 in read_fields(v2):
                    if n3 == 1:
                        key = v3.decode()
                    elif n3 == 2:
                        gv = decode_any(v3)
                if key is not None:
                    req.hyper[key] = gv
        elif no == 8 and wt == WT_VARINT:
            req.optimizer_id = int(v)
        elif no == 9 and wt == WT_LEN:
            req.goal = decode_goal(v)
    return req


def decode_domain(buf: bytes) -> DomainMsg:
    dm = DomainMsg()
    for no, wt, v in read_fields(buf):
        if no == 1 and wt == WT_LEN:
            name, attr = None, None
            for n2, _, v2 in read_fields(v):
                if n2 == 1:
                    name = v2.decode()
                elif n2 == 2:
                    attr = decode_param_attr(v2)
            if name and attr:
                dm.params[name] = attr
        elif no == 2 and wt == WT_VARINT:
            dm.discrete = (v == 2)
        elif no == 3 and wt == WT_VARINT:
            dm.batch_size = int(v)
    return dm


def decode_param_attr(buf: bytes) -> ParamAttr:
    attr = ParamAttr()
    for no, wt, v in read_fields(buf):
        if no == 1 and wt == WT_LEN:
            attr.kind = "continuous"
            for n2, w2, v2 in read_fields(v):
                val = None
                for n3, _, v3 in read_fields(v2):
                    if n3 == 1:
                        val = float(v3)
                if n2 == 1:
                    attr.lo = val
                elif n2 == 2:
                    attr.hi = val
                elif n2 == 3:
                    attr.nominal = val
        elif no == 2 and wt == WT_LEN:
            for n2, _, v2 in read_fields(v):
                def any_of(buf):
                    for n4, _, v4 in read_fields(buf):
                        if n4 == 1:
                            return float(v4)
                    return None

                if n2 == 1:            # DiscreteValuesParam
                    attr.kind = "values"
                    for n3, _, v3 in read_fields(v2):
                        if n3 == 1:
                            for n4, _, v4 in read_fields(v3):
                                if n4 == 1:
                                    for n5, _, v5 in read_fields(v4):
                                        if n5 == 1:
                                            attr.values.append(float(v5))
                        elif n3 == 2:
                            attr.nominal = any_of(v3)
                elif n2 == 2:          # DiscreteStepParam
                    attr.kind = "step"
                    for n3, _, v3 in read_fields(v2):
                        if n3 == 1:
                            attr.lo = any_of(v3)
                        elif n3 == 2:
                            attr.hi = any_of(v3)
                        elif n3 == 3:
                            attr.step = any_of(v3)
                        elif n3 == 4:
                            attr.nominal = any_of(v3)
    return attr


def decode_goal(buf: bytes) -> GoalMsg:
    gm = GoalMsg()
    for no, wt, v in read_fields(buf):
        if no == 2 and wt == WT_LEN:
            name, rm = None, ResponseMsg()
            for n2, _, v2 in read_fields(v):
                if n2 == 1:
                    name = v2.decode()
                elif n2 == 2:
                    rm = decode_response(v2)
            if name:
                gm.responses[name] = rm
    return gm


def decode_response(buf: bytes) -> ResponseMsg:
    rm = ResponseMsg()
    for no, wt, v in read_fields(buf):
        if no == 1 and wt == WT_VARINT:
            rm.objective = int(v)
        elif no == 2 and wt == WT_LEN:
            for n2, _, v2 in read_fields(v):
                if n2 == 4:
                    rm.constraint_type = int(v2)
                elif n2 == 1:
                    for n3, _, v3 in read_fields(v2):
                        if n3 == 1:
                            rm.lower = float(v3)
                        elif n3 == 2:
                            rm.upper = float(v3)
                elif n2 == 2:
                    for n3, _, v3 in read_fields(v2):
                        if n3 == 1:
                            rm.upper = float(v3)
                elif n2 == 3:
                    for n3, _, v3 in read_fields(v2):
                        if n3 == 1:
                            rm.upper = float(v3)
    return rm


def encode_evaluation_result(results: list[dict], batch_id: int = 0) -> bytes:
    """客户端回报：``EvaluationResult{batchResult:[{allResponses:{k:{value}}}]}``。"""
    w = Writer()
    for k, res in enumerate(results):
        entry = Writer()
        for name, val in res.items():
            rv = Writer().double(1, float(val)).bytes()
            entry.message(1, Writer().string(1, name).message(2, rv).bytes())
        entry.varint(4, batch_id)
        entry.varint(5, k)
        w.message(1, entry.bytes())
    return w.bytes()


def decode_evaluation_result(buf: bytes) -> list[dict]:
    out = []
    for no, wt, v in read_fields(buf):
        if no != 1 or wt != WT_LEN:
            continue
        res = {}
        for n2, _, v2 in read_fields(v):
            if n2 != 1:
                continue
            key, val = None, None
            for n3, _, v3 in read_fields(v2):
                if n3 == 1:
                    key = v3.decode()
                elif n3 == 2:
                    for n4, _, v4 in read_fields(v3):
                        if n4 == 1:
                            val = float(v4)
            if key is not None:
                res[key] = val
        out.append(res)
    return out
