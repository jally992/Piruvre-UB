#!/usr/bin/env python3
"""Analyse a posteriori du journal de trades : quels critères font la différence ?

    python3 backtest/analyse_trades.py backtest/resultats/trades_structured.csv
"""

import csv
import sys
from collections import defaultdict


def buckets(rows, key, edges, label):
    groups = defaultdict(list)
    for r in rows:
        v = float(r[key])
        name = f"< {edges[0]:g}"
        for lo, hi in zip(edges, edges[1:]):
            if lo <= v < hi:
                name = f"{lo:g} – {hi:g}"
                break
        else:
            if v >= edges[-1]:
                name = f"≥ {edges[-1]:g}"
        groups[name].append(r)

    order = [f"< {edges[0]:g}"] + [f"{lo:g} – {hi:g}" for lo, hi in zip(edges, edges[1:])] \
            + [f"≥ {edges[-1]:g}"]
    print(f"\n--- {label} " + "-" * max(0, 70 - len(label)))
    print(f"  {'tranche':<14}{'n':>5}{'réussite':>10}{'espérance':>12}{'net $':>12}")
    for name in order:
        g = groups.get(name)
        if not g:
            continue
        n = len(g)
        wins = sum(1 for r in g if float(r["pnl_usd"]) > 0)
        exp_r = sum(float(r["r_multiple"]) for r in g) / n
        net = sum(float(r["pnl_usd"]) for r in g)
        print(f"  {name:<14}{n:>5}{wins/n*100:>9.1f}%{exp_r:>+11.3f}R{net:>+12.2f}")


def main(path):
    with open(path) as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        print("journal vide")
        return 1
    print(f"{len(rows)} trades — {path}")
    print("Note : le simulateur ne reproduit pas fidèlement les déséquilibres diagonaux "
          "empilés ;\n       cette ligne n'est donc pas concluante et attend une "
          "validation sur données réelles.")
    buckets(rows, "absorption_ratio", [2.5, 3.5, 5.0, 8.0], "Force de l'absorption (volume zone / médiane)")
    buckets(rows, "aggressor_share", [0.50, 0.55, 0.60, 0.70], "Part du flux agressif piégé")
    buckets(rows, "lvn_strength", [0.01, 0.75, 0.90, 0.97], "Profondeur du LVN (0 = pas de LVN, 1 = niveau vierge)")
    buckets(rows, "stacked", [1, 2, 3, 5], "Déséquilibres empilés (NON exploitable sur données simulées)")
    buckets(rows, "risk_ticks", [6, 10, 14, 18], "Taille du stop (ticks)")
    buckets(rows, "mae_ticks", [-16, -12, -8, -4],
            "MAE — DESCRIPTIF, pas un filtre : inconnue au moment d'entrer")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1
                          else "backtest/resultats/trades_structured.csv"))
