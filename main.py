"""
Desktop screen pet: an always-on-top transparent window that stays hidden
most of the time and periodically runs a little scripted routine before
exiting off the right side of the screen.

Routines:
  - cross: enter from the left, run all the way across, exit right.
  - shake: enter from the left, stop in the middle, shake butt, exit right.
  - jump:  enter from the left, jump while running, exit right.

Tray menu (menu bar on macOS, system tray elsewhere):
  - Show now > pick a routine immediately (handy for testing).
  - Pause appearances (stops auto-scheduling; current routine still finishes).
  - Quit.
"""

from __future__ import annotations

import enum
import pathlib
import random
import sys
from typing import Callable, Generator, Optional

from PyQt6.QtCore import QElapsedTimer, QPoint, QRect, QSize, Qt, QTimer
from PyQt6.QtGui import (
    QAction,
    QColor,
    QIcon,
    QKeyEvent,
    QKeySequence,
    QPaintEvent,
    QPainter,
    QPixmap,
    QShortcut,
    QTransform,
)
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMenu,
    QPushButton,
    QSystemTrayIcon,
    QWidget,
)


# ---------- sprite source ----------
HERE = pathlib.Path(__file__).parent
SPRITES_DIR = HERE / "sprites"
PIXMAP_WALK_FILES = ("walk_0.png", "walk_1.png")
PIXMAP_SHAKE_FILES = ("shake_0.png", "shake_1.png")
SPRITE_TARGET_PX = 64
SPRITE_SOURCE_FACES_RIGHT = True  # flip direction only if your sheet faces left

# ---------- fallback programmatic sprite (used if PNGs are missing) ----------
SPRITE_WALK_A: list[list[int]] = [
    [0, 0, 0, 1, 1, 0, 0, 0],
    [0, 0, 1, 1, 1, 1, 0, 0],
    [0, 1, 1, 2, 2, 1, 1, 0],
    [0, 1, 1, 2, 2, 1, 1, 0],
    [0, 0, 1, 1, 1, 1, 0, 0],
    [0, 1, 0, 1, 1, 0, 1, 0],
    [0, 1, 0, 0, 0, 0, 1, 0],
    [0, 1, 0, 0, 0, 0, 1, 0],
]
SPRITE_WALK_B: list[list[int]] = [
    [0, 0, 0, 1, 1, 0, 0, 0],
    [0, 0, 1, 1, 1, 1, 0, 0],
    [0, 1, 1, 2, 2, 1, 1, 0],
    [0, 1, 1, 2, 2, 1, 1, 0],
    [0, 0, 1, 1, 1, 1, 0, 0],
    [0, 0, 1, 0, 0, 1, 0, 0],
    [0, 1, 0, 1, 0, 0, 1, 0],
    [0, 1, 0, 0, 1, 0, 1, 0],
]
SPRITE_W = 8
SPRITE_H = 8
CELL = 6
MARGIN = 8  # generous so the jump offset doesn't clip the sprite

COLOR_BODY = QColor(80, 180, 100, 255)
COLOR_EYES = QColor(20, 40, 20, 255)

# ---------- tick + timings ----------
TICK_MS = 16                    # ~60 Hz
WALK_FRAME_MS = 120             # walk cycle cadence
BLINK_INTERVAL_MS = 2800
BLINK_DUR_MS = 140
RUN_SPEED_PX_S = 180.0          # horizontal travel speed during routines

# Appearance scheduling (ms). First appearance is quick so you see it work.
FIRST_APPEARANCE_MIN_MS = 3_000
FIRST_APPEARANCE_MAX_MS = 8_000
APPEARANCE_MIN_MS = 45_000
APPEARANCE_MAX_MS = 120_000

# Routine tuning
SHAKE_DURATION_MS = 1400
SHAKE_FRAME_MS = 130             # cadence for the back-facing sway frame swap
JUMP_DURATION_MS = 700
JUMP_HEIGHT_PX = 60
EXIT_MARGIN_PX = 16             # how far past the right edge before we hide
BOTTOM_MARGIN_PX = 8            # gap between pet feet and screen bottom


