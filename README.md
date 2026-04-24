# screen-pet-prototype

A tiny **desktop screen pet** built with Python and PyQt6. A small pixel-style sprite moves along the bottom of your real screen in its own always-on-top window (not a browser and not a normal app chrome).

## Requirements

- Python 3.10+
- macOS is the primary target (this prototype is tuned for it).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
source .venv/bin/activate
python main.py
```

## Controls

| Key / action   | Effect              |
| ---------------| --------------------|
| **Space**      | Pause / resume walk |
| **Escape**     | Quit                |
| **Click+drag** | Move the pet window |

## Behaviour

- Frameless, transparent, always-on-top `Tool` window.
- The pet walks from left to right along the **bottom** of the primary display and wraps to the left when it reaches the right edge.
- You can drag the window anywhere; when you resume, walking continues from that position.

## Git / GitHub

The default `origin` remote is **SSH** (`git@github.com:…/screen-pet-prototype.git`). For `git push` and `git pull` to work, add your public key to GitHub and ensure your key is available to the SSH agent (for example, `ssh -T git@github.com` should succeed). If SSH is not set up, you can push once with HTTPS or use the [GitHub CLI](https://cli.github.com/) (`gh auth login`).

## macOS and transparency

On macOS, Qt uses a layered, translucent window (`WA_TranslucentBackground` plus `FramelessWindowHint`). The sprite is drawn in `paintEvent` on a fully transparent background. This combination works well on recent macOS and Qt6 builds.

**Limitations:**

- The window is a real OS window: it must stay within the logical screen; multi-monitor support here is “primary screen only” for the automated walk.
- If anything looks opaque or flickers after a system update, try running from a terminal to see Qt warnings, and ensure you are on PyQt6 6.5+.
- `WindowStaysOnTopHint` places the pet above normal windows; fullscreen apps or certain system UIs can still cover it.

## License

Prototype / educational use; add a license as you like.
