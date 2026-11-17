"""OldHookeJeeves — extracted from libalchemy.so (algs/OldHookeJeeves.cpp).

Instruction-level proof (see OldHookeJeeves.md): every run() body of this class is
*bite-for-byte identical* to HookeJeeves after normalising addresses, rel32 fields
and symbol names.  Only the embedded assertion strings differ (source file name,
qualified function name, and one line number in MoveDone::run).  It is a frozen
duplicate kept for replay, so this module simply delegates.

states:
    Init -> Start -> {Start_WaitForStartingPoint, Start_HaveStartingPoint}
    Start_HaveStartingPoint -> Fork(2*n) Move | Move
    Move -> MoveWait -> MoveDone -> {Done | Fork(2*n) Move | Move}
    Done -> {Return, Start}

hyperparams (Data::Data @0x361304, same keys, separate .rodata pool @0x56cce0):
    current_point (default: empty DesignPoint), population (default: Population(1)),
    parallel (default: true), should_rerun (default: true)
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from HookeJeeves import run as _hooke_jeeves_run  # noqa: E402  (sibling module)

K_CURRENT_POINT = "current_point"  # 0x56cce0
K_POPULATION = "population"        # 0x56ccee
K_PARALLEL = "parallel"            # 0x56ccf9
K_SHOULD_RERUN = "should_rerun"    # 0x56cd02

INITIAL_STEP_FACTOR = 0.1    # Alchemy::OldHookeJeeves::InitialStepFactor 0x56ccd0
STEP_REDUCTION_FACTOR = 0.5  # Alchemy::OldHookeJeeves::StepReductionFactor 0x56ccd8


def run(domain, goal, start=None, params=None):
    """yield a batch of points; receive costs via send(); return best index list

    Delegates to HookeJeeves.run: the two state machines are instruction-identical
    (8/8 run() bodies compared, 0 structural differences).
    """
    return (yield from _hooke_jeeves_run(domain, goal, start=start, params=params))


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    class Quad:
        tunables = [41, 41]

        def from_index(self, ix):
            return [-2.0 + 4.0 * ix[0] / 40.0, -2.0 + 4.0 * ix[1] / 40.0]

        def to_index(self, point):
            return [self.clamp(int(round((point[0] + 2.0) * 10))),
                    self.clamp(int(round((point[1] + 2.0) * 10)))]

        def center_index(self):
            return [20, 20]

        def clamp(self, i):
            return max(0, min(40, int(i)))

        def clamp_index(self, ix):
            return [self.clamp(i) for i in ix]

    dom = Quad()
    nfev = [0]
    seen = {}

    def cost(p):
        nfev[0] += 1
        ix = tuple(dom.to_index(p))
        seen[ix] = p[0] * p[0] + p[1] * p[1]
        return seen[ix]

    gen = run(dom, None, start=dom.from_index([0, 0]), params={"should_rerun": False})
    batch = next(gen)
    while True:
        try:
            batch = gen.send([cost(p) for p in batch])
        except StopIteration as stop:
            best = stop.value
            break
    print("OldHookeJeeves final=%s cost=%.6e nfev=%d" % (best, seen[tuple(best)], nfev[0]))
