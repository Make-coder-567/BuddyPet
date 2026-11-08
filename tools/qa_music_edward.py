"""Edward / QA 独立验收脚本：音乐状态检测延迟优化。

与 ``tools/bench_music_latency.py`` 完全独立编写，不引用其任何结论数字。
所有子进程请用临时 APPDATA + ``QT_QPA_PLATFORM=offscreen`` 运行。

用法::

    python tools/qa_music_edward.py --cmd semantics   # C 语义不退化（确定性，秒级）
    python tools/qa_music_edward.py --cmd bench       # A 真实 QTimer 延迟基准，3 个采样相位（约 75s）
    python tools/qa_music_edward.py --cmd risks       # B 四类触发风险 + P1 疑点（真实 QTimer，约 80s）
    python tools/qa_music_edward.py --cmd perf        # E 单次探测真实耗时
    python tools/qa_music_edward.py --cmd ast         # 静态：import / 常量 / 改动面

设计要点：
- 延迟基准用可控峰值 + **真实 QCoreApplication / QTimer 事件循环**，wall-clock
  时间由 ``time.monotonic()`` 记录；场景与 team-lead 基线一致（t=START 起播、
  t=START+18 停播），每个相位单独一次完整事件循环。
- 四类触发风险同样走真实 QTimer 路径（``_on_tick`` 传 elapsed），不只用手动
  ``_tick()``；手动推进只用于 C 段的确定性语义断言。
- 只报告，不改任何源码或产物。
"""
from __future__ import annotations

import argparse
import ast
import inspect
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PyQt5.QtCore import QCoreApplication, QTimer  # noqa: E402

from app import audio_probe  # noqa: E402
from app import music as music_mod  # noqa: E402
from app.music import MusicDetector  # noqa: E402

# team-lead 实测基线（可直接采信）
BASE_START = 14.041
BASE_STOP = 12.042
BASE_STARTED_T = 16.041
BASE_STOPPED_T = 32.042

RESULTS: list[tuple[str, bool, str]] = []


