"""music.py 播放状态检测延迟基准 + 四类触发风险针对性用例。

用法（务必用临时 APPDATA，不要污染用户真实配置）::

    set QT_QPA_PLATFORM=offscreen
    set APPDATA=<临时目录>
    python tools/bench_music_latency.py                # 延迟基准 + 全部用例
    python tools/bench_music_latency.py --bench         # 只跑延迟基准（约 24s）
    python tools/bench_music_latency.py --cases         # 只跑针对性用例（秒级）
    python tools/bench_music_latency.py --baseline      # 只打印 team-lead 实测基线表

说明
----
- 延迟基准：可控峰值 + **真实 QTimer**（QCoreApplication 事件循环），复现
  team-lead 的基线场景 —— t=2.000s 起播 ``peak 0→0.5``，t=20.000s 停播
  ``peak 0.5→0``，记录 ``started`` / ``stopped`` 的真实墙钟时间与延迟。
- ``--baseline`` 不重复测量（旧参数跑完要 32s+），只打印 team-lead 已实测的
  基线值作为对照。
- 针对性用例：可控峰值序列 + 显式注入 ``elapsed``（模拟真实定时器推进，
  确定性、无需等墙钟），覆盖误触发 / 漏触发 / 重复触发 / 状态不同步、
  ``force()`` 语义、``peak is None`` 降级、``poll_now()`` 异常兜底。

不启动任何可见 GUI 窗口；不引入任何新依赖。
"""
from __future__ import annotations

import argparse
import math
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
from app import config  # noqa: E402
from app import music as music_mod  # noqa: E402
from app.music import MusicDetector  # noqa: E402

#: team-lead 实测基线（POLL_MS=8000 / DEBOUNCE=2 / FIRST_DELAY_MS=1500）
BASELINE_STARTED_S = 16.041
BASELINE_STOPPED_S = 32.042
BASELINE_START_LATENCY_S = 14.041
BASELINE_STOP_LATENCY_S = 12.042

#: 基准场景时间轴（秒）
START_AT_S = 2.0
STOP_AT_S = 20.0
BENCH_TOTAL_S = 24.0


class Feeder:
    """可控的 ``probe_peak`` / ``detect_music_app`` 序列。

    与 ``qa_features._Ctl`` 同口径：序列取完后**粘住最后一个值**，
    避免后续 tick 意外落到 None 把测试自己搞乱。
    """

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


def _print_constants() -> None:
    print("当前常量：")
    print(f"  POLL_MS            = {music_mod.POLL_MS} ms")
    print(f"  FIRST_DELAY_MS     = {music_mod.FIRST_DELAY_MS} ms")
    print(f"  START_PEAK         = {music_mod.START_PEAK}")
    print(f"  STOP_PEAK          = {music_mod.STOP_PEAK}")
    print(f"  DEBOUNCE           = {music_mod.DEBOUNCE}")
    print(f"  START_CONFIRM_MS   = {music_mod.START_CONFIRM_MS} ms")
    print(f"  STOP_CONFIRM_MS    = {music_mod.STOP_CONFIRM_MS} ms")
    print(f"  APP_SCAN_EVERY     = {music_mod.APP_SCAN_EVERY}")
    n_start = math.ceil(music_mod.START_CONFIRM_MS / music_mod.POLL_MS) + 1
    print(f"  理论起播延迟       ≈ 采样相位(0~{music_mod.POLL_MS}ms) + 连续 {n_start} 个同向采样点"
          f" ≈ {(n_start - 1) * music_mod.POLL_MS / 1000:.1f}~{n_start * music_mod.POLL_MS / 1000:.1f}s"
          f"（窗口 {music_mod.START_CONFIRM_MS}ms）")
    print(f"  理论停播延迟       ≈ 采样相位(0~{music_mod.POLL_MS}ms) + 连续静音 {music_mod.STOP_CONFIRM_MS}ms"
          f" ≈ 1.5~2.0s")


def _print_baseline() -> None:
    print("\n基线（team-lead 实测，POLL_MS=8000 / DEBOUNCE=2 / FIRST_DELAY_MS=1500）：")
    print(f"  起播 t=2.000s  -> started  @ {BASELINE_STARTED_S:.3f}s  延迟 {BASELINE_START_LATENCY_S:.3f}s")
    print(f"  停播 t=20.000s -> stopped  @ {BASELINE_STOPPED_S:.3f}s  延迟 {BASELINE_STOP_LATENCY_S:.3f}s")
    print("  理论最坏 16s（8s 采样相位 + 8s 第二次确认），均值约 12s")


