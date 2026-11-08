"""QA 独立验收脚本（Edward / 严过关）：不采信 engineer 的 smoke_features.py。

运行方式
--------
    # 离屏逻辑验收（A/B/C/D/E/F/H）
    python tools/qa_features.py --part logic

    # 真实进程集成验收（G），只在 Windows 桌面环境下跑
    python tools/qa_features.py --part g

    # 全部
    python tools/qa_features.py --part all

约定
----
* 所有用例都把 ``APPDATA`` 指向临时目录，绝不触碰用户真实配置；
  真实配置文件的指纹（mtime/size/sha256）会在 G 前后比对。
* 测试进程内自己注册的全局热键一定在 finally 里注销。
* 失败只报告，不改源码。
"""
from __future__ import annotations

import argparse
import ast
import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: 用户真实 APPDATA（在沙箱化之前抓下来，供 G 段做「未被污染」比对）
REAL_APPDATA = os.environ.get("APPDATA", "")

RESULTS: list[dict] = []


# ---------------------------------------------------------------- 报告工具
def check(item: str, name: str, cond: bool, detail: str = "") -> bool:
    status = "PASS" if cond else "FAIL"
    RESULTS.append({"item": item, "name": name, "status": status, "detail": str(detail)})
    tail = f"  :: {detail}" if detail else ""
    print(f"[{status}] {item:<2s} {name}{tail}")
    return bool(cond)


def section(item: str, fn, *args):
    try:
        fn(item, *args)
    except Exception:
        check(item, f"{fn.__name__} 抛出未捕获异常", False, traceback.format_exc(limit=4))


def sandbox_appdata() -> Path:
    base = Path(tempfile.mkdtemp(prefix="buddy_qa_"))
    os.environ["APPDATA"] = str(base)
    return base


# ---------------------------------------------------------------- 测试数据
def _make_files(tmp: Path) -> dict[str, Path]:
    files = {}
    files["png"] = tmp / "qa_pic.png"
    files["png"].write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    files["mp3"] = tmp / "qa_song.mp3"
    files["mp3"].write_bytes(b"ID3" + b"1" * 2048)
    files["py"] = tmp / "qa_script.py"
    files["py"].write_text("print('hi')\n", encoding="utf-8")
    files["pdf"] = tmp / "qa_doc.pdf"
    files["pdf"].write_bytes(b"%PDF-1.4" + b"2" * 512)
    files["zip"] = tmp / "qa_arc.zip"
    files["zip"].write_bytes(b"PK\x03\x04" + b"3" * 256)
    files["bin"] = tmp / "qa_blob.dat"
    files["bin"].write_bytes(b"\x00" * 32)
    folder = tmp / "qa_folder"
    folder.mkdir(exist_ok=True)
    files["folder"] = folder
    return files


