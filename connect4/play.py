#!/usr/bin/env python3
"""
Community Connect 4 - game engine

Reads the current board state from connect4/state.json, applies a move
parsed from a GitHub issue title of the form:

    connect4|drop|<color>|<column>

where <color> is "red" or "blue" and <column> is 1-7. Renders board.png
and status.png, updates state.json, and rewrites the game block in
README.md. Prints a JSON object to stdout describing what happened, so
the workflow can post a comment on the issue and decide whether to
close it.

Never raises on bad input - invalid moves are reported back as a
rejected move with an explanation, so the workflow can always safely
close the triggering issue.
"""
import json
import os
import re
import sys
import argparse

from PIL import Image, ImageDraw, ImageFont

ROWS = 6
COLS = 7

COLORS = {
    "red": (255, 20, 147),    # hot pink
    "blue": (255, 255, 255),  # white
}
DISPLAY = {"red": "Pink", "blue": "White"}
EMOJI = {"red": "\U0001FA77", "blue": "\U0001F90D"}  # 🩷 🤍
EMPTY_OUTLINE = (90, 95, 105)  # subtle hole outline against the dark board
WHITE_OUTLINE = (255, 20, 147)  # thin pink outline so white hearts read against the dark bg
BOARD_BG = (13, 17, 23)  # GitHub dark background

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

README_START = "<!-- START CONNECT 4 GAME -->"
README_END = "<!-- END CONNECT 4 GAME -->"


def heart_points(cx, cy, scale):
    """Points for a heart outline, centered at (cx, cy), using the
    classic parametric heart curve. `scale` roughly maps to half the
    heart's width in pixels."""
    import math

    pts = []
    for deg in range(0, 360, 4):
        t = math.radians(deg)
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        pts.append((cx + x * scale / 16, cy - y * scale / 16))
    return pts


def draw_heart(draw, cx, cy, size, fill, outline=None, outline_width=3):
    """Draw a filled heart centered at (cx, cy). `size` is roughly the
    heart's full width in pixels."""
    pts = heart_points(cx, cy, size / 2)
    draw.polygon(pts, fill=fill)
    if outline:
        draw.line(pts + [pts[0]], fill=outline, width=outline_width, joint="curve")


def load_font(size):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def new_state():
    return {
        "board": [[None] * COLS for _ in range(ROWS)],
        "turn": "red",
        "status": "in_progress",
        "winner": None,
        "moves": 0,
    }


def load_state(path):
    if not os.path.exists(path):
        return new_state()
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return new_state()


def save_state(path, state):
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


def parse_title(title):
    """Return (color, column) or None if the title doesn't match."""
    parts = title.strip().split("|")
    if len(parts) != 4:
        return None
    _, action, color, col = parts
    if action.lower() != "drop":
        return None
    if color.lower() not in COLORS:
        return None
    try:
        col = int(col)
    except ValueError:
        return None
    if not (1 <= col <= COLS):
        return None
    return color.lower(), col - 1  # zero-indexed column


def drop_disc(board, col, color):
    """Place a disc in the given column. Returns the row it landed on,
    or None if the column is full."""
    for r in range(ROWS - 1, -1, -1):
        if board[r][col] is None:
            board[r][col] = color
            return r
    return None


def check_winner(board, row, col, color):
    directions = [
        (0, 1),   # horizontal
        (1, 0),   # vertical
        (1, 1),   # diagonal down-right
        (1, -1),  # diagonal down-left
    ]
    for dr, dc in directions:
        count = 1
        for sign in (1, -1):
            r, c = row + dr * sign, col + dc * sign
            while 0 <= r < ROWS and 0 <= c < COLS and board[r][c] == color:
                count += 1
                r += dr * sign
                c += dc * sign
        if count >= 4:
            return True
    return False


def board_full(board):
    return all(board[0][c] is not None for c in range(COLS))


def render_board(board, path):
    cell = 90
    margin = 20
    width = COLS * cell + margin * 2
    height = ROWS * cell + margin * 2
    img = Image.new("RGB", (width, height), BOARD_BG)
    draw = ImageDraw.Draw(img)
    pad = 10
    for r in range(ROWS):
        for c in range(COLS):
            cx = margin + c * cell + cell / 2
            cy = margin + r * cell + cell / 2
            size = cell - pad * 2
            color = board[r][c]
            if color is None:
                # Empty slot: just a faint hole outline.
                x0, y0 = cx - size / 2, cy - size / 2
                x1, y1 = cx + size / 2, cy + size / 2
                draw.ellipse([x0, y0, x1, y1], outline=EMPTY_OUTLINE, width=3)
            elif color == "blue":
                # White heart needs an outline to read against the dark board.
                draw_heart(draw, cx, cy, size, fill=COLORS[color], outline=WHITE_OUTLINE)
            else:
                draw_heart(draw, cx, cy, size, fill=COLORS[color])
    img.save(path)


