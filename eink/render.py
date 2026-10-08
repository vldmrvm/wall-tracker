#!/usr/bin/env python3
"""
Render the 400x300 e-ink screen for the wall tracker (percent-only, no EUR shown).

Inputs : data/holdings.json (plan), data/history.json, data/benchmark.json, data/prices.json
Outputs: eink/out/screen{,1,2}.png  - previews (main, holdings, vs plan/VUAA)
         eink/out/screen{,1,2}.bin  - raw 1-bit bitmaps for the ESP32 (15000 bytes, 1 = black, MSB first)

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
    # 30-day return excluding deposits/withdrawals
    base_day = last_day - timedelta(days=30)
    base_pts = [p for p in history if date.fromisoformat(p["date"]) <= base_day]
    base = base_pts[-1] if base_pts else history[0]
    base_date = date.fromisoformat(base["date"])
    net_flows = sum(
        float(f["eur"]) for f in holdings.get("flows", [])
        if base_date < date.fromisoformat(f["date"]) <= last_day
    )
    month_ret = 100.0 * (total - base["total_eur"] - net_flows) / base["total_eur"]

    bench_now = value_on(bench, last_day)
    vs_bench = 100.0 * (total / bench_now - 1) if bench_now else 0.0
    vs_plan = 100.0 * (total / plan_value(plan, last_day) - 1)

    # Chart series: both lines as % of goal, aligned on history dates
    me = [100.0 * p["total_eur"] / target for p in history]
    vb = []
    for p in history:
        b = value_on(bench, date.fromisoformat(p["date"]))
        vb.append(100.0 * b / target if b else me[len(vb)])
    pl = [100.0 * plan_value(plan, date.fromisoformat(p["date"])) / target for p in history]

    # Deviation over time: portfolio vs plan and vs VUAA, in %
    dev_plan = [100.0 * (p["total_eur"] / plan_value(plan, date.fromisoformat(p["date"])) - 1)
                for p in history]
    dev_bench = []
    for p in history:
        b = value_on(bench, date.fromisoformat(p["date"]))
        dev_bench.append(100.0 * (p["total_eur"] / b - 1) if b else 0.0)

    # Holdings: weight in portfolio and day change
    items = [{"name": r["symbol"], "value": float(r["value_eur"]), "day": r.get("day_pct")}
             for r in prices.get("positions", [])]
    bonds_v = sum(float(b["value_eur"]) for b in prices.get("bonds", []))
    if bonds_v:
        items.append({"name": "Bonds", "value": bonds_v, "day": None})
    cash_v = float(prices.get("cash_eur") or 0)
    if cash_v:
        items.append({"name": "Cash/MMF", "value": cash_v, "day": None})
    for it in items:
        it["weight"] = 100.0 * it["value"] / total
    items.sort(key=lambda it: -it["weight"])
    # Portfolio day change from positions only (deposits do not distort it)
    day_eur = sum(it["value"] * it["day"] / (100 + it["day"]) for it in items if it["day"] is not None)
    day_ret = 100.0 * day_eur / (total - day_eur)

    updated = prices.get("updated_utc", history[-1]["date"])
    upd = datetime.fromisoformat(updated.replace("Z", "+00:00")).strftime("%d.%m.%Y")

    return {
        "goal_pct": min(goal_pct, 100.0),
        "month_ret": month_ret,
        "vs_bench": vs_bench,
        "vs_plan": vs_plan,
        "me": me,
        "bench": vb,
        "plan": pl,
        "since": date.fromisoformat(history[0]["date"]).strftime("since %b %Y"),
        "updated": upd,
        "dev_plan": dev_plan,
        "dev_bench": dev_bench,
        "dates": [p["date"] for p in history],
        "items": items,
        "day_ret": day_ret,
    }


# ── drawing helpers ────────────────────────────────────────────────────
def signed(v: float, unit: str = "%") -> str:
    if abs(v) < 0.05:
        v = 0.0  # avoid "-0.0"
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


def chart(d, x, y, w, h, main, dashed, thin):
    lo, hi = min(main + dashed + thin), max(main + dashed + thin)
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
    d.line(pts(thin), fill=BLACK, width=1)  # plan: thin solid
    dashed_line(d, pts(dashed))             # benchmark: dashed
    d.line(pts(main), fill=BLACK, width=2)  # portfolio: thick solid
    return pts(main)[-1]


# ── layout D: line chart + goal pie + metrics ──────────────────────────
def render(m: dict) -> Image.Image:
    img = Image.new("1", (W, H), WHITE)
    d = ImageDraw.Draw(img)

    # Left: % of goal over time, me vs VUAA
    d.text((12, 8), "Progress to goal", font=font(14, bold=True), fill=BLACK)
    ex, ey = chart(d, 12, 40, 218, 206, m["me"], m["bench"], m["plan"])
    d.ellipse([ex - 3, ey - 3, ex + 3, ey + 3], fill=BLACK)
    lf = font(11)
    d.line([12, 262, 28, 262], fill=BLACK, width=2)
    d.text((32, 255), "me", font=lf, fill=BLACK)
    dashed_line(d, [(60, 262), (76, 262)])
    d.text((80, 255), "VUAA", font=lf, fill=BLACK)
    d.line([118, 262, 134, 262], fill=BLACK, width=1)
    d.text((138, 255), "plan", font=lf, fill=BLACK)
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
        ("Month", signed(m["month_ret"])),
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


# ── screen 1: holdings ─────────────────────────────────────────────────
def render_positions(m: dict) -> Image.Image:
    img = Image.new("1", (W, H), WHITE)
    d = ImageDraw.Draw(img)
    d.text((12, 8), "Holdings", font=font(14, bold=True), fill=BLACK)
    hf = font(11)
    s = "today " + signed(m["day_ret"])
    d.text((W - 12 - tw(d, s, font(13, bold=True)), 8), s, font=font(13, bold=True), fill=BLACK)
    d.text((W - 12 - tw(d, "day", hf), 32), "day", font=hf, fill=BLACK)
    d.text((262 - tw(d, "weight", hf), 32), "weight", font=hf, fill=BLACK)

    items = m["items"][:15]
    top, bottom = 50, 270
    step = (bottom - top) / max(len(items), 1)
    step = min(step, 18)
    max_w = max(it["weight"] for it in items)
    lf, vf = font(12), font(12, bold=True)
    bar_x, bar_w = 90, 120
    for i, it in enumerate(items):
        y = top + i * step
        d.text((12, y), it["name"], font=lf, fill=BLACK)
        bw = bar_w * it["weight"] / max_w
        d.rectangle([bar_x, y + 3, bar_x + bw, y + step - 5], fill=BLACK)
        ws = f"{it['weight']:.0f}%"
        d.text((262 - tw(d, ws, lf), y), ws, font=lf, fill=BLACK)
        ds = signed(it["day"]) if it["day"] is not None else "–"
        d.text((W - 12 - tw(d, ds, vf), y), ds, font=vf, fill=BLACK)

    d.line([12, 276, W - 12, 276], fill=BLACK)
    ff = font(12)
    s = f"upd {m['updated']}"
    d.text((W - 12 - tw(d, s, ff), 282), s, font=ff, fill=BLACK)
    return img


# ── screen 2: deviation from plan and VUAA ─────────────────────────────
def render_deviation(m: dict) -> Image.Image:
    img = Image.new("1", (W, H), WHITE)
    d = ImageDraw.Draw(img)
    d.text((12, 8), "Me vs plan / VUAA", font=font(14, bold=True), fill=BLACK)

    big = font(18, bold=True)
    lab = font(11)
    for right, label, v in ((300, "vs plan", m["vs_plan"]), (W - 12, "vs VUAA", m["vs_bench"])):
        s = signed(v)
        d.text((right - tw(d, s, big), 6), s, font=big, fill=BLACK)
        d.text((right - tw(d, label, lab), 30), label, font=lab, fill=BLACK)

    x0, y0, w, h = 12, 60, 340, 190
    plan_s, bench_s = m["dev_plan"], m["dev_bench"]
    lo = min(plan_s + bench_s + [0.0])
    hi = max(plan_s + bench_s + [0.0])
    pad = max((hi - lo) * 0.08, 0.5)
    lo, hi = lo - pad, hi + pad
    n = max(len(plan_s) - 1, 1)

    def yy(v):
        return y0 + h - h * (v - lo) / (hi - lo)

    def pts(series):
        return [(x0 + w * i / n, yy(v)) for i, v in enumerate(series)]

    # zero line = on plan / equal to VUAA
    d.line([x0, yy(0), x0 + w, yy(0)], fill=BLACK)
    d.text((x0 + w + 4, yy(0) - 6), "0%", font=lab, fill=BLACK)
    for v in (hi - pad, lo + pad):
        if abs(yy(v) - yy(0)) > 14:
            gy = yy(v)
            for gx in range(x0, x0 + w, 4):
                d.point((gx, gy), fill=BLACK)
            d.text((x0 + w + 4, gy - 6), f"{v:+.0f}%".replace("-", "−"), font=lab, fill=BLACK)
    d.line(pts(plan_s), fill=BLACK, width=2)
    dashed_line(d, pts(bench_s))

    d.line([12, 262, 28, 262], fill=BLACK, width=2)
    d.text((32, 255), "vs plan", font=lab, fill=BLACK)
    dashed_line(d, [(90, 262), (106, 262)])
    d.text((110, 255), "vs VUAA", font=lab, fill=BLACK)
    d.text((x0 + w - tw(d, m["since"], lab), 255), m["since"], font=lab, fill=BLACK)

    d.line([12, 276, W - 12, 276], fill=BLACK)
    ff = font(12)
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
    OUT.mkdir(parents=True, exist_ok=True)
    # screen.bin is the main screen; screen1/2 are opened with NFC tags
    screens = {"screen": render(m), "screen1": render_positions(m), "screen2": render_deviation(m)}
    for name, img in screens.items():
        img.save(OUT / f"{name}.png")
        (OUT / f"{name}.bin").write_bytes(to_bin(img))
    print(f"OK: goal {m['goal_pct']:.1f}%, month {m['month_ret']:+.1f}%, "
          f"vs VUAA {m['vs_bench']:+.1f}%, vs plan {m['vs_plan']:+.1f}%")


if __name__ == "__main__":
    main()
