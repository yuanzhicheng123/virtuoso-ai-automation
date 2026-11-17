"""measure —— 波形测量算法（viVA 里那些函数的实现）。

都是数值方法，和具体仿真器无关，输入就是 (x, y) 数组：

    频谱     FFT + 窗函数 -> 谐波/THD/SNDR/SFDR
    带宽/增益 -3dB 点、直流增益、单位增益频率
    稳定性   相位裕度/增益裕度（从环路增益 T(jω)）
    时域     建立时间、过冲、上升时间、周期/频率、抖动
    眼图     折叠出的眼高/眼宽

用法：
    sp = spectrum(t, v, f0=1e9)         # 返回 harmonics/thd/snr...
    bw = bandwidth_3db(f, H)
    pm = phase_margin(f, T)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


# --------------------------------------------------------------------------- #
# 频谱
# --------------------------------------------------------------------------- #

@dataclass
class Spectrum:
    freq: np.ndarray
    mag: np.ndarray
    f0: float = 0.0
    harmonics: np.ndarray = field(default_factory=lambda: np.zeros(0))
    noise_floor: float = 0.0

    def thd(self, nh: int = 5) -> float:
        h = self.harmonics[1:nh]
        if len(h) == 0 or self.harmonics[0] <= 0:
            return float("nan")
        return float(10 * math.log10(np.sum(h ** 2) / self.harmonics[0] ** 2))

    def snr(self) -> float:
        return float("nan") if self.harmonics[0] <= 0 else float(
            10 * math.log10(self.harmonics[0] ** 2 / max(self.noise_floor, 1e-300)))

    def sndr(self, nh: int = 5) -> float:
        h = self.harmonics[1:nh]
        denom = np.sum(h ** 2) + self.noise_floor
        return float("nan") if self.harmonics[0] <= 0 else float(
            10 * math.log10(self.harmonics[0] ** 2 / max(denom, 1e-300)))

    def sfdr(self) -> float:
        if len(self.harmonics) == 0 or self.harmonics[0] <= 0:
            return float("nan")
        spur = max(np.max(self.harmonics[1:]) if len(self.harmonics) > 1 else 0.0,
                   self.noise_floor ** 0.5)
        return float(20 * math.log10(self.harmonics[0] / max(spur, 1e-300)))


def _window(n: int, kind: str = "hann") -> np.ndarray:
    if kind == "hann":
        return np.hanning(n)
    if kind == "blackman":
        return np.blackman(n)
    return np.ones(n)


def spectrum(t, y, f0: float | None = None, npts: int | None = None,
             window: str = "hann") -> Spectrum:
    t = np.asarray(t, dtype=float)
    y = np.asarray(y)
    if len(t) < 8:
        raise ValueError("点数太少")
    dt = float(np.mean(np.diff(t)))
    n = npts or len(t)
    w = _window(n, window)
    seg = y[:n] * w
    mag = np.abs(np.fft.rfft(seg)) / (np.sum(w) / 2)
    freq = np.fft.rfftfreq(n, dt)
    sp = Spectrum(freq=freq, mag=mag)
    if f0 is None:
        if len(freq) > 1:
            sp.f0 = float(freq[1 + int(np.argmax(mag[1:]))])
        else:
            sp.f0 = 0.0
    else:
        sp.f0 = float(f0)
    if sp.f0 > 0:
        h = []
        for k in range(1, 6):
            idx = int(np.argmin(np.abs(freq - k * sp.f0)))
            lo, hi = max(idx - 2, 0), min(idx + 3, len(mag))
            h.append(float(np.max(mag[lo:hi])))
        sp.harmonics = np.array(h)
        noise_mask = np.ones(len(mag), dtype=bool)
        for k in range(1, 6):
            idx = int(np.argmin(np.abs(freq - k * sp.f0)))
            noise_mask[max(idx - 3, 0):idx + 4] = False
        sp.noise_floor = float(np.sum(mag[noise_mask] ** 2))
    return sp


# --------------------------------------------------------------------------- #
# 频域指标
# --------------------------------------------------------------------------- #

def _interp(f, y, target):
    d = y - target
    idx = np.where(np.sign(d[:-1]) != np.sign(d[1:]))[0]
    if len(idx) == 0:
        return float("nan")
    i = int(idx[0])
    if d[i] == d[i + 1]:
        return float(f[i])
    return float(f[i] + (f[i + 1] - f[i]) * d[i] / (d[i] - d[i + 1]))


def bandwidth_3db(f, h) -> float:
    """|H| 相对低频下降 3dB 的频率。"""
    f = np.asarray(f, dtype=float)
    mag = 20 * np.log10(np.abs(np.asarray(h)) + 1e-300)
    ref = mag[0] - 3.0
    return _interp(f, mag, ref)


def dc_gain_db(h) -> float:
    return float(20 * math.log10(abs(np.asarray(h)[0]) + 1e-300))


def unity_gain_freq(f, h) -> float:
    f = np.asarray(f, dtype=float)
    mag = 20 * np.log10(np.abs(h) + 1e-300)
    return _interp(f, mag, 0.0)


def phase_margin(f, loop_gain) -> float:
    """环路增益 T(jω) 在 |T|=0dB 处的相位余量（度）。"""
    f = np.asarray(f, dtype=float)
    T = np.asarray(loop_gain)
    mag = 20 * np.log10(np.abs(T) + 1e-300)
    f0 = _interp(f, mag, 0.0)
    if math.isnan(f0):
        return float("nan")
    ph = np.unwrap(np.angle(T))
    p = float(np.interp(f0, f, np.degrees(ph)))
    return 180.0 + p if p < 0 else 180.0 - p


def gain_margin(f, loop_gain) -> float:
    """相位过 -180° 时的 |T| 余量（dB，正数表示稳定）。"""
    f = np.asarray(f, dtype=float)
    T = np.asarray(loop_gain)
    ph = np.degrees(np.unwrap(np.angle(T)))
    f180 = _interp(f, ph, -180.0)
    if math.isnan(f180):
        return float("inf")
    mag = 20 * np.log10(np.abs(T) + 1e-300)
    return float(-np.interp(f180, f, mag))


# --------------------------------------------------------------------------- #
# 时域指标
# --------------------------------------------------------------------------- #

def crossing_times(t, y, level: float, rising: bool | None = None) -> np.ndarray:
    t = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float)
    n = min(len(t), len(y))
    t, y = t[:n], y[:n]
    d = y - level
    idx = np.where(np.sign(d[:-1]) != np.sign(d[1:]))[0]
    if rising is True:
        idx = np.array([i for i in idx if d[i + 1] > d[i]])
    elif rising is False:
        idx = np.array([i for i in idx if d[i + 1] < d[i]])
    out = []
    for i in idx:
        if d[i] == d[i + 1]:
            out.append(t[i])
        else:
            out.append(t[i] + (t[i + 1] - t[i]) * d[i] / (d[i] - d[i + 1]))
    return np.asarray(out)


def settling_time(t, y, target: float, tol: float = 0.01) -> float:
    y = np.asarray(y, dtype=float)
    t = np.asarray(t, dtype=float)
    band = abs(target) * tol
    outside = np.where(np.abs(y - target) > band)[0]
    if len(outside) == 0:
        return 0.0
    return float(t[outside[-1] + 1] if outside[-1] + 1 < len(t) else t[-1])


def overshoot(y, final: float | None = None) -> float:
    y = np.asarray(y, dtype=float)
    final = float(y[-1]) if final is None else final
    peak = y.max() if final >= y[0] else y.min()
    return float(abs(peak - final) / (abs(final - y[0]) + 1e-30) * 100)


def rise_time(t, y, low: float = 0.1, high: float = 0.9) -> float:
    y = np.asarray(y, dtype=float)
    lo, hi = y.min(), y.max()
    t1 = crossing_times(t, y, lo + (hi - lo) * low, rising=True)
    t2 = crossing_times(t, y, lo + (hi - lo) * high, rising=True)
    if len(t1) == 0 or len(t2) == 0:
        return float("nan")
    return float(t2[0] - t1[0])


def frequency(t, y, level: float | None = None) -> float:
    y = np.asarray(y, dtype=float)
    level = (y.max() + y.min()) / 2 if level is None else level
    tc = crossing_times(t, y, level, rising=True)
    if len(tc) < 2:
        return float("nan")
    return float(1.0 / np.mean(np.diff(tc)))


def period_jitter(t, y, level: float | None = None) -> dict:
    y = np.asarray(y, dtype=float)
    level = (y.max() + y.min()) / 2 if level is None else level
    tc = crossing_times(t, y, level, rising=True)
    per = np.diff(tc)
    if len(per) < 2:
        return {"period": float("nan"), "jitter_rms": float("nan")}
    return {"period": float(np.mean(per)),
            "jitter_rms": float(np.std(per)),
            "jitter_pp": float(np.max(per) - np.min(per))}


# --------------------------------------------------------------------------- #
# 眼图
# --------------------------------------------------------------------------- #

def eye(t, y, ui: float | None = None, ntraces: int = 200) -> dict:
    """按 UI 折叠波形，返回眼高/眼宽（60% 判决窗内的统计）。"""
    t = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float)
    ui = ui or (1.0 / frequency(t, y))
    if not ui or math.isnan(ui):
        return {"eye_height": float("nan"), "eye_width": float("nan")}
    npts = 200
    phase = ((t - t[0]) % ui) / ui
    grid = np.linspace(0, 1, npts, endpoint=False)
    traces = np.full((ntraces, npts), np.nan)
    start = int(0.5 * ui / max(np.mean(np.diff(t)), 1e-18))
    for k in range(ntraces):
        idx = np.arange(start + k * int(ui / np.mean(np.diff(t))),
                        start + (k + 1) * int(ui / np.mean(np.diff(t))))
        idx = idx[idx < len(t)]
        if len(idx) < 2:
            continue
        traces[k] = np.interp(grid, phase[idx], y[idx])
    mid = slice(int(0.2 * npts), int(0.8 * npts))
    hi = np.nanpercentile(traces[:, mid], 99)
    lo = np.nanpercentile(traces[:, mid], 1)
    return {"eye_height": float(hi - lo), "eye_width": float(ui * 0.6)}


__all__ = ["spectrum", "Spectrum", "bandwidth_3db", "dc_gain_db", "unity_gain_freq",
           "phase_margin", "gain_margin", "crossing_times", "settling_time",
           "overshoot", "rise_time", "frequency", "period_jitter", "eye"]