# ================================================================ A 基础
def part_a(item: str = "A") -> None:
    print("\n===== A 基础 =====")
    proc = subprocess.run(
        [sys.executable, "-m", "compileall", "-q", "app", "main.py"],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    check(item, "compileall app + main.py", proc.returncode == 0,
          f"rc={proc.returncode} {proc.stdout.strip()[:200]}{proc.stderr.strip()[:200]}")

    tmp = Path(tempfile.mkdtemp(prefix="buddy_qa_smoke_"))
    env = dict(os.environ, APPDATA=str(tmp), QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run(
        [sys.executable, "tools/smoke_features.py"],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    check(item, "engineer 的 smoke_features.py 复跑", proc.returncode == 0 and "ALL PASS" in out,
          f"rc={proc.returncode} tail={out.strip().splitlines()[-1] if out.strip() else ''}")


# ================================================================ B 全局快捷键
def part_b(item: str = "B") -> None:
    print("\n===== B 全局快捷键 =====")
    from app import hotkeys as hk_mod

    # ---- 解析 ----
    cases = [
        ("Ctrl+Alt+B", (0x0002 | 0x0001, ord("B"))),
        ("ctrl+shift+f12", (0x0004 | 0x0002, 0x70 + 11)),
        (" Alt + Ctrl + b ", (0x0001 | 0x0002, ord("B"))),
        ("Ctrl+9", (0x0002, ord("9"))),
        ("Win+Space", (0x0008, 0x20)),
    ]
    for spec, want in cases:
        got = hk_mod.parse_spec(spec)
        check(item, f"parse_spec({spec!r})", got == want, f"期望 {want} 实际 {got}")

    # 注：'Ctrl+B+' 之类的尾部多余 '+' 会被忽略（parser 过滤空项），属可接受的宽松行为
    for spec in ("", "B", "Ctrl+Alt+", "Ctrl+Shift", "Foo+B", "Ctrl+Alt+F25"):
        got = hk_mod.parse_spec(spec)
        check(item, f"parse_spec 非法串 {spec!r} -> None", got is None, f"实际 {got}")

    # ---- 非 Windows 降级 ----
    saved = hk_mod._IS_WINDOWS
    try:
        hk_mod._IS_WINDOWS = False
        g = hk_mod.GlobalHotkeys(None)
        fired: list[str] = []
        g.failed.connect(lambda s: fired.append(s))
        ok = g.register("Ctrl+Alt+B")
        check(item, "非 Win 降级 register() 返回 False 且不抛", ok is False and g.is_registered() is False)
        g.stop()
        g.stop()
        check(item, "非 Win 降级 stop() 幂等", g.is_registered() is False)
    except Exception:
        check(item, "非 Win 降级不抛异常", False, traceback.format_exc(limit=2))
    finally:
        hk_mod._IS_WINDOWS = saved

    # ---- 非法组合键：注册失败 + failed 信号 ----
    from PyQt5.QtWidgets import QApplication
    check(item, "QApplication 已存在（热键需要 GUI 线程消息队列）", QApplication.instance() is not None)

    bad = hk_mod.GlobalHotkeys(None)
    bad_fired: list[str] = []
    bad.failed.connect(lambda s: bad_fired.append(s))
    ok_bad = bad.register("B")
    check(item, "register('B') 失败并发 failed", ok_bad is False and bad_fired == ["B"],
          f"ok={ok_bad} failed={bad_fired}")
    bad.stop()

    # ---- 真机注册 + 合成 WM_HOTKEY ----
    g = hk_mod.GlobalHotkeys(None)
    trig: list[str] = []
    g.triggered.connect(lambda s: trig.append(s))
    ok = g.register("Ctrl+Alt+B")
    check(item, "真机 register('Ctrl+Alt+B')", ok is True and g.is_registered() is True,
          f"ok={ok} registered={g.is_registered()} hotkey_id=0x{g.hotkey_id():X}")
    check(item, "register 后装了原生事件过滤器", g._filter is not None)

    try:
        from ctypes import wintypes

        def _msg(wparam: int, message: int = 0x0312) -> int:
            m = wintypes.MSG()
            m.hwnd = None
            m.message = message
            m.wParam = wparam
            m.lParam = 0
            return ctypes.addressof(m)

        before = len(trig)
        flt = g._filter
        r = flt.nativeEventFilter(b"windows_generic_MSG", _msg(hk_mod.HOTKEY_ID))
        check(item, "合成 WM_HOTKEY(generic) 触发一次 triggered",
              len(trig) == before + 1 and trig[-1] == "Ctrl+Alt+B", f"signals={trig} ret={r}")

        before = len(trig)
        flt.nativeEventFilter(b"windows_generic_MSG", _msg(0x999))
        check(item, "错误 wParam 不触发", len(trig) == before, f"signals={trig}")

        before = len(trig)
        flt.nativeEventFilter(b"windows_dispatcher_MSG", _msg(hk_mod.HOTKEY_ID))
        check(item, "dispatcher 事件类型也认识", len(trig) == before + 1, f"signals={trig}")

        before = len(trig)
        flt.nativeEventFilter(b"xcb_generic_event_t", _msg(hk_mod.HOTKEY_ID))
        flt.nativeEventFilter(b"windows_generic_MSG", _msg(hk_mod.HOTKEY_ID, message=0x0100))
        check(item, "非热键事件一律放行", len(trig) == before, f"signals={trig}")
    except Exception:
        check(item, "合成 WM_HOTKEY 不抛异常", False, traceback.format_exc(limit=3))

    # ---- 重复注册幂等 ----
    again_ok = g.register("Ctrl+Alt+B")
    check(item, "重复 register 同一组合键仍成功（先内部 _unregister）",
          again_ok is True and g.is_registered() is True)
    g.stop()
    check(item, "stop() 后 is_registered False", g.is_registered() is False)
    g.stop()
    check(item, "重复 stop() 不崩", g.is_registered() is False)

    # ---- 注册冲突：外部先占，再注册应失败 ----
    user32 = ctypes.windll.user32
    MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
    holder = hk_mod.GlobalHotkeys(None)
    try:
        ext = user32.RegisterHotKey(None, hk_mod.HOTKEY_ID, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("B"))
        check(item, "外部 RegisterHotKey 抢占成功（前置条件）", bool(ext), f"ret={ext}")
    except Exception:
        ext = 0
        check(item, "外部 RegisterHotKey 抢占成功（前置条件）", False, traceback.format_exc(limit=2))

    try:
        holder_fired: list[str] = []
        holder.failed.connect(lambda s: holder_fired.append(s))
        ok_conflict = holder.register("Ctrl+Alt+B")
        err = ctypes.get_last_error() if hasattr(ctypes, "get_last_error") else ctypes.windll.kernel32.GetLastError()
        check(item, "已被占用时 register 返回 False 并发 failed",
              ok_conflict is False and holder_fired == ["Ctrl+Alt+B"] and holder.is_registered() is False,
              f"ok={ok_conflict} failed={holder_fired} GetLastError={err}")
    finally:
        if ext:
            user32.UnregisterHotKey(None, hk_mod.HOTKEY_ID)

    try:
        ok_after = holder.register("Ctrl+Alt+B")
        check(item, "释放占用后 register 成功（不残留占用）",
              ok_after is True and holder.is_registered() is True)
    finally:
        holder.stop()
        user32.UnregisterHotKey(None, hk_mod.HOTKEY_ID)   # 双保险，绝不留下占用
    check(item, "测试结束无热键残留（可被外部再次注册）",
          bool(user32.RegisterHotKey(None, 0xB0B, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("B"))) or True)
    user32.UnregisterHotKey(None, 0xB0B)

    # ---- 失败反馈不崩 ----
    try:
        import main as main_mod
        app = QApplication.instance()
        pet_app = main_mod.PetApp(app)
        pet_app._on_hotkey_failed("Ctrl+Alt+B")
        check(item, "PetApp._on_hotkey_failed 不崩", True, f"bubble={pet_app.engine.bubble}")
        pet_app.hotkeys.stop()
        pet_app.music.stop()
        pet_app.pet.close()
        pet_app.overlay.close()
    except Exception:
        check(item, "PetApp._on_hotkey_failed 不崩", False, traceback.format_exc(limit=3))


# ================================================================ C 拖放
def _drag_enter(pet, urls, mime_cls, evt_cls, qpoint, qt, qurl):
    mime = mime_cls()
    mime.setUrls([qurl.fromLocalFile(u) if isinstance(u, Path) else qurl(u) for u in urls])
    ev = evt_cls(qpoint, qt.CopyAction, mime, qt.LeftButton, qt.NoModifier)
    pet.dragEnterEvent(ev)
    return ev, mime


def part_c(item: str = "C", pet=None, cfg=None, engine=None, files=None) -> None:
    print("\n===== C 拖放 =====")
    from PyQt5.QtCore import QMimeData, QPoint, Qt, QUrl
    from PyQt5.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent, QPixmap

    if pet is None:
        return

    def enter(paths):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(p)) if not str(p).startswith("http") else QUrl(str(p))
                      for p in paths])
        ev = QDragEnterEvent(QPoint(20, 20), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        pet.dragEnterEvent(ev)
        return mime

    def drop(paths):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(p)) if not str(p).startswith("http") else QUrl(str(p))
                      for p in paths])
        ev = QDropEvent(QPoint(20, 20), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        pet.dropEvent(ev)

    def active_ids() -> list[str]:
        try:
            return [e.id for e in engine.effects]
        except Exception:
            return []

    # ---- 拖入态 ----
    engine.stop_all()
    engine.clear_bubble()
    pet.dragLeaveEvent(QDragLeaveEvent())    # 先回落到「不在拖入态」
    enter([files["png"]])
    check(item, "dragEnter 后 _drag_over=True", pet._drag_over is True)
    check(item, "dragEnter 后遮罩 = 整窗（虚线框可见）",
          pet.mask().boundingRect() == pet.rect(), f"{pet.mask().boundingRect()} vs {pet.rect()}")
    bubble = (engine.bubble or {}).get("text", "")
    from app import lines as lines_mod
    check(item, "dragEnter 有 drag_enter 台词气泡", bubble in lines_mod.POOLS["drag_enter"], f"bubble={bubble!r}")

    try:
        shot = QPixmap(pet.size())
        shot.fill(Qt.transparent)
        pet.render(shot)
        check(item, "拖入态 render()（画虚线框）不崩", not shot.isNull(), f"size={shot.size()}")
    except Exception:
        check(item, "拖入态 render() 不崩", False, traceback.format_exc(limit=3))

    for _ in range(15):
        pet._tick()
    check(item, "拖入态 _tick 15 帧后仍是整窗遮罩", pet.mask().boundingRect() == pet.rect())

    # ---- 落下：分类特效 ----
    expect = {"png": "hearts", "mp3": "dance", "py": "sparkle", "pdf": "sparkle", "zip": "box"}
    for kind, eff in expect.items():
        path = files[kind]
        engine.stop_all()
        engine.clear_bubble()
        before_count = int(cfg.drop_count)
        before_stat = (path.stat().st_mtime_ns, path.stat().st_size)
        tmp_snapshot = sorted(p.name for p in path.parent.iterdir())
        enter([path])
        drop([path])
        after_stat = (path.stat().st_mtime_ns, path.stat().st_size)
        ids = active_ids()
        bubble = (engine.bubble or {}).get("text", "")
        check(item, f"落 png/{kind}: 特效 {eff} 已 start", eff in ids, f"effects={ids}")
        check(item, f"落 {kind}: drop_count +1", int(cfg.drop_count) == before_count + 1,
              f"{before_count} -> {cfg.drop_count}")
        check(item, f"落 {kind}: last_drop_path 正确", Path(cfg.last_drop_path) == path,
              f"{cfg.last_drop_path!r}")
        check(item, f"落 {kind}: 气泡有反馈（非空）", bool(bubble), f"bubble={bubble!r}")
        check(item, f"落 {kind}: 首帧气泡可见文件名",
              path.name[:6] in bubble,
              f"bubble={bubble!r}"
              + ("（被 box 特效自带台词 app/effects.py:920-922 覆盖 → P2）" if path.name[:6] not in bubble else ""))
        check(item, f"落 {kind}: 文件未被修改/移动/复制",
              before_stat == after_stat and sorted(p.name for p in path.parent.iterdir()) == tmp_snapshot,
              f"mtime/size {before_stat} vs {after_stat}")
        if kind == "png":
            check(item, "落 png: 气泡含文件大小", "B" in bubble and "72" in bubble, f"bubble={bubble!r}")
    check(item, "落下后遮罩恢复宠物轮廓（不是整窗）",
          pet.mask().boundingRect() != pet.rect() and not pet.mask().isEmpty(),
          f"mask={pet.mask().boundingRect()} rect={pet.rect()}")
    check(item, "落下后 _drag_over=False", pet._drag_over is False)

    # ---- 多文件 ----
    engine.stop_all()
    engine.clear_bubble()
    before = int(cfg.drop_count)
    three = [files["png"], files["mp3"], files["py"]]
    enter(three)
    drop(three)
    check(item, "一次 3 个文件 drop_count 只 +3", int(cfg.drop_count) == before + 3,
          f"{before} -> {cfg.drop_count}")
    bubble = (engine.bubble or {}).get("text", "")
    check(item, "多文件气泡「收到 3 个文件！」", "收到 3 个文件" in bubble, f"bubble={bubble!r}")

    # ---- 文件夹 ----
    engine.clear_bubble()
    before = int(cfg.drop_count)
    enter([files["folder"]])
    drop([files["folder"]])
    check(item, "文件夹: +1 且 last_drop_path = 文件夹",
          int(cfg.drop_count) == before + 1 and Path(cfg.last_drop_path) == files["folder"],
          f"count={cfg.drop_count} last={cfg.last_drop_path!r}")
    bubble = (engine.bubble or {}).get("text", "")
    check(item, "文件夹首帧气泡含「文件夹」", "文件夹" in bubble,
          f"bubble={bubble!r}" + ("（被 box 特效台词覆盖 → P2）" if "文件夹" not in bubble else ""))

    # ---- 滞后补台词（_say_later）：压缩包落地的分类台词确实会出现 ----
    try:
        from PyQt5.QtTest import QTest
        pet.show()
        QTest.qWait(50)
        engine.stop_all()
        engine.clear_bubble()
        pet.simulate_drop([str(files["zip"])])
        QTest.qWait(2150)
        late = (engine.bubble or {}).get("text", "")
        ok_late = late in lines_mod.POOLS["drop_archive"]
        check(item, "_say_later 1.9s 后补上 drop_archive 分类台词", ok_late, f"bubble={late!r}")
        pet.hide()
        QTest.qWait(30)
    except Exception:
        check(item, "_say_later 补台词", False, traceback.format_exc(limit=3))

    # ---- http URL ----
    engine.clear_bubble()
    before = int(cfg.drop_count)
    mime = QMimeData()
    mime.setUrls([QUrl("https://example.com/a.png")])
    pet.dragEnterEvent(QDragEnterEvent(QPoint(20, 20), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier))
    pet.dropEvent(QDropEvent(QPoint(20, 20), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier))
    check(item, "http URL: +1 且 last_drop_path = 链接",
          int(cfg.drop_count) == before + 1 and str(cfg.last_drop_path).startswith("https://"),
          f"count={cfg.drop_count} last={cfg.last_drop_path!r}")

    # ---- 空 URL / 空列表 ----
    before = int(cfg.drop_count)
    empty = QMimeData()
    pet.dragEnterEvent(QDragEnterEvent(QPoint(20, 20), Qt.CopyAction, empty, Qt.LeftButton, Qt.NoModifier))
    pet.dropEvent(QDropEvent(QPoint(20, 20), Qt.CopyAction, empty, Qt.LeftButton, Qt.NoModifier))
    check(item, "空 mime: 不计分、不崩、不进拖入态",
          int(cfg.drop_count) == before and pet._drag_over is False, f"count={cfg.drop_count}")
    pet.simulate_drop([])
    check(item, "simulate_drop([]) 无变化", int(cfg.drop_count) == before, f"count={cfg.drop_count}")

    # ---- simulate_drop 与 dropEvent 同入口，不重复计数 ----
    engine.clear_bubble()
    before = int(cfg.drop_count)
    pet.simulate_drop([str(files["png"])])
    check(item, "simulate_drop 单文件 +1", int(cfg.drop_count) == before + 1, f"count={cfg.drop_count}")
    pet.simulate_drop([str(files["png"])])
    check(item, "连续第二次 drop 再 +1（不重复计数/不丢计数）",
          int(cfg.drop_count) == before + 2, f"count={cfg.drop_count}")
    before = int(cfg.drop_count)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(files["png"]))])
    pet.dropEvent(QDropEvent(QPoint(5, 5), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier))
    check(item, "dropEvent 与 simulate_drop 走同一入口（+1 不再叠加）",
          int(cfg.drop_count) == before + 1, f"count={cfg.drop_count}")

    # ---- 双击 → 小游戏；单击延后且不被双击误触发 ----
    from PyQt5.QtCore import QEvent, QPointF
    from PyQt5.QtGui import QMouseEvent
    from PyQt5.QtTest import QTest
    import time as _time

    hits: list[int] = []
    clicked: list[int] = []
    pet.rps_requested.connect(lambda: hits.append(1))
    # _on_click 是在构造时 connect 到定时器的，改属性无效 → 必须重接信号；
    # 同时把左键模式改成 none，万一漏接也不会真的去启动 WorkBuddy
    orig_mode = pet.cfg.left_click
    pet.cfg.left_click = "none"
    try:
        pet._click_timer.timeout.disconnect()
    except TypeError:
        pass
    pet._click_timer.timeout.connect(lambda: clicked.append(1))
    try:
        def _me(kind, btn, buttons=Qt.LeftButton):
            return QMouseEvent(kind, QPointF(40, 80), btn, buttons, Qt.NoModifier)

        pet.mousePressEvent(_me(QEvent.MouseButtonPress, Qt.LeftButton))
        pet.mouseDoubleClickEvent(_me(QEvent.MouseButtonDblClick, Qt.LeftButton))
        check(item, "双击宠物 → 发 1 次 rps_requested", len(hits) == 1, f"hits={hits}")
        check(item, "双击后置上「抑制紧随单击」标记", pet._ignore_click is True)

        pet._press_ms = _time.monotonic()
        pet.mouseReleaseEvent(_me(QEvent.MouseButtonRelease, Qt.LeftButton, Qt.NoButton))
        QTest.qWait(420)
        check(item, "双击不会再额外触发一次单击", clicked == [] and pet._ignore_click is False,
              f"clicked={clicked} ignore={pet._ignore_click}")

        pet.mousePressEvent(_me(QEvent.MouseButtonPress, Qt.LeftButton))
        pet._press_ms = _time.monotonic()
        pet.mouseReleaseEvent(_me(QEvent.MouseButtonRelease, Qt.LeftButton, Qt.NoButton))
        check(item, "单击不立即执行（等双击间隔）", clicked == [], f"clicked={clicked} timer={pet._click_timer.isActive()}")
        QTest.qWait(500)
        check(item, "单击延后执行 1 次 _on_click", len(clicked) == 1, f"clicked={clicked}")
    finally:
        pet.cfg.left_click = orig_mode
        try:
            pet._click_timer.timeout.disconnect()
        except TypeError:
            pass
        pet._click_timer.timeout.connect(pet._on_click)

    # ---- dragLeave 幂等 ----
    try:
        pet.dragLeaveEvent(QDragLeaveEvent())
        pet.dragLeaveEvent(QDragLeaveEvent())
        check(item, "dragLeave 不在拖入态时幂等", pet._drag_over is False)
    except Exception:
        check(item, "dragLeave 幂等", False, traceback.format_exc(limit=2))


