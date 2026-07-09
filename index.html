#!/usr/bin/env python3
"""
Тянет цены с Yahoo Finance, считает стоимость портфеля в EUR,
пишет data/prices.json и добавляет точку в data/history.json (одна на день,
последнее значение дня побеждает).

Запуск: python scripts/update_prices.py
Зависимости: pip install yfinance
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

HOLDINGS_F = DATA / "holdings.json"
PRICES_F = DATA / "prices.json"
HISTORY_F = DATA / "history.json"


# ── котировки ──────────────────────────────────────────────────────────
def fetch_quotes(tickers: list[str]) -> dict[str, float]:
    """Последняя цена по каждому тикеру. Падает по одному тикеру — не падает весь скрипт."""
    import yfinance as yf

    out: dict[str, float] = {}
    for t in tickers:
        try:
            h = yf.Ticker(t).history(period="5d")
            if len(h):
                out[t] = float(h["Close"].iloc[-1])
            else:
                print(f"WARN: нет данных по {t}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"WARN: {t}: {e}", file=sys.stderr)
    return out


def to_eur(price: float, ticker: str, fx: dict[str, float]) -> float:
    """Конвертация в EUR. AZN.L котируется в пенсах (GBp)."""
    if ticker.endswith(".PA") or ticker.endswith(".DE") or ticker.endswith(".AS"):
        return price
    if ticker.endswith(".L"):
        gbp = price / 100.0  # пенсы -> фунты
        return gbp * fx["GBPEUR"]
    # всё остальное считаем USD-листингом
    return price / fx["EURUSD"]


# ── облигации: номинал + накопленный купон ─────────────────────────────
def last_coupon_date(today: date, months: list[int], day: int) -> date:
    candidates = []
    for y in (today.year - 1, today.year):
        for m in months:
            try:
                d = date(y, m, day)
            except ValueError:
                continue
            if d <= today:
                candidates.append(d)
    return max(candidates)


def bond_value(bond: dict, today: date) -> float:
    nominal = float(bond["nominal_eur"])
    rate = float(bond["coupon_rate_pct"]) / 100.0
    last = last_coupon_date(today, bond["coupon_months"], bond["coupon_day"])
    accrued = nominal * rate * (today - last).days / 365.0
    return nominal + accrued


# ── main ───────────────────────────────────────────────────────────────
def main() -> None:
    holdings = json.loads(HOLDINGS_F.read_text(encoding="utf-8"))
    today = datetime.now(timezone.utc)

    tickers = [p["yahoo"] for p in holdings["positions"]]
    quotes = fetch_quotes(tickers + ["EURUSD=X", "GBPEUR=X"])

    fx = {
        "EURUSD": quotes.get("EURUSD=X", 1.10),
        "GBPEUR": quotes.get("GBPEUR=X", 1.15),
    }

    rows = []
    total = 0.0
    stale = []

    # если тикер не ответил — берём прошлую цену из prices.json, чтобы не «терять» позицию
    prev = {}
    if PRICES_F.exists():
        try:
            prev = {r["symbol"]: r for r in json.loads(PRICES_F.read_text())["positions"]}
        except Exception:  # noqa: BLE001
            prev = {}

    for p in holdings["positions"]:
        t, sym, qty = p["yahoo"], p["symbol"], float(p["qty"])
        if t in quotes:
            price_eur = to_eur(quotes[t], t, fx)
        elif sym in prev:
            price_eur = prev[sym]["price_eur"]
            stale.append(sym)
        else:
            print(f"WARN: пропускаю {sym} — нет ни свежей, ни старой цены", file=sys.stderr)
            continue
        value = price_eur * qty
        total += value
        rows.append({
            "symbol": sym, "name": p["name"], "qty": qty,
            "price_eur": round(price_eur, 2), "value_eur": round(value, 2),
            "stale": sym in stale,
        })

    bond_rows = []
    for b in holdings["bonds"]:
        v = bond_value(b, today.date())
        total += v
        bond_rows.append({"name": b["name"], "value_eur": round(v, 2)})

    cash = float(holdings["cash"]["mmf_eur"])
    total += cash

    prices = {
        "updated_utc": today.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fx": {k: round(v, 5) for k, v in fx.items()},
        "positions": rows,
        "bonds": bond_rows,
        "cash_eur": round(cash, 2),
        "total_eur": round(total, 2),
        "stale_symbols": stale,
    }
    PRICES_F.write_text(json.dumps(prices, ensure_ascii=False, indent=2), encoding="utf-8")

    # история: одна точка на день
    history = []
    if HISTORY_F.exists():
        try:
            history = json.loads(HISTORY_F.read_text())
        except Exception:  # noqa: BLE001
            history = []
    today_s = today.strftime("%Y-%m-%d")
    history = [h for h in history if h["date"] != today_s]
    history.append({"date": today_s, "total_eur": round(total, 2)})
    history.sort(key=lambda h: h["date"])
    HISTORY_F.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"OK: total €{total:,.0f} ({len(rows)} акций, {len(bond_rows)} бондов, кэш €{cash:,.0f})")
    if stale:
        print(f"Устаревшие цены: {', '.join(stale)}", file=sys.stderr)


if __name__ == "__main__":
    main()
