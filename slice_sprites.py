"""
Slice a sprite sheet into individual sprite PNGs.

Usage:
    # Save your sheet as sprites/pikmin_sheet.png, then:
    python slice_sprites.py                     # extract all, write annotated preview
    python slice_sprites.py --walk 12 13        # also copy those two indices as
                                                 # sprites/walk_0.png, sprites/walk_1.png

How it works: auto-detects the background color from the corners and extracts
each connected region of non-background pixels as a tightly-cropped PNG. Runs
on Pillow only (no numpy/scipy) so it has no extra dependencies.

If the auto-detected background color is wrong, pass --bg 255,255,255,255 or
any other RGBA tuple.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from collections import deque

from PIL import Image, ImageDraw, ImageFont

HERE = pathlib.Path(__file__).parent
SPRITES = HERE / "sprites"
SHEET = SPRITES / "pikmin_sheet.png"

# pixels closer than this (channel-wise) to the detected bg colour are treated
# as background. Sheets from PNGs with semi-JPEG artefacts need slack.
BG_TOLERANCE = 12
# blobs smaller than this are discarded (UI digits, stray specks)
MIN_BLOB_PX = 80


def detect_bg(im: Image.Image) -> tuple[int, int, int, int]:
    """Pick the most common corner pixel as background."""
    w, h = im.size
    candidates = [im.getpixel(p) for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
    # majority vote; falls back to top-left
    counts: dict[tuple, int] = {}
    for c in candidates:
        counts[c] = counts.get(c, 0) + 1
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _color_matches_bg(px: tuple, bg: tuple, tol: int) -> bool:
    """Pure color comparison — does NOT imply this pixel is actually background.
    (Interior pixels that match the bg color, like eye whites on a white sheet,
    match here but are not reachable from the border and thus not background.)"""
    if len(px) == 4 and px[3] == 0:
        return True
    if len(bg) == 4 and bg[3] == 0:
        return False
    return all(abs(a - b) <= tol for a, b in zip(px[:3], bg[:3]))


def compute_bg_mask(im: Image.Image, bg: tuple, tol: int) -> list[list[bool]]:
    """Flood-fill from every border pixel that matches the bg color; only
    reachable bg-coloured pixels are marked as background. Interior white
    pixels (eye whites, pellet highlights, etc.) stay OFF."""
    w, h = im.size
    pixels = im.load()
    mask = [[False] * w for _ in range(h)]
    q: deque[tuple[int, int]] = deque()

    def seed(x: int, y: int) -> None:
        if not mask[y][x] and _color_matches_bg(pixels[x, y], bg, tol):
            mask[y][x] = True
            q.append((x, y))

    for x in range(w):
        seed(x, 0)
        seed(x, h - 1)
    for y in range(h):
        seed(0, y)
        seed(w - 1, y)

    # 4-connected fill is correct here: diagonals can leak past thin antennae
    # and accidentally swallow the sprite interior.
    while q:
        cx, cy = q.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = cx + dx, cy + dy
            if 0 <= nx < w and 0 <= ny < h and not mask[ny][nx]:
                if _color_matches_bg(pixels[nx, ny], bg, tol):
                    mask[ny][nx] = True
                    q.append((nx, ny))
    return mask


def find_blobs(mask: list[list[bool]]) -> list[tuple[int, int, int, int]]:
    """Return bboxes of connected non-background regions using the bg mask."""
    h = len(mask)
    w = len(mask[0]) if h else 0
    visited = [[False] * w for _ in range(h)]
    bboxes: list[tuple[int, int, int, int]] = []

    for y in range(h):
        for x in range(w):
            if visited[y][x] or mask[y][x]:
                continue
            # 8-connected so antennae aren't clipped off the body.
            q = deque([(x, y)])
            visited[y][x] = True
            minx = maxx = x
            miny = maxy = y
            count = 0
            while q:
                cx, cy = q.popleft()
                count += 1
                if cx < minx: minx = cx
                if cx > maxx: maxx = cx
                if cy < miny: miny = cy
                if cy > maxy: maxy = cy
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < w and 0 <= ny < h and not visited[ny][nx]:
                            visited[ny][nx] = True
                            if not mask[ny][nx]:
                                q.append((nx, ny))
            if count >= MIN_BLOB_PX:
                bboxes.append((minx, miny, maxx + 1, maxy + 1))

    bboxes.sort(key=lambda b: (b[1] // 8, b[0]))
    return bboxes


def cut_sprite(im: Image.Image, mask: list[list[bool]], bbox: tuple[int, int, int, int]) -> Image.Image:
    """Crop the bbox; zero-alpha only pixels the mask marks as background."""
    l, t, r, b = bbox
    crop = im.crop(bbox).convert("RGBA")
    px = crop.load()
    for y in range(b - t):
        mrow = mask[t + y]
        for x in range(r - l):
            if mrow[l + x]:
                px[x, y] = (0, 0, 0, 0)
    return crop


def annotate(im: Image.Image, bboxes: list[tuple[int, int, int, int]]) -> Image.Image:
    out = im.convert("RGBA").copy()
    draw = ImageDraw.Draw(out)
    try:
        font = ImageFont.load_default()
    except Exception:  # noqa: BLE001
        font = None
    for i, (l, t, r, b) in enumerate(bboxes):
        draw.rectangle([l, t, r - 1, b - 1], outline=(255, 0, 0, 255), width=1)
        draw.text((l + 1, t + 1), str(i), fill=(255, 0, 0, 255), font=font)
    return out


def parse_bg(s: str | None) -> tuple[int, int, int, int] | None:
    if not s:
        return None
    parts = [int(p) for p in s.split(",")]
    if len(parts) == 3:
        parts.append(255)
    if len(parts) != 4:
        raise ValueError(f"--bg expects 3 or 4 comma-separated ints, got {s!r}")
    return tuple(parts)  # type: ignore[return-value]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", default=str(SHEET))
    ap.add_argument("--out", default=str(SPRITES))
    ap.add_argument("--walk", nargs=2, type=int, metavar=("IDX0", "IDX1"),
                    help="indices to copy as walk_0.png / walk_1.png")
    ap.add_argument("--bg", type=str, help="override background, e.g. 255,255,255")
    ap.add_argument("--tolerance", type=int, default=BG_TOLERANCE)
    args = ap.parse_args()

    sheet_path = pathlib.Path(args.sheet)
    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not sheet_path.exists():
        print(f"error: sheet not found at {sheet_path}", file=sys.stderr)
        print(f"save your PNG there and re-run.", file=sys.stderr)
        return 1

    im = Image.open(sheet_path).convert("RGBA")
    bg = parse_bg(args.bg) or detect_bg(im)
    print(f"sheet: {sheet_path} ({im.size[0]}x{im.size[1]})  bg={bg}")

    mask = compute_bg_mask(im, bg, args.tolerance)
    bboxes = find_blobs(mask)
    print(f"found {len(bboxes)} sprite candidates (>= {MIN_BLOB_PX}px each)")

    # Clear any old sprite_*.png before dumping fresh ones. Best-effort:
    # if a file can't be unlinked (e.g. read-only mount), we'll just overwrite.
    for old in out_dir.glob("sprite_*.png"):
        try:
            old.unlink()
        except OSError:
            pass

    for i, bbox in enumerate(bboxes):
        sprite = cut_sprite(im, mask, bbox)
        sprite.save(out_dir / f"sprite_{i:02d}.png")

    annotate(im, bboxes).save(out_dir / "sheet_annotated.png")
    print(f"wrote {len(bboxes)} sprites + sheet_annotated.png to {out_dir}")

    if args.walk is not None:
        a, b = args.walk
        if not (0 <= a < len(bboxes) and 0 <= b < len(bboxes)):
            print(f"error: --walk indices out of range 0..{len(bboxes)-1}", file=sys.stderr)
            return 2
        cut_sprite(im, mask, bboxes[a]).save(out_dir / "walk_0.png")
        cut_sprite(im, mask, bboxes[b]).save(out_dir / "walk_1.png")
        print(f"wrote walk_0.png <- sprite {a}, walk_1.png <- sprite {b}")
    else:
        print("tip: open sheet_annotated.png, pick the two frames you want as walking,")
        print("     then re-run with --walk <idx0> <idx1>")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
