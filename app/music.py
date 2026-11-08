"""音乐感知状态机：把「系统在放音频」翻译成宠物能理解的事件。

判定逻辑
-------
1. 每 ``POLL_MS``（500 毫秒）轮询一次：
   - ``probe_peak()`` 读默认输出设备的峰值电平；
   - ``detect_music_app()`` 看有没有音乐播放器窗口（稳态下按
     ``APP_SCAN_EVERY`` 复用上次结果，省掉反复枚举窗口）。
2. 起播阈值 ``0.004``、停播阈值 ``0.0015``（滞回），避免音量抖动来回翻转；
3. 状态翻转需 **连续 ``DEBOUNCE`` 次同向采样**，且在真实定时器路径下还要
   满足**方向相关的确认窗口**：起播 ``START_CONFIRM_MS`` 短（快速响应），
   停播 ``STOP_CONFIRM_MS`` 长（跨过歌曲里 0.3~1s 的静音间隙）。
   一次瞬时的「砰」或歌曲里的短暂停顿都不会让宠物跳舞 / 收律动；
4. ``playing=True`` 且命中音乐应用 → ``kind="music"``，否则 ``kind="audio"``；
5. ``peak is None``（没有音频设备 / 非 Windows）时降级为只看音乐应用，标记 ``degraded``。

首次采样只建档、不发信号 —— 否则一开机宠物就跳舞。注意：周期缩到
``POLL_MS=500`` 后，QTimer 的首个 tick（约 0.5s）就会建档，
``FIRST_DELAY_MS`` 的 singleShot 只是兜底（届时 ``_primed`` 已为真，直接返回）；
「首采不发任何信号」这一可观察语义不变。
只在状态翻转时发 ``started`` / ``stopped`` / ``state``。

延迟说明
-------
旧实现 ``POLL_MS=8000`` + 连续 2 次确认，最坏 16s、均值约 12s；而单次探测
实测约 5.8ms，延迟几乎全部来自轮询节奏而非探测成本。现在延迟 = 采样相位
（0~POLL_MS）+ 确认窗口：起播 ≈ 1.0~1.5s，停播 ≈ 1.5~2.0s；500ms 周期下
探测约占 1.1% 单核，缩短周期的代价可以忽略。

``_tick()`` 被手动同步推进（QA / 调试，没有墙钟）时沿用旧的「连续 N 次」
口径，既有回归脚本行为不变；只有 QTimer 回调才启用 ``time.monotonic()``
累积的确认窗口与窗口枚举缓存。
"""
from __future__ import annotations

import time

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from . import audio_probe

#: 轮询间隔（毫秒）。从 8s 缩到 0.5s：延迟几乎全部来自轮询节奏，
#: 单次探测实测约 5.8ms，500ms 周期约 1.2% 单核。
POLL_MS = 500
#: 启动后首次采样延迟（毫秒）—— 只建档不发信号，避免开机就跳舞
FIRST_DELAY_MS = 1500
#: 起播 / 停播阈值（滞回）
START_PEAK = 0.004
STOP_PEAK = 0.0015
#: 连续多少次同向采样才允许翻转（保留 2：降到 1 会让瞬时爆音误触发）
DEBOUNCE = 2
#: 起播确认窗口（毫秒）：仍属「短」，但刻意 > 一个轮询周期（500ms）。
#: 这样 0.2~0.8s 的系统提示音最多只能命中 2 个采样点（间隔 0.5s），
#: 窗口不足，必须连续 3 个同向采样点（≈1.0s 持续有声）才确认起播 ——
#: 旧 8s 轮询对短提示音天然免疫，缩周期后靠这道窗口把该防护补回来。
START_CONFIRM_MS = 600
#: 停播确认窗口（毫秒）：长，跨过歌曲 / 视频里典型的 0.3~1s 静音间隙。
#: 取 1200ms > 1s 上限；真实路径需连续静音约 1.5s 才会发 stopped。
STOP_CONFIRM_MS = 1200
#: 稳态下每隔多少次自动轮询才真正枚举一次窗口（其余复用缓存结果）。
#: 待确认窗口内、以及 poll_now() 显式查询时始终真实枚举，保证 kind 不滞后。
APP_SCAN_EVERY = 4


