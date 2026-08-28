#!/usr/bin/env python3
"""Backtest footprint / absorption — MES Range 8 ticks (données synthétiques).

Exemples
--------
    python3 backtest/run_backtest.py --days 40 --seeds 5
    python3 backtest/run_backtest.py --mode null --days 40 --seeds 5
    python3 backtest/run_backtest.py --set absorption.volume_multiplier=2.2 \
                                     --set trade.target_r_multiple=1.5
    python3 backtest/run_backtest.py --sweep absorption.volume_multiplier=1.2:3.0:0.2
    python3 backtest/run_backtest.py --csv trades.csv
"""

import argparse
import csv
import os
import sys
from datetime import datetime
from typing import List, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine import metrics
from engine.backtest import Backtester, BacktestResult, Trade
from engine.bars import build_range_bars
from engine.config import Config
from engine.synthetic import generate

SETUP_LABELS = {"lvn": "Absorption LVN", "structure": "Pullback structure",
                "lvn+structure": "LVN + structure"}
TREND_LABELS = {1: "haussière", -1: "baissière", 0: "range"}


def session_break(a, b) -> bool:
    return a.ts.date() != b.ts.date()


def run_one(cfg: Config, seed: int, days: int, mode: str):
    ticks, events = generate(seed=seed, days=days, mode=mode)
    bars = build_range_bars(ticks, cfg.bar.range_ticks, session_break)
    result = Backtester(cfg).run(bars)
    return bars, events, result


def print_stats_block(title: str, groups, cfg: Config) -> None:
    print(f"\n{title}")
    print(f"  {'':<22}{metrics.HEADER}")
    for label, trades in groups:
        st = metrics.compute(trades, cfg.instrument.commission_per_side,
                             cfg.trade.contracts)
        print(f"  {label:<22}{st.as_row()}")


def report(cfg: Config, mode: str, seeds: Sequence[int], days: int,
           csv_path: str = "") -> None:
    all_trades: List[Trade] = []
    total_bars = 0
    agg = BacktestResult()
    det_prec: List[float] = []
    det_any: List[float] = []
    det_rec: List[float] = []
    ambiguous = 0

    print("=" * 96)
    print(f"BACKTEST FOOTPRINT / ABSORPTION — {cfg.instrument.symbol} Range "
          f"{cfg.bar.range_ticks} ticks — jeu « {mode} »")
    print(f"{len(seeds)} tirage(s) × {days} jours | "
          f"objectif {cfg.trade.target_r_multiple}R | "
          f"confirmation barre suivante : {'oui' if cfg.absorption.confirm_next_bar else 'non'} | "
          f"slippage {cfg.instrument.slippage_ticks} tick(s) | "
          f"commission {2*cfg.instrument.commission_per_side:.2f} $ A/R")
    print("=" * 96)

    for seed in seeds:
        bars, events, res = run_one(cfg, seed, days, mode)
        total_bars += res.bars
        agg.signals_raw += res.signals_raw
        agg.signals_confirmed += res.signals_confirmed
        agg.signals_rejected_risk += res.signals_rejected_risk
        agg.signals_skipped_busy += res.signals_skipped_busy
        ambiguous += res.ambiguous_bars
        all_trades.extend(res.trades)
        q = metrics.detection_quality(res.trades, events)
        if res.trades:
            det_prec.append(q.precision)
            det_any.append(q.precision_any)
        if q.reverting_events:
            det_rec.append(q.recall)

    st = metrics.compute(all_trades, cfg.instrument.commission_per_side, cfg.trade.contracts)

    print(f"\nDonnées      : {total_bars} barres range, ~{total_bars/max(1,len(seeds)*days):.0f} barres/jour")
    print(f"Signaux      : {agg.signals_raw} bruts | {agg.signals_confirmed} confirmés | "
          f"{agg.signals_rejected_risk} rejetés (stop hors bornes) | "
          f"{agg.signals_skipped_busy} ignorés (position/cooldown)")
    print(f"Trades       : {st.n}  ({st.n/max(1,len(seeds)*days):.2f} par jour)")
    print(f"Barres où stop ET objectif étaient touchés (ambigu) : {ambiguous}")

    if not all_trades:
        print("\nAucun trade — assouplir les filtres.")
        return

    print("\n--- GLOBAL " + "-" * 84)
    print(f"  {'':<22}{metrics.HEADER}")
    print(f"  {'toutes configs':<22}{st.as_row()}")
    print(f"\n  Gain moyen {st.avg_win_r:+.2f}R | perte moyenne {st.avg_loss_r:+.2f}R | "
          f"série perdante max {st.max_consec_losses}")
    print(f"  MAE moyen {st.avg_mae_ticks:.1f} ticks | MFE moyen {st.avg_mfe_ticks:.1f} ticks | "
          f"durée moyenne {st.avg_bars_held:.1f} barres")
    print(f"  Commissions payées : {st.commissions_usd:.2f} $ "
          f"({st.commissions_usd/abs(st.net_usd)*100 if st.net_usd else 0:.0f} % du net en valeur absolue)")

    by_setup = metrics.group_by(all_trades, lambda t: t.setup)
    print_stats_block("--- PAR SETUP " + "-" * 81,
                      [(SETUP_LABELS.get(k, k), v) for k, v in sorted(by_setup.items())], cfg)

    by_dir = metrics.group_by(all_trades, lambda t: t.direction)
    print_stats_block("--- PAR SENS " + "-" * 82,
                      [("long" if k > 0 else "short", v) for k, v in sorted(by_dir.items(), reverse=True)], cfg)

    by_trend = metrics.group_by(all_trades, lambda t: t.trend)
    print_stats_block("--- PAR CONTEXTE " + "-" * 78,
                      [(TREND_LABELS[k], v) for k, v in sorted(by_trend.items(), reverse=True)], cfg)

    by_hour = metrics.group_by(all_trades, lambda t: t.hour)
    print_stats_block("--- PAR HEURE (ET) " + "-" * 76,
                      [(f"{h:02d}h", v) for h, v in sorted(by_hour.items())], cfg)

    print("\n--- SORTIES " + "-" * 83)
    by_reason = metrics.group_by(all_trades, lambda t: t.reason)
    for reason, trades in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
        sub = metrics.compute(trades)
        print(f"  {reason:<22}{len(trades):>5} trades  net {sub.net_usd:>+10.2f} $")

    if det_prec:
        print("\n--- QUALITÉ DE DÉTECTION (vs absorptions réellement injectées) " + "-" * 32)
        print(f"  Trades dont le niveau absorbé est un vrai ordre passif injecté : "
              f"{sum(det_any)/len(det_any)*100:5.1f} %")
        print(f"  ... et dont le retournement a effectivement eu lieu              : "
              f"{sum(det_prec)/len(det_prec)*100:5.1f} %")
        if det_rec:
            print(f"  Rappel : {sum(det_rec)/len(det_rec)*100:5.1f} % des absorptions "
                  f"retournantes injectées ont été tradées")

    if csv_path:
        write_csv(csv_path, all_trades)
        print(f"\nJournal des trades écrit dans {csv_path}")


