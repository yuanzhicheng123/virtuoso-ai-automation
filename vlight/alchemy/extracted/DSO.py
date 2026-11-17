"""DSO — extracted from libalchemy.so  (algs/DSO.cpp, registered "Design Space Optimization" @0x565371).

state machine (VMA addresses):

    DSO::Init            @0x3107b6  -> DSO::Start
        Data(Params) @0x3131f6 .. 0x31520b, Start ctor @0x310848
    DSO::Start           @0x31094c   builds the dso::eval::Project / OptimizerClient, marshals the
                                     parameters, `protocol->startTx()` (vtable +0x10 @0x3112d9),
                                     then -> DSO::GetDSOPoints          @0x311317
    DSO::GetDSOPoints    @0x311574  `protocol->getPointsTx()` (vtable +0x28 @0x3115c6)
                                     -> Fork<DSO::AlgState, DSO::PointSubmitFork>(n)  @0x311ce3
    DSO::PointSubmitFork @0x311fb2  factory->create(values[index]) (vtable +0x18 @0x31203e),
                                     point->setSkipCorners(...) @0x312095  -> DSO::PointWaitFork
    DSO::PointWaitFork   @0x3121c6  while !point->isEvaluated() -> self      @0x3121f6
                                     else build the response map, -> DSO::AllPointsDone
    DSO::AllPointsDone   @0x312822  State::JOIN() @0x312848, sendResponseTx (vtable +0x30 @0x3128c8),
                                     doneTx (vtable +0x20 @0x3128df)
                                        true  -> endTx("") (vtable +0x18) @0x312947, throw Return @0x312980
                                        false -> DSO::GetDSOPoints again    @0x3129bb

Strings / constants (.rodata):
    0x56dace "neocircuitGlobal"   0x56dadf "PDE"        (see Optimize)
    0x565425 "ade:opt:merit"      0x565433 "adeSkipCorner:"
    0x5653db "DSOProject"         0x5653f5 "DSOExperiment"
    0x5654ba "Double"  0x5654c1 "Int"  0x5654c5 "UInt"  0x5654ca "String"
    0x5654d8 "Unsupported hyper parameter type"
    0x565500 "INVALID Parameters passed into DSO start state"
    0x56552f "paramAttr.values > 0"
    0x56564e "pIt.valueIdx < values->size()"
    0x56569a "m_setOfPoints.size()>0"
    0x5655c8 "Something is wrong, we have empty points"

hyperparams read by DSO::Data::Data(Params) (offsets are AlgState-relative; Data starts at 0x40):
    key               type                               default      read @
    current_point     shared_ptr<DesignPoint>            new point    0x313344
    parameters        shared_ptr<vector<DSOParamAttr>>   -            0x3134a1
    hyperParams       shared_ptr<vector<DSOHyperParam>>  -            0x3136ae
    budget            shared_ptr<int>                    -            0x313965  (unused here)
    batchSize         shared_ptr<int>                    -            0x313b59  (unused here)
    optimizerName     shared_ptr<string>                 ""           0x313d4d
    projectName       string                             "DSOProject" 0x313e9f
    experimentName    string                             "DSOExperiment" 0x3140cf (unused here)
    projectObj        shared_ptr<dso::eval::Project>     -            0x3143a6
    evaluationResult  shared_ptr<EvaluationReturnType>   -            0x3145aa
    goals             shared_ptr<dso::goal::Goal>        -            0x3147ae

generator protocol:  `batch = yield points`  -> driver sends `list[float]`; `run` returns the
best index list once the server's `doneTx()` answers true.
"""

from typing import Any, Dict, List, Optional, Sequence

# ---- protocol vtable slots (dso::eval::Protocol) --------------------------
PROTO_END_TX = 0x18         # @0x312947  endTx("")
PROTO_DONE_TX = 0x20        # @0x3128df  doneTx()
PROTO_GET_POINTS_TX = 0x28  # @0x3115c6  getPointsTx()
PROTO_SEND_RESPONSE_TX = 0x30   # @0x3128c8  sendResponseTx(EvaluationReturnType const&)
PROTO_START_TX = 0x10       # @0x3112d9  startTx()

# ---- .rodata --------------------------------------------------------------
MERIT_KEY = "ade:opt:merit"          # @0x565425
SKIP_PREFIX = "adeSkipCorner:"       # @0x565433  (14 bytes, substring(14) @0x31190b)
DEFAULT_PROJECT = "DSOProject"       # @0x5653db
DEFAULT_EXPERIMENT = "DSOExperiment"  # @0x5653f5
TYPE_STRING = 3                      # attr.type == 3 -> StringParameter (@0x3110e3)

