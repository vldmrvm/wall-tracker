#!/usr/bin/env python3
"""
Render the 400x300 e-ink screen for the wall tracker (percent-only, no EUR shown).

Inputs : data/holdings.json (plan), data/history.json, data/benchmark.json, data/prices.json
Outputs: eink/out/screen.png  - preview
         eink/out/screen.bin  - raw 1-bit bitmap for the ESP32 (15000 bytes, 1 = black, MSB first)

Run: python eink/render.py      Deps: pip install pillow
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "eink" / "out"

W, H = 400, 300
BLACK, WHITE = 0, 1

FONT_DIRS = [
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/dejavu"),
    ROOT / "eink" / "fonts",
]


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for d in FONT_DIRS:
        if (d / name).exists():
            return ImageFont.truetype(str(d / name), size)
    raise FileNotFoundError(f"Font {name} not found")


# ── metrics ────────────────────────────────────────────────────────────
def plan_value(plan: dict, on: date) -> float:
    """Plan line: start value compounding at annual_rate_pct, +monthly on every 10th after start."""
    start = date.fromisoformat(plan["start_date"])
    r = float(plan["annual_rate_pct"]) / 100.0

    def grow(amount: float, since: date) -> float:
        return amount * (1 + r) ** ((on - since).days / 365.25)

    value = grow(float(plan["start_value"]), start)
    y, m = start.year, start.month
    while True:
        d = date(y, m, 10)
        if d > on:
            break
        if d > start:
            value += grow(float(plan["monthly"]), d)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return value


def value_on(series: list[dict], on: date) -> float | None:
    """Last known value on or before a date."""
    best = None
    for p in series:
        if date.fromisoformat(p["date"]) <= on:
            best = p["total_eur"]
    return best


def load_metrics() -> dict:
    holdings = json.loads((DATA / "holdings.json").read_text(encoding="utf-8"))
    history = json.loads((DATA / "history.json").read_text(encoding="utf-8"))
    bench = json.loads((DATA / "benchmark.json").read_text(encoding="utf-8"))["series"]
    prices = json.loads((DATA / "prices.json").read_text(encoding="utf-8"))

    plan = holdings["plan"]
    target = float(plan["target"])
    history.sort(key=lambda p: p["date"])
    last_day = date.fromisoformat(history[-1]["date"])
    total = float(prices.get("total_eur") or history[-1]["total_eur"])

    goal_pct = 100.0 * total / target
    month_ago = value_on(history, last_day - timedelta(days=30)) or history[0]["total_eur"]
    month_pp = goal_pct - 100.0 * month_ago / target  # how much closer to goal in 30 days

    bench_now = value_on(bench, last_day)
    vs_bench = 100.0 * (total / bench_now - 1) if bench_now else 0.0
    vs_plan = 100.0 * (total / plan_value(plan, last_day) - 1)

    # Chart series: both lines as % of goal, aligned on history dates
    me = [100.0 * p["total_eur"] / target for p in history]
    vb = []
    for p in history:
        b = value_on(bench, date.fromisoformat(p["date"]))
        vb.append(100.0 * b / target if b else me[len(vb)])

    updated = prices.get("updated_utc", history[-1]["date"])
    upd = datetime.fromisoformat(updated.replace("Z", "+00:00")).strftime("%d.%m.%Y")

    return {
        "goal_pct": min(goal_pct, 100.0),
        "month_pp": month_pp,
        "vs_bench": vs_bench,
        "vs_plan": vs_plan,
        "me": me,
        "bench": vb,
        "since": date.fromisoformat(history[0]["date"]).strftime("since %b %Y"),
        "updated": upd,
    }


# ── drawing helpers ────────────────────────────────────────────────────
def signed(v: float, unit: str = "%") -> str:
    return f"{v:+.1f}{unit}".replace("-", "−")


def tw(d: ImageDraw.ImageDraw, s: str, f) -> float:
    return d.textlength(s, font=f)


def dashed_line(d, pts, dash=4, gap=3):
    acc, on = 0.0, True
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        seg = math.hypot(x2 - x1, y2 - y1)
        pos = 0.0
        while pos < seg:
            step = min((dash if on else gap) - acc, seg - pos)
            if on:
                t0, t1 = pos / seg, (pos + step) / seg
                d.line([x1 + (x2 - x1) * t0, y1 + (y2 - y1) * t0,
                        x1 + (x2 - x1) * t1, y1 + (y2 - y1) * t1], fill=BLACK)
            pos += step
            acc += step
            if acc >= (dash if on else gap):
                acc, on = 0.0, not on


def chart(d, x, y, w, h, main, dashed):
    lo, hi = min(main + dashed), max(main + dashed)
    pad = max((hi - lo) * 0.08, 0.5)
    lo, hi = lo - pad, hi + pad
    n = max(len(main) - 1, 1)

    def pts(s):
        return [(x + w * i / n, y + h - h * (v - lo) / (hi - lo)) for i, v in enumerate(s)]

    # Min / max gridline labels (percent of goal)
    lf = font(10)
    for v in (lo + pad, hi - pad):
        gy = y + h - h * (v - lo) / (hi - lo)
        for gx in range(x, x + w, 4):
            d.point((gx, gy), fill=BLACK)
        d.text((x + w + 3, gy - 6), f"{v:.0f}%", font=lf, fill=BLACK)
    dashed_line(d, pts(dashed))
    d.line(pts(main), fill=BLACK, width=2)
    return pts(main)[-1]


# ── layout D: line chart + goal pie + metrics ──────────────────────────
def render(m: dict) -> Image.Image:
    img = Image.new("1", (W, H), WHITE)
    d = ImageDraw.Draw(img)

    # Left: % of goal over time, me vs VUAA
    d.text((12, 8), "Progress to goal", font=font(14, bold=True), fill=BLACK)
    ex, ey = chart(d, 12, 40, 218, 206, m["me"], m["bench"])
    d.ellipse([ex - 3, ey - 3, ex + 3, ey + 3], fill=BLACK)
    lf = font(11)
    d.line([12, 262, 28, 262], fill=BLACK, width=2)
    d.text((32, 255), "me", font=lf, fill=BLACK)
    dashed_line(d, [(60, 262), (76, 262)])
    d.text((80, 255), "VUAA", font=lf, fill=BLACK)
    d.text((258 - tw(d, m["since"], lf), 255), m["since"], font=lf, fill=BLACK)

    d.line([270, 12, 270, 268], fill=BLACK)

    # Right: goal pie
    cx, r = 334, 46
    box = [cx - r, 16, cx + r, 16 + 2 * r]
    d.ellipse(box, outline=BLACK, width=2)
    d.pieslice(box, -90, -90 + 360 * m["goal_pct"] / 100, fill=BLACK)
    gf = font(26, bold=True)
    gs = f"{m['goal_pct']:.0f}%"
    d.text((cx - tw(d, gs, gf) / 2, 112), gs, font=gf, fill=BLACK)
    sf = font(11)
    d.text((cx - tw(d, "of goal", sf) / 2, 142), "of goal", font=sf, fill=BLACK)

    # Right: three metrics
    lab, val = font(11), font(13, bold=True)
    rows = [
        ("Month", signed(m["month_pp"], "pp")),
        ("vs VUAA", signed(m["vs_bench"])),
        ("vs plan", signed(m["vs_plan"])),
    ]
    y = 172
    d.line([280, y - 8, W - 12, y - 8], fill=BLACK)
    for label, v in rows:
        d.text((280, y + 2), label, font=lab, fill=BLACK)
        d.text((W - 12 - tw(d, v, val), y + 1), v, font=val, fill=BLACK)
        y += 30

    # Footer
    d.line([12, 276, W - 12, 276], fill=BLACK)
    ff = font(12)
    d.text((12, 282), "Path to goal", font=ff, fill=BLACK)
    s = f"upd {m['updated']}"
    d.text((W - 12 - tw(d, s, ff), 282), s, font=ff, fill=BLACK)
    return img


def to_bin(img: Image.Image) -> bytes:
    """Pack as 1 bit per pixel, MSB first, 1 = black (GxEPD2 drawBitmap format)."""
    inv = ImageOps.invert(img.convert("L")).convert("1")
    data = inv.tobytes()
    assert len(data) == W * H // 8
    return data


def main() -> None:
    m = load_metrics()
    img = render(m)
    OUT.mkdir(parents=True, exist_ok=True)
    img.save(OUT / "screen.png")
    (OUT / "screen.bin").write_bytes(to_bin(img))
    print(f"OK: goal {m['goal_pct']:.1f}%, month {m['month_pp']:+.1f}pp, "
          f"vs VUAA {m['vs_bench']:+.1f}%, vs plan {m['vs_plan']:+.1f}%")


if __name__ == "__main__":
    main()