class RoutineName(enum.Enum):
    CROSS = "cross"
    SHAKE = "shake"
    JUMP = "jump"

    @property
    def label(self) -> str:
        return {
            "cross": "Walk across",
            "shake": "Shake in the middle",
            "jump": "Jump while running",
        }[self.value]


def _load_pixmap_set(filenames: tuple[str, ...]) -> list[QPixmap]:
    """Load and uniformly scale a set of PNG frames. Returns [] if any file is
    missing or fails to decode — caller decides how to degrade."""
    out: list[QPixmap] = []
    for name in filenames:
        path = SPRITES_DIR / name
        if not path.exists():
            return []
        pm = QPixmap(str(path))
        if pm.isNull():
            sys.stderr.write(f"screen-pet: failed to load {path}\n")
            return []
        pm = pm.scaled(
            SPRITE_TARGET_PX,
            SPRITE_TARGET_PX,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        out.append(pm)
    return out


def _load_walk_pixmaps() -> list[QPixmap]:
    return _load_pixmap_set(PIXMAP_WALK_FILES)


def _load_shake_pixmaps() -> list[QPixmap]:
    return _load_pixmap_set(PIXMAP_SHAKE_FILES)


def _flip_pixmap(pm: QPixmap) -> QPixmap:
    return pm.transformed(
        QTransform().scale(-1, 1), Qt.TransformationMode.SmoothTransformation
    )


def _primary_available_rect(app: Optional[QApplication]) -> QRect:
    if app is None:
        return QRect(0, 0, 400, 300)
    s = app.primaryScreen()
    if s is not None:
        return s.availableGeometry()
    for x in app.screens():
        return x.availableGeometry()
    return QRect(0, 0, 400, 300)


# A routine is a generator-based coroutine. Each `yield` hands control back
# to the tick loop; `send(dt_ms)` resumes it with the frame's delta.
Routine = Generator[None, int, None]


class PetView(QWidget):
    def __init__(self) -> None:
        super().__init__(None)

        # ---- sprite loading ----
        self._walk_right: list[QPixmap] = _load_walk_pixmaps()
        self._walk_left: list[QPixmap] = [_flip_pixmap(pm) for pm in self._walk_right]
        self._using_pixmaps: bool = bool(self._walk_right)

        # Back-facing frames are used for the middle of the shake routine.
        # They are NEVER flipped — "facing away" is the same whether the pet
        # came in from the left or the right, so we render them as-is.
        self._shake_frames: list[QPixmap] = _load_shake_pixmaps()
        self._has_shake_frames: bool = bool(self._shake_frames)

        if self._using_pixmaps:
            fw = max(pm.width() for pm in self._walk_right)
            fh = max(pm.height() for pm in self._walk_right)
            if self._has_shake_frames:
                fw = max(fw, max(pm.width() for pm in self._shake_frames))
                fh = max(fh, max(pm.height() for pm in self._shake_frames))
            w = fw + 2 * MARGIN
            h = fh + 2 * MARGIN
        else:
            w = SPRITE_W * CELL + 2 * MARGIN
            h = SPRITE_H * CELL + 2 * MARGIN
        self.setFixedSize(QSize(w, h))

        # WA_MacAlwaysShowToolWindow prevents the macOS Tool-hides-on-focus-loss
        # behaviour that would otherwise make the pet invisible whenever you
        # switch away from Python.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint
        )

        # Esc quits. No more Space/drag — the pet is a visitor, not a resident.
        esc = QShortcut(QKeySequence("Esc"), self)
        esc.setContext(Qt.ShortcutContext.ApplicationShortcut)
        esc.activated.connect(self.close)

        # ---- state ----
        self._direction: int = 1
        self._walk_frame: int = 0
        self._walk_acc_ms: int = 0
        self._blink_acc_ms: int = 0
        self._blinking: bool = False

        # Floating-point "ground" position; the widget is moved to
        # (fx, fy + jump_offset) each tick.
        self._fx: float = 0.0
        self._fy: float = 0.0
        self._jump_offset_y: float = 0.0

        # Animation mode flips to "shake" for the held-in-place sway; the
        # paint method picks which frame set to draw based on this.
        self._anim_mode: str = "walk"  # "walk" | "shake"
        self._shake_frame: int = 0
        self._shake_acc_ms: int = 0

        self._routine: Optional[Routine] = None
        self._cooldown_ms: int = random.randint(
            FIRST_APPEARANCE_MIN_MS, FIRST_APPEARANCE_MAX_MS
        )
        self._appearances_paused: bool = False

        self._clock = QElapsedTimer()
        self._clock.start()
        self._last_tick_ms: int = self._clock.elapsed()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)
        self._timer.setInterval(TICK_MS)
        self._timer.start()

        self.hide()  # visitors aren't visible until they visit

    # ---------- public control (used by tray) ----------

    def set_appearances_paused(self, paused: bool) -> None:
        self._appearances_paused = paused

    def appearances_paused(self) -> bool:
        return self._appearances_paused

    def trigger(self, name: RoutineName) -> None:
        """Start a specific routine right now, cancelling any in-flight one."""
        self._cancel_active_routine()
        factory = {
            RoutineName.CROSS: self._routine_cross,
            RoutineName.SHAKE: self._routine_shake,
            RoutineName.JUMP: self._routine_jump,
        }[name]
        self._start_routine(factory)

    # ---------- routine lifecycle ----------

    def _pick_random_routine(self) -> Callable[[], Routine]:
        return random.choice(
            [self._routine_cross, self._routine_shake, self._routine_jump]
        )

    def _start_routine(self, factory: Callable[[], Routine]) -> None:
        co = factory()
        try:
            next(co)  # prime: runs up to the first `yield`
        except StopIteration:
            self._routine = None
            return
        self._routine = co
        # The routine set fx/fy during priming; move the widget there before
        # showing it so it doesn't flash at a stale location.
        self._apply_position()
        self.show()
        self.raise_()

    def _cancel_active_routine(self) -> None:
        if self._routine is not None:
            try:
                self._routine.close()
            except Exception:  # noqa: BLE001  best-effort cleanup
                pass
            self._routine = None
        self._jump_offset_y = 0.0
        self._anim_mode = "walk"
        self.hide()

    def _finish_routine(self) -> None:
        self._routine = None
        self._jump_offset_y = 0.0
        self._anim_mode = "walk"
        self.hide()
        self._cooldown_ms = random.randint(APPEARANCE_MIN_MS, APPEARANCE_MAX_MS)

    # ---------- tick ----------

    def _current_geometry(self) -> QRect:
        s = self.screen()
        if s is None:
            return _primary_available_rect(QApplication.instance())
        return s.availableGeometry()

    def _on_tick(self) -> None:
        now = self._clock.elapsed()
        # Clamp dt so a sleep/wake or stall can't teleport the pet mid-routine.
        dt = max(0, min(now - self._last_tick_ms, 100))
        self._last_tick_ms = now

        self._advance_blink(dt)

        if self._routine is not None:
            try:
                self._routine.send(dt)
            except StopIteration:
                self._finish_routine()
                return
            self._apply_position()
            self.update()
        else:
            if self._appearances_paused:
                return
            self._cooldown_ms -= dt
            if self._cooldown_ms <= 0:
                self._start_routine(self._pick_random_routine())

    def _apply_position(self) -> None:
        x = int(round(self._fx))
        y = int(round(self._fy + self._jump_offset_y))
        self.move(x, y)

    def _advance_blink(self, dt: int) -> None:
        self._blink_acc_ms += dt
        if self._blinking:
            if self._blink_acc_ms >= BLINK_DUR_MS:
                self._blinking = False
                self._blink_acc_ms = 0
        elif self._blink_acc_ms >= BLINK_INTERVAL_MS:
            self._blinking = True
            self._blink_acc_ms = 0

    def _advance_walk_anim(self, dt: int) -> None:
        self._walk_acc_ms += dt
        if self._walk_acc_ms >= WALK_FRAME_MS:
            self._walk_frame ^= 1
            self._walk_acc_ms = 0

    def _advance_shake_anim(self, dt: int) -> None:
        self._shake_acc_ms += dt
        if self._shake_acc_ms >= SHAKE_FRAME_MS:
            self._shake_frame ^= 1
            self._shake_acc_ms = 0

    def _step_horizontal(self, dt: int) -> None:
        self._fx += RUN_SPEED_PX_S * (dt / 1000.0) * self._direction

    # ---------- routines (generators) ----------

    def _ground_y(self, g: QRect) -> float:
        return float(g.bottom() - self.height() - BOTTOM_MARGIN_PX)

    def _enter_left(self, g: QRect) -> None:
        """Position the pet just off the left edge, facing right."""
        self._fy = self._ground_y(g)
        self._fx = float(g.left() - self.width() - 4)
        self._direction = 1
        self._jump_offset_y = 0.0
        self._anim_mode = "walk"
        self._walk_frame = 0
        self._walk_acc_ms = 0
        self._shake_frame = 0
        self._shake_acc_ms = 0

    def _routine_cross(self) -> Routine:
        g = self._current_geometry()
        self._enter_left(g)
        exit_x = g.right() + EXIT_MARGIN_PX
        while self._fx < exit_x:
            dt = (yield)
            self._advance_walk_anim(dt)
            self._step_horizontal(dt)

    def _routine_shake(self) -> Routine:
        g = self._current_geometry()
        self._enter_left(g)
        middle_x = (g.left() + g.right()) / 2.0 - self.width() / 2.0
        exit_x = g.right() + EXIT_MARGIN_PX

        # Run to the middle.
        while self._fx < middle_x:
            dt = (yield)
            self._advance_walk_anim(dt)
            self._step_horizontal(dt)

        # Turn around (face away from the viewer) and sway in place by
        # alternating between the two back-facing frames. No horizontal
        # motion during this phase — he's planted.
        self._anim_mode = "shake"
        self._shake_frame = 0
        self._shake_acc_ms = 0
        elapsed = 0
        while elapsed < SHAKE_DURATION_MS:
            dt = (yield)
            elapsed += dt
            self._advance_shake_anim(dt)

        # Resume walking frames and exit stage right.
        self._anim_mode = "walk"
        self._walk_frame = 0
        self._walk_acc_ms = 0
        while self._fx < exit_x:
            dt = (yield)
            self._advance_walk_anim(dt)
            self._step_horizontal(dt)

    def _routine_jump(self) -> Routine:
        g = self._current_geometry()
        self._enter_left(g)
        span = g.right() - g.left()
        jump_start_x = g.left() + span * 0.35
        exit_x = g.right() + EXIT_MARGIN_PX

        # Run up to the jump trigger.
        while self._fx < jump_start_x:
            dt = (yield)
            self._advance_walk_anim(dt)
            self._step_horizontal(dt)

        # Jump in place-on-the-move: parabolic y offset, keep running.
        elapsed = 0
        while elapsed < JUMP_DURATION_MS:
            dt = (yield)
            elapsed += dt
            p = min(1.0, elapsed / JUMP_DURATION_MS)
            # 4·p·(1-p) is a parabola that peaks at 1.0 when p=0.5.
            self._jump_offset_y = -JUMP_HEIGHT_PX * 4 * p * (1 - p)
            self._advance_walk_anim(dt)
            self._step_horizontal(dt)
        self._jump_offset_y = 0.0

        # Finish the run off the right edge.
        while self._fx < exit_x:
            dt = (yield)
            self._advance_walk_anim(dt)
            self._step_horizontal(dt)

    # ---------- painting ----------

    def paintEvent(self, e: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(self.rect(), QColor(0, 0, 0, 0))
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        if self._using_pixmaps:
            self._paint_pixmap(p)
        else:
            self._paint_programmatic(p)
        p.end()

    def _paint_pixmap(self, p: QPainter) -> None:
        if self._anim_mode == "shake" and self._has_shake_frames:
            frames = self._shake_frames  # never mirrored — back view is symmetric-ish
            pm = frames[self._shake_frame % len(frames)]
        else:
            faces_right_wanted = (self._direction > 0) == SPRITE_SOURCE_FACES_RIGHT
            frames = self._walk_right if faces_right_wanted else self._walk_left
            pm = frames[self._walk_frame % len(frames)]
        x = (self.width() - pm.width()) // 2
        y = (self.height() - pm.height()) // 2
        p.drawPixmap(x, y, pm)

    def _paint_programmatic(self, p: QPainter) -> None:
        grid = SPRITE_WALK_B if self._walk_frame else SPRITE_WALK_A
        flip_h = self._direction < 0
        eye_color = COLOR_BODY if self._blinking else COLOR_EYES
        for row in range(SPRITE_H):
            for col in range(SPRITE_W):
                c = grid[row][col]
                if c == 0:
                    continue
                draw_col = (SPRITE_W - 1 - col) if flip_h else col
                r = QRect(
                    MARGIN + draw_col * CELL,
                    MARGIN + row * CELL,
                    CELL,
                    CELL,
                )
                p.fillRect(r, eye_color if c == 2 else COLOR_BODY)

    # ---------- input ----------

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(e)


# ---------- on-screen control panel ----------

# Short labels for the control panel buttons. Fuller text on the tray menu is
# fine, but on a floating chip we want something compact.
ROUTINE_SHORT_LABEL = {
    RoutineName.CROSS: "Walk",
    RoutineName.SHAKE: "Shake",
    RoutineName.JUMP: "Jump",
}

PANEL_DEFAULT_MARGIN_PX = 16
PANEL_DEFAULT_TOP_OFFSET_PX = 40  # leave room under the macOS menu bar


class ControlPanel(QWidget):
    """A small draggable always-on-top bar of buttons, one per routine.

    Implementation notes:
      * Frameless + Tool + WindowStaysOnTopHint so it hovers over other apps.
      * WA_TranslucentBackground + styled background on a named widget gives a
        rounded semi-transparent chip. WA_StyledBackground is required because
        stylesheet-rendered backgrounds don't paint through a translucent
        window otherwise.
      * Drag is handled on the panel itself; button clicks consume their own
        mouse events so they never look like a drag start.
    """

    def __init__(self, pet: PetView) -> None:
        super().__init__(None)
        self._pet = pet
        self._drag_offset: Optional[QPoint] = None

        self.setObjectName("ControlPanel")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

        self.setStyleSheet(
            """
            QWidget#ControlPanel {
                background-color: rgba(28, 28, 30, 215);
                border-radius: 12px;
            }
            QPushButton {
                background-color: rgba(70, 70, 74, 220);
                color: white;
                border: 1px solid rgba(255, 255, 255, 35);
                border-radius: 8px;
                padding: 6px 12px;
                font-size: 12px;
            }
            QPushButton:hover  { background-color: rgba(110, 110, 116, 235); }
            QPushButton:pressed{ background-color: rgba(50,  50,  54,  235); }
            QPushButton#QuitBtn {
                background-color: rgba(170, 45, 45, 225);
                border: 1px solid rgba(255, 255, 255, 45);
            }
            QPushButton#QuitBtn:hover  { background-color: rgba(205, 60, 60, 240); }
            QPushButton#QuitBtn:pressed{ background-color: rgba(140, 35, 35, 240); }
            """
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        for name in RoutineName:
            btn = QPushButton(ROUTINE_SHORT_LABEL[name])
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # keep focus off the panel
            # default-arg bind to avoid the late-binding-loop lambda trap.
            btn.clicked.connect(lambda _checked=False, n=name: self._pet.trigger(n))
            layout.addWidget(btn)

        # End-session button, styled red so it reads as destructive and can't
        # be confused for a routine trigger. Sits to the right of the routines.
        quit_btn = QPushButton("Quit")
        quit_btn.setObjectName("QuitBtn")
        quit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        quit_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        quit_btn.setToolTip("End the screen-pet session")
        quit_btn.clicked.connect(QApplication.quit)
        layout.addWidget(quit_btn)

        self.adjustSize()

    # ---------- placement ----------

    def place_default(self, app: QApplication) -> None:
        """Park the panel in the top-right of the primary screen."""
        rect = _primary_available_rect(app)
        self.adjustSize()
        x = rect.right() - self.width() - PANEL_DEFAULT_MARGIN_PX
        y = rect.top() + PANEL_DEFAULT_TOP_OFFSET_PX
        self.move(x, y)

    # ---------- drag ----------

    def mousePressEvent(self, e) -> None:  # type: ignore[override]
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:  # type: ignore[override]
        if self._drag_offset is not None and (e.buttons() & Qt.MouseButton.LeftButton):
            self.move(e.globalPosition().toPoint() - self._drag_offset)
            e.accept()
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # type: ignore[override]
        self._drag_offset = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().mouseReleaseEvent(e)


# ---------- tray icon ----------

def _tray_icon_pixmap() -> QPixmap:
    """Tray icon: first walk frame if present, else a monochrome silhouette."""
    size = 22
    png = SPRITES_DIR / PIXMAP_WALK_FILES[0]
    if png.exists():
        pm = QPixmap(str(png))
        if not pm.isNull():
            return pm.scaled(
                size, size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

    cell = 2
    off_x = (size - SPRITE_W * cell) // 2
    off_y = (size - SPRITE_H * cell) // 2
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    for row in range(SPRITE_H):
        for col in range(SPRITE_W):
            if SPRITE_WALK_A[row][col] != 0:
                p.fillRect(
                    off_x + col * cell, off_y + row * cell,
                    cell, cell, QColor(0, 0, 0, 255),
                )
    p.end()
    return pm


def _build_tray(
    app: QApplication,
    pet: PetView,
    panel: Optional[ControlPanel],
) -> Optional[QSystemTrayIcon]:
    if not QSystemTrayIcon.isSystemTrayAvailable():
        sys.stderr.write(
            "screen-pet: system tray not available; use Esc or Cmd+Q to quit.\n"
        )
        return None

    tray = QSystemTrayIcon(QIcon(_tray_icon_pixmap()), app)
    tray.setToolTip("Screen Pet")

    menu = QMenu()

    show_menu = QMenu("Show now", menu)
    for r in RoutineName:
        action = QAction(r.label, show_menu)
        # Bind the routine name at lambda-definition time via default arg,
        # otherwise late binding makes every entry fire the last routine.
        action.triggered.connect(lambda _checked=False, name=r: pet.trigger(name))
        show_menu.addAction(action)
    menu.addMenu(show_menu)

    menu.addSeparator()

    if panel is not None:
        panel_action = QAction("Show control panel", menu)
        panel_action.setCheckable(True)
        panel_action.setChecked(panel.isVisible())
        # setVisible takes (bool), which is exactly what `toggled` emits.
        panel_action.toggled.connect(panel.setVisible)
        # Resync the checkbox each time the menu opens, in case the user
        # closed the panel another way (e.g. Cmd-W from its system menu).
        menu.aboutToShow.connect(
            lambda: panel_action.setChecked(panel.isVisible())
        )
        menu.addAction(panel_action)

    pause_action = QAction("Pause appearances", menu)
    pause_action.setCheckable(True)
    pause_action.setChecked(pet.appearances_paused())
    pause_action.toggled.connect(pet.set_appearances_paused)
    menu.aboutToShow.connect(
        lambda: pause_action.setChecked(pet.appearances_paused())
    )
    menu.addAction(pause_action)

    menu.addSeparator()

    quit_action = QAction("Quit", menu)
    quit_action.triggered.connect(app.quit)
    menu.addAction(quit_action)

    tray.setContextMenu(menu)
    tray.show()
    return tray


def main() -> int:
    app = QApplication(sys.argv)
    # The pet hides between appearances, which would look like "last window
    # closed" to Qt's default quit-on-last-closed logic. Keep us alive.
    app.setQuitOnLastWindowClosed(False)

    pet = PetView()
    # Position is set by each routine on start; nothing to do here.

    # Floating button panel. Shown by default so first-time users discover it;
    # hideable via the tray menu. Kept on `app` so it isn't GC'd when main()
    # returns through app.exec().
    panel = ControlPanel(pet)
    panel.place_default(app)
    panel.show()
    app._panel = panel  # type: ignore[attr-defined]

    app._tray = _build_tray(app, pet, panel)  # type: ignore[attr-defined]

    if app.__dict__.get("_tray") is None and not QSystemTrayIcon.isSystemTrayAvailable():
        # With no tray and the pet hidden, there's no UI at all. Fall back to
        # showing an immediate cross routine so the user at least sees signs of life.
        pet.trigger(RoutineName.CROSS)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