DEFAULTS = {
    "projectName": DEFAULT_PROJECT,
    "experimentName": DEFAULT_EXPERIMENT,
    "optimizerName": "",
    "budget": None,
    "batchSize": None,
    # host-side guards, not present in the binary:
    "max_rounds": 100000,
}


def _pget(params, key, default=None):
    if params is None:
        return default
    if isinstance(params, dict):
        return params.get(key, default)
    get = getattr(params, "get", None)
    if get is not None:
        return get(key, default)
    return default


def _merged(params) -> Dict[str, Any]:
    out = dict(DEFAULTS)
    if params is None:
        return out
    if isinstance(params, dict):
        out.update(params)
        return out
    for k in DEFAULTS:
        v = _pget(params, k, _sentinel := object())
        if v is not _sentinel:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# pure helpers, each one is a slice of DSO::Start::run / PointWaitFork::run
# ---------------------------------------------------------------------------
def split_skips(point_dict: Dict[str, Any], prefix: str = SKIP_PREFIX):
    """@0x3118dc: keys starting with `adeSkipCorner:` are corner skips, not parameters.

    `if (key.find(SKIP_PREFIX) == 0) { skipped.push_back(key.substr(14)); continue; }`
    Returns (parameters, skipped_corners).
    """
    params, skips = {}, []
    for k, v in (point_dict or {}).items():
        if k.startswith(prefix):
            skips.append(k[len(prefix):])       # substr(0xe) @0x31191e
        else:
            params[k] = v
    return params, skips


def marshal_parameter(name: str, values: Sequence[Any], value_idx: Optional[int],
                      attr_type: int, tunable_levels: Optional[int] = None) -> Dict[str, Any]:
    """@0x310f8a .. @0x31117a  per-parameter discrete marshalling.

        dso::base::Parameter p(attr.name.c_str());            @0x310f8a
        populateParamDataMap(attr, paramDataMap);             @0x310fa7
        DSOAny nominal; if (warm) nominal = values[attr.valueIdx];   @0x311074..0x3110d7
        if (attr.type == 3) p.setSampler(StringParameter(values, nominal));       @0x311103
        else                p.setSampler(DiscreteValuesSampleInterface(values, nominal));  @0x31114d
        domain->addParameter(p);                              @0x31118b
    """
    nominal = None
    if value_idx is not None and 0 <= value_idx < len(values):
        nominal = values[value_idx]
    sampler = "StringParameter" if attr_type == TYPE_STRING else "DiscreteValuesSampleInterface"
    return {
        "name": name,
        "values": list(values),
        "type": attr_type,
        "valueIdx": value_idx,
        "nominal": nominal,
        "sampler": sampler,
        "tunable_levels": tunable_levels,
    }


def merit_response(cost: float, corners: Sequence[Sequence[str]] = (),
                   spec_values: Sequence[float] = ()) -> Dict[str, float]:
    """@0x3122f0 + @0x3125c9  response map built by DSO::PointWaitFork::run.

        map["ade:opt:merit"] = point->cost();                          @0x3122f0/0x3122f5
        assert(sValues.size() == getEnabledSpecCorners().size());      @0x31241f
        for (auto& c : getEnabledSpecCorners())                        @0x31247c ..
            map[c.first + ":" + c.second] = sValues[i++];               @0x3124f8/0x312512/0x3125c9
    """
    resp = {MERIT_KEY: float(cost)}
    for (spec, corner), value in zip(corners, spec_values):
        resp[f"{spec}:{corner}"] = float(value)     # ':' literal @0x565801
    return resp


