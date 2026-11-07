"""钉钉扫码代登录：每个会话一个线程，独占一个无头 Chromium。"""
from __future__ import annotations

import base64
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field

from ..config import get_settings

log = logging.getLogger(__name__)

QR_FRAME = "iframe[src*='login.dingtalk.com']"
QR_TTL_S = 110


@dataclass
class LoginSession:
    id: str
    status: str = "starting"  # starting | pending | success | expired | error | cancelled
    qr_png: bytes | None = None
    qr_at: float = 0
    cookie: str | None = None
    error: str = ""
    created_at: float = field(default_factory=time.time)
    refresh_flag: threading.Event = field(default_factory=threading.Event)
    cancel_flag: threading.Event = field(default_factory=threading.Event)

    def public(self) -> dict:
        return {"id": self.id, "status": self.status, "error": self.error,
                "qr": base64.b64encode(self.qr_png).decode() if self.qr_png else None,
                "qr_expires_in": max(0, int(QR_TTL_S - (time.time() - self.qr_at))) if self.qr_at else None}


class LoginManager:
    def __init__(self):
        self.sessions: dict[str, LoginSession] = {}
        self.lock = threading.Lock()

    def _active(self) -> int:
        return sum(1 for s in self.sessions.values() if s.status in {"starting", "pending"})

    def start(self) -> LoginSession:
        with self.lock:
            self._gc()
            if self._active() >= get_settings().max_login_sessions:
                raise RuntimeError("同时进行的扫码登录过多，请稍后再试")
            sess = LoginSession(id=secrets.token_urlsafe(16))
            self.sessions[sess.id] = sess
        threading.Thread(target=self._run, args=(sess,), daemon=True, name=f"login-{sess.id[:6]}").start()
        return sess

    def get(self, sid: str) -> LoginSession | None:
        return self.sessions.get(sid)

    def take_cookie(self, sid: str) -> str | None:
        """取出 Cookie 后立即从内存移除，只能使用一次。"""
        with self.lock:
            sess = self.sessions.get(sid)
            if not sess or sess.status != "success" or not sess.cookie:
                return None
            cookie, sess.cookie = sess.cookie, None
            sess.status = "used"
            return cookie

    def _gc(self) -> None:
        now = time.time()
        for k in [k for k, s in self.sessions.items() if now - s.created_at > 3600]:
            self.sessions.pop(k, None)

    def _open_qr(self, page) -> bytes:
        s = get_settings()
        page.goto(f"{s.ctp_base_url}/fe-ctpsystem/", wait_until="networkidle", timeout=30000)
        page.get_by_text("钉钉扫码").first.click()
        page.frame_locator(QR_FRAME).locator("canvas").first.wait_for(state="attached", timeout=20000)
        page.wait_for_timeout(1500)
        return page.locator(QR_FRAME).screenshot()

    def _run(self, sess: LoginSession) -> None:
        from playwright.sync_api import sync_playwright

        deadline = time.time() + get_settings().login_timeout_s
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN")
                    page = ctx.new_page()
                    sess.qr_png, sess.qr_at, sess.status = self._open_qr(page), time.time(), "pending"
                    while time.time() < deadline:
                        if sess.cancel_flag.is_set():
                            sess.status = "cancelled"
                            return
                        token = next((c["value"] for c in ctx.cookies() if c["name"] == "ZYBIPSCAS"), None)
                        if token:
                            sess.cookie, sess.status, sess.qr_png = f"ZYBIPSCAS={token}", "success", None
                            return
                        if sess.refresh_flag.is_set():
                            sess.refresh_flag.clear()
                            sess.qr_png, sess.qr_at = self._open_qr(page), time.time()
                        page.wait_for_timeout(1000)
                    sess.status, sess.qr_png = "expired", None
                finally:
                    browser.close()
        except Exception as e:  # noqa: BLE001
            log.exception("扫码登录失败")
            sess.status, sess.error = "error", f"扫码登录不可用（{type(e).__name__}），请改用粘贴 Cookie"


login_manager = LoginManager()