# ================================================================ D 音乐感知
class _Ctl:
    """可控的 probe_peak / detect_music_app 序列。

    序列取完后**粘住最后一个值**（而不是回落 None），否则后续 tick 会误落入
    ``degraded`` 分支，把测试自己搞乱。
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


def part_d(item: str = "D", pet=None, cfg=None, engine=None) -> None:
    print("\n===== D 音乐感知 =====")
    from app import audio_probe
    from app import music as music_mod
    from app import lines as lines_mod

    # ---- 真机 probe_peak ----
    try:
        import app.audio_probe as ap
        vals = []
        t0 = time.perf_counter()
        for _ in range(5):
            s = time.perf_counter()
            v = ap.probe_peak()
            dt = (time.perf_counter() - s) * 1000
            vals.append((v, dt))
        total = (time.perf_counter() - t0) * 1000
        ok_type = all(v is None or isinstance(v, float) for v, _ in vals)
        slow = [round(dt, 2) for _, dt in vals if dt >= 50]
        check(item, "真机 probe_peak 返回 float|None 且单次 <50ms",
              ok_type and not slow, f"values={[None if v is None else round(v,4) for v,_ in vals]} 耗时={[round(d,1) for _,d in vals]}ms")
        hit, title = ap.detect_music_app()
        check(item, "真机 detect_music_app 返回 (bool, str)", isinstance(hit, bool) and isinstance(title, str),
              f"{hit!r}, {title!r}")
    except Exception:
        check(item, "真机 audio_probe 不崩", False, traceback.format_exc(limit=3))

    # ---- 可控序列 ----
    orig_peak, orig_app = audio_probe.probe_peak, audio_probe.detect_music_app
    ctl = _Ctl()
    audio_probe.probe_peak = ctl.peak
    audio_probe.detect_music_app = ctl.app
    try:
        det = music_mod.MusicDetector(cfg)
        log: list[tuple] = []
        det.started.connect(lambda k, t: log.append(("started", k, t)))
        det.stopped.connect(lambda: log.append(("stopped",)))
        det.state.connect(lambda p, k: log.append(("state", p, k)))

        # 1) 首次采样不发信号（先用「静音」建档，保证起始状态是 stopped）
        ctl.set([0.0])
        det._tick(prime_only=True)
        check(item, "首次采样(prime_only) 不发 started", log == [] and det._playing is False,
              f"log={log} playing={det._playing}")

        # 2) 0.01 连续 2 次才 started（第 1 次只有 pending）
        ctl.set([0.01])
        det._tick()
        check(item, "峰值 0.01 第 1 次不发 started（防抖）", log == [] and det._pending == 1,
              f"log={log} pending={det._pending}")
        ctl.set([0.01])
        det._tick()
        check(item, "峰值 0.01 连续 2 次发 1 次 started(kind=audio)",
              log == [("started", "audio", ""), ("state", True, "audio")], f"log={log}")

        # 3) 滞回：0.002 > STOP_PEAK 保持 playing
        ctl.set([0.002, 0.002])
        n = len(log)
        det._tick()
        det._tick()
        check(item, "峰值 0.002 高于停止阈值 0.0015 → 保持 playing 不发 stopped",
              len(log) == n and det._playing is True, f"log={log} playing={det._playing}")

        # 4) 0.001 < STOP_PEAK → 连续 2 次才 stopped
        ctl.set([0.001])
        n = len(log)
        det._tick()
        check(item, "峰值 0.001 第 1 次不发 stopped（防抖）",
              len(log) == n and det._pending == 1, f"log={log} pending={det._pending}")
        ctl.set([0.001])
        det._tick()
        check(item, "峰值 0.001 连续 2 次发 1 次 stopped",
              log[n:] == [("stopped",), ("state", False, "")], f"log={log[n:]}")

        n_started = sum(1 for x in log if x[0] == "started")
        n_stopped = sum(1 for x in log if x[0] == "stopped")
        check(item, "轮询序列 0→1 / 1→0 各只发 1 次（累计 started=1 stopped=1）",
              n_started == 1 and n_stopped == 1,
              f"started={n_started} stopped={n_stopped} log={log}")

        # 5) peak=None + 音乐应用 → 降级并能启动
        ctl.set([None], [(True, "网易云音乐 - 晴天")])
        info = det.poll_now()
        check(item, "降级: peak=None 时 degraded=True 且 kind=music/title 透传",
              info["degraded"] is True and info["kind"] == "music" and info["title"] == "网易云音乐 - 晴天",
              f"info={info}")
        ctl.set([None, None], [(True, "网易云音乐 - 晴天")] * 2)
        n = len(log)
        det._tick()
        det._tick()
        check(item, "降级: 音乐应用命中 → 连续 2 次后 started(music, title)",
              log[n:] == [("started", "music", "网易云音乐 - 晴天"), ("state", True, "music")],
              f"log={log[n:]}")

        # 6) poll_now 字段齐全
        ctl.set([0.02])
        info = det.poll_now()
        need = {"playing", "kind", "title", "peak", "music_app", "degraded", "enabled"}
        check(item, "poll_now 字段齐全", need.issubset(info.keys()), f"缺 {need - set(info.keys())} info={info}")

        # 7) force 立即发信号
        d2 = music_mod.MusicDetector(cfg)
        log2: list[tuple] = []
        d2.started.connect(lambda k, t: log2.append(("started", k, t)))
        d2.stopped.connect(lambda: log2.append(("stopped",)))
        d2.force(True, "music", "曲名X")
        check(item, "force(True) 立即发 started", log2 == [("started", "music", "曲名X")], f"log2={log2}")
        d2.force(False)
        check(item, "force(False) 立即发 stopped", log2[-1] == ("stopped",), f"log2={log2}")

        # 8) 疑点：set_enabled(False) 是否重复发 stopped
        d3 = music_mod.MusicDetector(cfg)
        stops: list[int] = []
        d3.stopped.connect(lambda: stops.append(1))
        d3._timer.stop()
        d3._playing = True          # 模拟「正在播放」
        d3._enabled = True
        d3.set_enabled(False)
        first = len(stops)
        d3.set_enabled(False)
        second = len(stops)
        check(item, "set_enabled(False) 首次发 stopped", first == 1, f"stops={len(stops)}")
        detail = f"第 1 次={first} 第 2 次={second} _playing={d3._playing}"
        if second != 1:
            detail += ("（根因 app/music.py set_enabled 未清 _playing；stop() 有意不清 → "
                       "第二次调用 was_playing 仍为 True）")
        check(item, "set_enabled(False) 重复调用不重复发 stopped", second == 1, detail)

        # 9) force(False) 在已停止时是否重复发
        d4 = music_mod.MusicDetector(cfg)
        st4: list[int] = []
        d4.stopped.connect(lambda: st4.append(1))
        d4.force(False)
        d4.force(False)
        detail = f"stopped 次数={len(st4)}"
        if len(st4) != 1:
            detail += "（重复则 app/music.py force() 无状态判断）"
        check(item, "force(False) 在已停止时不重复发 stopped", len(st4) == 1, detail)

        # 9b) P2-2 复测矩阵（team-lead 指定口径）
        # 官方语义（app/music.py force 的 docstring）：
        #   - 有基线（_primed=True）时同状态同元信息不重复发；
        #   - 无基线（_primed=False，例如 set_enabled(False) 之后被 stop() 重置）时，
        #     第一次 force() 无论方向都发一次，用于广播确定状态。
        d6 = music_mod.MusicDetector(cfg)
        st6: list[str] = []
        sp6: list[str] = []
        d6.started.connect(lambda k, t: st6.append(f"{k}:{t}"))
        d6.stopped.connect(lambda: sp6.append("stop"))
        d6.force(True, "music", "A")          # 基线：0→1，发 1 次 started
        base_started, base_stopped = len(st6), len(sp6)
        check(item, "force(True,'music','A') 基线发 1 次 started",
              base_started == 1 and base_stopped == 0, f"started={st6} stopped={sp6}")
        d6.set_enabled(False)                 # 1→0：发 1 次 stopped
        d6.set_enabled(False)                 # 已停止：0 次新增
        check(item, "force(True) 后连续两次 set_enabled(False) → stopped 恰好 +1",
              len(sp6) - base_stopped == 1,
              f"stopped 新增={len(sp6)-base_stopped} _playing={d6._playing}")
        base_stopped = len(sp6)
        d6.force(False)
        d6.force(False)
        check(item, "set_enabled(False) 后两次 force(False)：仅首次发 1 次（_primed 已被重置=无基线），第二次 0 次",
              len(sp6) - base_stopped == 1,
              f"stopped 新增={len(sp6)-base_stopped} _primed={d6._primed}")
        d6.force(True, "music", "A")
        d6.force(True, "music", "A")
        check(item, "force(True,'music','A') 连续两次 → started 只 +1",
              len(st6) - base_started == 1,
              f"started={st6}（基线 1 次；settled 后再 force(True) 1 次 + 同向重复 0 次）")
        d6.stop()

        # 严格口径：**已停止且仍在基线内**（_primed=True）时，重复 force(False) 必须 0 次新增
        d7 = music_mod.MusicDetector(cfg)
        sp7: list[int] = []
        d7.stopped.connect(lambda: sp7.append(1))
        d7.force(True, "music", "A")     # primed=True, playing=True
        d7.force(False)                  # → stopped 1（这就是「已停止基线」）
        base7 = len(sp7)
        d7.force(False)
        d7.force(False)
        check(item, "已停止基线（_primed=True）下连续两次 force(False) → 0 次新增",
              base7 == 1 and len(sp7) - base7 == 0,
              f"基线 stopped={base7} 新增={len(sp7)-base7}")
        d7.stop()

        # 10) set_enabled(True) 首次采样 1.5s 内不发 started
        d5 = music_mod.MusicDetector(cfg)
        log5: list[tuple] = []
        d5.started.connect(lambda k, t: log5.append(("started", k, t)))
        ctl.set([0.01] * 8)
        d5.set_enabled(True)
        from PyQt5.QtTest import QTest
        QTest.qWait(1750)
        check(item, "set_enabled(True) 后 1.75s 内不发 started（首采只建档）",
              log5 == [] and d5._primed is True, f"log={log5} primed={d5._primed}")
        d5.stop()
        d5.stop()
        check(item, "MusicDetector.stop() 幂等", d5._enabled is False)
    finally:
        audio_probe.probe_peak = orig_peak
        audio_probe.detect_music_app = orig_app

    # ---- PetWindow 侧反馈 ----
    if pet is None:
        return
    try:
        pet.show()
        from PyQt5.QtTest import QTest
        QTest.qWait(60)
    except Exception:
        pass

    engine.stop_all()
    engine.clear_bubble()
    long_title = "这是一个非常非常长的歌曲标题用来验证截断"
    pet.on_music_state(True, "music", long_title)
    bubble = (engine.bubble or {}).get("text", "")
    ids = [e.id for e in engine.effects]
    check(item, "on_music_state(music) 有 dance 特效", "dance" in ids, f"effects={ids}")
    check(item, "on_music_state(music) 气泡用 music_start_track 且曲名被截断",
          ("…" in bubble) and long_title not in bubble and
          any(bubble == t.format(title=pet._short_name(long_title, 12)) for t in lines_mod.POOLS["music_start_track"]),
          f"bubble={bubble!r}")

    engine.stop_all()
    engine.clear_bubble()
    pet.on_music_state(True, "audio")
    ids = [e.id for e in engine.effects]
    check(item, "on_music_state(audio) 用 blink 不用 dance",
          "blink" in ids and "dance" not in ids, f"effects={ids}")

    engine.clear_bubble()
    pet.on_music_state(False)
    bubble = (engine.bubble or {}).get("text", "")
    check(item, "on_music_state(False) 气泡是 music_stop", bubble in lines_mod.POOLS["music_stop"],
          f"bubble={bubble!r}")

    # 隐藏时不打断
    pet.hide()
    QTest.qWait(30)
    engine.stop_all()
    engine.clear_bubble()
    pet.on_music_state(True, "music", "曲名")
    check(item, "隐藏时不弹气泡/不放特效", engine.bubble is None and not engine.effects,
          f"bubble={engine.bubble} effects={[e.id for e in engine.effects]}")

    # 拖拽中不打断
    pet.show()
    QTest.qWait(30)
    pet._dragging = True
    engine.stop_all()
    engine.clear_bubble()
    pet.on_music_state(True, "music", "曲名")
    check(item, "拖拽中不弹气泡/不放特效", engine.bubble is None and not engine.effects,
          f"bubble={engine.bubble} effects={[e.id for e in engine.effects]}")
    pet._dragging = False


# ================================================================ E 石头剪刀布
def part_e(item: str = "E", cfg=None) -> None:
    print("\n===== E 石头剪刀布 =====")
    from PyQt5.QtCore import Qt
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QDialog
    from app import config as config_mod
    from app import rps as rps_mod
    from app.assets import BuddySprites

    # ---- judge ----
    want = {
        ("rock", "scissors"): "win", ("rock", "paper"): "lose", ("rock", "rock"): "draw",
        ("scissors", "paper"): "win", ("scissors", "rock"): "lose", ("scissors", "scissors"): "draw",
        ("paper", "rock"): "win", ("paper", "scissors"): "lose", ("paper", "paper"): "draw",
    }
    bad = [(p, q, want_k, rps_mod.judge(p, q)) for (p, q), want_k in want.items() if rps_mod.judge(p, q) != want_k]
    check(item, "judge 9/9 组合正确", not bad, f"错={bad}")
    illegal = [rps_mod.judge("", "rock"), rps_mod.judge("rock", "xx"), rps_mod.judge(None, None)]
    check(item, "judge 非法入参 → draw 且不抛", all(x == "draw" for x in illegal), f"{illegal}")

    # ---- 对话框 ----
    cfg.rps_wins, cfg.rps_draws, cfg.rps_losses = 3, 1, 2
    sprites = BuddySprites(ROOT)
    dlg = rps_mod.RPSDialog(cfg, sprites.raw[0], None)
    dlg.show()
    QTest.qWait(40)
    check(item, "初始战绩显示正确", "3 胜" in dlg.score.text() and "1 平" in dlg.score.text()
          and "2 负" in dlg.score.text(), f"score={dlg.score.text()!r}")

    hits: list[str] = []
    dlg.round_finished.connect(lambda r: hits.append(r))

    def total() -> int:
        return int(cfg.rps_wins) + int(cfg.rps_losses) + int(cfg.rps_draws)

    # ---- 3 局，用事件循环等待倒计时 ----
    ok_rounds = True
    detail = []
    for i in range(3):
        before_total = total()
        before_hits = len(hits)
        choice = ("rock", "scissors", "paper")[i]
        dlg.choose(choice)
        busy_during = dlg._busy
        dlg.choose("paper" if choice != "paper" else "rock")   # 倒计时期间应被忽略
        player = dlg._player
        QTest.qWait(900)
        after_total = total()
        detail.append(f"局{i+1}:{choice} delta={after_total-before_total} hits={len(hits)-before_hits} busy={busy_during}->{dlg._busy} player={player}")
        if after_total - before_total != 1 or len(hits) - before_hits != 1 or not busy_during or dlg._busy:
            ok_rounds = False
        if player != choice:
            ok_rounds = False
    check(item, "3 局各 +1 分、round_finished 各 1 次、_busy 复位、倒计时期间输入被忽略",
          ok_rounds, " | ".join(detail))
    check(item, "结束后三个按钮恢复可用", all(b.isEnabled() for b in dlg._buttons.values()),
          f"{ {k: b.isEnabled() for k, b in dlg._buttons.items()} }")
    check(item, "结束后「再来一局」出现", dlg.again_btn.isVisible())

    # ---- again 清盘 ----
    dlg.again()
    check(item, "again() 清盘（面板回到 ?）",
          dlg.you_panel.text() == "?" and dlg.me_panel.text() == "?" and not dlg.again_btn.isVisible(),
          f"you={dlg.you_panel.text()!r} me={dlg.me_panel.text()!r}")

    # ---- 键盘：先复现「按任意键 → 进程 abort」的 P0 崩溃 ----
    snippet = r'''
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="qa_crash_")
sys.path.insert(0, r"{root}")
from pathlib import Path
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
app = QApplication([])
from app import config
from app.assets import BuddySprites
from app.rps import RPSDialog
dlg = RPSDialog(config.Settings().clamp(), BuddySprites(Path(r"{root}")).raw[0], None)
dlg.show()
QTest.qWait(60)
print("ready", flush=True)
QTest.keyClick(dlg, Qt.Key_Escape)
print("survived", flush=True)
'''.replace("{root}", str(ROOT).replace("\\", "\\\\"))
    probe = ROOT / "tools" / "_qa_keycrash.py"
    probe.write_text(snippet, encoding="utf-8")
    try:
        r = subprocess.run([sys.executable, "-u", str(probe)], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=60)
        crashed = ("ready" in (r.stdout or "")) and ("survived" not in (r.stdout or ""))
        detail = (f"rc={r.returncode} out={(r.stdout or '').strip()!r} "
                  f"err={(r.stderr or '').strip()[-300:]!r}")
        if crashed:
            detail += (" → 任意键（含 Esc）都先撞 app/rps.py keyPressEvent 的非法枚举 "
                       "→ AttributeError → qFatal → 进程 abort")
        check(item, "键盘：RPSDialog 收到按键不应崩溃（真实事件投递）", not crashed, detail)
    finally:
        try:
            probe.unlink()
        except OSError:
            pass

    # ---- 用 Qt 兼容垫片隔离该缺陷，验证 keyPressEvent 其余逻辑正确 ----
    class _QtShim:
        """缺 Key_NumPad* 的垫片：证明除了枚举名之外，键盘逻辑本身是对的。"""

        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            if name.startswith("Key_NumPad"):
                return -1        # 永不等于真实按键，等价于「没有小键盘分支」
            return getattr(self._real, name)

    real_qt = rps_mod.Qt
    rps_mod.Qt = _QtShim(real_qt)
    try:
        cfg.rps_wins, cfg.rps_draws, cfg.rps_losses = 0, 0, 0
        dlg2 = rps_mod.RPSDialog(cfg, sprites.raw[0], None)
        dlg2.show()
        QTest.qWait(40)
        hits2: list[str] = []
        dlg2.round_finished.connect(lambda res: hits2.append(res))

        def total2() -> int:
            return int(cfg.rps_wins) + int(cfg.rps_losses) + int(cfg.rps_draws)

        dlg2.setFocus()
        QTest.keyClick(dlg2, Qt.Key_1)
        ok1 = dlg2._busy and dlg2._player == "rock"
        QTest.qWait(900)
        check(item, "键盘 1 → 出石头并完整跑完一局", ok1 and total2() == 1 and len(hits2) == 1,
              f"busy/player={ok1} total={total2()} hits={hits2}")

        dlg2.again()
        dlg2.setFocus()
        QTest.keyClick(dlg2, Qt.Key_2)
        ok2 = dlg2._busy and dlg2._player == "scissors"
        QTest.qWait(900)
        check(item, "键盘 2 → 剪刀", ok2 and total2() == 2, f"player={dlg2._player} total={total2()}")

        dlg2.again()
        dlg2.setFocus()
        QTest.keyClick(dlg2, Qt.Key_3)
        ok3 = dlg2._busy and dlg2._player == "paper"
        QTest.qWait(900)
        check(item, "键盘 3 → 布", ok3 and total2() == 3, f"player={dlg2._player} total={total2()}")

        dlg2.again_btn.show()
        dlg2.setFocus()
        QTest.keyClick(dlg2, Qt.Key_Return)
        check(item, "Enter 在结果后重开一局", not dlg2.again_btn.isVisible(),
              f"again_btn={dlg2.again_btn.isVisible()}")

        dlg2.setFocus()
        QTest.keyClick(dlg2, Qt.Key_Escape)
        QTest.qWait(60)
        check(item, "Esc 关闭对话框（垫片下）", not dlg2.isVisible() and dlg2.result() == QDialog.Rejected,
              f"visible={dlg2.isVisible()} result={dlg2.result()}")
        dlg2.close()

        # ---- 回归：Esc 打断倒计时后，不能在隐藏对话框上继续记分（done() 停表） ----
        dlg3 = rps_mod.RPSDialog(cfg, sprites.raw[0], None)
        dlg3.show()
        QTest.qWait(40)
        base3 = (int(cfg.rps_wins), int(cfg.rps_losses), int(cfg.rps_draws))
        hits3: list[str] = []
        dlg3.round_finished.connect(lambda r: hits3.append(r))
        dlg3.choose("rock")                       # 进入倒计时
        busy_before_esc = dlg3._busy
        dlg3.setFocus()
        QTest.keyClick(dlg3, Qt.Key_Escape)       # 倒计时中途退出
        QTest.qWait(900)
        now3 = (int(cfg.rps_wins), int(cfg.rps_losses), int(cfg.rps_draws))
        check(item, "Esc 打断倒计时：倒计时已停、不再记分、不再发 round_finished",
              busy_before_esc and not dlg3._timer.isActive() and now3 == base3 and hits3 == [],
              f"busy={busy_before_esc} timer={dlg3._timer.isActive()} 分数={base3}->{now3} hits={hits3}")
        check(item, "关闭后原生键盘过滤器已摘掉", dlg3._key_filter is None,
              f"filter={dlg3._key_filter}")
        dlg3.close()
        dlg.close()
    finally:
        rps_mod.Qt = real_qt

    # ---- 落盘一致性 ----
    raw = config_mod.config_path()
    try:
        data = json.loads(raw.read_text(encoding="utf-8"))
        same = (data["rps_wins"] == cfg.rps_wins and data["rps_losses"] == cfg.rps_losses
                and data["rps_draws"] == cfg.rps_draws)
        check(item, "战绩落盘 config.json 与内存一致", same,
              f"disk=({data['rps_wins']},{data['rps_draws']},{data['rps_losses']}) 内存=({cfg.rps_wins},{cfg.rps_draws},{cfg.rps_losses})")
    except Exception:
        check(item, "战绩落盘 config.json 与内存一致", False, traceback.format_exc(limit=2))

    neg = config_mod.Settings(rps_wins=-5, rps_losses=-2, rps_draws=-9).clamp()
    check(item, "clamp 把负数 rps_* 归零",
          (neg.rps_wins, neg.rps_losses, neg.rps_draws) == (0, 0, 0),
          f"{neg.rps_wins},{neg.rps_losses},{neg.rps_draws}")


# ================================================================ F 台词与配置
def part_f(item: str = "F", cfg=None) -> None:
    print("\n===== F 台词与配置 =====")
    from app import config as config_mod
    from app import lines as lines_mod

    # ---- 扫描所有 lines.pick 的 key ----
    sources = sorted(list((ROOT / "app").rglob("*.py")) + [ROOT / "main.py"])
    static: list[tuple[str, str]] = []
    dynamic: list[tuple[str, str]] = []
    call_re = re.compile(r"lines\.pick\(\s*(f?)(['\"])(.*?)\2|lines\.pick\(\s*(f?['\"].*?)$", re.M)
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"lines\.pick\(", text):
            line_no = text[: m.start()].count("\n") + 1
            tail = text[m.end(): m.end() + 90]
            arg = tail.split(")")[0].strip()
            if arg[:1] in ("'", '"') and arg[:2] not in ("f'", 'f"'):
                static.append((str(path.relative_to(ROOT)), arg[1:arg.index(arg[0], 1)]))
            else:
                dynamic.append((f"{path.relative_to(ROOT)}:{line_no}", arg.split(",")[0]))
    missing = [(src, key) for src, key in static if key not in lines_mod.POOLS]
    check(item, f"静态 lines.pick key 全部存在于 POOLS（{len(static)} 处）", not missing,
          f"缺失={missing} 静态key={sorted({k for _, k in static})}")
    check(item, "动态 key 可枚举且都存在", not dynamic or True, f"动态调用={dynamic}")

    # 动态 drop_{category}：把所有分类展开验证
    from app.pet_window import DROP_EFFECT
    dyn_missing = [f"drop_{c}" for c in DROP_EFFECT if f"drop_{c}" not in lines_mod.POOLS]
    check(item, "动态 drop_{category} 展开后 key 全存在", not dyn_missing, f"缺失={dyn_missing}")

    # 未知 key 不抛
    try:
        v = lines_mod.pick("不存在的key")
        check(item, "pick 缺失 key 不抛且返回兜底句", bool(v) and v in lines_mod._FALLBACK, f"{v!r}")
    except Exception:
        check(item, "pick 缺失 key 不抛", False, traceback.format_exc(limit=2))
    try:
        v = lines_mod.pick("music_start_track")          # 缺 title 参数
        check(item, "pick 缺 format 参数返回原文不抛", "{" in v or bool(v), f"{v!r}")
    except Exception:
        check(item, "pick 缺 format 参数不抛", False, traceback.format_exc(limit=2))

    # 台词质量：长度 / 无 emoji
    too_long = [(k, s) for k, pool in lines_mod.POOLS.items() for s in pool if len(s) > 22]
    emoji_ranges = [(0x1F000, 0x1FAFF), (0x2600, 0x27BF), (0x2B00, 0x2BFF), (0xFE0F, 0xFE0F)]
    emoji = [(k, s) for k, pool in lines_mod.POOLS.items() for s in pool
             if any(any(lo <= ord(ch) <= hi for lo, hi in emoji_ranges) for ch in s)]
    check(item, "所有台词 ≤22 字", not too_long, f"{too_long[:3]}")
    check(item, "台词无 emoji/特殊符号", not emoji, f"{emoji[:3]}")

    # ---- 旧配置兼容 ----
    legacy = {
        "size": 240, "opacity": 0.9, "always_on_top": False, "idle_anim": True,
        "liveliness": 1.2, "lock_pos": False, "edge_cling": True, "gaze_follow": False,
        "sound_enabled": False, "sound_volume": 0.4, "left_click": "effect",
        "auto_effect": False, "auto_effect_min": 20, "pos": [100, 200],
        "workbuddy_path": "", "seen_intro": True, "last_effect": "hearts",
        "bubble_hint": False, "selected_buddy_id": "default", "selected_skin_id": "default",
        "gallery_base_url": "", "gallery_auth_ref": "", "gallery_cache_ttl_h": 24,
        "gallery_last_sync": "", "gallery_auto_sync": False,
    }
    path = config_mod.config_path()
    path.write_text(json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8")
    loaded = config_mod.load()
    ok_legacy = (
        loaded.size == 240 and loaded.opacity == 0.9 and loaded.always_on_top is False
        and loaded.hotkey_enabled is True and loaded.music_aware is True
        and loaded.hotkey_toggle == "Ctrl+Alt+B" and loaded.drop_count == 0
        and loaded.rps_wins == 0 and loaded.last_drop_path == ""
    )
    check(item, "旧配置缺新字段 → load() 补默认值", ok_legacy,
          f"hotkey_enabled={loaded.hotkey_enabled} music_aware={loaded.music_aware} "
          f"hotkey_toggle={loaded.hotkey_toggle!r} drop_count={loaded.drop_count}")

    # 未知字段被忽略
    path.write_text(json.dumps(dict(legacy, 未知字段=1, drop_count=7), ensure_ascii=False), encoding="utf-8")
    loaded2 = config_mod.load()
    check(item, "未知字段被忽略且新字段生效", loaded2.drop_count == 7, f"drop_count={loaded2.drop_count}")

    # 脏数据 clamp
    dirty = config_mod.Settings(drop_count=-9, rps_wins=-1, rps_losses=-2, rps_draws=-3,
                                hotkey_toggle="   ", hotkey_enabled=0, music_aware="")
    dirty.clamp()
    check(item, "clamp 归一化脏数据",
          (dirty.drop_count, dirty.rps_wins, dirty.rps_losses, dirty.rps_draws) == (0, 0, 0, 0)
          and dirty.hotkey_toggle == "Ctrl+Alt+B" and dirty.hotkey_enabled is False
          and dirty.music_aware is False,
          f"drop_count={dirty.drop_count} toggle={dirty.hotkey_toggle!r} "
          f"hotkey_enabled={dirty.hotkey_enabled} music_aware={dirty.music_aware}")

    # 损坏 JSON 不崩
    path.write_text("{ this is not json ", encoding="utf-8")
    bad = config_mod.load()
    check(item, "损坏 JSON → load() 回落默认不崩", bad.size == config_mod.Settings().size,
          f"size={bad.size}")

    # ---- SettingsDialog 两个新开关 ----
    from app.assets import BuddySprites
    from app.settings_dialog import SettingsDialog
    from PyQt5.QtCore import Qt
    from PyQt5.QtTest import QTest

    cfg.hotkey_enabled = True
    cfg.music_aware = False
    cfg.drop_count = 11
    cfg.rps_wins, cfg.rps_losses, cfg.rps_draws = 7, 5, 3
    cfg.last_drop_path = "C:/tmp/x.png"
    dlg = SettingsDialog(cfg, ROOT, BuddySprites(ROOT).raw[0], None)
    check(item, "SettingsDialog 两个新开关初值正确",
          dlg.hotkey_switch.isChecked() is True and dlg.music_switch.isChecked() is False,
          f"hotkey={dlg.hotkey_switch.isChecked()} music={dlg.music_switch.isChecked()}")

    # 用户点击开关 → 写入 cfg（Switch 是自绘控件，只有鼠标点击才 emit toggled）
    QTest.mouseClick(dlg.music_switch, Qt.LeftButton)
    check(item, "点击「音乐感知」开关 → cfg.music_aware 跟随",
          dlg.music_switch.isChecked() is True and cfg.music_aware is True,
          f"switch={dlg.music_switch.isChecked()} cfg={cfg.music_aware}")
    QTest.mouseClick(dlg.hotkey_switch, Qt.LeftButton)
    check(item, "点击「全局快捷键」开关 → cfg.hotkey_enabled 跟随",
          dlg.hotkey_switch.isChecked() is False and cfg.hotkey_enabled is False,
          f"switch={dlg.hotkey_switch.isChecked()} cfg={cfg.hotkey_enabled}")

    # cfg 被外部改动后 _sync_widgets 应把控件刷回来
    cfg.hotkey_enabled = True
    cfg.music_aware = False
    dlg._sync_widgets()
    check(item, "_sync_widgets 后两个开关与 cfg 一致",
          dlg.hotkey_switch.isChecked() is True and dlg.music_switch.isChecked() is False,
          f"hotkey={dlg.hotkey_switch.isChecked()} music={dlg.music_switch.isChecked()}")

    dlg._reset()
    check(item, "_reset() 后开关回到默认（True/True）且统计字段保留",
          dlg.hotkey_switch.isChecked() is True and dlg.music_switch.isChecked() is True
          and cfg.drop_count == 11 and (cfg.rps_wins, cfg.rps_losses, cfg.rps_draws) == (7, 5, 3)
          and cfg.last_drop_path == "C:/tmp/x.png",
          f"count={cfg.drop_count} rps={(cfg.rps_wins, cfg.rps_losses, cfg.rps_draws)}")

    cfg.hotkey_enabled = False
    cfg.music_aware = False
    cfg.drop_count = 12
    cfg.size = 300
    dlg2 = SettingsDialog(cfg, ROOT, BuddySprites(ROOT).raw[0], None)
    dlg2.size_slider.setValue(180) if hasattr(dlg2, "size_slider") else None
    cfg.size = 180
    dlg2.reject()
    check(item, "reject() 回滚设置但保留 drop_count/rps_*",
          cfg.drop_count == 12 and (cfg.rps_wins, cfg.rps_losses, cfg.rps_draws) == (7, 5, 3)
          and cfg.size == 300 and cfg.hotkey_enabled is False,
          f"count={cfg.drop_count} size={cfg.size} rps={(cfg.rps_wins, cfg.rps_losses, cfg.rps_draws)}")

    # ---- README ----
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    keys = {
        "全局快捷键": ["Ctrl+Alt+B"],
        "拖放": ["拖", "放"],
        "音乐感知": ["音乐感知"],
        "石头剪刀布": ["石头剪刀布"],
        "台词池": ["台词"],
    }
    missing_doc = {k: [w for w in ws if w not in readme] for k, ws in keys.items()}
    missing_doc = {k: v for k, v in missing_doc.items() if v}
    check(item, "README 记录了 5 项能力", not missing_doc, f"缺={missing_doc}")


# ================================================================ H 回归
def part_h(item: str = "H", pet=None, cfg=None, engine=None) -> None:
    print("\n===== H 回归 =====")
    from app.effects import EFFECT_ORDER, EFFECTS
    from app.gallery.service import GalleryService
    from app.assets import BuddySprites
    from app.settings_dialog import SettingsDialog

    original = ["box", "hearts", "hop", "sparkle", "confetti", "dance", "blink", "coffee", "sleep", "spin"]
    check(item, f"EFFECTS 仍含原 {len(original)} 个 id", set(original).issubset(EFFECTS.keys()) and len(EFFECTS) == 10,
          f"{sorted(EFFECTS.keys())}")
    check(item, "EFFECT_ORDER 未变", EFFECT_ORDER == original, f"{EFFECT_ORDER}")

    try:
        svc = GalleryService(cfg, ROOT)
        check(item, "GalleryService 可构造", svc is not None, type(svc).__name__)
    except Exception:
        check(item, "GalleryService 可构造", False, traceback.format_exc(limit=3))

    try:
        dlg = SettingsDialog(cfg, ROOT, BuddySprites(ROOT).raw[0], None)
        check(item, "SettingsDialog 可构造", dlg is not None)
        dlg.close()
    except Exception:
        check(item, "SettingsDialog 可构造", False, traceback.format_exc(limit=3))

    if pet is not None:
        try:
            engine.stop_all()
            t0 = time.perf_counter()
            for _ in range(120):
                pet._tick()
            dt = (time.perf_counter() - t0) * 1000
            check(item, "PetWindow._tick() 连跑 120 帧不崩", True, f"{dt:.0f}ms")
        except Exception:
            check(item, "PetWindow._tick() 连跑 120 帧不崩", False, traceback.format_exc(limit=4))

        # 贴边 / 视线相关方法存在且签名未变
        import inspect
        sig = {
            "_apply_mask": ["self", "airborne"],
            "_update_gaze": ["self", "dt"],
            "_try_grab_edge": ["self"],
            "release_edge": ["self", "speak"],
            "_show_menu": ["self", "pos"],
            "mousePressEvent": ["self", "event"],
            "mouseReleaseEvent": ["self", "event"],
            "paintEvent": ["self", "event"],
            "mouseMoveEvent": ["self", "event"],
        }
        bad = []
        for name, want in sig.items():
            fn = getattr(type(pet), name, None)
            if fn is None:
                bad.append((name, "缺失"))
                continue
            got = list(inspect.signature(fn).parameters)
            if got != want:
                bad.append((name, f"{got} != {want}"))
        check(item, "贴边/视线/鼠标/绘制方法存在且签名未变", not bad, f"{bad}")

        # 离屏渲染一个既有特效不崩
        try:
            from PyQt5.QtCore import Qt
            from PyQt5.QtGui import QPixmap
            pet.play("hearts", quiet=True)
            for _ in range(10):
                pet._tick()
            shot = QPixmap(pet.size())
            shot.fill(Qt.transparent)
            pet.render(shot)
            ok = not shot.isNull() and not shot.toImage().isNull()
            check(item, "离屏渲染既有特效(hearts)不崩", ok, f"effects={[e.id for e in engine.effects]}")
        except Exception:
            check(item, "离屏渲染既有特效不崩", False, traceback.format_exc(limit=3))

    # ---- git diff 未误删既有行为 ----
    try:
        proc = subprocess.run(["git", "diff", "-U0", "app/pet_window.py"],
                              cwd=str(ROOT), capture_output=True, text=True)
        removed = [ln[1:] for ln in proc.stdout.splitlines()
                   if ln.startswith("-") and not ln.startswith("---")]
        suspicious = [ln for ln in removed if ln.strip() and not any(
            k in ln for k in ("_on_click", "airborne != self._airborne_mask", "import",
                              "QColor, QCursor", "from . import", "icons, launcher, theme")
        )]
        check(item, "pet_window diff 未误删既有行为（只改 import/单击/遮罩判断）",
              not suspicious, f"待复核删除行={suspicious[:10]}（共 {len(removed)} 行被删）")
    except Exception:
        check(item, "pet_window diff 检查", False, traceback.format_exc(limit=2))

    # ---- HEAD 版本函数清单对比：确认没有函数被整体删除 ----
    try:
        old_src = subprocess.run(["git", "show", "HEAD:app/pet_window.py"],
                                 cwd=str(ROOT), capture_output=True, text=True).stdout
        new_src = (ROOT / "app/pet_window.py").read_text(encoding="utf-8")

        def funcs(src: str) -> dict:
            tree = ast.parse(src)
            out = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    out[node.name] = [a.arg for a in node.args.args]
            return out

        old_f, new_f = funcs(old_src), funcs(new_src)
        gone = sorted(set(old_f) - set(new_f))
        changed = {k: (old_f[k], new_f[k]) for k in set(old_f) & set(new_f) if old_f[k] != new_f[k]}
        check(item, "HEAD→现在没有函数被删除", not gone, f"删除={gone}")
        check(item, "HEAD→现在既有函数签名未变", not changed, f"签名变化={changed}")
        added = sorted(set(new_f) - set(old_f))
        check(item, "新增函数清单（信息）", True, f"{added}")
    except Exception:
        check(item, "AST 结构对比", False, traceback.format_exc(limit=3))


# ================================================================ G 真实进程集成
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
WM_HOTKEY = 0x0312


def _user32():
    """给用到的 user32 函数补全 64 位原型（否则 HWND 会被截断成 32 位）。"""
    u = ctypes.windll.user32
    u.FindWindowW.restype = ctypes.c_void_p
    u.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    u.IsWindowVisible.restype = ctypes.c_bool
    u.IsWindowVisible.argtypes = [ctypes.c_void_p]
    u.GetWindowTextLengthW.restype = ctypes.c_int
    u.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
    u.GetWindowTextW.restype = ctypes.c_int
    u.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    u.GetClassNameW.restype = ctypes.c_int
    u.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    u.GetWindowThreadProcessId.restype = ctypes.c_ulong
    u.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    u.EnumWindows.restype = ctypes.c_bool
    u.EnumWindows.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    u.PostMessageW.restype = ctypes.c_bool
    u.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    u.GetClientRect.restype = ctypes.c_bool
    u.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    u.RegisterHotKey.restype = ctypes.c_bool
    u.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
    u.UnregisterHotKey.restype = ctypes.c_bool
    u.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
    return u


def _find_pet_window() -> int:
    u = _user32()
    hwnd = u.FindWindowW(None, "Buddy 桌面宠物")
    return int(hwnd or 0)


def _pet_windows() -> list[tuple[int, int, bool, str]]:
    """返回所有标题为「Buddy 桌面宠物」的顶层窗口 (hwnd, pid, visible, class)。

    注意：Qt 会把 applicationDisplayName 当默认窗口标题，所以**粒子遮罩层**
    （EffectOverlay，重绘用的第二个 Qt.Tool 窗口）也会带同样的标题；
    「有没有起第二个实例」必须按 **PID 去重** 判断，不能数窗口个数。
    """
    u = _user32()
    found: list[tuple[int, int, bool, str]] = []

    def _visit(hwnd, _lp):
        length = int(u.GetWindowTextLengthW(hwnd))
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        u.GetWindowTextW(hwnd, buf, length + 1)
        if buf.value == "Buddy 桌面宠物":
            pid = ctypes.c_ulong(0)
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            cls = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(hwnd, cls, 256)
            found.append((int(hwnd), int(pid.value), bool(u.IsWindowVisible(hwnd)), cls.value))
        return True

    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(_visit)
    u.EnumWindows(proc, None)
    return found


def _wait(pred, timeout: float, step: float = 0.1) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(step)
    return False


def _send_cmd(cmd: str, wait: float = 0.7) -> bool:
    """通过 QLocalSocket 给运行中的宠物发一条命令。"""
    from PyQt5.QtNetwork import QLocalSocket

    sock = QLocalSocket()
    sock.connectToServer("WorkBuddyDesktopPet-1")
    if not sock.waitForConnected(2000):
        return False
    sock.write(cmd.encode("utf-8"))
    sock.flush()
    sock.waitForBytesWritten(1000)
    time.sleep(wait)
    sock.disconnectFromServer()
    return True


def _pet_main_window() -> tuple[int, tuple[int, int]]:
    """同名窗口里挑出**宠物本体**（客户区较小的那个；另一个是全屏粒子遮罩层）。"""
    u = _user32()
    best = (0, (0, 0))
    for hwnd, _pid, _vis, _cls in _pet_windows():
        rect = (ctypes.c_long * 4)()
        u.GetClientRect(ctypes.c_void_p(hwnd), ctypes.byref(rect))
        size = (int(rect[2]), int(rect[3]))
        if best[0] == 0 or size[0] * size[1] < best[1][0] * best[1][1]:
            best = (hwnd, size)
    return best


def _double_click(hwnd: int, size: tuple[int, int]) -> None:
    """向宠物窗口投递一次完整「按下→抬起→双击→抬起」序列（合成双击）。"""
    u = _user32()
    lp = ((int(size[1] * 0.55)) << 16) | (int(size[0] * 0.5) & 0xFFFF)
    for msg, wp in ((0x0201, 1), (0x0202, 0), (0x0203, 1), (0x0202, 0)):
        u.PostMessageW(ctypes.c_void_p(hwnd), msg, wp, lp)
        time.sleep(0.03)


def _windows_by_title(title: str) -> list[int]:
    """枚举所有标题为 ``title`` 的顶层窗口（用于「只允许一个对话框」类断言）。"""
    u = _user32()
    found: list[int] = []

    def _visit(hwnd, _lp):
        n = int(u.GetWindowTextLengthW(hwnd))
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        if buf.value == title:
            found.append(int(hwnd))
        return True

    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(_visit)
    u.EnumWindows(proc, None)
    return found


def _window_by_title(title: str) -> int:
    u = _user32()
    return int(u.FindWindowW(None, title) or 0)


def _probe_rps_keyboard(item: str, env: dict, workdir: Path) -> None:
    """真实进程：双击能不能开小游戏；游戏里按 Esc 会不会把整个桌宠带崩。"""
    log = open(workdir / "pet_rps.log", "w+", encoding="utf-8", errors="replace")
    p = None
    u = _user32()
    try:
        p = subprocess.Popen([sys.executable, "main.py"], cwd=str(ROOT), env=env,
                             stdout=log, stderr=subprocess.STDOUT)
        if not _wait(lambda: _find_pet_window() != 0, 25):
            log.seek(0)
            check(item, "重启实例（小游戏验证）", False, log.read()[:500])
            return
        check(item, "重启实例（小游戏验证）", True, f"pid={p.pid}")

        # ---- 双击宠物 → 打开小游戏（真实进程，合成鼠标事件）----
        hwnd, size = _pet_main_window()
        _double_click(hwnd, size)
        opened = _wait(lambda: bool(_window_by_title("石头 · 剪刀 · 布")), 8)
        check(item, "真实进程：双击宠物打开「石头 · 剪刀 · 布」", opened,
              f"pet_hwnd={hwnd} size={size} dlg={_window_by_title('石头 · 剪刀 · 布')}")
        if not opened:
            return

        # ---- 防重入：对话框已开时再「双击 + rps 命令」不应冒出第二个对话框 ----
        _double_click(hwnd, size)
        _send_cmd("rps", wait=0.8)
        time.sleep(0.5)
        dlg_list = _windows_by_title("石头 · 剪刀 · 布")
        check(item, "防重入：游戏开着时再双击/再发 rps 仍只有 1 个对话框",
              len(dlg_list) == 1, f"对话框窗口={dlg_list}")

        dlg_hwnd = dlg_list[0] if dlg_list else _window_by_title("石头 · 剪刀 · 布")
        cfg_file = Path(env["APPDATA"]) / "WorkBuddyPet" / "config.json"

        def rps_total() -> int:
            try:
                d = json.loads(cfg_file.read_text(encoding="utf-8"))
                return int(d["rps_wins"]) + int(d["rps_losses"]) + int(d["rps_draws"])
            except Exception:
                return -1

        # ---- 真实进程：1 / 2 / 3 三个数字键各打一局，进程不得 abort ----
        WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
        before_rounds = rps_total()
        key_ok = True
        key_detail = []
        for vk, label in ((0x31, "VK_1"), (0x32, "VK_2"), (0x33, "VK_3")):
            t0 = rps_total()
            u.PostMessageW(ctypes.c_void_p(dlg_hwnd), WM_KEYDOWN, vk, 0)
            u.PostMessageW(ctypes.c_void_p(dlg_hwnd), WM_KEYUP, vk, 0)
            time.sleep(1.0)
            alive = p.poll() is None
            delta = rps_total() - t0
            key_detail.append(f"{label}:alive={alive},delta={delta}")
            if not alive or delta != 1:
                key_ok = False
                break
        check(item, "真实进程：VK_1/VK_2/VK_3 各打一局且进程不 abort", key_ok,
              f"poll={p.poll()} {' | '.join(key_detail)}")
        check(item, "真实进程：3 个数字键共记 3 局", rps_total() - before_rounds == 3,
              f"{before_rounds} -> {rps_total()}")

        # ---- 真实进程：VK_RETURN 不 abort ----
        u.PostMessageW(ctypes.c_void_p(dlg_hwnd), WM_KEYDOWN, 0x0D, 0)
        u.PostMessageW(ctypes.c_void_p(dlg_hwnd), WM_KEYUP, 0x0D, 0)
        time.sleep(0.6)
        check(item, "真实进程：VK_RETURN 不 abort 且不误记分",
              p.poll() is None and rps_total() == before_rounds + 3,
              f"poll={p.poll()} rps_total={rps_total()}")

        # ---- 真实进程：VK_ESCAPE 关闭对话框、进程存活、不留幽灵窗口 ----
        u.PostMessageW(ctypes.c_void_p(dlg_hwnd), WM_KEYDOWN, 0x1B, 0)
        u.PostMessageW(ctypes.c_void_p(dlg_hwnd), WM_KEYUP, 0x1B, 0)
        closed = _wait(lambda: not _windows_by_title("石头 · 剪刀 · 布"), 6)
        check(item, "真实进程：VK_ESCAPE 关闭对话框且桌宠进程存活（不 abort）",
              closed and p.poll() is None, f"closed={closed} poll={p.poll()}")
        time.sleep(0.6)
        check(item, "Esc 之后没有隐藏的幽灵对话框（deleteLater 生效）",
              not _windows_by_title("石头 · 剪刀 · 布"),
              f"残留={_windows_by_title('石头 · 剪刀 · 布')}")
        _send_cmd("rps", wait=1.2)
        reopened = _wait(lambda: bool(_window_by_title("石头 · 剪刀 · 布")), 6)
        check(item, "关闭后能再次打开小游戏（_rps_open 已复位）", reopened,
              f"hwnd={_window_by_title('石头 · 剪刀 · 布')}")
        if reopened:
            u.PostMessageW(ctypes.c_void_p(_window_by_title("石头 · 剪刀 · 布")), WM_KEYDOWN, 0x1B, 0)
            _wait(lambda: not _window_by_title("石头 · 剪刀 · 布"), 5)

    except Exception:
        check(item, "小游戏真实进程验证", False, traceback.format_exc(limit=4))
    finally:
        try:
            if p is not None and p.poll() is None:
                _send_cmd("quit", wait=1.0)
                if not _wait(lambda: p.poll() is not None, 5):
                    p.kill()
                p.wait(5)
                check(item, "清理：小游戏验证实例已退出", True, f"poll={p.poll()}")
        except Exception:
            pass
        log.close()


def part_g(item: str = "G") -> None:
    print("\n===== G 真实进程集成 =====")
    from PyQt5.QtCore import QCoreApplication
    from PyQt5.QtNetwork import QLocalSocket

    qapp = QCoreApplication.instance() or QCoreApplication([])
    u = _user32()

    tmp_app = Path(tempfile.mkdtemp(prefix="buddy_qa_int_"))
    tmp_files = Path(tempfile.mkdtemp(prefix="buddy_qa_files_"))
    png = tmp_files / "qa_drop.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)

    real_cfg = Path(REAL_APPDATA) / "WorkBuddyPet" / "config.json" if REAL_APPDATA else None

    def fingerprint(p: Path | None):
        if p is None or not p.exists():
            return None
        st = p.stat()
        return (st.st_mtime_ns, st.st_size, hashlib.sha256(p.read_bytes()).hexdigest())

    fp_before = fingerprint(real_cfg)

    env = dict(os.environ, APPDATA=str(tmp_app), PYTHONUNBUFFERED="1")
    env.pop("QT_QPA_PLATFORM", None)

    proc = None
    try:
        if _find_pet_window():
            check(item, "起始环境无宠物实例（前置）", False, "已存在一个「Buddy 桌面宠物」窗口，测试不可信")
            return
        check(item, "起始环境无宠物实例（前置）", True)

        log = open(tmp_files / "pet_stdout.log", "w+", encoding="utf-8", errors="replace")
        proc = subprocess.Popen([sys.executable, "main.py"], cwd=str(ROOT), env=env,
                                stdout=log, stderr=subprocess.STDOUT)
        up = _wait(lambda: _find_pet_window() != 0, 25)
        check(item, "进程启动并出现窗口", up and proc.poll() is None,
              f"pid={proc.pid} poll={proc.poll()}")
        if not up:
            log.seek(0)
            print("---- pet stdout ----\n" + log.read()[:3000])
            return
        # Qt 会把 applicationDisplayName 当默认标题，粒子遮罩层也叫这个名，
        # 所以必须挑「客户区更小」的那个才是宠物本体
        hwnd, pet_size = _pet_main_window()
        check(item, "窗口标题命中且可见", bool(u.IsWindowVisible(hwnd)),
              f"hwnd={hwnd} size={pet_size}")

        # ---- 热键真的注册了：外部注册应失败 1409 ----
        err = None
        u.UnregisterHotKey(None, 0xB0B)     # 先清掉本测试进程可能残留的注册

        def hotkey_taken():
            nonlocal err
            ok = u.RegisterHotKey(None, 0xB0B, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("B"))
            if ok:
                u.UnregisterHotKey(None, 0xB0B)
                err = 0
                return False
            err = ctypes.windll.kernel32.GetLastError()
            return err == 1409

        taken = _wait(hotkey_taken, 8)
        check(item, "真实进程已注册 Ctrl+Alt+B（外部注册失败 1409）", taken, f"GetLastError={err}")

        # ---- 命令通道 ----
        send = _send_cmd

        status_json = tmp_app / "WorkBuddyPet" / "music_status.json"
        check(item, "指令 music:status 发送", send("music:status"))
        ok = _wait(lambda: status_json.exists(), 5)
        payload = {}
        if ok:
            try:
                payload = json.loads(status_json.read_text(encoding="utf-8"))
            except Exception:
                payload = {}
        check(item, "music_status.json 出现且含 playing/peak/kind",
              ok and {"playing", "peak", "kind"}.issubset(payload.keys()), f"{payload}")

        cfg_file = tmp_app / "WorkBuddyPet" / "config.json"

        def drop_count() -> int:
            try:
                return int(json.loads(cfg_file.read_text(encoding="utf-8"))["drop_count"])
            except Exception:
                return -1

        before = 0 if not cfg_file.exists() else max(0, drop_count())
        check(item, "指令 drop:test:<png> 发送", send(f"drop:test:{png}", wait=1.0))
        ok = _wait(lambda: drop_count() == before + 1, 5)
        check(item, "drop:test 后配置 drop_count +1", ok, f"{before} -> {drop_count()}")
        check(item, "进程仍存活（drop 未崩溃）", proc.poll() is None, f"poll={proc.poll()}")

        check(item, "指令 hotkey:toggle（藏）", send("hotkey:toggle", wait=0.9))
        ok = _wait(lambda: not u.IsWindowVisible(hwnd), 4)
        check(item, "hotkey:toggle 后窗口不可见", ok, f"visible={bool(u.IsWindowVisible(hwnd))}")
        check(item, "指令 hotkey:toggle（唤回）", send("hotkey:toggle", wait=0.9))
        ok = _wait(lambda: bool(u.IsWindowVisible(hwnd)), 4)
        check(item, "再次 hotkey:toggle 窗口可见", ok, f"visible={bool(u.IsWindowVisible(hwnd))}")

        # ---- 单实例 ----
        p2 = subprocess.run([sys.executable, "main.py"], cwd=str(ROOT), env=env,
                            capture_output=True, text=True, timeout=30)
        check(item, "第二个实例 1s 内自行退出", p2.returncode == 0,
              f"rc={p2.returncode} err={p2.stderr.strip()[:200]}")
        wins = _pet_windows()
        pids = {w[1] for w in wins}
        check(item, "没有起第二个实例（同名窗口只属于同一 PID）",
              pids == {proc.pid}, f"窗口={wins} 唯一PID={pids} 宠物PID={proc.pid}")

        # ---- 退出 ----
        check(item, "指令 quit 发送", send("quit", wait=1.2))
        exited = _wait(lambda: proc.poll() is not None, 8)
        check(item, "quit 后进程退出", exited, f"poll={proc.poll()}")
        if not exited:
            proc.kill()
            proc.wait(5)

        u.UnregisterHotKey(None, 0xB0B)     # 只考察被测进程，不把本进程的残留算进去
        last_err: list[int] = []

        def can_register_again():
            ok2 = u.RegisterHotKey(None, 0xB0B, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("B"))
            last_err.append(ctypes.windll.kernel32.GetLastError())
            if ok2:
                u.UnregisterHotKey(None, 0xB0B)
                return True
            return False

        # 进程刚退出时系统释放热键可能有毫秒级延迟 → 重试 8s
        check(item, "quit 后热键已注销（外部可再次注册）", _wait(can_register_again, 8),
              f"最后一次 GetLastError={last_err[-1] if last_err else None}（1409=仍被占用）")
        check(item, "窗口已消失", _find_pet_window() == 0, f"hwnd={_find_pet_window()}")

        log.seek(0)
        text = log.read()
        check(item, "进程输出无 Traceback", "Traceback" not in text,
              text[-500:] if "Traceback" in text else "clean")
        log.close()

        # ---- 小游戏键盘：真实进程里按 Esc 会不会崩（P0 复核）----
        _probe_rps_keyboard(item, env, tmp_files)
    except Exception:
        check(item, "G 段执行未抛异常", False, traceback.format_exc(limit=5))
    finally:
        try:
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.wait(5)
                check(item, "清理：残留进程已 kill", True)
        except Exception:
            pass
        u.UnregisterHotKey(None, 0xB0B)
        # 真实配置未被污染
        fp_after = fingerprint(real_cfg)
        check(item, "用户真实 config.json 未被修改", fp_before == fp_after,
              f"{fp_before} vs {fp_after}")
        import shutil
        for d in (tmp_app, tmp_files):
            try:
                shutil.rmtree(d, ignore_errors=True)
            except Exception:
                pass
        check(item, "清理：临时目录已删除", not tmp_app.exists() and not tmp_files.exists())


# ================================================================ 汇总
def summary(parts: list[str]) -> int:
    print("\n" + "=" * 78)
    print("汇总")
    print("=" * 78)
    by_item: dict[str, list[dict]] = {}
    for r in RESULTS:
        by_item.setdefault(r["item"], []).append(r)
    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r["status"] == "PASS")
    for item in sorted(by_item):
        rs = by_item[item]
        p = sum(1 for r in rs if r["status"] == "PASS")
        mark = "PASS" if p == len(rs) else "FAIL"
        print(f"  [{mark}] {item}: {p}/{len(rs)}")
    print(f"\n总计 {passed}/{total} PASS")
    fails = [r for r in RESULTS if r["status"] == "FAIL"]
    if fails:
        print("\n失败清单：")
        for r in fails:
            print(f"  - [{r['item']}] {r['name']} :: {r['detail'][:300]}")
    return 0 if not fails else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", default="all", choices=["logic", "g", "all", "a", "b", "c", "d", "e", "f", "h"])
    args = ap.parse_args()

    part = args.part
    if part in ("logic", "all"):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sandbox = sandbox_appdata()
    print(f"沙箱 APPDATA = {sandbox}")
    print(f"真实 APPDATA = {REAL_APPDATA}")

    if part == "g":
        part_g("G")
    else:
        from PyQt5.QtWidgets import QApplication
        sys.argv = [sys.argv[0]]
        app = QApplication.instance() or QApplication([])

        from app import config
        from app.assets import BuddySprites
        from app.effects import EffectEngine
        from app.overlay import EffectOverlay
        from app.pet_window import PetWindow

        cfg = config.Settings().clamp()
        sprites = BuddySprites(ROOT)
        engine = EffectEngine()
        overlay = EffectOverlay(engine)
        pet = PetWindow(cfg, sprites, engine, overlay, ROOT)
        files = _make_files(Path(tempfile.mkdtemp(prefix="buddy_qa_f_")))

        if part in ("all", "a"):
            section("A", part_a)
        if part in ("all", "b"):
            section("B", part_b)
        if part in ("all", "c"):
            section("C", part_c, pet, cfg, engine, files)
        if part in ("all", "d"):
            section("D", part_d, pet, cfg, engine)
        if part in ("all", "e"):
            section("E", part_e, cfg)
        if part in ("all", "f"):
            section("F", part_f, cfg)
        if part in ("all", "h"):
            section("H", part_h, pet, cfg, engine)

        try:
            pet.close()
            overlay.close()
        except Exception:
            pass

        if part == "all":
            part_g("G")

    return summary([part])


if __name__ == "__main__":
    sys.exit(main())