def write_csv(path: str, trades: Sequence[Trade]) -> None:
    cols = ["setup", "direction", "entry_ts", "exit_ts", "entry_t", "exit_t", "extreme_t", "stop_t",
            "target_t", "risk_ticks", "pnl_ticks", "pnl_usd", "r_multiple", "reason",
            "mae_ticks", "mfe_ticks", "trend", "lvn_strength", "absorption_ratio",
            "aggressor_share", "stacked", "hour"]
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for t in trades:
            w.writerow([getattr(t, c) for c in cols])


def sweep(cfg: Config, spec: str, seeds: Sequence[int], days: int, mode: str) -> None:
    key, _, rng = spec.partition("=")
    a, b, step = (float(x) for x in rng.split(":"))
    print(f"\nBALAYAGE {key} de {a} à {b} (pas {step}) — jeu « {mode} »")
    print(f"  {'valeur':>8} {metrics.HEADER}")
    v = a
    while v <= b + 1e-9:
        cfg.set_path(key, str(v))
        trades: List[Trade] = []
        for seed in seeds:
            trades.extend(run_one(cfg, seed, days, mode)[2].trades)
        st = metrics.compute(trades, cfg.instrument.commission_per_side, cfg.trade.contracts)
        print(f"  {v:>8.2f} {st.as_row()}")
        v += step


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=40)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--seeds", type=int, default=1, help="nombre de tirages consécutifs")
    ap.add_argument("--mode", choices=["structured", "placebo", "null", "all"],
                    default="structured")
    ap.add_argument("--set", action="append", default=[], metavar="SECTION.CLE=VALEUR")
    ap.add_argument("--sweep", default="", metavar="SECTION.CLE=DEB:FIN:PAS")
    ap.add_argument("--csv", default="")
    args = ap.parse_args(argv)

    cfg = Config()
    for item in args.set:
        key, _, val = item.partition("=")
        cfg.set_path(key.strip(), val.strip())

    seeds = list(range(args.seed, args.seed + args.seeds))
    modes = ["structured", "placebo", "null"] if args.mode == "all" else [args.mode]

    if args.sweep:
        for mode in modes:
            sweep(cfg, args.sweep, seeds, args.days, mode)
        return 0

    for mode in modes:
        report(cfg, mode, seeds, args.days, args.csv if mode == modes[0] else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
