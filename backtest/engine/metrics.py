"""Statistiques de performance et mesure de la qualité de détection."""

from dataclasses import dataclass
from datetime import timedelta
from typing import Dict, Iterable, List, Optional, Sequence

from .backtest import Trade
from .synthetic import AbsorptionEvent


@dataclass
class Stats:
    n: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    gross_win: float = 0.0
    gross_loss: float = 0.0
    profit_factor: float = 0.0
    net_usd: float = 0.0
    expectancy_usd: float = 0.0
    expectancy_r: float = 0.0
    avg_win_r: float = 0.0
    avg_loss_r: float = 0.0
    max_drawdown_usd: float = 0.0
    max_consec_losses: int = 0
    avg_bars_held: float = 0.0
    avg_mae_ticks: float = 0.0
    avg_mfe_ticks: float = 0.0
    commissions_usd: float = 0.0

    def as_row(self) -> str:
        return (f"{self.n:>5} {self.win_rate*100:>6.1f}% {self.profit_factor:>6.2f} "
                f"{self.expectancy_r:>+7.3f}R {self.expectancy_usd:>+8.2f}$ "
                f"{self.net_usd:>+10.2f}$ {self.max_drawdown_usd:>9.2f}$")


HEADER = (f"{'n':>5} {'gagn.':>7} {'PF':>6} {'espér.':>8} {'espér.$':>9} "
          f"{'net':>11} {'DD max':>10}")


def compute(trades: Sequence[Trade], commission_per_side: float = 0.0,
            contracts: int = 1) -> Stats:
    s = Stats(n=len(trades))
    if not trades:
        return s
    equity = peak = 0.0
    streak = 0
    for t in trades:
        equity += t.pnl_usd
        peak = max(peak, equity)
        s.max_drawdown_usd = max(s.max_drawdown_usd, peak - equity)
        if t.pnl_usd > 0:
            s.wins += 1
            s.gross_win += t.pnl_usd
            streak = 0
        else:
            s.losses += 1
            s.gross_loss += -t.pnl_usd
            streak += 1
            s.max_consec_losses = max(s.max_consec_losses, streak)
    s.net_usd = equity
    s.win_rate = s.wins / s.n
    s.profit_factor = s.gross_win / s.gross_loss if s.gross_loss > 0 else float("inf")
    s.expectancy_usd = s.net_usd / s.n
    s.expectancy_r = sum(t.r_multiple for t in trades) / s.n
    wins_r = [t.r_multiple for t in trades if t.pnl_usd > 0]
    loss_r = [t.r_multiple for t in trades if t.pnl_usd <= 0]
    s.avg_win_r = sum(wins_r) / len(wins_r) if wins_r else 0.0
    s.avg_loss_r = sum(loss_r) / len(loss_r) if loss_r else 0.0
    s.avg_bars_held = sum(t.exit_index - t.entry_index for t in trades) / s.n
    s.avg_mae_ticks = sum(t.mae_ticks for t in trades) / s.n
    s.avg_mfe_ticks = sum(t.mfe_ticks for t in trades) / s.n
    s.commissions_usd = 2 * commission_per_side * contracts * s.n
    return s


def group_by(trades: Iterable[Trade], key) -> Dict[object, List[Trade]]:
    out: Dict[object, List[Trade]] = {}
    for t in trades:
        out.setdefault(key(t), []).append(t)
    return out


@dataclass
class DetectionQuality:
    """Le détecteur retrouve-t-il les absorptions réellement injectées ?"""
    true_events: int = 0
    reverting_events: int = 0
    trades: int = 0
    trades_on_event: int = 0
    trades_on_any: int = 0
    events_caught: int = 0
    precision: float = 0.0      # part des trades pris sur une absorption SUIVIE d'un retournement
    precision_any: float = 0.0  # part des trades pris sur une absorption réelle, retournée ou non
    recall: float = 0.0         # part des absorptions retournantes effectivement tradées


def detection_quality(trades: Sequence[Trade], events: Sequence[AbsorptionEvent],
                      tick_tolerance: int = 3,
                      before_min: int = 20, after_min: int = 5) -> DetectionQuality:
    """Attribution au niveau du SIGNAL : le niveau que la stratégie a jugé
    absorbé est-il celui où un ordre passif a réellement absorbé du volume,
    pendant la barre de signal ? Tolérance serrée (3 ticks) : c'est une
    attribution causale, pas une coïncidence de zone.
    """
    q = DetectionQuality(true_events=len(events),
                         reverting_events=sum(1 for e in events if e.reverted),
                         trades=len(trades))
    if not trades or not events:
        return q
    before = timedelta(minutes=before_min)
    after = timedelta(minutes=after_min)
    caught = set()
    for t in trades:
        matched_any = False
        t0 = (t.signal_ts_open or t.entry_ts) - before
        t1 = (t.signal_ts_close or t.entry_ts) + after
        for i, e in enumerate(events):
            if abs(e.price_t - t.extreme_t) > tick_tolerance:
                continue
            if not (t0 <= e.ts <= t1):
                continue
            matched_any = True
            if e.reverted and e.direction == t.direction:
                q.trades_on_event += 1
                caught.add(i)
                break
        if matched_any:
            q.trades_on_any += 1
    q.events_caught = len(caught)
    q.precision = q.trades_on_event / q.trades
    q.precision_any = q.trades_on_any / q.trades
    q.recall = q.events_caught / q.reverting_events if q.reverting_events else 0.0
    return q
