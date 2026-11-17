"""参考服务端 —— 复刻 DSO/``libCadenceDSO`` 的 ask/tell 会话。

真实链路：客户端（ADE）把 ``EvaluationInitialRequest`` 发过去，服务端用注册表造一个
优化器，回 ``OptimizationResult{batchOfPoints}``；客户端仿真完把 ``EvaluationResult``
发回来，服务端推进算法、回下一批，直到算法结束或预算耗尽。

这里用同一套消息（``vlight.alchemy.proto``）实现一个**可直接跑的参考服务**：
既能在进程内当对象用（``Experiment``），也能起一个 HTTP 端点（``serve_http``）
让外部脚本按 ``/api/v1/experiment`` 交互 —— 这就是对拍/回归用的接口。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

from . import proto
from .core import Params
from .lattice import Domain, Param
from .spec import Goal, Response
from .registry import create, resolve


# --------------------------------------------------------------------------- #
# 消息 -> 引擎对象
# --------------------------------------------------------------------------- #

def domain_from_msg(dm: proto.DomainMsg) -> Domain:
    params = []
    for name, attr in dm.params.items():
        if attr.kind == "values":
            params.append(Param(name, values=attr.values, nominal=attr.nominal))
        elif attr.kind == "step":
            params.append(Param(name, attr.lo, attr.hi, step=attr.step or None,
                                nominal=attr.nominal))
        else:
            params.append(Param(name, attr.lo, attr.hi, nominal=attr.nominal))
    return Domain(params)


def goal_from_msg(gm: proto.GoalMsg) -> Goal:
    responses = []
    for name, rm in gm.responses.items():
        if rm.constraint_type == 0:
            responses.append(Response(name, constraint=("range", rm.lower, rm.upper),
                                      weight=int(rm.weight)))
        elif rm.constraint_type == 1:
            responses.append(Response(name, constraint=("lt", rm.upper), weight=int(rm.weight)))
        elif rm.constraint_type == 2:
            responses.append(Response(name, constraint=("gt", rm.upper), weight=int(rm.weight)))
        elif rm.constraint_type == 3:
            responses.append(Response(name, constraint=("eq", rm.upper), weight=int(rm.weight)))
        else:
            obj = "max" if rm.objective == 1 else ("min" if rm.objective == 2 else None)
            responses.append(Response(name, objective=obj, target=rm.upper,
                                      weight=int(rm.weight)))
    return Goal(responses)


# --------------------------------------------------------------------------- #
# 一次实验
# --------------------------------------------------------------------------- #

@dataclass
class Experiment:
    name: str = ""
    domain: Domain | None = None
    goal: Goal | None = None
    algo: object = None
    budget: int = 100
    batch_size: int = 1
    done: bool = False
    n_points: int = 0
    best: object = None
    history: list = field(default_factory=list)
    message: str = ""
    _pending: list | None = None
    _started: bool = False

    @classmethod
    def start(cls, req: proto.InitialRequest) -> "Experiment":
        dom = domain_from_msg(req.domain)
        goal = goal_from_msg(req.goal)
        params = Params(dict(req.hyper))
        params.set("algorithm", req.optimizer)
        name = resolve(req.optimizer)
        algo = create(name, dom, goal, params)
        return cls(name=name, domain=dom, goal=goal, algo=algo,
                   budget=int(req.budget), batch_size=max(1, req.batch_size))

    # -- ask ---------------------------------------------------------------- #
    def _to_result(self, batch) -> proto.PointsResult:
        out = []
        for k, item in enumerate(batch or []):
            point = item if isinstance(item, dict) else self.domain.from_index(item)
            out.append(proto.PointMeta(point=point,
                                       batch_id=self.n_points // max(1, self.batch_size),
                                       observation_id=k))
        self._pending = None
        return proto.PointsResult(points=out)

    def get_points(self) -> proto.PointsResult:
        if self.done:
            return proto.PointsResult(points=[])
        if not self._started:
            self._started = True
            try:
                self._pending = next(self.algo)
            except StopIteration:
                self.done = True
                self.message = "optimizer finished"
                return proto.PointsResult(points=[])
        if self._pending is None:          # 还没 tell 就再要：协议顺序错误
            return proto.PointsResult(points=[])
        return self._to_result(self._pending)

    # -- tell --------------------------------------------------------------- #
    def send_results(self, results: list[dict]) -> proto.PointsResult:
        costs = []
        for res in results:
            cost, _, met = self.goal.cost(res)
            dp = type("DP", (), {"point": {}, "responses": res, "cost": cost,
                                 "all_met": met, "values": []})()
            self.history.append(dp)
            if self.best is None or cost < self.best.cost:
                self.best = dp
            costs.append(cost)
        self.n_points += len(costs)
        if self.n_points >= self.budget:
            self.done = True
            self.message = "budget exhausted"
            return proto.PointsResult(points=[])
        try:
            self._pending = self.algo.send(costs)
        except StopIteration:
            self.done = True
            self.message = "optimizer finished"
            return proto.PointsResult(points=[])
        return self._to_result(self._pending)


# --------------------------------------------------------------------------- #
# 最小 HTTP 端点（可选）
# --------------------------------------------------------------------------- #

def serve_http(host: str = "127.0.0.1", port: int = 8080) -> None:  # pragma: no cover
    """起一个极简 HTTP 服务：POST /api/v1/experiment 收 protobuf，回 protobuf。"""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    state: dict[str, Experiment] = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n)
            if self.path.rstrip("/") == "/api/v1/experiment/init":
                exp = Experiment.start(_decode_initial(body))
                state["exp"] = exp
                payload = proto.ResultMsg(points=exp.get_points()).encode()
            elif self.path.rstrip("/") == "/api/v1/experiment/loop":
                exp = state.get("exp")
                if exp is None:
                    self.send_error(409, "no experiment")
                    return
                payload = proto.ResultMsg(points=exp.send_results(_decode_results(body))).encode()
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if self.path.rstrip("/") == "/api/v1/experiment/status":
                exp = state.get("exp")
                payload = json.dumps({
                    "optimizer": getattr(exp, "name", None),
                    "done": getattr(exp, "done", None),
                    "points": getattr(exp, "n_points", None),
                    "message": getattr(exp, "message", ""),
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            else:
                self.send_error(404)

    HTTPServer((host, port), Handler).serve_forever()


def _decode_initial(body: bytes) -> proto.InitialRequest:  # pragma: no cover
    return proto.decode_initial(body)


def _decode_results(body: bytes) -> list[dict]:  # pragma: no cover
    return proto.decode_evaluation_result(body)