def run_bench(feed: Feeder) -> int:
    """真实 QTimer 事件循环测量起播 / 停播延迟。返回进程退出码。"""
    _print_constants()
    print(f"\n场景：t={START_AT_S:.3f}s 起播(peak 0→0.5)，t={STOP_AT_S:.3f}s 停播(peak 0.5→0)")
    print(f"运行 {BENCH_TOTAL_S:.0f}s 真实事件循环，请稍候…")

    app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    t0 = time.monotonic()

    def _timed_peak() -> float:
        t = time.monotonic() - t0
        if t < START_AT_S:
            return 0.0
        if t < STOP_AT_S:
            return 0.5
        return 0.0

    feed.set([])                        # 窗口枚举恒定 (False, "")
    audio_probe.probe_peak = _timed_peak   # 按时间轴返回可控峰值
    started_at: list[float] = []
    stopped_at: list[float] = []
    det = MusicDetector(None)
    det.started.connect(lambda _k, _t: started_at.append(time.monotonic() - t0))
    det.stopped.connect(lambda: stopped_at.append(time.monotonic() - t0))

    det.set_enabled(True)
    loop_t0 = time.monotonic()
    QTimer.singleShot(int(BENCH_TOTAL_S * 1000), app.quit)
    app.exec_()
    elapsed_loop = time.monotonic() - loop_t0
    print(f"  事件循环实际运行 {elapsed_loop:.3f}s；结束时 primed={det._primed} "
          f"pending={det._pending} playing={det._playing}")
    det.stop()

    ok = True
    if len(started_at) != 1:
        print(f"  [FAIL] started 次数 {len(started_at)}（期望 1）")
        ok = False
    if len(stopped_at) != 1:
        print(f"  [FAIL] stopped 次数 {len(stopped_at)}（期望 1）")
        ok = False
    if not ok:
        return 1

    start_lat = started_at[0] - START_AT_S
    stop_lat = stopped_at[0] - STOP_AT_S
    print("\n实测（真实墙钟）：")
    print(f"  started @ t={started_at[0]:.3f}s  起播延迟 = {start_lat:.3f}s")
    print(f"  stopped @ t={stopped_at[0]:.3f}s  停播延迟 = {stop_lat:.3f}s")

    _print_baseline()
    print("\n延迟对比：")
    print(f"  {'方向':<8}{'基线':>12}{'优化后':>12}{'降低':>12}")
    for name, base, now in (
        ("起播", BASELINE_START_LATENCY_S, start_lat),
        ("停播", BASELINE_STOP_LATENCY_S, stop_lat),
    ):
        drop = (base - now) / base * 100.0 if base > 0 else 0.0
        print(f"  {name:<8}{base:>10.3f}s{now:>11.3f}s{drop:>11.1f}%")
    return 0


def _attach(det: MusicDetector) -> tuple[list, list]:
    """挂上信号记录；第二项记录「发出信号时 _playing 是否同步」。"""
    log: list[tuple] = []
    sync: list[bool] = []
    det.started.connect(lambda k, t: (log.append(("started", k, t)), sync.append(det._playing is True)))
    det.stopped.connect(lambda: (log.append(("stopped",)), sync.append(det._playing is False)))
    det.state.connect(lambda p, k: log.append(("state", p, k)))
    return log, sync


