"""Boucle de backtest : gestion des positions, exécution, journal des trades.

Hypothèses d'exécution (volontairement pessimistes) :
  * entrée au marché à l'ouverture de la barre qui suit la confirmation,
    dégradée de `slippage_ticks` ;
  * stop au marché, dégradé de `slippage_ticks` ; take-profit en limite, sans
    amélioration de prix ;
  * si stop ET objectif sont touchés dans la même barre, on suppose le stop
    (le compteur `ambiguous_bars` mesure la fréquence de ce cas) ;
  * commissions comptées à l'aller et au retour.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from .bars import FootprintBar
from .config import Config
from .profile import RollingProfile
from .signals import (LONG, SHORT, Signal, ZoneBaseline, build_signal, confirms,
                      stacked_imbalances)
from .structure import StructureTracker


@dataclass
class Trade:
    setup: str
    direction: int
    entry_index: int
    entry_ts: datetime
    entry_t: float
    stop_t: float
    target_t: float
    risk_ticks: float
    exit_index: int = 0
    exit_ts: Optional[datetime] = None
    exit_t: float = 0.0
    reason: str = ""
    pnl_ticks: float = 0.0
    pnl_usd: float = 0.0
    r_multiple: float = 0.0
    mae_ticks: float = 0.0
    mfe_ticks: float = 0.0
    trend: int = 0
    lvn_strength: float = 0.0
    absorption_ratio: float = 0.0
    aggressor_share: float = 0.0
    stacked: int = 0
    hour: int = 0
    extreme_t: int = 0          # niveau réellement absorbé (attribution)
    signal_ts_open: Optional[datetime] = None
    signal_ts_close: Optional[datetime] = None


@dataclass
class Position:
    trade: Trade
    bars_held: int = 0
    breakeven_done: bool = False


@dataclass
class BacktestResult:
    trades: List[Trade] = field(default_factory=list)
    bars: int = 0
    signals_raw: int = 0
    signals_confirmed: int = 0
    signals_rejected_risk: int = 0
    signals_skipped_busy: int = 0
    ambiguous_bars: int = 0
    lvn_count_avg: float = 0.0


def _hhmm(ts: datetime) -> int:
    return ts.hour * 100 + ts.minute


class Backtester:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.profile = RollingProfile(cfg.profile)
        self.structure = StructureTracker(cfg.structure)
        self.baseline = ZoneBaseline(cfg.absorption)
        self.result = BacktestResult()
        self.position: Optional[Position] = None
        self.pending_signal: Optional[Signal] = None   # attend confirmation
        self.pending_entry: Optional[Signal] = None    # entrée à l'ouverture suivante
        self.cooldown = 0
        self._lvn_counts: List[int] = []

    # ------------------------------------------------------------------ run
    def run(self, bars: List[FootprintBar]) -> BacktestResult:
        for i, bar in enumerate(bars):
            self.result.bars += 1
            self._fill_pending_entry(bar)
            self._manage_position(bar)
            self._handle_confirmation(bar)
            self._detect(bar)
            self.profile.add_bar(bar)
            self.structure.add_bar(bar)
            self.baseline.update(bar)
            if self.profile.ready and i % 25 == 0:
                self._lvn_counts.append(len(self.profile.lvns()))
            if self.cooldown:
                self.cooldown -= 1
        if self.position is not None:
            self._close(bars[-1], bars[-1].close_t, "fin_de_donnees")
        if self._lvn_counts:
            self.result.lvn_count_avg = sum(self._lvn_counts) / len(self._lvn_counts)
        return self.result

    # -------------------------------------------------------------- entrées
    def _fill_pending_entry(self, bar: FootprintBar) -> None:
        sig, self.pending_entry = self.pending_entry, None
        if sig is None or self.position is not None:
            return
        slip = self.cfg.instrument.slippage_ticks
        entry_t = bar.open_t + slip if sig.direction == LONG else bar.open_t - slip
        risk = abs(entry_t - sig.stop_t)
        if not (self.cfg.trade.min_stop_ticks <= risk <= self.cfg.trade.max_stop_ticks):
            self.result.signals_rejected_risk += 1
            return
        target_t = (entry_t + risk * self.cfg.trade.target_r_multiple if sig.direction == LONG
                    else entry_t - risk * self.cfg.trade.target_r_multiple)
        trade = Trade(
            setup=sig.setup, direction=sig.direction, entry_index=bar.index,
            entry_ts=bar.ts_open, entry_t=entry_t, stop_t=float(sig.stop_t),
            target_t=target_t, risk_ticks=risk, trend=sig.trend,
            lvn_strength=sig.at_lvn.strength if sig.at_lvn else 0.0,
            absorption_ratio=sig.absorption.volume_ratio,
            aggressor_share=sig.absorption.aggressor_share,
            stacked=sig.stacked,
            hour=bar.ts_open.hour,
            extreme_t=sig.absorption.extreme_t,
            signal_ts_open=sig.ts_open, signal_ts_close=sig.ts_close,
        )
        self.position = Position(trade=trade)

    # ------------------------------------------------------------- gestion
    def _manage_position(self, bar: FootprintBar) -> None:
        pos = self.position
        if pos is None:
            return
        t = pos.trade
        pos.bars_held += 1

        # excursions
        if t.direction == LONG:
            t.mae_ticks = min(t.mae_ticks, bar.low_t - t.entry_t)
            t.mfe_ticks = max(t.mfe_ticks, bar.high_t - t.entry_t)
            hit_stop = bar.low_t <= t.stop_t
            hit_target = bar.high_t >= t.target_t
        else:
            t.mae_ticks = min(t.mae_ticks, t.entry_t - bar.high_t)
            t.mfe_ticks = max(t.mfe_ticks, t.entry_t - bar.low_t)
            hit_stop = bar.high_t >= t.stop_t
            hit_target = bar.low_t <= t.target_t

        if hit_stop and hit_target:
            self.result.ambiguous_bars += 1

        slip = self.cfg.instrument.slippage_ticks
        if hit_stop:  # priorité au stop (hypothèse défavorable)
            fill = t.stop_t - slip if t.direction == LONG else t.stop_t + slip
            self._close(bar, fill, "stop" if not pos.breakeven_done else "breakeven")
            return
        if hit_target:
            self._close(bar, t.target_t, "objectif")
            return

        if _hhmm(bar.ts_close) >= self.cfg.session.flat_at_hhmm:
            self._close(bar, bar.close_t, "fin_de_seance")
            return
        if pos.bars_held >= self.cfg.trade.max_bars_in_trade:
            self._close(bar, bar.close_t, "time_stop")
            return

        # passage à breakeven
        be_r = self.cfg.trade.breakeven_at_r
        if be_r and not pos.breakeven_done:
            reached = (t.mfe_ticks >= be_r * t.risk_ticks)
            if reached:
                off = self.cfg.trade.breakeven_offset_ticks
                t.stop_t = t.entry_t + off if t.direction == LONG else t.entry_t - off
                pos.breakeven_done = True

    def _close(self, bar: FootprintBar, exit_t: float, reason: str) -> None:
        pos = self.position
        self.position = None
        t = pos.trade
        t.exit_index, t.exit_ts, t.exit_t, t.reason = bar.index, bar.ts_close, exit_t, reason
        t.pnl_ticks = (exit_t - t.entry_t) * t.direction
        inst = self.cfg.instrument
        gross = t.pnl_ticks * inst.tick_value * self.cfg.trade.contracts
        t.pnl_usd = gross - 2 * inst.commission_per_side * self.cfg.trade.contracts
        t.r_multiple = t.pnl_ticks / t.risk_ticks if t.risk_ticks else 0.0
        self.result.trades.append(t)
        self.cooldown = self.cfg.trade.cooldown_bars_after_exit

    # -------------------------------------------------------------- signaux
    def _handle_confirmation(self, bar: FootprintBar) -> None:
        sig, self.pending_signal = self.pending_signal, None
        if sig is None:
            return
        if confirms(sig, bar):
            sig.confirmed = True
            self.result.signals_confirmed += 1
            self.pending_entry = sig

    def _detect(self, bar: FootprintBar) -> None:
        cfg = self.cfg
        if not self.profile.ready:
            return
        hhmm = _hhmm(bar.ts_close)
        if not (cfg.session.start_hhmm <= hhmm <= cfg.session.end_hhmm):
            return
        sig = build_signal(bar, self.profile, self.structure, self.baseline, cfg)
        if sig is None:
            return
        self.result.signals_raw += 1
        sig.stacked = stacked_imbalances(bar, sig.direction, cfg.footprint)
        busy = (self.position is not None and cfg.trade.one_position_at_a_time) \
            or self.pending_entry is not None or self.cooldown > 0
        if busy:
            self.result.signals_skipped_busy += 1
            return
        if cfg.absorption.confirm_next_bar:
            self.pending_signal = sig
        else:
            sig.confirmed = True
            self.result.signals_confirmed += 1
            self.pending_entry = sig
