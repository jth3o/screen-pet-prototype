"""
Desktop screen pet: a small always-on-top transparent window with a pixel sprite
that walks along the bottom of the primary screen. Not a web view.
"""

from __future__ import annotations

import sys

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PyQt6.QtGui import (
    QColor,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QShortcut,
)
from PyQt6.QtWidgets import QApplication, QWidget


# 8x8 design: 0 = skip; 1 = body; 2 = eyes
SPRITE: list[list[int]] = [
    [0, 0, 0, 1, 1, 0, 0, 0],
    [0, 0, 1, 1, 1, 1, 0, 0],
    [0, 1, 1, 2, 2, 1, 1, 0],
    [0, 1, 1, 2, 2, 1, 1, 0],
    [0, 0, 1, 1, 1, 1, 0, 0],
    [0, 1, 0, 1, 1, 0, 1, 0],
    [0, 1, 0, 0, 0, 0, 1, 0],
    [0, 1, 0, 0, 0, 0, 1, 0],
]

SPRITE_ALT: list[list[int]] = [
    [0, 0, 0, 1, 1, 0, 0, 0],
    [0, 0, 1, 1, 1, 1, 0, 0],
    [0, 1, 1, 2, 2, 1, 1, 0],
    [0, 1, 1, 2, 2, 1, 1, 0],
    [0, 0, 1, 1, 1, 1, 0, 0],
    [0, 0, 1, 0, 0, 1, 0, 0],
    [0, 1, 0, 1, 0, 0, 1, 0],
    [0, 1, 0, 0, 1, 0, 1, 0],
]

CELL = 5
MARGIN = 2

COLOR_BODY = QColor(80, 180, 100, 255)
COLOR_EYES = QColor(20, 40, 20, 255)
COLOR_OUTLINE = QColor(30, 90, 40, 255)

FRAME_MS = 33
STEP_PX = 1


def _primary_available_rect(app: QApplication) -> QRect:
    s = app.primaryScreen()
    if s is not None:
        return s.availableGeometry()
    for x in app.screens():
        return x.availableGeometry()
    return QRect(0, 0, 400, 300)


class PetView(QWidget):
    def __init__(self) -> None:
        super().__init__(None)
        self._paused = False
        self._dragging = False
        self._drag_start_global: QPoint | None = None
        self._window_start = QPoint(0, 0)
        self._frame = 0

        w = 8 * CELL + 2 * MARGIN
        h = 8 * CELL + 2 * MARGIN
        self.setFixedSize(QSize(w, h))
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)

        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setWindowFlags(flags)

        for seq, fn in ((QKeySequence("Esc"), self.close), (QKeySequence("Space"), self._toggle)):
            s = QShortcut(seq, self)
            s.setContext(Qt.ShortcutContext.ApplicationShortcut)
            s.activated.connect(fn)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_step)
        self._timer.setInterval(FRAME_MS)
        self._timer.start()

    @staticmethod
    def _screen_for(p: "PetView"):
        app = QApplication.instance()
        s = p.screen() if p.isVisible() else None
        if s is not None:
            return s
        if app and app.primaryScreen() is not None:
            return app.primaryScreen()
        for x in (app.screens() if app else ()):
            return x
        return p.screen()

    @staticmethod
    def _geom_for(s) -> QRect:
        if s is None:
            a = QApplication.instance()
            return _primary_available_rect(a) if a else QRect(0, 0, 400, 300)
        return s.availableGeometry()

    def _toggle(self) -> None:
        self._paused = not self._paused

    def _on_step(self) -> None:
        if self._paused or self._dragging:
            return
        s = self._screen_for(self)
        g = self._geom_for(s)
        x = self.x() + STEP_PX
        y = self.y()
        if x + self.width() > g.right():
            x = g.left() + 4
        self.move(x, y)
        self._frame = 1 - self._frame
        self.update()

    def paintEvent(self, e: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(self.rect(), QColor(0, 0, 0, 0))
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        grid = SPRITE_ALT if self._frame & 1 else SPRITE
        for row in range(8):
            for col in range(8):
                c = grid[row][col]
                if c == 0:
                    continue
                cl = COLOR_EYES if c == 2 else COLOR_BODY
                r = QRect(MARGIN + col * CELL, MARGIN + row * CELL, CELL, CELL)
                p.fillRect(r, cl)
                p.setPen(COLOR_OUTLINE)
                p.drawRect(r)
        p.end()

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.close()
        elif e.key() == Qt.Key.Key_Space:
            self._toggle()
        else:
            super().keyPressEvent(e)

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_start_global = e.globalPosition().toPoint()
            self._window_start = self.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._dragging and (e.buttons() & Qt.MouseButton.LeftButton) and self._drag_start_global:
            delta = e.globalPosition().toPoint() - self._drag_start_global
            self.move(self._window_start + delta)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
        super().mouseReleaseEvent(e)


def main() -> int:
    app = QApplication(sys.argv)
    g = _primary_available_rect(app)
    app.setQuitOnLastWindowClosed(True)
    w = PetView()
    w.move(g.left() + 8, g.bottom() - w.height() - 4)
    w.show()
    w.raise_()
    w.activateWindow()
    w.setFocus()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