def run_cases(feed: Feeder) -> int:
    """四类触发风险 + 降级 / 异常兜底 / force 语义的针对性用例。"""
    results: list[tuple[str, bool, str]] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))

    # ---- 1) 误触发：瞬时爆音（0.2s 有声后静音）不得发 started ----
    det1 = MusicDetector(None)
    log1, _ = _attach(det1)
    feed.set([0.0])
    det1._tick(prime_only=True)            # 建档：静音
    feed.set([0.5, 0.0, 0.0, 0.0, 0.0])   # 一次爆音，随后全是静音
    for e in (0.0, 0.5, 1.0, 1.5, 2.0):
        det1._tick(elapsed=e, allow_app_cache=True)
    add("误触发：瞬时爆音不发 started",
        not any(x[0] == "started" for x in log1) and det1._playing is False,
        f"log={log1} playing={det1._playing}")

    # ---- 1b) 误触发：0.5s 系统提示音（恰好命中 2 个采样点）不得发 started ----
    det1b = MusicDetector(None)
    log1b, _ = _attach(det1b)
    feed.set([0.0])
    det1b._tick(prime_only=True)
    feed.set([0.5, 0.5, 0.0, 0.0, 0.0])   # 0.5s 提示音 = 2 个采样点，随后静音
    for e in (0.0, 0.5, 0.0, 0.0, 0.0):
        det1b._tick(elapsed=e, allow_app_cache=True)
    add("误触发：0.5s 系统提示音（2 个采样点）不发 started",
        not any(x[0] == "started" for x in log1b) and det1b._playing is False,
        f"log={log1b} playing={det1b._playing}")

    # ---- 2) 误触发：播放中 0.5s 静音间隙不得发 stopped ----
    det2 = MusicDetector(None)
    log2, _ = _attach(det2)
    feed.set([0.0])
    det2._tick(prime_only=True)
    feed.set([0.5])
    det2._tick()                            # 手动口径：pending=1
    feed.set([0.5])
    det2._tick()                            # pending=2 → started
    n2 = len(log2)
    feed.set([0.0, 0.0, 0.5, 0.5, 0.5])   # 0.5s 间隙 = 2 次静音采样，随后恢复有声
    for e in (0.0, 0.5, 0.0, 0.0, 0.0):
        det2._tick(elapsed=e, allow_app_cache=True)
    add("误触发：播放中 0.5s 静音间隙不发 stopped",
        log2.count(("started", "audio", "")) == 1
        and not any(x[0] == "stopped" for x in log2[n2:])
        and det2._playing is True,
        f"新增 log={log2[n2:]} playing={det2._playing}")

    # ---- 3) 漏触发 / 重复触发：真实起播、停播各恰好 1 次 ----
    det3 = MusicDetector(None)
    log3, sync3 = _attach(det3)
    feed.set([0.0])
    det3._tick(prime_only=True)
    feed.set([0.5] * 3)                    # 起播：需 3 个连续同向采样点（elapsed=1.0s ≥ 0.6s）
    for e in (0.0, 0.5, 1.0):
        det3._tick(elapsed=e, allow_app_cache=True)
    feed.set([0.5] * 5)                    # 连续同向：不得重复发
    for e in (1.5, 2.0, 2.5, 3.0, 3.5):
        det3._tick(elapsed=e, allow_app_cache=True)
    feed.set([0.0] * 6)                    # 停播：需连续静音 ≥1.2s
    for e in (0.0, 0.5, 1.0, 1.5, 2.0, 2.5):
        det3._tick(elapsed=e, allow_app_cache=True)
    s3 = sum(1 for x in log3 if x[0] == "started")
    p3 = sum(1 for x in log3 if x[0] == "stopped")
    add("漏/重复触发：真实起播、停播各恰好 1 次；连续同向不重复发",
        s3 == 1 and p3 == 1 and det3._playing is False and all(sync3),
        f"started={s3} stopped={p3} playing={det3._playing} sync={sync3} log={log3}")

    # ---- 4) 状态不同步：信号与 _playing 一致（含 force / set_enabled） ----
    det4 = MusicDetector(None)
    log4, sync4 = _attach(det4)
    det4.force(True, "music", "命令曲")
    det4.force(True, "music", "命令曲")      # 同状态同元信息：不重复发
    det4.force(False)
    det4.force(False)                        # 已停止：不重复发
    det4.set_enabled(False)
    det4.set_enabled(False)                  # 不重复发
    # 序列：force(True) 发 1 次 started；force(False) 发 1 次 stopped；
    # 之后同向 force / set_enabled(False) 都幂等，不再新增 stopped。
    add("状态同步：force / set_enabled 的信号与 _playing 一致且不重复",
        all(sync4)
        and sum(1 for x in log4 if x[0] == "started") == 1
        and sum(1 for x in log4 if x[0] == "stopped") == 1
        and det4._playing is False,
        f"started={sum(1 for x in log4 if x[0] == 'started')} "
        f"stopped={sum(1 for x in log4 if x[0] == 'stopped')} "
        f"playing={det4._playing} sync={sync4}")

    # ---- 5) force() 后自动检测仍工作：确认窗口未满不推翻、满后纠正 1 次 ----
    # 旧语义：force() 只注入一次状态，轮询照常；若现实相反，必须重新累积
    # 完整的 DEBOUNCE 次 + 方向确认窗口才允许纠正（不会 1s 内突兀翻转）。
    det5 = MusicDetector(None)
    log5, _ = _attach(det5)
    det5.force(True, "music", "命令曲")
    feed.set([0.0] * 20)                    # 静音现实持续
    for e in (0.0, 0.5, 1.0):             # 计数够但 elapsed < STOP_CONFIRM_MS(1.2s)
        det5._tick(elapsed=e, allow_app_cache=True)
    early = not any(x[0] == "stopped" for x in log5) and det5._playing is True
    for e in (1.5, 2.0, 2.5, 3.0):        # 窗口满 → 自动纠正恰好 1 次
        det5._tick(elapsed=e, allow_app_cache=True)
    info5 = det5.poll_now()                 # 明细照常更新（peak=0.0）
    add("force() 后自动检测仍工作：确认窗口未满不推翻、满后纠正 1 次",
        early and info5["peak"] == 0.0 and info5["playing"] is False
        and sum(1 for x in log5 if x[0] == "stopped") == 1
        and det5._playing is False,
        f"early={early} stopped={sum(1 for x in log5 if x[0] == 'stopped')} "
        f"playing={det5._playing} info.playing={info5['playing']}")
    det5.set_enabled(False)                 # 已停止后显式关闭：不重复发 stopped
    add("自动纠正后 set_enabled(False) 不重复发 stopped",
        sum(1 for x in log5 if x[0] == "stopped") == 1 and det5._playing is False,
        f"stopped={sum(1 for x in log5 if x[0] == 'stopped')}")

    # ---- 6) peak is None 降级路径 ----
    det6 = MusicDetector(None)
    log6, _ = _attach(det6)
    feed.set([0.0])
    det6._tick(prime_only=True)
    feed.set([None], [(True, "网易云音乐 - 晴天")])
    info6 = det6.poll_now()
    feed.set([None, None], [(True, "网易云音乐 - 晴天")] * 2)
    det6._tick()
    det6._tick()
    add("降级：peak=None 时 degraded=True / kind=music / title 透传且能启动",
        info6["degraded"] is True and info6["kind"] == "music"
        and info6["title"] == "网易云音乐 - 晴天"
        and ("started", "music", "网易云音乐 - 晴天") in log6
        and det6._playing is True,
        f"info={info6} log={log6}")

    # ---- 7) poll_now() 异常兜底 ----
    det7 = MusicDetector(None)
    orig_peak = audio_probe.probe_peak

    def _boom():
        raise RuntimeError("bench: 模拟探测异常")

    try:
        audio_probe.probe_peak = _boom
        info7 = det7.poll_now()
    finally:
        audio_probe.probe_peak = orig_peak
    need = {"playing", "kind", "title", "peak", "music_app", "degraded", "enabled"}
    add("异常兜底：探测抛异常时 poll_now() 返回安全默认值且字段齐全",
        need.issubset(info7.keys()) and info7["degraded"] is True
        and info7["playing"] is False and info7["peak"] is None,
        f"info={info7}")

    # ---- 8) enable → disable → enable 正确重启 ----
    det8 = MusicDetector(None)
    det8.set_enabled(True)
    on1 = det8._timer.isActive()
    det8.set_enabled(False)
    off = (not det8._timer.isActive()) and det8._enabled is False
    det8.set_enabled(True)
    on2 = det8._timer.isActive() and det8._primed is False and det8._pending == 0
    det8.stop()
    det8.stop()
    add("生命周期：enable→disable→enable 正确重启且 stop() 幂等",
        on1 and off and on2 and det8._enabled is False,
        f"on1={on1} off={off} on2={on2} enabled={det8._enabled}")

    # ---- 9) 旧口径兼容：_pending / _primed 可读语义不变 ----
    det9 = MusicDetector(None)
    feed.set([0.0])
    det9._tick(prime_only=True)
    feed.set([0.01])
    det9._tick()
    pending_ok = det9._pending == 1 and det9._primed is True
    feed.set([0.01])
    det9._tick()
    add("兼容：_pending / _primed 语义保留，手动推进连续 2 次才翻转",
        pending_ok and det9._playing is True and det9._pending == 0,
        f"pending_after_1={1} playing={det9._playing} pending={det9._pending}")

    # ---- 10) 窗口枚举缓存不改变 kind 判定语义 ----
    det10 = MusicDetector(None)
    log10, _ = _attach(det10)
    real_app = audio_probe.detect_music_app
    app_calls = [0]

    def _counting_app():
        app_calls[0] += 1
        return real_app()

    audio_probe.detect_music_app = _counting_app
    try:
        feed.set([0.0] * 12, [(False, "")] * 12)
        det10._tick(prime_only=True)
        base10 = app_calls[0]
        for _ in range(12):
            det10._tick(elapsed=0.0, allow_app_cache=True)
        steady10 = app_calls[0] - base10       # 稳态 12 次轮询的真实枚举次数
        # 起播：待确认窗口内（_pending>0）的每次采样都强制刷新窗口枚举
        feed.set([0.5] * 3, [(True, "网易云音乐 - 晴天")] * 3)
        for e in (0.0, 0.5, 1.0):
            det10._tick(elapsed=e, allow_app_cache=True)
    finally:
        audio_probe.detect_music_app = real_app
    add("缓存：稳态降频枚举窗口，起播确认仍拿到最新 kind=music",
        steady10 <= 4 and ("started", "music", "网易云音乐 - 晴天") in log10,
        f"稳态 12 次轮询真实枚举 {steady10} 次；log={log10}")

    # ---- 11) 真实 QTimer：force(True) 后自动检测恢复，静音现实触发 1 次 stopped ----
    app11 = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    det11 = MusicDetector(None)
    stops11: list[float] = []
    det11.stopped.connect(lambda: stops11.append(time.monotonic()))
    orig11 = audio_probe.probe_peak
    audio_probe.probe_peak = lambda: 0.0
    try:
        det11.set_enabled(True)
        det11.force(True, "music", "命令曲")
        QTimer.singleShot(4000, app11.quit)
        app11.exec_()
    finally:
        audio_probe.probe_peak = orig11
        det11.stop()
    add("真实 QTimer：force(True) 后自动检测恢复，静音被识别且只发 1 次 stopped",
        len(stops11) == 1 and det11._playing is False,
        f"stopped 次数={len(stops11)} playing={det11._playing}")

    # ---- 12) P1 回归：enable→force→enable 后真实翻转不漏触发 ----
    # 修复前 _manual_override 在定时器 active 时无法解除，导致真实起播/停播
    # 全部漏触发。这里用确定性手动推进验证两个方向（QA Round 1 C10 复现）。
    det12a = MusicDetector(None)
    log12a, _ = _attach(det12a)
    det12a.set_enabled(True)
    det12a.force(True, "music", "命令曲")
    det12a.set_enabled(True)                # 修复前此调用清不掉覆盖
    feed.set([0.0] * 12)
    for e in (0.0, 0.5, 1.0, 1.5, 2.0, 2.5):
        det12a._tick(elapsed=e, allow_app_cache=True)
    det12a.stop()
    add("P1 回归：enable→force(True)→enable 后真实停播必发 stopped",
        sum(1 for x in log12a if x[0] == "stopped") == 1 and det12a._playing is False,
        f"stopped={sum(1 for x in log12a if x[0] == 'stopped')} "
        f"playing={det12a._playing} log={log12a}")

    det12b = MusicDetector(None)
    log12b, _ = _attach(det12b)
    det12b.set_enabled(True)
    det12b.force(False)
    det12b.set_enabled(True)
    feed.set([0.5] * 12)
    for e in (0.0, 0.5, 1.0, 1.5, 2.0, 2.5):
        det12b._tick(elapsed=e, allow_app_cache=True)
    det12b.stop()
    add("P1 回归：enable→force(False)→enable 后真实起播必发 started",
        sum(1 for x in log12b if x[0] == "started") == 1 and det12b._playing is True,
        f"started={sum(1 for x in log12b if x[0] == 'started')} "
        f"playing={det12b._playing} log={log12b}")

    # ---- 汇总 ----
    print("\n" + "=" * 78)
    print("四类触发风险 / 边界针对性用例")
    print("=" * 78)
    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        if not ok:
            print(f"         {detail}")
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n用例 {passed}/{len(results)} PASS")
    return 0 if passed == len(results) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="music.py 延迟基准与触发风险用例")
    ap.add_argument("--baseline", action="store_true", help="只打印 team-lead 实测基线表")
    ap.add_argument("--bench", action="store_true", help="只跑延迟基准")
    ap.add_argument("--cases", action="store_true", help="只跑针对性用例")
    args = ap.parse_args()

    print(f"脚本 APPDATA = {os.environ.get('APPDATA', '<未设置>')}")
    print(f"配置路径     = {config.config_path()}")
    print("（请在临时 APPDATA 下运行；本脚本不写用户真实配置）")

    if args.baseline:
        _print_constants()
        _print_baseline()
        return 0

    orig_peak, orig_app = audio_probe.probe_peak, audio_probe.detect_music_app
    feed = Feeder()
    audio_probe.probe_peak = feed.peak
    audio_probe.detect_music_app = feed.app
    try:
        if args.cases:
            return run_cases(feed)
        if args.bench:
            return run_bench(feed)
        rc1 = run_bench(feed)
        # run_bench 把 probe_peak 换成了按时间轴取值的函数，用例要换回可控序列
        audio_probe.probe_peak = feed.peak
        audio_probe.detect_music_app = feed.app
        rc2 = run_cases(feed)
        return 0 if rc1 == 0 and rc2 == 0 else 1
    finally:
        audio_probe.probe_peak = orig_peak
        audio_probe.detect_music_app = orig_app


if __name__ == "__main__":
    sys.exit(main())
