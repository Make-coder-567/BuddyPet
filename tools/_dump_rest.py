"""离屏渲染默认皮肤在五种姿态、视线居中时的画面，供人工核对「现有效果未破坏」。"""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.assets import BuddySprites  # noqa: E402
from app.config import Settings  # noqa: E402
from app.effects import EffectEngine  # noqa: E402
from app.pet_window import PetWindow  # noqa: E402


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    QGuiApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    cfg = Settings()
    cfg.gaze_follow = True
    cfg.edge_cling = True
    pet = PetWindow(cfg, BuddySprites(ROOT), EffectEngine(), None, ROOT)
    pet.show()
    app.processEvents()

    out = ROOT / "docs" / "img"
    out.mkdir(parents=True, exist_ok=True)
    for side in (None, "left", "right", "top", "bottom"):
        pet._edge = side
        if side is not None:
            pet._snap_edge_position(silent=True)
        app.processEvents()
        pet._pose.dy = 0.0
        pet._pose.sx = 1.0
        pet._pose.sy = 0.945 if side == "bottom" else 1.0
        pet.gaze.reset()  # 视线居中
        pet.clearMask()
        img = pet.grab().toImage() if not pet.grab().isNull() else None
        if img is None or img.isNull():
            # offscreen 下 grab 可能空，改用 render
            img = __import__("PyQt5.QtGui", fromlist=["QImage"]).QImage(
                pet.size(), __import__("PyQt5.QtGui", fromlist=["QImage"]).QImage.Format_ARGB32_Premultiplied
            )
            img.fill(Qt.transparent)
            pet.render(img)
        name = side or "center"
        img.save(str(out / f"rest_{name}.png"))
        print(f"已保存 rest_{name}.png  size={img.width()}x{img.height()}")
    pet.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