class MusicDetector(QObject):
    """轮询式音乐感知器。

    Signals:
        started: 开始有声（``kind`` 为 ``"music"`` 或 ``"audio"``，``title`` 可能为空）。
        stopped: 声音停了。
        state: 每次状态翻转都发一次，``(是否在响, kind)``。
    """

    started = pyqtSignal(str, str)
    stopped = pyqtSignal()
    state = pyqtSignal(bool, str)

    def __init__(self, cfg, parent: QObject | None = None):
        super().__init__(parent)
        self._cfg = cfg
        self._enabled = False
        self._playing = False
        self._kind = "music"
        self._title = ""
        self._peak: float | None = None
        self._music_app = False
        self._degraded = False
        self._primed = False          # 首次采样是否已完成（只建档）
        self._pending = 0             # 连续与当前状态相反的采样次数（确认进度）
        self._pending_since: float | None = None  # 本轮待确认窗口起点（monotonic，秒）
        self._app_hit = False         # detect_music_app() 的缓存结果
        self._app_title = ""
        self._app_scans_left = 0      # 距下次真实窗口枚举还剩几次自动轮询

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._on_tick)

    # ------------------------------------------------------------ 采样
    def _reset_pending(self) -> None:
        """清空待确认进度与确认窗口起点。"""
        self._pending = 0
        self._pending_since = None

    def _sample(self, allow_app_cache: bool = False) -> dict:
        """做一次原始采样，并按滞回规则给出「现在算不算在响」。

        Args:
            allow_app_cache: 仅 QTimer 自动轮询在**稳态**下置 True，允许复用
                上次 ``detect_music_app()`` 的结果；手动推进、``poll_now()``
                显式查询、以及待确认窗口内都强制真实枚举，保证 ``kind`` /
                ``title`` 在状态翻转时一定是新鲜值。
        """
        peak = audio_probe.probe_peak()
        if allow_app_cache and self._pending == 0 and self._app_scans_left > 0:
            # 稳态复用：窗口枚举单次约 1ms，高频轮询下每 4 次做一次即可
            self._app_scans_left -= 1
            hit, title = self._app_hit, self._app_title
        else:
            hit, title = audio_probe.detect_music_app()
            self._app_hit = bool(hit)
            self._app_title = (title or "") if hit else ""
            self._app_scans_left = APP_SCAN_EVERY - 1
        degraded = peak is None
        if degraded:
            # 没有音频设备：只能用「有没有音乐播放器」当代理指标
            playing_now = bool(hit)
        else:
            threshold = STOP_PEAK if self._playing else START_PEAK
            playing_now = float(peak) >= threshold
        self._peak = peak
        self._music_app = bool(hit)
        self._degraded = degraded
        return {
            "playing": bool(playing_now),
            "kind": "music" if hit else "audio",
            "title": (title or "") if hit else "",
            "peak": peak,
            "music_app": bool(hit),
            "degraded": degraded,
        }

    def _poll(self, allow_app_cache: bool = False) -> dict:
        """采样并补齐 ``enabled``；任何异常都退回「探测不到」的安全值。

        注意：异常兜底分支也必须带 ``enabled`` —— 旧实现在这里直接 ``return``
        漏掉了该字段，会让 ``poll_now()`` 在探测失败时少一个约定字段。
        """
        try:
            info = self._sample(allow_app_cache)
        except Exception:
            info = {
                "playing": False,
                "kind": "audio",
                "title": "",
                "peak": None,
                "music_app": False,
                "degraded": True,
            }
        info["enabled"] = self._enabled
        return info

    def poll_now(self) -> dict:
        """立即采样一次并返回明细（不改变定时器、不发信号）。

        Returns:
            dict: ``playing / kind / title / peak / music_app / degraded / enabled``。
        """
        return self._poll(False)

    # ------------------------------------------------------------ 生命周期
    def set_enabled(self, value: bool) -> None:
        """开关轮询；关闭时停止定时器并把内部状态归零。

        关闭时若之前「在响」，补发一次 ``stopped`` 让宠物收掉律动；
        **无论是否发过，分支末尾都要把 ``_playing`` / ``_kind`` / ``_title``
        归零** —— ``stop()`` 有意不清 ``_playing``，由这里负责。否则第二次
        ``set_enabled(False)`` 的 ``was_playing`` 仍是 True，会重复发 ``stopped``
        （宠物会把「音乐停了」说两遍）。
        """
        value = bool(value)
        self._enabled = value
        if not value:
            was_playing = self._playing
            self.stop()
            self._playing = False
            self._kind = "music"
            self._title = ""
            if was_playing:
                self.stopped.emit()
                self.state.emit(False, "")
            return
        # 重新启用自动检测：无论定时器是否已在运行，都清空确认进度，
        # 避免上一次关闭 / force() 留下的半截待确认窗口让状态机卡住。
        # 定时器已在运行时只重置进度，不重复 start / singleShot，也不重置
        # ``_primed``（保留既有基线，避免无谓地重新建档）。
        self._reset_pending()
        if self._timer.isActive():
            return
        self._primed = False
        self._app_scans_left = 0
        self._timer.start()
        QTimer.singleShot(FIRST_DELAY_MS, self._first_sample)

    def _first_sample(self) -> None:
        """启动后的首次采样：只建档，不发任何信号。"""
        if not self._enabled or not self._timer.isActive():
            return
        if self._primed:
            return
        self._tick(prime_only=True)

    def force(self, playing: bool, kind: str = "music", title: str = "") -> None:
        """QA/命令行用的强制覆盖：立即发信号，但**幂等**（同状态同元信息不重复发）。

        规则：
        - ``playing=True``：仅在「之前不在响」或 ``kind`` / ``title`` 变了时发
          ``started`` / ``state(True, ...)``；
        - ``playing=False``：仅在「之前在响」时发 ``stopped`` / ``state(False, "")``；
        - 例外：本对象还没有任何基线（``_primed`` 为假）时，第一次 ``force()``
          无论方向都发一次 —— 外部可能压根没轮询过，需要一次确定的状态广播。

        ``music:on`` / ``music:off`` 这类命令因此可以安全地重复下发，
        不会把「音乐停了」重复说两遍。

        强制只是**注入一次状态**，不停止定时器、也不挂起自动检测（旧语义）。
        为避免缩短后的 500ms 周期立刻把刚注入的状态推翻，这里清空确认进度：
        若现实音频与强制状态相反，自动检测仍会按真实情况纠正，但必须重新
        累积完整的 ``DEBOUNCE`` 次采样 + 方向确认窗口（起播 600ms /
        停播 1200ms），不会在 1 秒内突兀翻转，也不会出现状态长期不同步。
        """
        playing = bool(playing)
        kind = kind or "music"
        title = title or ""
        was_playing = self._playing
        same = self._kind == kind and self._title == title
        first = not self._primed

        self._playing = playing
        self._kind = kind
        self._title = title
        self._primed = True
        self._reset_pending()

        if playing:
            if was_playing and same and not first:
                return
            self.started.emit(self._kind, self._title)
            self.state.emit(True, self._kind)
        else:
            if not was_playing and not first:
                return
            self.stopped.emit()
            self.state.emit(False, "")

    def stop(self) -> None:
        """停止轮询（幂等）；不清 ``_playing``，由调用方决定是否补发 stopped。"""
        try:
            self._timer.stop()
        except Exception:
            pass
        self._enabled = False
        self._primed = False
        self._reset_pending()
        self._app_scans_left = 0

    # ------------------------------------------------------------ 轮询
    def _on_tick(self) -> None:
        """QTimer 回调：带上本轮确认窗口已持续的墙钟秒数。"""
        now = time.monotonic()
        elapsed: float | None = None
        if self._pending_since is not None:
            elapsed = max(0.0, now - self._pending_since)
        self._tick(elapsed=elapsed, allow_app_cache=True)

    def _tick(self, prime_only: bool = False, *, elapsed: float | None = None,
              allow_app_cache: bool = False) -> None:
        """推进一次状态机。

        Args:
            prime_only: 首次建档，只记录现实不发信号。
            elapsed: 本轮待确认窗口已持续的秒数（``time.monotonic()`` 差值），
                由 QTimer 回调传入；手动同步推进传 ``None``，沿用旧的
                「连续 ``DEBOUNCE`` 次」口径（无墙钟，兼容既有回归）。
            allow_app_cache: 是否允许在稳态复用窗口枚举结果（仅自动轮询）。
        """
        info = self._poll(allow_app_cache)
        if prime_only or not self._primed:
            # 首次建档：状态照抄当前现实，但不发信号
            self._primed = True
            self._reset_pending()
            self._playing = bool(info["playing"])
            self._kind = info["kind"] or "music"
            self._title = info["title"] or ""
            return
        if bool(info["playing"]) == self._playing:
            self._reset_pending()
            return
        if self._pending == 0:
            self._pending_since = time.monotonic()
        self._pending += 1
        if self._pending < DEBOUNCE:
            return
        # 方向相关的确认窗口：起播短、停播长。真实定时器必须同时满足
        # 计数与时长；手动推进（elapsed=None）只校验计数，与旧行为一致。
        window_ms = STOP_CONFIRM_MS if (self._playing and not info["playing"]) else START_CONFIRM_MS
        if elapsed is not None and (elapsed * 1000.0) < window_ms:
            return
        self._reset_pending()
        self._apply(info)

    def _apply(self, info: dict) -> None:
        """真正翻转状态并广播。"""
        was_playing = self._playing
        self._playing = bool(info["playing"])
        self._kind = info["kind"] or "music"
        self._title = info["title"] or ""
        if self._playing and not was_playing:
            self.started.emit(self._kind, self._title)
            self.state.emit(True, self._kind)
        elif was_playing and not self._playing:
            self.stopped.emit()
            self.state.emit(False, "")
