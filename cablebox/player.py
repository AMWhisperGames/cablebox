"""libmpv drawn inside a Qt OpenGL widget.

Rendering into the Qt window (instead of embedding a native mpv window) works
on Windows, X11, and Raspberry Pi OS Wayland.
"""

from __future__ import annotations

import logging

from cablebox.bootstrap import prepare_mpv_search_path

prepare_mpv_search_path()

from PySide6.QtCore import QByteArray, Qt, Signal
from PySide6.QtGui import QOpenGLContext
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from mpv import MPV, MpvGlGetProcAddressFn, MpvRenderContext

log = logging.getLogger(__name__)


def _gl_proc_address(_ctx, name) -> int:
    try:
        if not name:
            return 0
        if isinstance(name, str):
            name = name.encode("utf-8")
        context = QOpenGLContext.currentContext()
        if context is None:
            return 0
        address = context.getProcAddress(QByteArray(name))
        return int(address) if address else 0
    except Exception:
        return 0


class MpvWidget(QOpenGLWidget):
    """Plays one file at a broadcast offset. The widget does not take keyboard focus."""

    frame_ready = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._ctx: MpvRenderContext | None = None
        self._ready = False
        self._pending: tuple[str, float] | None = None
        self._proc_address = MpvGlGetProcAddressFn(_gl_proc_address)
        self._shut_down = False
        self.frame_ready.connect(self.update)
        self.setUpdateBehavior(QOpenGLWidget.UpdateBehavior.PartialUpdate)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mpv = MPV(
            vo="libmpv",
            hwdec="auto-safe",
            keep_open=True,
            idle=True,
            osc=False,
            ytdl=False,
            input_default_bindings=False,
            input_vo_keyboard=False,
            load_scripts=False,
            log_handler=_log_mpv,
            loglevel="warn",
        )
        self.mpv.volume = 70
        self.setMinimumSize(320, 180)

    def initializeGL(self) -> None:
        if self._shut_down:
            return
        try:
            self._ctx = MpvRenderContext(
                self.mpv,
                "opengl",
                opengl_init_params={"get_proc_address": self._proc_address},
            )
            self._ctx.update_cb = self._on_render_update
        except Exception:
            log.exception("could not create the mpv OpenGL context")
            return
        self._ready = True
        if self._pending is not None:
            path, offset = self._pending
            self._start(path, offset)

    def paintGL(self) -> None:
        if self._ctx is None:
            return
        ratio = self.devicePixelRatioF()
        width = max(1, int(self.width() * ratio))
        height = max(1, int(self.height() * ratio))
        self._ctx.render(
            flip_y=True,
            block_for_target_time=False,
            opengl_fbo={
                "fbo": int(self.defaultFramebufferObject()),
                "w": width,
                "h": height,
            },
        )
        self._ctx.report_swap()

    def play(self, path: str, offset: float) -> None:
        offset = max(0.0, float(offset))
        self._pending = (path, offset)
        if self._ready:
            self._start(path, offset)

    def stop(self) -> None:
        self._pending = None
        if self._shut_down:
            return
        try:
            self.mpv.command("stop")
        except Exception:
            log.exception("mpv stop failed")

    def seek(self, offset: float) -> None:
        if self._shut_down:
            return
        try:
            self.mpv.time_pos = max(0.0, float(offset))
        except Exception:
            log.warning("mpv seek to %.3f failed", offset, exc_info=True)

    def playback_time(self) -> float | None:
        if self._shut_down:
            return None
        try:
            value = self.mpv.time_pos
        except Exception:
            log.warning("could not read mpv playback time", exc_info=True)
            return None
        if value is None:
            return None
        return float(value)

    def set_paused(self, paused: bool) -> None:
        if self._shut_down:
            return
        try:
            self.mpv.pause = bool(paused)
        except Exception:
            log.warning("could not set pause=%s", paused, exc_info=True)

    def set_volume(self, level: int) -> int:
        level = max(0, min(100, int(level)))
        self.mpv.volume = level
        return level

    def volume(self) -> int:
        try:
            value = self.mpv.volume
        except Exception:
            return 70
        if value is None:
            return 70
        return int(round(float(value)))

    def shutdown(self) -> None:
        if self._shut_down:
            return
        self._shut_down = True
        self._ready = False
        context = self._ctx
        self._ctx = None
        if context is not None:
            try:
                self.makeCurrent()
                context.update_cb = None
                context.free()
                self.doneCurrent()
            except Exception:
                log.exception("mpv render shutdown failed")
        try:
            self.mpv.terminate()
        except Exception:
            log.exception("mpv terminate failed")

    def _start(self, path: str, offset: float) -> None:
        log.info("load %s at %.3f seconds", path, offset)
        self.mpv.loadfile(path, "replace", start=f"{offset:.3f}")
        self.mpv.pause = False

    def _on_render_update(self) -> None:
        if not self._shut_down:
            self.frame_ready.emit()


def _log_mpv(loglevel, component, message) -> None:
    text = str(message).rstrip()
    if loglevel in ("error", "fatal"):
        log.error("mpv %s: %s", component, text)
    elif loglevel == "warn":
        log.warning("mpv %s: %s", component, text)