def render_status(state, path):
    height = 90
    font = load_font(34)

    if state["status"] == "won":
        text = f"{DISPLAY[state['winner']]} wins! Click a column to start a new game."
        color = COLORS[state["winner"]]
        outline = WHITE_OUTLINE if state["winner"] == "blue" else None
    elif state["status"] == "draw":
        text = "It's a draw! Click a column to start a new game."
        color = (200, 200, 200)
        outline = None
    else:
        text = f"{DISPLAY[state['turn']]}'s turn"
        color = COLORS[state["turn"]]
        outline = WHITE_OUTLINE if state["turn"] == "blue" else None

    # Measure text first so the image is always wide enough (win/draw
    # messages are longer than the plain turn indicator).
    measure_img = Image.new("RGB", (10, 10))
    measure_draw = ImageDraw.Draw(measure_img)
    bbox = measure_draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

    disc_d = 40
    disc_x0 = 20
    left_gap = 20
    right_margin = 20
    width = disc_x0 + disc_d + left_gap + tw + right_margin

    img = Image.new("RGB", (width, height), BOARD_BG)
    draw = ImageDraw.Draw(img)
    disc_cy = height / 2
    disc_cx = disc_x0 + disc_d / 2
    draw_heart(draw, disc_cx, disc_cy, disc_d, fill=color, outline=outline)
    tx = disc_x0 + disc_d + left_gap
    ty = (height - th) // 2 - bbox[1]
    draw.text((tx, ty), text, font=font, fill=(255, 255, 255))
    img.save(path)


def build_readme_block(state, repo, branch, cache_bust):
    turn = state["turn"]
    color = turn
    links = []
    for c in range(COLS):
        col_num = c + 1
        top_full = state["board"][0][c] is not None
        if state["status"] == "in_progress" and top_full:
            links.append(f"<b>[ {col_num} \U0001F6AB ]</b>")
        else:
            issue_title = f"connect4|drop|{color}|{col_num}"
            url = (
                f"https://github.com/{repo}/issues/new?title="
                + issue_title.replace("|", "%7C")
            )
            links.append(
                f'<a href="{url}"><b>[ {col_num} {EMOJI[color]} ]</b></a>'
            )
    links_html = "\n    &nbsp;\n    ".join(links)

    board_url = (
        f"https://raw.githubusercontent.com/{repo}/{branch}/connect4/board.png"
        f"?v={cache_bust}"
    )
    status_url = (
        f"https://raw.githubusercontent.com/{repo}/{branch}/connect4/status.png"
        f"?v={cache_bust}"
    )

    return f"""{README_START}
<div align="center">

  <h3>\U0001F3AE Community Connect 4</h3>
  <p>Click a column number below to drop your disc!</p>

  <p>
    {links_html}
  </p>

  <br />

  <img
    src="{board_url}"
    alt="Connect 4 Board"
    width="350"
  />

  <br />

  <img
    src="{status_url}"
    alt="Connect 4 Game Status"
  />

</div>
{README_END}"""


def update_readme(readme_path, block):
    if not os.path.exists(readme_path):
        # No README yet - just create one with the block.
        with open(readme_path, "w") as f:
            f.write(block + "\n")
        return
    with open(readme_path) as f:
        content = f.read()
    pattern = re.compile(
        re.escape(README_START) + r".*?" + re.escape(README_END), re.DOTALL
    )
    if pattern.search(content):
        content = pattern.sub(block, content)
    else:
        content = content.rstrip("\n") + "\n\n" + block + "\n"
    with open(readme_path, "w") as f:
        f.write(content)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", required=True, help="Issue title")
    parser.add_argument("--repo", required=True, help="owner/repo")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--state", default="connect4/state.json")
    parser.add_argument("--board-out", default="connect4/board.png")
    parser.add_argument("--status-out", default="connect4/status.png")
    parser.add_argument("--readme", default="README.md")
    args = parser.parse_args()

    state = load_state(args.state)
    parsed = parse_title(args.title)

    result = {"changed": False, "comment": "", "close": True}

    if parsed is None:
        result["comment"] = (
            "This doesn't look like a valid Connect 4 move "
            "(expected a title like 'connect4|drop|red|3'). No changes made."
        )
    else:
        color, col = parsed

        if state["status"] != "in_progress":
            # Previous game finished - this click starts a fresh game.
            state = new_state()
            save_state(args.state, state)
            result["comment"] = (
                "The previous game had already ended, so a new board has "
                "started. Click a column again to make the first move!"
            )
            result["changed"] = True
        elif color != state["turn"]:
            result["comment"] = (
                f"It's **{DISPLAY[state['turn']]}**'s turn right now, not "
                f"{DISPLAY[color]}'s. No move was made - check the README "
                "for the current turn and try again."
            )
        else:
            row = drop_disc(state["board"], col, color)
            if row is None:
                result["comment"] = (
                    f"Column {col + 1} is already full. Pick a different column."
                )
            else:
                state["moves"] += 1
                if check_winner(state["board"], row, col, color):
                    state["status"] = "won"
                    state["winner"] = color
                    result["comment"] = f"{DISPLAY[color]} wins! \U0001F389"
                elif board_full(state["board"]):
                    state["status"] = "draw"
                    result["comment"] = "It's a draw!"
                else:
                    state["turn"] = "blue" if color == "red" else "red"
                    result["comment"] = (
                        f"Disc dropped in column {col + 1}. "
                        f"{DISPLAY[state['turn']]}'s turn next."
                    )
                save_state(args.state, state)
                result["changed"] = True

    if result["changed"]:
        render_board(state["board"], args.board_out)
        render_status(state, args.status_out)
        block = build_readme_block(state, args.repo, args.branch, state["moves"])
        update_readme(args.readme, block)

    print(json.dumps(result))


if __name__ == "__main__":
    main()