def _hook(server, slot_name, *args):
    fn = getattr(server, slot_name, None)
    if fn is None:
        return None
    return fn(*args)


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------
def run(domain, goal, start=None, params=None, sub=None):
    """orchestrates sub-algorithms; yields their points through; returns best index list.

    `goal` (or `params["server"]`) plays the role of `dso::eval::Protocol`; it may expose
    `get_points_tx()`, `send_response_tx(result)`, `done_tx()` and `end_tx(msg)` — the four
    vtable slots listed above.  `sub["GetPoints"]` (a generator factory) is used instead of
    `get_points_tx` when the protocol has none, so the stage can be composed without importing
    the DSO server.
    """
    p = _merged(params)
    sub = sub or {}
    server = p.get("server") or goal

    best: Optional[List[int]] = None
    best_cost = float("inf")
    nfev = 0
    rounds = 0
    history: List[Dict[str, Any]] = []

    # @0x31094c DSO::Start::run
    #   assert(projectObj); projectObj->setName(projectName);          @0x31099b / 0x3109db
    #   experiment = projectObj->createExperiment("");                 @0x3109ff
    #   config->setOptimizer(optimizerName);                           @0x310a48
    #   *goal = *params.goals; goal->addResponse("ade:opt:merit")      @0x310a7e / 0x310aa0
    #                                   ->addObjective(MINIMIZE=2)     @0x310aad
    project = {"name": p["projectName"], "experiment": p["experimentName"],
               "optimizer": p["optimizerName"],
               "responses": {MERIT_KEY: {"objectives": ["MINIMIZE"]}}}
    hyper = p.get("hyperParams") or {}
    for key, (typ, value) in dict(hyper).items():
        # "Double"/"Int"/"UInt"/"String" dispatch @0x310c14 / 0x310c91 / 0x310ccd / 0x310d3d
        if typ in ("Double", "Int", "UInt", "String"):
            project.setdefault("hyperparams", {})[key] = value
        else:
            project.setdefault("hyperparams_unsupported", []).append(key)   # @0x5654d8

    attrs = p.get("parameters") or {}
    warm = bool(p.get("warm_start", start is not None))
    domain_params = []
    for name, attr in dict(attrs).items():
        values = attr.get("values") or []
        assert values, "paramAttr.values > 0"                     # @0x310f69
        domain_params.append(marshal_parameter(
            name, values, attr.get("valueIdx"), int(attr.get("type", 0)),
            (domain.tunables[attr["valueIdx"]]
             if attr.get("valueIdx") is not None and attr["valueIdx"] < len(domain.tunables) else None)))

    _hook(server, "start_tx")                                      # vtable +0x10 @0x3112d9

    while rounds < int(p["max_rounds"]):
        rounds += 1

        # ---- @0x311574 GetDSOPoints::run ---------------------------------
        pts = None
        gt = getattr(server, "get_points_tx", None)
        if gt is not None:
            pts = gt()
        elif "GetPoints" in sub:
            batch_gen = sub["GetPoints"](domain=domain, goal=goal, start=best, params=p)
            try:
                pts = next(batch_gen)
            except StopIteration:
                pts = []
        if not pts:
            if rounds == 1:
                # no server wired at all: degenerate one-point transaction (host-side fallback)
                ix = best if best is not None else (
                    start if start is not None
                    else (domain.nominal_index() if hasattr(domain, "nominal_index")
                          else [t // 2 for t in domain.tunables]))
                pts = [list(ix)]
            else:
                break                                              # "empty points" @0x5655c8

        idx_batch = []
        skip_batch = []
        for raw in pts:
            if isinstance(raw, dict):
                body, skips = split_skips(raw)                     # @0x3118dc
                idx_batch.append(list(domain.to_index(body)))
                skip_batch.append(skips)
            else:
                idx_batch.append([int(v) for v in raw])
                skip_batch.append([])
        assert idx_batch, "m_setOfPoints.size()>0"                 # @0x311c82

        # Fork<DSO::AlgState, DSO::PointSubmitFork>(n) @0x311ce3 -> one branch per point
        costs = yield [list(ix) for ix in idx_batch]
        nfev += len(idx_batch)

        # ---- @0x3121c6 PointWaitFork::run: build the response map --------
        result = []
        for i, (ix, c) in enumerate(zip(idx_batch, costs)):
            if c is None:                                          # !isEvaluated -> self loop
                costs = yield [list(ix)]
                nfev += 1
                c = costs[0]
            corners = p.get("corners") or []
            spec_values = (getattr(server, "spec_values", None) or {})
            if callable(spec_values):
                spec_values = spec_values(ix)
            svals = [spec_values.get(f"{s}:{k}", 0.0) for (s, k) in corners]
            resp = merit_response(c, corners, svals)
            if c < best_cost:
                best, best_cost = list(ix), c
            result.append({"index": list(ix), "cost": float(c),
                           "skipped_corners": skip_batch[i], "response": resp})
        history.extend(result)

        # ---- @0x312822 AllPointsDone::run ---------------------------------
        _hook(server, "join")                                      # State::JOIN() @0x312848
        _hook(server, "send_response_tx", result)                  # vtable +0x30 @0x3128c8
        if _hook(server, "done_tx"):                               # vtable +0x20 @0x3128df
            _hook(server, "end_tx", "")                            # vtable +0x18 @0x312947
            break                                                  # -> throw Return @0x312980
        # else: loop -> new GetDSOPoints(AlgState(this)) @0x3129bb

    if best is None:
        raise RuntimeError("DSO: no point was ever evaluated")
    resp = {"best": list(best), "cost": best_cost, "iterations": rounds, "nfev": nfev,
            "history": history, "project": project, "domain": domain_params}
    try:                                   # hand the transaction log back to the caller
        server.response = resp
    except Exception:
        pass
    return list(best)


# ---------------------------------------------------------------------------
# self test: 2-D quadratic, 41 levels per dimension
# ---------------------------------------------------------------------------
LEVELS = 41


class _Domain2D:
    tunables = [LEVELS, LEVELS]
    lo, step = -10.0, 0.5

    def from_index(self, ix):
        return {f"x{i}": self.lo + self.step * ix[i] for i in range(2)}

    def to_index(self, pt):
        return [int(round((pt[f"x{i}"] - self.lo) / self.step)) for i in range(2)]

    def nominal_index(self):
        return [20, 20]


def _f(point):
    x, y = point["x0"], point["x1"]
    return (x - 1.5) ** 2 + (y + 2.5) ** 2      # optimum at index (23, 15)


class _FakeProtocol:
    """minimal stand-in for dso::eval::Protocol (+ OptimizerClient)."""

    SPEC_CORNERS = [("ac", "tt"), ("ac", "ss")]

    def __init__(self, domain, f, rounds=3, width=4):
        self.domain, self.f = domain, f
        self.rounds_left, self.width = rounds, width
        self.rng = 12345
        self.best, self.best_cost = None, None
        self.evaluated = 0
        self.ended = False

    # LCG so the trace is reproducible without importing random
    def _rnd(self, n):
        self.rng = (1103515245 * self.rng + 12345) & 0x7FFFFFFF
        return self.rng % n

    def start_tx(self):
        pass

    def get_points_tx(self):
        pts = []
        for _ in range(self.width):
            if self.best is None:
                ix = [self._rnd(LEVELS), self._rnd(LEVELS)]
            else:                                   # local box around the incumbent
                ix = [min(max(b + self._rnd(9) - 4, 0), LEVELS - 1) for b in self.best]
            pts.append({f"x{i}": self.domain.lo + self.domain.step * ix[i] for i in range(2)})
        return pts

    def spec_values(self, ix):
        pt = self.domain.from_index(ix)
        return {f"ac:tt": abs(pt["x0"]), f"ac:ss": abs(pt["x1"])}

    def send_response_tx(self, result):
        self.evaluated += len(result)
        best = min(result, key=lambda r: r["cost"])
        if self.best_cost is None or best["cost"] < self.best_cost:
            self.best_cost, self.best = best["cost"], best["index"]

    def done_tx(self):
        self.rounds_left -= 1
        return self.rounds_left <= 0

    def end_tx(self, msg):
        self.ended = True


if __name__ == "__main__":
    dom = _Domain2D()
    proto = _FakeProtocol(dom, _f, rounds=3, width=4)

    gen = run(dom, _f, params={
        "server": proto,
        "projectName": "DSOProject",
        "optimizerName": "Powell",
        "corners": _FakeProtocol.SPEC_CORNERS,
    })

    nfev, batch = 0, next(gen)
    while True:
        costs = []
        for ix in batch:
            costs.append(_f(dom.from_index(ix)))
            nfev += 1
        try:
            batch = gen.send(costs)
        except StopIteration as stop:
            best = stop.value
            break

    resp = proto.response
    print("DSO self-test: 2-D quadratic, 41 levels/dim, 3 protocol rounds x 4 points")
    print("  rounds     :", resp["iterations"], "(getPointsTx per round: 4 points)")
    print("  point      :", dom.from_index(best), "index", best)
    print("  cost       :", resp["cost"])
    print("  nfev       :", nfev)
    print("  endTx msg  :", repr("") if proto.ended else "not called")
    print("  response   :", sorted(resp["history"][-1]["response"]))
    print("  history    :", len(resp["history"]), "evaluated points")