def add(section: str, name: str, cond: bool, detail: str = "") -> bool:
    RESULTS.append((section, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    if detail:
        print(f"         {detail}")
    return bool(cond)


def summary() -> int:
    ok = sum(1 for _, o, _ in RESULTS if o)
    print(f"\n小计 {ok}/{len(RESULTS)} PASS")
    for sec, o, detail in RESULTS:
        if not o:
            print(f"  FAIL 明细 [{sec}] {detail[:300]}")
    return 0 if ok == len(RESULTS) else 1


# ---------------------------------------------------------------- 可控采样
class Feeder:
    """可控峰值 / 窗口序列；取完后粘住最后一个值。"""

    def __init__(self) -> None:
        self.peaks: list = []
        self.apps: list = []
        self._last_peak: float | None = 0.0
        self._last_app: tuple = (False, "")

    def peak(self):
        if self.peaks:
            self._last_peak = self.peaks.pop(0)
        return self._last_peak

    def app(self):
        if self.apps:
            self._last_app = self.apps.pop(0)
        return self._last_app

    def set(self, peaks: list, apps: list | None = None) -> None:
        self.peaks = list(peaks)
        self.apps = list(apps) if apps is not None else [(False, "")] * len(peaks)


FEED = Feeder()


def attach(det: MusicDetector):
    log: list[tuple] = []
    sync: list[bool] = []
    det.started.connect(lambda k, t: (log.append(("started", k, t)), sync.append(det._playing is True)))
    det.stopped.connect(lambda: (log.append(("stopped",)), sync.append(det._playing is False)))
    det.state.connect(lambda p, k: log.append(("state", p, k)))
    return log, sync


def install_feed() -> tuple:
    orig = (audio_probe.probe_peak, audio_probe.detect_music_app)
    audio_probe.probe_peak = FEED.peak
    audio_probe.detect_music_app = FEED.app
    return orig


def restore_feed(orig: tuple) -> None:
    audio_probe.probe_peak, audio_probe.detect_music_app = orig


# ---------------------------------------------------------------- 真实事件循环
def _core_app() -> QCoreApplication:
    return QCoreApplication.instance() or QCoreApplication(sys.argv[:1])


def run_loop(name: str, total_s: float, peak_fn, *, app_fn=None, setup=None,
             count_sound: bool = False) -> dict:
    """在真实 QTimer 事件循环里跑一个场景，返回信号与证据。

    ``peak_fn(t)`` 中的 t 是相对本场景 t0 的墙钟秒；``setup(det)`` 在
    ``set_enabled`` 之前/之后自定义生命周期（默认只 ``set_enabled(True)``）。
    """
    app = _core_app()
    orig_peak, orig_app = audio_probe.probe_peak, audio_probe.detect_music_app
    t0 = time.monotonic()
    events: list[tuple] = []
    tick_times: list[float] = []
    sound_samples = [0]

    def _peak():
        t = time.monotonic() - t0
        tick_times.append(round(t, 3))
        v = peak_fn(t)
        if count_sound and v is not None and float(v) >= music_mod.START_PEAK:
            sound_samples[0] += 1
        return v

    def _appfn():
        t = time.monotonic() - t0
        return app_fn(t) if app_fn else (False, "")

    det = MusicDetector(None)
    det.started.connect(
        lambda k, t: events.append((round(time.monotonic() - t0, 3), "started", k, t)))
    det.stopped.connect(
        lambda: events.append((round(time.monotonic() - t0, 3), "stopped")))
    det.state.connect(
        lambda p, k: events.append((round(time.monotonic() - t0, 3), "state", p, k)))

    audio_probe.probe_peak = _peak
    audio_probe.detect_music_app = _appfn
    loop_t0 = time.monotonic()
    snap: dict = {}
    setup_n = len(events)
    try:
        if setup:
            setup(det)
        else:
            det.set_enabled(True)
        setup_n = len(events)   # setup 阶段（force/set_enabled）已发出的信号数
        QTimer.singleShot(int(total_s * 1000), app.quit)
        app.exec_()
    finally:
        wall = time.monotonic() - loop_t0
        # 必须在 det.stop() 之前抓状态快照。
        # P1 修复后 _manual_override 机制已被移除（force 回到旧语义），
        # 用 getattr 兼容两种实现，缺失时以 False 表示「无持久覆盖」。
        snap = {
            "_manual_override": getattr(det, "_manual_override", False),
            "_playing": det._playing,
            "_primed": det._primed,
            "_pending": det._pending,
            "_enabled": det._enabled,
            "timer_active": det._timer.isActive(),
        }
        audio_probe.probe_peak = orig_peak
        audio_probe.detect_music_app = orig_app
        det.stop()

    print(f"   状态快照(stop 前): {snap}")
    started = [e for e in events if e[1] == "started"]
    stopped = [e for e in events if e[1] == "stopped"]
    state = [e for e in events if e[1] == "state"]
    print(f"\n-- {name} --  循环 {wall:.3f}s / 采样 {len(tick_times)} 次 / "
          f"有声采样 {sound_samples[0]} 次")
    print(f"   started={started}")
    print(f"   stopped={stopped}")
    print(f"   state  ={state}")
    return {
        "name": name, "events": events, "started": started, "stopped": stopped,
        "state": state, "ticks": tick_times, "sound_samples": sound_samples[0],
        "wall": wall, "det": det, "snap": snap,
        "setup_n": setup_n, "loop_events": events[setup_n:],
    }


# ================================================================ A 延迟基准
def cmd_bench() -> int:
    print("=" * 82)
    print("A. 延迟基准（Edward 独立编写，真实 QCoreApplication + QTimer + 可控峰值）")
    print("=" * 82)
    print(f"常量: POLL_MS={music_mod.POLL_MS} FIRST_DELAY_MS={music_mod.FIRST_DELAY_MS} "
          f"DEBOUNCE={music_mod.DEBOUNCE} START_CONFIRM_MS={music_mod.START_CONFIRM_MS} "
          f"STOP_CONFIRM_MS={music_mod.STOP_CONFIRM_MS} APP_SCAN_EVERY={music_mod.APP_SCAN_EVERY}")
    print(f"阈值: START_PEAK={music_mod.START_PEAK} STOP_PEAK={music_mod.STOP_PEAK}")
    print(f"team-lead 基线: started @ {BASE_STARTED_T:.3f}s (延迟 {BASE_START:.3f}s), "
          f"stopped @ {BASE_STOPPED_T:.3f}s (延迟 {BASE_STOP:.3f}s)")

    phases = [2.000, 2.250, 2.450]
    rows: list[dict] = []
    for st in phases:
        sp = st + 18.0
        total = sp + 4.0

        def _peak(t: float, _st=st, _sp=sp) -> float:
            if t < _st:
                return 0.0
            if t < _sp:
                return 0.5
            return 0.0

        r = run_loop(f"相位 START={st:.3f}s / STOP={sp:.3f}s / 总长 {total:.1f}s",
                     total, _peak, count_sound=True)
        s_lat = (r["started"][0][0] - st) if len(r["started"]) == 1 else None
        p_lat = (r["stopped"][0][0] - sp) if len(r["stopped"]) == 1 else None
        rows.append({
            "phase": st, "stop_at": sp, "start_at": r["started"][0][0] if r["started"] else None,
            "stopped_at": r["stopped"][0][0] if r["stopped"] else None,
            "n_start": len(r["started"]), "n_stop": len(r["stopped"]),
            "start_lat": s_lat, "stop_lat": p_lat,
            "ticks": r["ticks"],
        })
        add("A", f"相位 {st:.3f}s：started/stopped 各恰好 1 次",
            len(r["started"]) == 1 and len(r["stopped"]) == 1,
            f"started={len(r['started'])} stopped={len(r['stopped'])}")

    print("\n实测汇总（墙钟秒）：")
    print(f"  {'相位':>8}{'起播点':>10}{'started@':>11}{'起播延迟':>11}"
          f"{'停播点':>10}{'stopped@':>11}{'停播延迟':>11}")
    for r in rows:
        print(f"  {r['phase']:>8.3f}{r['phase']:>10.3f}{r['start_at']:>11.3f}"
              f"{r['start_lat']:>11.3f}{r['stop_at']:>10.3f}{r['stopped_at']:>11.3f}"
              f"{r['stop_lat']:>11.3f}")

    sl = [r["start_lat"] for r in rows if r["start_lat"] is not None]
    pl = [r["stop_lat"] for r in rows if r["stop_lat"] is not None]
    if sl and pl:
        print(f"\n  起播延迟: 均值 {sum(sl)/len(sl):.3f}s  最小 {min(sl):.3f}s  最坏 {max(sl):.3f}s")
        print(f"  停播延迟: 均值 {sum(pl)/len(pl):.3f}s  最小 {min(pl):.3f}s  最坏 {max(pl):.3f}s")
        add("A", "起播延迟落在理论区间 1.0~1.5s", all(1.0 <= v <= 1.5 for v in sl),
            f"实测 {[round(v,3) for v in sl]}")
        add("A", "停播延迟落在理论区间 1.5~2.0s", all(1.5 <= v <= 2.0 for v in pl),
            f"实测 {[round(v,3) for v in pl]}")
        print("\n改动前后延迟对比（Edward 实测）：")
        print(f"  {'方向':<8}{'基线':>12}{'优化后均值':>14}{'优化后最坏':>14}{'均值降幅':>12}{'最坏降幅':>12}")
        for nm, base, vals in (("起播", BASE_START, sl), ("停播", BASE_STOP, pl)):
            mean = sum(vals) / len(vals)
            worst = max(vals)
            print(f"  {nm:<8}{base:>10.3f}s{mean:>13.3f}s{worst:>13.3f}s"
                  f"{(base-mean)/base*100:>11.1f}%{(base-worst)/base*100:>11.1f}%")
        add("A", "两类操作延迟均显著降低（相对基线降幅 > 80%）",
            (BASE_START - sum(sl)/len(sl)) / BASE_START > 0.8
            and (BASE_STOP - sum(pl)/len(pl)) / BASE_STOP > 0.8,
            f"起播 {(BASE_START-sum(sl)/len(sl))/BASE_START*100:.1f}% "
            f"停播 {(BASE_STOP-sum(pl)/len(pl))/BASE_STOP*100:.1f}%")
    return summary()


# ================================================================ B 四类触发风险
def cmd_risks() -> int:
    print("=" * 82)
    print("B. 四类触发风险（真实 QTimer 路径，_on_tick 传 elapsed）")
    print("=" * 82)

    # ---- 误触发：0.2s 瞬时爆音 ----
    r = run_loop("误触发 / 0.2s 瞬时爆音 [2.0,2.2)", 6.0,
                 lambda t: 0.5 if 2.0 <= t < 2.2 else 0.0, count_sound=True)
    add("B-误触发", "0.2s 瞬时爆音不发 started",
        len(r["started"]) == 0 and r["snap"]["_playing"] is False,
        f"有声采样数={r['sound_samples']} started={len(r['started'])} playing={r['det']._playing}")

    # ---- 误触发：0.5s 系统提示音（恰好命中 2 个 500ms 采样点）----
    r = run_loop("误触发 / 0.5s 提示音 [2.0,2.6) 命中 2 个采样点", 6.0,
                 lambda t: 0.5 if 2.0 <= t < 2.6 else 0.0, count_sound=True)
    add("B-误触发", "0.5s 系统提示音（2 个采样点）不发 started",
        len(r["started"]) == 0 and r["snap"]["_playing"] is False
        and 1 <= r["sound_samples"] <= 2,
        f"有声采样数={r['sound_samples']} started={len(r['started'])} playing={r['det']._playing}")

    # ---- 误触发：播放中 0.5s 静音间隙 ----
    def _gap(g0: float, g1: float):
        def f(t: float) -> float:
            if t < 2.0:
                return 0.0
            if g0 <= t < g1:
                return 0.0
            return 0.5
        return f

    r = run_loop("误触发 / 播放中 0.5s 静音间隙 [6.0,6.5)", 9.5, _gap(6.0, 6.5))
    add("B-误触发", "播放中 0.5s 静音间隙不发 stopped",
        len(r["started"]) == 1 and len(r["stopped"]) == 0 and r["snap"]["_playing"] is True,
        f"started={len(r['started'])} stopped={len(r['stopped'])} playing={r['det']._playing}")

    r = run_loop("误触发 / 播放中 1.0s 静音间隙 [6.0,7.0)", 9.5, _gap(6.0, 7.0))
    add("B-误触发", "播放中 1.0s 静音间隙不发 stopped",
        len(r["started"]) == 1 and len(r["stopped"]) == 0 and r["snap"]["_playing"] is True,
        f"started={len(r['started'])} stopped={len(r['stopped'])} playing={r['det']._playing}")

    # ---- 漏触发 / 重复触发 / state 一致性 ----
    r = run_loop("漏触发+重复触发 / 持续有声 [2.0,7.0) 后持续静音 [7.0,13.0)", 13.0,
                 lambda t: 0.5 if 2.0 <= t < 7.0 else 0.0, count_sound=True)
    add("B-漏触发", "持续有声 5s 必发 started（≥2s 要求满足）",
        len(r["started"]) == 1, f"started={r['started']}")
    add("B-漏触发", "持续静音 6s 必发 stopped（≥2.5s 要求满足）",
        len(r["stopped"]) == 1, f"stopped={r['stopped']}")
    add("B-重复触发", "连续多次同向采样只发 1 组 started / stopped",
        len(r["started"]) == 1 and len(r["stopped"]) == 1,
        f"有声采样数={r['sound_samples']} started={len(r['started'])} stopped={len(r['stopped'])}")
    add("B-状态不同步", "一次翻转只发一组 started/stopped/state 且 playing 与信号一致",
        len(r["state"]) == 2
        and r["state"][0][2:] == (True, "audio")
        and r["state"][1][2:] == (False, "")
        and r["snap"]["_playing"] is False,
        f"state={r['state']} playing={r['det']._playing}")

    # ---- 边界：长静音间隙（工程师列为已知取舍）----
    for g in (1.2, 1.5, 2.0):
        r = run_loop(f"边界 / 播放中 {g:.1f}s 长静音间隙 [5.0,{5.0+g:.1f})", 10.0,
                     _gap(5.0, 5.0 + g))
        print(f"   -> 间隙 {g:.1f}s: stopped={len(r['stopped'])} "
              f"started={len(r['started'])} playing={r['det']._playing}")
        add("B-边界", f"{g:.1f}s 长静音间隙实测结论已记录（非 FAIL 判定）", True,
            f"stopped={len(r['stopped'])} started={len(r['started'])}")

    # ---- P1 回归：真实 QTimer 下 force() 后自动检测仍工作 ----
    # 修复后 force() 只注入一次状态、不挂起自动检测；这里验证真实时间轴上
    # 仍能观察到自动信号（修复前为 0 次）。
    def _setup_override(det):
        det.set_enabled(True)
        det.force(True, "music", "命令曲")
        det.set_enabled(True)

    p1_peak = lambda t: (0.5 if 2.0 <= t < 6.0 else 0.0)

    # 对照组：同一时间轴、未 force → 应当 started=1 / stopped=1
    rc = run_loop("P1 / 对照组（同时间轴、无 force）自动检测正常", 10.0, p1_peak)
    add("B-P1", "[对照组] 同一时间轴下自动检测正常起播并停播",
        len(rc["loop_events"]) and
        sum(1 for x in rc["loop_events"] if x[1] == "started") == 1
        and sum(1 for x in rc["loop_events"] if x[1] == "stopped") == 1,
        f"loop_events={rc['loop_events']}")

    r = run_loop("P1 / set_enabled(True)→force(True)→set_enabled(True) 后真实状态变化",
                 10.0, p1_peak,
                 setup=_setup_override, count_sound=True)
    loop_start = sum(1 for x in r["loop_events"] if x[1] == "started")
    loop_stop = sum(1 for x in r["loop_events"] if x[1] == "stopped")
    add("B-P1", "[回归] enable→force(True)→enable 后自动检测未挂起，仍发自动信号",
        loop_start >= 1 and loop_stop >= 1
        and r["snap"]["_manual_override"] is False,
        f"override={r['snap']['_manual_override']} playing={r['snap']['_playing']} "
        f"事件循环内 started={loop_start} stopped={loop_stop} "
        f"（setup 阶段 force 自身发了 {r['setup_n']} 个信号，已排除）")

    # ---- disable→enable 重启后自动检测仍能起播 ----
    def _setup_cycle(det):
        det.set_enabled(True)
        det.force(True, "music", "命令曲")
        det.set_enabled(False)
        det.set_enabled(True)

    r2 = run_loop("P1 / disable→enable 重启后自动检测恢复", 7.0,
                  lambda t: 0.5 if 2.0 <= t < 6.0 else 0.0,
                  setup=_setup_cycle)
    r2_start = sum(1 for x in r2["loop_events"] if x[1] == "started")
    add("B-P1", "disable→enable 周期可解除覆盖并恢复自动起播",
        r2_start == 1 and r2["snap"]["_manual_override"] is False,
        f"事件循环内 started={r2_start} @ {r2['loop_events']} "
        f"override={r2['snap']['_manual_override']}（setup 阶段 {r2['setup_n']} 个信号已排除）")

    return summary()


# ================================================================ C 语义不退化
def cmd_semantics() -> int:
    print("=" * 82)
    print("C. 语义不退化（ Edward 独立确定性用例）")
    print("=" * 82)
    orig = install_feed()
    try:
        # ---- C1 对外接口 ----
        sig = {n: inspect.signature(getattr(MusicDetector, n))
               for n in ("__init__", "set_enabled", "force", "poll_now", "stop")}
        print(f"  方法签名: {sig}")
        d = MusicDetector(None)
        print(f"  信号签名: started={d.started.signal!r} stopped={d.stopped.signal!r} state={d.state.signal!r}")
        names_ok = (
            list(sig["__init__"].parameters) == ["self", "cfg", "parent"]
            and list(sig["set_enabled"].parameters) == ["self", "value"]
            and list(sig["force"].parameters) == ["self", "playing", "kind", "title"]
            and sig["force"].parameters["kind"].default == "music"
            and sig["force"].parameters["title"].default == ""
            and list(sig["poll_now"].parameters) == ["self"]
            and list(sig["stop"].parameters) == ["self"]
        )
        sig_ok = ("started(QString,QString)" in d.started.signal
                  and "stopped()" in d.stopped.signal
                  and "state(bool,QString)" in d.state.signal)
        FEED.set([0.0])
        info = d.poll_now()
        need = {"playing", "kind", "title", "peak", "music_app", "degraded", "enabled"}
        add("C1", "接口签名（信号 3 个 / 方法 5 个）与 poll_now 七字段完全不变",
            names_ok and sig_ok and set(info.keys()) == need,
            f"names_ok={names_ok} sig_ok={sig_ok} keys={sorted(info.keys())}")

        # ---- C2 滞回双阈值 ----
        d2 = MusicDetector(None)
        log2, sync2 = attach(d2)
        FEED.set([0.0])
        d2._tick(prime_only=True)
        FEED.set([0.01]); d2._tick()
        FEED.set([0.01]); d2._tick()
        base2 = len(log2)
        FEED.set([0.002, 0.002]); d2._tick(); d2._tick()
        hy1 = len(log2) == base2 and d2._playing is True
        FEED.set([0.001]); d2._tick()
        pending_first = d2._pending
        hy2 = pending_first == 1 and len(log2) == base2
        FEED.set([0.001]); d2._tick()
        hy3 = d2._playing is False and log2[-2:] == [("stopped",), ("state", False, "")]
        add("C2", "滞回：0.002 在 playing 态保持 / 0.001 连续 2 次触发停",
            hy1 and hy2 and hy3,
            f"0.002 不发={hy1} 0.001 首次 pending={pending_first}({hy2}) 停播={hy3} log={log2}")

        # ---- C3 首采静默（真实 QTimer）----
        def _first_sample_peak(_t):
            return 0.5
        r3 = run_loop("C3 / set_enabled(True) 后全程有声，1.40s 内观察信号", 1.40,
                      _first_sample_peak)
        add("C3", "1500ms 首采静默：set_enabled(True) 后 1.4s 内不发任何信号",
            len(r3["events"]) == 0,
            f"events={r3['events']} 采样时刻={r3['ticks']} primed={r3['snap']['_primed']}")
        r3b = run_loop("C3 / 全程有声运行到 1.80s（首采已过）", 1.80, _first_sample_peak)
        add("C3", "1.8s 内首采只建档不发信号，之后同向采样也不补发",
            len(r3b["events"]) == 0 and r3b["snap"]["_primed"] is True
            and r3b["snap"]["_playing"] is True,
            f"events={r3b['events']} 采样时刻={r3b['ticks']} "
            f"primed={r3b['snap']['_primed']} playing={r3b['snap']['_playing']}")
        print(f"  [INFO] 首次真实采样时刻 = {r3b['ticks'][0] if r3b['ticks'] else 'n/a'}s；"
              f"FIRST_DELAY_MS={music_mod.FIRST_DELAY_MS}ms 的 singleShot(_first_sample) "
              f"在 POLL_MS={music_mod.POLL_MS}ms 下已被首个 QTimer tick 抢先建档，"
              f"实际成为 no-op（无信号语义仍成立，记为遗留信息项）")

        # ---- C4 force 幂等 ----
        d4 = MusicDetector(None)
        log4, sync4 = attach(d4)
        d4.force(True, "music", "A")
        a = len(log4)
        d4.force(True, "music", "A")
        b = len(log4)
        d4.force(False)
        c = len(log4)
        d4.force(False)
        e = len(log4)
        d4b = MusicDetector(None)
        log4b, _ = attach(d4b)
        d4b.force(False)
        f = len(log4b)
        add("C4", "force 幂等：同状态同元信息不重复发 / 无基线首调必发一次",
            a == 2 and b == 2 and c == 4 and e == 4 and f == 2 and all(sync4),
            f"force(True)={a} 重复={b} force(False)={c} 重复={e} "
            f"无基线 force(False)={f} sync={sync4} log={log4}")

        # ---- C5 set_enabled(False) 连续两次 / stop 幂等 / enable-disable-enable ----
        d5 = MusicDetector(None)
        log5, sync5 = attach(d5)
        d5.force(True, "music", "A")
        d5.set_enabled(False)
        n1 = sum(1 for x in log5 if x[0] == "stopped")
        d5.set_enabled(False)
        n2 = sum(1 for x in log5 if x[0] == "stopped")
        d5.stop(); d5.stop()
        stop_idem = d5._enabled is False and d5._primed is False
        d5.set_enabled(True)
        restarted = (d5._timer.isActive() and d5._primed is False
                     and getattr(d5, "_manual_override", False) is False
                     and d5._pending == 0 and d5._enabled is True)
        add("C5", "set_enabled(False) 连续两次只发 1 次 stopped / stop() 幂等 / 重启正确",
            n1 == 1 and n2 == 1 and d5._playing is False and stop_idem and restarted,
            f"stopped#1={n1} #2={n2} playing={d5._playing} "
            f"stop_idem={stop_idem} restarted={restarted} sync={sync5}")
        d5.stop()

        # ---- C6 peak None 降级 ----
        d6 = MusicDetector(None)
        log6, _ = attach(d6)
        FEED.set([0.0]); d6._tick(prime_only=True)
        FEED.set([None], [(True, "网易云音乐 - 晴天")])
        info6 = d6.poll_now()
        FEED.set([None, None], [(True, "网易云音乐 - 晴天")] * 2)
        d6._tick(); d6._tick()
        add("C6", "peak=None 降级：degraded=True / 按音乐窗口判定 / 能发 started",
            info6["degraded"] is True and info6["kind"] == "music"
            and info6["title"] == "网易云音乐 - 晴天"
            and ("started", "music", "网易云音乐 - 晴天") in log6
            and d6._playing is True,
            f"info={info6} log={log6}")

        # ---- C7 异常兜底 ----
        d7 = MusicDetector(None)
        real_peak, real_app = audio_probe.probe_peak, audio_probe.detect_music_app
        calls = {"n": 0}

        def _boom():
            calls["n"] += 1
            raise RuntimeError("edward: 模拟 probe_peak 异常")

        try:
            audio_probe.probe_peak = _boom
            info7 = d7.poll_now()
            d7._tick()      # 自动轮询路径同样不得抛异常
            info7b = d7.poll_now()
        except Exception as exc:
            info7, info7b = {}, {}
            print(f"   [异常抛出] {exc!r}")
        finally:
            audio_probe.probe_peak, audio_probe.detect_music_app = real_peak, real_app
        add("C7", "probe_peak 抛异常：poll_now 安全值 + 7 字段齐全 + _tick 不崩",
            set(info7.keys()) == need and info7["degraded"] is True
            and info7["playing"] is False and info7["peak"] is None
            and info7["enabled"] is False
            and set(info7b.keys()) == need and calls["n"] >= 3,
            f"info={info7} 异常调用次数={calls['n']}")

        # ---- C8 APP_SCAN_EVERY 缓存不污染 kind/title ----
        d8 = MusicDetector(None)
        log8, _ = attach(d8)
        real_app8 = audio_probe.detect_music_app
        state8 = {"v": (False, "")}
        cnt8 = [0]

        def _counting_app():
            cnt8[0] += 1
            return state8["v"]

        try:
            audio_probe.detect_music_app = _counting_app
            FEED.set([0.0]); d8._tick(prime_only=True)
            base8 = cnt8[0]
            FEED.set([0.0] * 12)
            for _ in range(12):
                d8._tick(elapsed=0.0, allow_app_cache=True)
            steady8 = cnt8[0] - base8
            state8["v"] = (True, "网易云音乐 - 晴天")
            before_flip = cnt8[0]
            FEED.set([0.5] * 3)
            for el in (0.0, 0.5, 1.0):
                d8._tick(elapsed=el, allow_app_cache=True)
            pending_scans = cnt8[0] - before_flip
        finally:
            audio_probe.detect_music_app = real_app8
        add("C8", "稳态降频枚举 / 翻转前真实枚举 / started 携带新鲜 kind+title",
            steady8 <= 4 and pending_scans >= 2
            and ("started", "music", "网易云音乐 - 晴天") in log8,
            f"稳态 12 次轮询真实枚举={steady8} 次；待确认窗口真实枚举={pending_scans} 次；log={log8}")

        # ---- C9 poll_now 明细实时更新（force 覆盖期间）----
        d9 = MusicDetector(None)
        d9.force(True, "music", "命令曲")
        FEED.set([0.0])
        info9 = d9.poll_now()
        FEED.set([0.5])
        info9b = d9.poll_now()
        add("C9", "force() 覆盖期间 poll_now()/music:status 明细仍实时更新且 enabled 存在",
            info9["playing"] is False and info9["peak"] == 0.0
            and info9b["playing"] is True and info9b["peak"] == 0.5
            and info9b["enabled"] is False and d9._playing is True,
            f"静音={info9} 有声={info9b} _playing={d9._playing}")

        # ---- C9b 确定性补充：2 个采样点（0.5s 提示音）被确认窗口挡住 ----
        d9b = MusicDetector(None)
        log9b, _ = attach(d9b)
        FEED.set([0.0]); d9b._tick(prime_only=True)
        FEED.set([0.5, 0.5, 0.5])
        d9b._tick(elapsed=0.0, allow_app_cache=True)   # pending=1
        d9b._tick(elapsed=0.5, allow_app_cache=True)   # pending=2 但 500ms < 600ms → 不发
        n_after_2 = sum(1 for x in log9b if x[0] == "started")
        pending_after_2 = d9b._pending
        blocked = (n_after_2 == 0 and pending_after_2 == 2)
        d9b._tick(elapsed=1.0, allow_app_cache=True)   # pending=3, 1000ms ≥ 600ms → 发
        n_after_3 = sum(1 for x in log9b if x[0] == "started")
        allowed = n_after_3 == 1
        add("C9b", "确定性：2 个同向采样点（0.5s 提示音）被 START_CONFIRM_MS 挡住，3 个点才起播",
            blocked and allowed,
            f"2 点后 started={n_after_2} pending={pending_after_2}；"
            f"3 点后 started={n_after_3} log={log9b}")

        # ---- C10 / P1 回归：确定性最小复现（修复后应为 PASS）----
        print("\n  ---- C10 / P1 回归：force 后自动检测不漏触发（确定性，无事件循环）----")
        # 对照组 A：正常自动检测，playing=True 后遇持续静音应发 stopped
        cA = MusicDetector(None)
        logA, _ = attach(cA)
        FEED.set([0.5]); cA._tick(prime_only=True)       # 建档 playing=True 不发信号
        FEED.set([0.0] * 8)
        for _ in range(8):
            cA._tick(elapsed=2.0, allow_app_cache=True)
        # 复现组 A：force(True) 后按 docstring 用 set_enabled(True) 解除覆盖
        pA = MusicDetector(None)
        logPA, _ = attach(pA)
        pA.set_enabled(True)
        pA.force(True, "music", "命令曲")
        ov_after_force = getattr(pA, "_manual_override", False)
        pA.set_enabled(True)
        ov_after_enable = getattr(pA, "_manual_override", False)
        timer_active = pA._timer.isActive()
        FEED.set([0.0] * 8)
        for _ in range(8):
            pA._tick(elapsed=2.0, allow_app_cache=True)
        nA_stop = sum(1 for x in logPA if x[0] == "stopped")
        print(f"   对照组 A stopped={sum(1 for x in logA if x[0]=='stopped')} log={logA}")
        print(f"   复现组 A: force 后 override={ov_after_force}, "
              f"set_enabled(True) 后 override={ov_after_enable}, timer.isActive()={timer_active}")
        print(f"   复现组 A stopped={nA_stop} _playing={pA._playing} log={logPA}")
        add("C10", "[回归] 无持久覆盖（_manual_override 机制已移除/恒为 False）",
            ov_after_force is False and ov_after_enable is False,
            f"force 后={ov_after_force}、set_enabled(True) 后={ov_after_enable}（均期望 False）")
        add("C10", "[回归] enable→force(True)→enable 后真实停播不漏触发",
            sum(1 for x in logA if x[0] == "stopped") == 1 and nA_stop == 1
            and pA._playing is False,
            f"对照组 stopped=1 证明序列有效；复现组 stopped={nA_stop} "
            f"playing={pA._playing}（修复前为 0 / True）")

        # 对照组 B：正常自动检测，静音建档后遇持续有声应发 started
        cB = MusicDetector(None)
        logB, _ = attach(cB)
        FEED.set([0.0]); cB._tick(prime_only=True)
        FEED.set([0.5] * 8)
        for _ in range(8):
            cB._tick(elapsed=1.0, allow_app_cache=True)
        pB = MusicDetector(None)
        logPB, _ = attach(pB)
        pB.set_enabled(True)
        pB.force(False)
        pB.set_enabled(True)
        FEED.set([0.5] * 8)
        for _ in range(8):
            pB._tick(elapsed=1.0, allow_app_cache=True)
        nB_start = sum(1 for x in logPB if x[0] == "started")
        print(f"   对照组 B started={sum(1 for x in logB if x[0]=='started')} log={logB}")
        print(f"   复现组 B started={nB_start} _playing={pB._playing} "
              f"override={getattr(pB, '_manual_override', False)} log={logPB}")
        add("C10", "[回归] enable→force(False)→enable 后真实起播不漏触发",
            sum(1 for x in logB if x[0] == "started") == 1 and nB_start == 1
            and pB._playing is True,
            f"对照组 started=1 证明序列有效；复现组 started={nB_start} "
            f"playing={pB._playing}（修复前为 0 / False）")

        # disable→enable 周期仍能正确重启
        pB.set_enabled(False)
        pB.set_enabled(True)
        cleared = getattr(pB, "_manual_override", False) is False
        FEED.set([0.0]); pB._tick(prime_only=True)
        FEED.set([0.5] * 4)
        for _ in range(4):
            pB._tick(elapsed=1.0, allow_app_cache=True)
        print(f"   disable→enable 后 override={getattr(pB, '_manual_override', False)}，"
              f"started={sum(1 for x in logPB if x[0]=='started')}")
        add("C10", "[回归] disable→enable 周期可重启自动检测",
            cleared and sum(1 for x in logPB if x[0] == "started") >= 2,
            f"cleared={cleared} 累计 started={sum(1 for x in logPB if x[0]=='started')}")
        pA.stop(); pB.stop(); cA.stop(); cB.stop()
    finally:
        restore_feed(orig)
    return summary()


# ================================================================ E 性能
def cmd_perf() -> int:
    print("=" * 82)
    print("E. 单次探测真实耗时（真机 COM / EnumWindows，非 mock）")
    print("=" * 82)

    def measure(fn, n: int) -> list[float]:
        for _ in range(5):
            fn()
        out = []
        for _ in range(n):
            t = time.perf_counter()
            fn()
            out.append((time.perf_counter() - t) * 1000.0)
        return out

    p = measure(audio_probe.probe_peak, 200)
    a = measure(audio_probe.detect_music_app, 200)
    p_mean = sum(p) / len(p)
    a_mean = sum(a) / len(a)
    p_max = max(p)
    a_max = max(a)
    print(f"  probe_peak()      n=200 均值 {p_mean:.3f} ms 最大 {p_max:.3f} ms")
    print(f"  detect_music_app() n=200 均值 {a_mean:.3f} ms 最大 {a_max:.3f} ms")
    print(f"  合计单次采样（无缓存）均值 {p_mean + a_mean:.3f} ms")
    cyc_worst = (p_mean + a_mean) / (music_mod.POLL_MS) * 100.0
    cyc_cached = (p_mean + a_mean / music_mod.APP_SCAN_EVERY) / (music_mod.POLL_MS) * 100.0
    print(f"  500ms 周期估算单核占比：每轮都枚举 {cyc_worst:.3f}% ；"
          f"APP_SCAN_EVERY={music_mod.APP_SCAN_EVERY} 稳态降频 {cyc_cached:.3f}%")
    print(f"  对照：原 8000ms 周期（每轮都枚举）"
          f"{(p_mean + a_mean) / 8000 * 100:.4f}%")
    add("E", "高频轮询 CPU 开销可忽略（< 5% 单核）",
        cyc_worst < 5.0, f"最坏 {cyc_worst:.3f}% / 稳态 {cyc_cached:.3f}%")
    return summary()


# ================================================================ 静态改动面
def cmd_ast() -> int:
    print("=" * 82)
    print("静态：import / 常量 / 改动面")
    print("=" * 82)
    src = (ROOT / "app" / "music.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imports = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            imports += [f"import {a.name}" for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imports.append(f"from {'.'*node.level}{node.module or ''} import "
                           + ", ".join(a.name for a in node.names))
    print("  app/music.py 顶层 import:")
    for i in imports:
        print(f"    {i}")
    consts = {k: getattr(music_mod, k) for k in
              ("POLL_MS", "FIRST_DELAY_MS", "START_PEAK", "STOP_PEAK", "DEBOUNCE",
               "START_CONFIRM_MS", "STOP_CONFIRM_MS", "APP_SCAN_EVERY")}
    print(f"  常量: {consts}")
    add("AST", "app/music.py 只新增 import time，无新第三方依赖",
        any("import time" == i for i in imports)
        and not any(("PyQt5" not in i and "audio_probe" not in i
                     and "__future__" not in i and "import time" != i) for i in imports),
        f"imports={imports}")
    return summary()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cmd", required=True,
                    choices=["bench", "risks", "semantics", "perf", "ast"])
    args = ap.parse_args()
    print(f"APPDATA = {os.environ.get('APPDATA', '<未设置>')}")
    print(f"QT_QPA_PLATFORM = {os.environ.get('QT_QPA_PLATFORM', '<未设置>')}")
    return {"bench": cmd_bench, "risks": cmd_risks, "semantics": cmd_semantics,
            "perf": cmd_perf, "ast": cmd_ast}[args.cmd]()


if __name__ == "__main__":
    sys.exit(main())
