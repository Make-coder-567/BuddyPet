"""配色与字体：橙色工装 + 青色眼睛的品牌色板。"""
from __future__ import annotations

from PyQt5.QtGui import QColor, QFont

ORANGE = "#FF7A3D"
ORANGE_DEEP = "#E85E22"
ORANGE_SOFT = "rgba(255, 122, 61, 0.16)"
TEAL = "#26DFBB"
TEAL_DEEP = "#12B79A"

INK = "#16181D"
CARD = "#1C1E24"
CARD_HI = "#262932"
LINE = "rgba(255, 255, 255, 0.08)"
TEXT = "#F3F4F6"
MUTED = "#9CA1AB"

Q_ORANGE = QColor(ORANGE)
Q_TEAL = QColor(TEAL)
Q_INK = QColor(INK)

FONT_FAMILY = "Microsoft YaHei UI"


def font(size: int = 10, bold: bool = False, spacing: float = 0.0) -> QFont:
    f = QFont(FONT_FAMILY, size)
    f.setBold(bold)
    f.setStyleStrategy(QFont.PreferAntialias)
    if spacing:
        f.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
    return f


MENU_QSS = f"""
QMenu {{
    background-color: {CARD};
    color: {TEXT};
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 12px;
    padding: 7px;
    font-family: "{FONT_FAMILY}";
    font-size: 9.5pt;
}}
QMenu::item {{
    padding: 7px 18px 7px 10px;
    border-radius: 8px;
    margin: 1px 3px;
    min-width: 138px;
}}
QMenu::item:selected {{
    background-color: rgba(255, 122, 61, 0.18);
    color: {ORANGE};
}}
QMenu::item:disabled {{
    color: rgba(243,244,246,0.32);
}}
QMenu::separator {{
    height: 1px;
    background: rgba(255,255,255,0.07);
    margin: 6px 12px;
}}
QMenu::icon {{
    padding-left: 12px;
}}
QMenu::right-arrow {{
    width: 9px;
    height: 9px;
    image: none;
    margin-right: 12px;
}}
"""

DIALOG_QSS = f"""
QDialog {{
    background: transparent;
}}
QFrame#Card {{
    background-color: {CARD};
    border: 1px solid rgba(255,255,255,0.09);
    border-radius: 16px;
}}
QLabel {{
    color: {TEXT};
    font-family: "{FONT_FAMILY}";
    font-size: 9.5pt;
    background: transparent;
}}
QLabel#Title {{
    font-size: 12pt;
    font-weight: 600;
}}
QLabel#Sub {{
    color: {MUTED};
    font-size: 8.5pt;
}}
QLabel#Section {{
    color: {ORANGE};
    font-size: 8.5pt;
    font-weight: 600;
    letter-spacing: 1px;
}}
QLabel#Value {{
    color: {MUTED};
    font-size: 8.5pt;
}}
QPushButton {{
    background-color: rgba(255,255,255,0.06);
    color: {TEXT};
    border: 1px solid rgba(255,255,255,0.09);
    border-radius: 9px;
    padding: 7px 16px;
    font-family: "{FONT_FAMILY}";
    font-size: 9pt;
}}
QPushButton:hover {{
    background-color: rgba(255,255,255,0.11);
}}
QPushButton:pressed {{
    background-color: rgba(255,255,255,0.05);
}}
QPushButton#Primary {{
    background-color: {ORANGE};
    border: none;
    color: #201006;
    font-weight: 600;
}}
QPushButton#Primary:hover {{
    background-color: #FF8C56;
}}
QPushButton#Ghost {{
    background: transparent;
    border: none;
    color: {MUTED};
    padding: 6px 10px;
}}
QPushButton#Ghost:hover {{
    color: {TEXT};
    background-color: rgba(255,255,255,0.06);
}}
/* 禁用态必须"看得出是禁用"，但不能糊成一块无法阅读的深色 ——
   图鉴的「应用」按钮在未修改时就是禁用态，截图自审时几乎读不出字。 */
QPushButton:disabled {{
    background-color: rgba(255,255,255,0.03);
    border-color: rgba(255,255,255,0.06);
    color: rgba(243,244,246,0.30);
}}
QPushButton#Primary:disabled {{
    background-color: rgba(255,122,61,0.26);
    border: none;
    color: rgba(255,255,255,0.45);
}}
QPushButton#Ghost:disabled {{
    background: transparent;
    border: none;
    color: rgba(243,244,246,0.24);
}}
QComboBox {{
    background-color: rgba(255,255,255,0.05);
    border: 1px solid rgba(255,255,255,0.09);
    border-radius: 9px;
    padding: 6px 10px;
    color: {TEXT};
    font-family: "{FONT_FAMILY}";
    font-size: 9pt;
    min-width: 150px;
}}
QComboBox:hover {{
    border-color: rgba(255,122,61,0.5);
}}
QComboBox::drop-down {{
    border: none;
    width: 18px;
}}
QComboBox::down-arrow {{
    image: none;
}}
QComboBox QAbstractItemView {{
    background-color: {CARD_HI};
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 8px;
    padding: 4px;
    color: {TEXT};
    outline: none;
    selection-background-color: rgba(255,122,61,0.22);
    selection-color: {ORANGE};
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: rgba(255,255,255,0.12);
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {ORANGE};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: #FFFFFF;
    width: 13px;
    height: 13px;
    margin: -5px 0;
    border-radius: 6.5px;
}}
QSlider::handle:horizontal:hover {{
    background: {ORANGE};
}}
"""
