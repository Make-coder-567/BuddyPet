"""最小自检：offscreen 下构造 SoundManager 并 play，确认不抛异常、WAV 有效。

用法：``QT_QPA_PLATFORM=offscreen python tools/_selftest_sfx.py``
"""
from __future__ import annotations

import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt5.QtWidgets import QApplication  # noqa: E402

from app.config import Settings  # noqa: E402
from app.sound import SoundManager  # noqa: E402


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)

    # 1) 默认开启：play 不应抛异常
    sm = SoundManager(Settings(), ROOT)
    sm.play("click")
    sm.play("hover", cooldown_ms=600)
    sm.play("complete", cooldown_ms=120)
    sm.play("release")
    sm.play("grab")

    # 2) 关闭音效：必须零开销且无异常
    sm.set_enabled(False)
    assert sm._cfg.sound_enabled is False
    sm.play("click")  # 直接短路
    sm.set_enabled(True)

    # 3) 节流：极短时间内重复 play 不应连发（cooldown 生效，不抛异常即可）
    for _ in range(50):
        sm.play("click", cooldown_ms=60)

    # 4) WAV 文件校验
    for name in ("click", "hover", "complete", "release", "grab"):
        path = ROOT / "assets" / "sfx" / f"{name}.wav"
        with wave.open(str(path), "r") as w:
            assert w.getnchannels() == 1, name
            assert w.getsampwidth() == 2, name
            assert w.getframerate() == 44100, name
            assert w.getnframes() > 0, name
        size = path.stat().st_size
        assert size > 0, name
        print(f"  ok  {name}.wav  帧数={w.getnframes()}  字节={size}")

    print("自检通过：offscreen 下构造/play/关闭均无异常，WAV 格式正确")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
