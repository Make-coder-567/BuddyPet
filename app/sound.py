"""互动音效管理：基于 ``QSoundEffect`` 的低延迟、可关闭声音反馈。

设计要点（贴合需求）
-------------------
- 预加载：每个音效文件只加载一次（懒加载 + 缓存），避免重复解码；
- 节流：同一音效短时间内重复触发被 per-name cooldown 抑制，杜绝「机关枪」连发；
- 零开销关闭：``sound_enabled`` 为假时 ``play()`` 直接返回，不解码、不触碰音频设备；
- 健壮性：构造 ``QSoundEffect`` 必须在 ``QApplication`` 之后（PetWindow 在 GUI 线程构造，
  安全）；所有 ``play()`` 路径用 ``try/except`` 包裹，headless/offscreen 或无音频设备
  环境下不会抛异常。

注意：本模块不要被没有 ``QApplication`` 的场景在 import 时构造 ``QSoundEffect``；
SoundManager 实例只在 PetWindow 构造期创建（彼时 QApplication 已就绪）。
"""
from __future__ import annotations

import time
from pathlib import Path

from PyQt5.QtCore import QObject, QUrl
from PyQt5.QtMultimedia import QSoundEffect

#: 每个音效名字对应的默认增益（在 ``cfg.sound_volume`` 之外再乘的一层微调）。
#: 悬停更轻、完成音保持原样，其余默认 1.0。
_DEFAULT_GAIN: dict[str, float] = {
    "click": 1.0,
    "hover": 0.85,
    "complete": 1.0,
    "release": 1.0,
    "grab": 1.0,
}


class SoundManager(QObject):
    """管理一组预加载的 ``QSoundEffect``，提供节流、可关闭的播放接口。"""

    def __init__(self, cfg, root: Path):
        super().__init__()
        self._cfg = cfg
        #: name -> QSoundEffect（懒加载；懒加载保证仅在真正需要时构造音频对象）
        self._fx: dict[str, QSoundEffect] = {}
        #: name -> 上次成功播放的 time.monotonic() 秒，用于 per-name 节流
        self._cooldown: dict[str, float] = {}
        #: name -> 额外增益（可选，默认 1.0）
        self._gain: dict[str, float] = dict(_DEFAULT_GAIN)
        self._root = root

    # ------------------------------------------------------------ 内部
    def _ensure(self, name: str) -> bool:
        """确保某音效已加载；已加载直接返回 True，否则构造并返回是否成功。"""
        if name in self._fx:
            return True
        try:
            fx = QSoundEffect()
            src = QUrl.fromLocalFile(str(self._root / "assets" / "sfx" / f"{name}.wav"))
            fx.setSource(src)
            # 实际音量在 play 时按 cfg 计算，这里固定 1.0 作为基准
            fx.setVolume(1.0)
            # 文件不存在 / 无效源时 isLoaded 为 False，但构造本身不报错；
            # 若源为空或路径非法，play 不会产生声音，这里视为「已登记但不保证有声」。
            self._fx[name] = fx
            return True
        except Exception:
            # 即便在异常环境下，也不应让调用方崩溃
            return False

    # ------------------------------------------------------------ 对外接口
    def play(self, name: str, cooldown_ms: int = 60) -> None:
        """播放一个音效；在关闭、未就绪或冷却期内则静默返回。"""
        # 关闭音效：直接短路，零开销（不解码、不触碰音频设备）
        if not self._cfg.sound_enabled:
            return
        if not self._ensure(name):
            return

        now = time.monotonic()
        last = self._cooldown.get(name, -1e9)
        # per-name 节流：冷却期内直接跳过，避免连发
        if (now - last) * 1000.0 < cooldown_ms:
            return
        self._cooldown[name] = now

        try:
            fx = self._fx[name]
            vol = min(1.0, max(0.0, self._cfg.sound_volume * self._gain.get(name, 1.0)))
            fx.setVolume(vol)
            fx.setMuted(False)
            fx.play()
        except Exception:
            # 无音频设备 / offscreen / 其它异常：静默吞掉，保证主线程不被打断
            pass

    def set_enabled(self, v: bool) -> None:
        """开关音效；关闭后 ``play()`` 直接短路，完全无播放开销。"""
        self._cfg.sound_enabled = bool(v)

    def set_volume(self, v: float) -> None:
        """设置音量（0.0~1.0 自动夹紧）。"""
        self._cfg.sound_volume = float(max(0.0, min(1.0, v)))

    def set_gain(self, name: str, gain: float) -> None:
        """可选：设置某个音效的额外增益。"""
        self._gain[name] = float(max(0.0, gain))
