"""云端客户端：``QNetworkAccessManager`` 异步 GET + 超时 + 鉴权头 + 错误码归一。

线程模型（架构 D3 / §5）：**主线程异步，不用 QThread / QThreadPool**。
``reply.finished`` 在主线程事件循环里触发，调用方可以直接构造 ``QPixmap``。

兼容性（§5.2）：不使用 ``QNetworkRequest.setTransferTimeout``（5.15 才引入），
改用 ``QTimer`` + ``abort()``，兼容 Qt 5.9+。

本模块**绝不把异常抛给调用方**：所有失败都通过
``finished(ok=False, payload=None, reason=<CODE>)`` 回来，
reason 取值：``NOT_CONFIGURED`` / ``TIMEOUT`` / ``NETWORK`` / ``HTTP_<code>`` / ``PARSE``。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from PyQt5.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from ..config import Settings
from .cache import credential_path

__all__ = [
    "CONNECT_TIMEOUT_MS",
    "READ_TIMEOUT_MS",
    "AUTH_HEADER_TEMPLATE",
    "R_NOT_CONFIGURED",
    "R_TIMEOUT",
    "R_NETWORK",
    "R_PARSE",
    "mask_token",
    "resolve_token",
    "CloudClient",
]

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_MS = 3000
READ_TIMEOUT_MS = 5000

#: Q2 未定：换 Cookie / 签名只改这一行
AUTH_HEADER_TEMPLATE = "Bearer {token}"

#: 归一后的错误码
R_NOT_CONFIGURED = "NOT_CONFIGURED"
R_TIMEOUT = "TIMEOUT"
R_NETWORK = "NETWORK"
R_PARSE = "PARSE"

#: 允许的 URL scheme（与 config.clamp 对齐）
_ALLOWED_SCHEMES = ("http://", "https://")


def mask_token(token: str) -> str:
    """日志脱敏：只保留末 4 位（K2：不打印完整 token）。"""
    if not token:
        return "<none>"
    tail = token[-4:] if len(token) > 4 else token
    return f"***{tail}"


def resolve_token(auth_ref: str) -> str:
    """按 §12-Q2 的顺序解析 token。

    ① 环境变量（``auth_ref`` 本身作为变量名）；
    ② ``%APPDATA%/WorkBuddyPet/credentials.json`` 的 ``{auth_ref: token}``。

    Returns:
        token 字符串；解析不到返回 ``""``（不抛）。
    """
    ref = (auth_ref or "").strip()
    if not ref:
        return ""
    value = os.environ.get(ref, "").strip()
    if value:
        return value
    path: Path = credential_path()
    try:
        if not path.is_file():
            return ""
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ""
    if not isinstance(data, dict):
        return ""
    token = data.get(ref)
    return token.strip() if isinstance(token, str) else ""


class CloudClient(QObject):
    """一次性的图鉴拉取客户端（可复用：每次 :meth:`fetch` 只跑一个请求）。

    Signals:
        finished (bool, object, str): ``(ok, payload, reason)``。
            ``ok=True`` 时 ``payload`` 是已解析的 JSON（``dict``/``list``）；
            ``ok=False`` 时 ``payload=None``，``reason`` 为归一错误码。
    """

    finished = pyqtSignal(bool, object, str)

    def __init__(
        self,
        cfg: Settings,
        parent: QObject | None = None,
        nam: QNetworkAccessManager | None = None,
    ) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self._nam = nam if nam is not None else QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None
        self._connect_timer = QTimer(self)
        self._connect_timer.setSingleShot(True)
        self._connect_timer.timeout.connect(self._on_connect_timeout)
        self._read_timer = QTimer(self)
        self._read_timer.setSingleShot(True)
        self._read_timer.timeout.connect(self._on_read_timeout)
        self._got_bytes = False
        self._timed_out = ""
        self._done = True
        #: 本次请求是否已经发过 finished（保证一次 fetch 只收口一次）
        self._emitted = True
        #: 供 provider 区分连接超时 / 读取超时（reason 仍是 "TIMEOUT"）
        self.last_timeout: str = ""

    # ---------------- 状态 ----------------
    @property
    def is_busy(self) -> bool:
        return not self._done

    def _base_url(self) -> str:
        return (getattr(self.cfg, "gallery_base_url", "") or "").strip()

    # ---------------- 入口 ----------------
    def fetch(self) -> None:
        """发起一次 GET。

        ``gallery_base_url`` 为空（或不是 http(s)）时**不发任何网络请求**，
        同步回调 ``finished(False, None, "NOT_CONFIGURED")`` 后返回。
        """
        base_url = self._base_url()
        if not base_url or not base_url.startswith(_ALLOWED_SCHEMES):
            self._done = True
            self._emitted = False
            logger.info("gallery cloud: not configured, skip network")
            self._finish(False, None, R_NOT_CONFIGURED)
            return

        if not self._done:  # 已有请求在飞，abort 旧的，保证单飞
            self.abort()

        self._done = False
        self._emitted = False
        self._got_bytes = False
        self._timed_out = ""
        self.last_timeout = ""

        token = resolve_token(getattr(self.cfg, "gallery_auth_ref", ""))
        request = QNetworkRequest(QUrl(base_url))
        request.setHeader(QNetworkRequest.UserAgentHeader, "WorkBuddyPet/Gallery")
        request.setRawHeader(b"Accept", b"application/json")
        if token:
            request.setRawHeader(b"Authorization", AUTH_HEADER_TEMPLATE.format(token=token).encode("utf-8"))
            logger.info("gallery cloud: GET %s (token=%s)", base_url, mask_token(token))
        else:
            logger.info("gallery cloud: GET %s (no token)", base_url)

        # P7：必须是实例属性，否则 reply 被 GC，finished 不触发
        self._reply = self._nam.get(request)
        self._reply.downloadProgress.connect(self._on_progress)
        self._reply.finished.connect(self._on_finished)
        self._connect_timer.start(CONNECT_TIMEOUT_MS)

    def abort(self) -> bool:
        """中止进行中的请求（幂等）。"""
        self._connect_timer.stop()
        self._read_timer.stop()
        reply = self._reply
        if reply is None:
            self._done = True
            return False
        try:
            reply.abort()
        except Exception:  # noqa: BLE001
            pass
        return True

    # ---------------- 超时 ----------------
    def _on_connect_timeout(self) -> None:
        if self._got_bytes or self._reply is None:
            return
        self._timed_out = "connect_timeout"
        self.last_timeout = "connect_timeout"
        try:
            self._reply.abort()  # abort 也会发 finished，统一在那里收口
        except Exception:  # noqa: BLE001
            self._finish(False, None, R_TIMEOUT)

    def _on_read_timeout(self) -> None:
        if self._reply is None:
            return
        self._timed_out = "read_timeout"
        self.last_timeout = "read_timeout"
        try:
            self._reply.abort()
        except Exception:  # noqa: BLE001
            self._finish(False, None, R_TIMEOUT)

    def _on_progress(self, received: int, total: int) -> None:
        # 首次收到字节 → 连接已建立，切到 5s 读超时
        if received > 0 and not self._got_bytes:
            self._got_bytes = True
            self._connect_timer.stop()
            if not self._timed_out:
                self._read_timer.start(READ_TIMEOUT_MS)

    # ---------------- 收口 ----------------
    def _on_finished(self) -> None:
        self._connect_timer.stop()
        self._read_timer.stop()
        reply = self._reply
        if reply is None:
            self._finish(False, None, R_NETWORK)
            return

        status = 0
        body = b""
        error = QNetworkReply.NoError
        try:
            status = int(reply.attribute(QNetworkRequest.HttpStatusCodeAttribute) or 0)
            error = reply.error()
            body = bytes(reply.readAll())
        except Exception:  # noqa: BLE001
            body = b""
        finally:
            try:
                reply.deleteLater()
            except Exception:  # noqa: BLE001
                pass
            self._reply = None

        if self._timed_out:
            self._finish(False, None, R_TIMEOUT)
            return
        if status >= 400:
            self._finish(False, None, f"HTTP_{status}")
            return
        if error != QNetworkReply.NoError and not body:
            self._finish(False, None, R_NETWORK)
            return
        if not body.strip():
            self._finish(False, None, R_NETWORK if error != QNetworkReply.NoError else R_PARSE)
            return
        try:
            payload = json.loads(body.decode("utf-8", errors="replace"))
        except Exception:  # noqa: BLE001
            self._finish(False, None, R_PARSE)
            return
        self._finish(True, payload, "")

    def _finish(self, ok: bool, payload: object, reason: str) -> None:
        """唯一的信号出口（同一次 fetch 只发一次）。"""
        if self._emitted:
            return
        self._emitted = True
        self._done = True
        self._connect_timer.stop()
        self._read_timer.stop()
        logger.info("gallery cloud: finished ok=%s reason=%s", ok, reason or "-")
        self.finished.emit(ok, payload, reason or "")
