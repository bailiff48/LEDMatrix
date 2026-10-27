"""Screens for the fantasy-leagues plugin. Pure functions: data in, image out.

Laid out for 128x32 (two 64x32 panels) with the 3x5 font; narrower panels
get the same layout with names cut to fit. Every function returns a new RGB
image the size of the panel.
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

from PIL import Image

import fl_font as font

RGB = Tuple[int, int, int]
INK: RGB = (235, 235, 235)
DIM: RGB = (110, 110, 110)
FAINT: RGB = (45, 45, 45)
OPP: RGB = (235, 235, 235)
OPP_ALERT: RGB = (255, 95, 60)
GOLD: RGB = (255, 200, 40)
RED: RGB = (230, 40, 40)
AMBER: RGB = (255, 160, 0)
LINE = 6  # 5px glyphs + 1px gap
NAME_X = 16  # after the widest slot label ("R/W", "DST")

INJURY = {"QUESTIONABLE": ("Q", AMBER), "DOUBTFUL": ("D", AMBER), "OUT": ("O", RED),
          "INJURY_RESERVE": ("IR", RED), "SUSPENSION": ("SSP", RED)}


# ----------------------------------------------------------------------
# primitives
# ----------------------------------------------------------------------

def blank(w: int, h: int) -> Image.Image:
    return Image.new("RGB", (w, h), (0, 0, 0))


def rect(img: Image.Image, x: int, y: int, w: int, h: int, color: Sequence[int]) -> None:
    if w > 0 and h > 0:
        img.paste(tuple(int(c) for c in color), (int(x), int(y), int(x + w), int(y + h)))


def text(img: Image.Image, s: Any, x: int, y: int, color: RGB, scale: int = 1,
         align: str = "left") -> int:
    return font.draw_text(img, s, x, y, color, scale=scale, align=align)


def readable(bg: Sequence[int]) -> RGB:
    lum = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
    return (0, 0, 0) if lum > 150 else (255, 255, 255)


def chip(img: Image.Image, x: int, y: int, label: str, color: RGB) -> int:
    """Host chip: label on the host colour, 7px tall. Returns its width."""
    w = font.text_width(label) + 4
    rect(img, x, y, w, 7, color)
    text(img, label, x + 2, y + 1, readable(color))
    return w


def pts(value: Any) -> str:
    """36.0 -> '36', 12.48 -> '12.5', None -> ''."""
    if value is None:
        return ""
    v = round(float(value), 1)
    return str(int(v)) if v == int(v) else f"{v:.1f}"


def share_bar(img: Image.Image, x: int, y: int, w: int, h: int, mine: float, theirs: float,
              color: RGB) -> None:
    rect(img, x, y, w, h, FAINT)
    total = (mine or 0) + (theirs or 0)
    if total <= 0:
        return
    rect(img, x, y, round(w * max(0.0, mine) / total), h, color)
    rect(img, x + w // 2, y, 1, h, (0, 0, 0))  # the 50% mark


def status(m: Dict[str, Any]) -> Tuple[str, RGB]:
    if m.get("final"):
        mine, theirs = m["me"]["points"], m["opp"]["points"]
        if mine > theirs:
            return "W", m["_team_color"]
        if mine < theirs:
            return "L", RED
        return "T", GOLD
    return f"WK{m['week']}", DIM


def _bar_values(m: Dict[str, Any]) -> Tuple[float, float]:
    if m.get("final"):
        return m["me"]["points"], m["opp"]["points"]
    mp, op = m["me"].get("projected"), m["opp"].get("projected")
    if mp is None or op is None:
        return m["me"]["points"], m["opp"]["points"]
    return mp, op


# ----------------------------------------------------------------------
# screens
# ----------------------------------------------------------------------

def summary(w: int, h: int, rows: List[Dict[str, Any]], title: str, page: str = "") -> Image.Image:
    """My leagues: a title line, then up to two leagues of 12px each.

    rows: {"label", "host_color", "team_color", "matchup" or None, "error"}.
    """
    img = blank(w, h)
    text(img, title, 0, 0, DIM)
    if page:
        text(img, page, w - 1, 0, DIM, align="right")
    y = 8
    for r in rows[:2]:
        cx = chip(img, 0, y, r["label"], r["host_color"]) + 3
        m = r.get("matchup")
        if m is None:
            text(img, font.fit(r.get("error") or "NO DATA", w - cx), cx, y + 1, AMBER)
            y += 12
            continue
        me, opp, color = m["me"], m["opp"], r["team_color"]
        x = cx + text(img, me["abbrev"], cx, y + 1, color) + 3
        text(img, pts(me["points"]), x, y + 1, color)
        ox = w - 1 - text(img, opp["abbrev"], w - 1, y + 1, OPP, align="right") - 3
        text(img, pts(opp["points"]), ox, y + 1, OPP, align="right")
        label, lcolor = status(dict(m, _team_color=color))
        mid = (x + font.text_width(pts(me["points"])) + ox - font.text_width(pts(opp["points"]))) // 2
        text(img, label, mid, y + 1, lcolor, align="center")
        share_bar(img, 0, y + 9, w, 2, *_bar_values(m), color=color)
        y += 12
    return img


def matchup(w: int, h: int, m: Dict[str, Any], label: str, host_color: RGB,
            team_color: RGB) -> Image.Image:
    """One league: header, big scores, records, projections, bar."""
    img = blank(w, h)
    cx = chip(img, 0, 0, label, host_color) + 3
    st, st_color = status(dict(m, _team_color=team_color))
    right = st if not m.get("final") else f"WK{m['week']} FINAL"
    rw = text(img, right, w - 1, 1, DIM if not m.get("final") else INK, align="right")
    text(img, font.fit(m.get("league_name") or "", w - cx - rw - 3), cx, 1, DIM)

    me, opp = m["me"], m["opp"]
    half = w // 2
    # me: abbrev + record on the left, big score toward the middle
    text(img, me["abbrev"], 0, 10, team_color)
    text(img, me.get("record") or "", 0, 17, DIM)
    text(img, pts(me["points"]), half - 4, 10, team_color, scale=2, align="right")
    text(img, opp["abbrev"], w - 1, 10, OPP, align="right")
    text(img, opp.get("record") or "", w - 1, 17, DIM, align="right")
    text(img, pts(opp["points"]), half + 4, 10, OPP, scale=2)
    if m.get("final"):
        word = {"W": "WIN", "L": "LOSS", "T": "TIE"}[st]
        text(img, word, half, 23, st_color, align="center")

    mp, op = me.get("projected"), opp.get("projected")
    if not m.get("final") and mp is not None and op is not None:
        text(img, f"PROJ {pts(mp)}", 0, 23, DIM)
        text(img, pts(op), w - 1, 23, DIM, align="right")
    share_bar(img, 0, 30, w, 2, *_bar_values(m), color=team_color)
    return img


def roster(w: int, h: int, side: Dict[str, Any], players: List[Dict[str, Any]], label: str,
           host_color: RGB, color: RGB, page: str, show_proj: bool = True) -> Image.Image:
    """Header (chip, team, points, page) and up to four starters."""
    img = blank(w, h)
    cx = chip(img, 0, 0, label, host_color) + 3
    x = cx + text(img, side["abbrev"], cx, 1, color) + 3
    text(img, pts(side["points"]), x, 1, color)
    if page:
        text(img, page, w - 1, 1, DIM, align="right")

    pts_right, proj_right = w - 18, w - 1
    for i, p in enumerate(players[:4]):
        y = 8 + i * LINE
        text(img, p["slot"], 0, y, DIM)
        injury = INJURY.get(str(p.get("injury") or "").upper())
        tag_w = font.text_width(injury[0]) + 2 if injury else 0
        value = pts(p["points"])
        name_max = (pts_right - font.text_width(value) - 3) - NAME_X - tag_w
        name = font.fit(p["short"], name_max)
        nx = NAME_X + text(img, name, NAME_X, y, INK)
        if injury:
            text(img, injury[0], nx + 2, y, injury[1])
        text(img, value, pts_right, y, color if p["points"] else INK, align="right")
        if show_proj and p.get("projected") is not None:
            text(img, pts(p["projected"]), proj_right, y, DIM, align="right")
    return img


def alert(w: int, h: int, a: Dict[str, Any], t: float) -> Image.Image:
    """A big play by one of my starters (or my opponent's).

    ``t`` is seconds since the alert started: the border flashes for the
    first 1.5s and the score line cycles through leagues every 2s.
    """
    img = blank(w, h)
    first = a["leagues"][0]
    mine = first["side"] == "me"
    accent = first["team_color"] if mine else OPP_ALERT
    if t < 1.5 and int(t * 4) % 2 == 0:
        rect(img, 0, 0, w, 1, accent)
        rect(img, 0, h - 1, w, 1, accent)
        rect(img, 0, 0, 1, h, accent)
        rect(img, w - 1, 0, 1, h, accent)

    x = 2
    for lg in a["leagues"][:3]:
        x += chip(img, x, 2, lg["league"], lg["host_color"]) + 2
    tag = "MY PLAYER" if mine else "OPP PLAYER"
    text(img, font.fit(f"{tag}  {a['pos']} {a['team']}", w - x - 3), x + 1, 3, accent)

    delta = f"+{pts(first['delta'])}"
    dw = text(img, delta, w - 3, 11, GOLD, scale=2, align="right")
    room = w - 3 - dw - 4 - 2
    if font.text_width(a["short"], 2) <= room:
        text(img, a["short"], 2, 11, INK, scale=2)
    else:
        text(img, font.fit(a["short"], room), 2, 14, INK)

    lg = a["leagues"][int(t // 2) % len(a["leagues"])]
    (ma, mp), (oa, op) = lg["me"], lg["opp"]
    left = f"{ma} {pts(mp)}"
    right = f"{pts(op)} {oa}"
    mid = w // 2
    text(img, left, mid - 4, 25, lg["team_color"], align="right")
    text(img, "-", mid, 25, DIM, align="center")
    text(img, right, mid + 4, 25, OPP)
    return img
