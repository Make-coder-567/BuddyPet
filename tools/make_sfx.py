"""合成 Buddy 互动音效（纯标准库，无第三方依赖）。

用带包络的正弦/轻钟声合成 5 个柔和音效 WAV，写入 ``assets/sfx/`` ：

    click.wav    柔和的「噗」一声（点击反馈）
    hover.wav    更轻更高的悬停提示音
    complete.wav 悦耳的两音上行小钟声（完成操作）
    release.wav  柔和低频「噗」（拖拽松手）
    grab.wav     轻微的低频「咔/嗒」（边缘抓握成功）

所有文件统一为 16-bit PCM、单声道、44100Hz。

设计原则（贴合用户审美：拒绝刺耳/不真实）
-----------------------------------------
- 只用正弦波叠加指数包络，绝不用方波蜂鸣；
- 起音快、衰减柔，整体「圆润」无爆音；
- 每个文件做峰值归一化，音量稳定可控。

使用方法
--------
    python tools/make_sfx.py            # 生成缺失/无效的音效（幂等）
    python tools/make_sfx.py --force    # 强制覆盖重新生成

退出码 0 表示全部生成成功（或已存在且有效）。
"""
from __future__ import annotations

import argparse
import math
import struct
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "assets" / "sfx"
SR = 44100  # 采样率


# ---------------------------------------------------------------- 合成核心
def _render(events: list[dict], dur: float) -> list[float]:
    """把若干「音符事件」合成到长度为 ``dur`` 秒的浮点缓冲（范围约 [-1, 1]）。

    每个事件 ``ev`` 字段：
        t0     起始时间（秒）
        dur    时长（秒）
        f0,f1  起始/结束频率（Hz），f0!=f1 即线性滑音
        attack 起音时长（秒），过短会爆音
        tau    指数衰减时间常数（秒）
        gain   相对增益（默认 1.0）
    """
    n = int(round(SR * dur))
    buf = [0.0] * n
    for ev in events:
        s0 = int(round(SR * ev.get("t0", 0.0)))
        m = int(round(SR * ev["dur"]))
        attack = ev.get("attack", 0.003)
        tau = ev.get("tau", 0.05)
        gain = ev.get("gain", 1.0)
        f0 = ev["f0"]
        f1 = ev["f1"]
        phase = 0.0
        for j in range(m):
            i = s0 + j
            if i >= n:
                break
            t = j / SR
            frac = t / ev["dur"]
            freq = f0 + (f1 - f0) * frac
            # 包络：快起音（线性）+ 指数衰减，二者在 attack 处衔接为 1.0，无跳变
            if attack > 0 and t < attack:
                env = t / attack
            else:
                env = math.exp(-(t - attack) / tau) if tau > 0 else 1.0
            phase += 2.0 * math.pi * freq / SR
            buf[i] += math.sin(phase) * env * gain
    return buf


def _normalize(buf: list[float], peak: float) -> list[float]:
    """把缓冲整体缩放，使绝对峰值等于 ``peak``。"""
    mx = max((abs(x) for x in buf), default=0.0)
    if mx <= 0.0:
        return buf
    k = peak / mx
    return [x * k for x in buf]


def _write_wav(path: Path, buf: list[float]) -> None:
    """把浮点缓冲（[-1,1]）写成 16-bit PCM 单声道 WAV。"""
    frames = bytearray()
    for x in buf:
        v = int(round(max(-1.0, min(1.0, x)) * 32767))
        v = max(-32768, min(32767, v))
        frames += struct.pack("<h", v)
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(bytes(frames))


def _is_valid_wav(path: Path) -> bool:
    """文件已存在且可被 wave 正常打开、含非空帧，则视为有效。"""
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        with wave.open(str(path), "r") as w:
            return w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() == SR and w.getnframes() > 0
    except Exception:
        return False


# ---------------------------------------------------------------- 音效规格
def _build_specs() -> dict[str, tuple[float, list[dict], float]]:
    """返回 {文件名: (总时长秒, 事件列表, 目标峰值)}。"""
    specs: dict[str, tuple[float, list[dict], float]] = {}

    # 点击：柔和「噗」，基频 880→520Hz 轻微下滑，快起音+指数衰减
    specs["click.wav"] = (
        0.06,
        [{"t0": 0.0, "dur": 0.06, "f0": 880.0, "f1": 520.0, "attack": 0.003, "tau": 0.045, "gain": 1.0}],
        0.45,
    )

    # 悬停：更轻更高的提示音，极柔
    specs["hover.wav"] = (
        0.045,
        [{"t0": 0.0, "dur": 0.045, "f0": 990.0, "f1": 990.0, "attack": 0.003, "tau": 0.030, "gain": 1.0}],
        0.32,
    )

    # 完成：两音上行小钟声 660→880Hz，第二音约 90ms 起，钟形衰减
    specs["complete.wav"] = (
        0.26,
        [
            {"t0": 0.0, "dur": 0.20, "f0": 660.0, "f1": 660.0, "attack": 0.005, "tau": 0.170, "gain": 1.0},
            {"t0": 0.09, "dur": 0.17, "f0": 880.0, "f1": 880.0, "attack": 0.005, "tau": 0.170, "gain": 1.0},
        ],
        0.42,
    )

    # 松手：柔和低频「噗」，440→600Hz 轻微上滑
    specs["release.wav"] = (
        0.06,
        [{"t0": 0.0, "dur": 0.06, "f0": 440.0, "f1": 600.0, "attack": 0.003, "tau": 0.045, "gain": 1.0}],
        0.40,
    )

    # 抓握：轻微低频「咔/嗒」，短促
    specs["grab.wav"] = (
        0.05,
        [{"t0": 0.0, "dur": 0.05, "f0": 320.0, "f1": 320.0, "attack": 0.002, "tau": 0.030, "gain": 1.0}],
        0.38,
    )

    return specs


def generate(force: bool = False) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    specs = _build_specs()
    ok = 0
    for name, (dur, events, peak) in specs.items():
        path = OUT_DIR / name
        if not force and _is_valid_wav(path):
            print(f"  跳过（已存在且有效）：{path.relative_to(ROOT)}")
            ok += 1
            continue
        buf = _render(events, dur)
        buf = _normalize(buf, peak)
        _write_wav(path, buf)
        # 写后回读校验，确保文件可被正常打开
        with wave.open(str(path), "r") as w:
            nframes = w.getnframes()
        print(f"  生成：{path.relative_to(ROOT)}  ({nframes} 帧, {path.stat().st_size} 字节)")
        ok += 1
    return 0 if ok == len(specs) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="合成 Buddy 互动音效")
    parser.add_argument("--force", action="store_true", help="强制覆盖已存在的音效文件")
    args = parser.parse_args()
    print(f"音效输出目录：{OUT_DIR}")
    return generate(force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
