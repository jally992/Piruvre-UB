"""Structure de marché : pivots (swings), tendance, niveaux pour les pullbacks.

Un pivot n'est confirmé qu'après `swing_right` barres : le moteur ne l'utilise
donc jamais avant qu'il soit réellement connaissable (pas de look-ahead).
"""

from dataclasses import dataclass, field
from typing import List, Optional

from .bars import FootprintBar
from .config import StructureConfig

UP, DOWN, RANGE = 1, -1, 0


@dataclass
class Swing:
    index: int         # index de la barre pivot
    price_t: int
    kind: int          # +1 = swing high, -1 = swing low
    confirmed_at: int  # index de barre à partir duquel il est utilisable


@dataclass
class Level:
    """Niveau structurel exploitable pour un retest."""
    price_t: int
    side: int          # +1 = support (on achète dessus), -1 = résistance
    origin: str        # 'swing_low' | 'broken_high' | 'swing_high' | 'broken_low'
    created_at: int
    touches: int = 0
    broken: bool = False


class StructureTracker:
    def __init__(self, cfg: StructureConfig):
        self.cfg = cfg
        self.bars: List[FootprintBar] = []
        self.swings: List[Swing] = []
        self.levels: List[Level] = []

    # ---- alimentation ------------------------------------------------------
    def add_bar(self, bar: FootprintBar) -> None:
        self.bars.append(bar)
        self._detect_pivot()
        self._update_levels(bar)

    def _detect_pivot(self) -> None:
        left, right = self.cfg.swing_left, self.cfg.swing_right
        i = len(self.bars) - 1 - right          # barre candidate
        if i < left:
            return
        cand = self.bars[i]
        window_left = self.bars[i - left:i]
        window_right = self.bars[i + 1:i + 1 + right]
        now = len(self.bars) - 1

        if (all(cand.high_t > b.high_t for b in window_left)
                and all(cand.high_t >= b.high_t for b in window_right)):
            self._push_swing(Swing(i, cand.high_t, +1, now))
        if (all(cand.low_t < b.low_t for b in window_left)
                and all(cand.low_t <= b.low_t for b in window_right)):
            self._push_swing(Swing(i, cand.low_t, -1, now))

    def _push_swing(self, swing: Swing) -> None:
        self.swings.append(swing)
        del self.swings[:-self.cfg.max_swings_kept]
        # Un swing low devient un support, un swing high une résistance.
        self.levels.append(Level(
            price_t=swing.price_t,
            side=+1 if swing.kind == -1 else -1,
            origin="swing_low" if swing.kind == -1 else "swing_high",
            created_at=swing.confirmed_at,
        ))
        self._prune_levels(swing.confirmed_at)

    def _update_levels(self, bar: FootprintBar) -> None:
        """Une résistance cassée par le haut devient un support (et inversement)."""
        idx = len(self.bars) - 1
        for lvl in self.levels:
            if lvl.broken:
                continue
            if lvl.side == -1 and bar.close_t > lvl.price_t + self.cfg.touch_tolerance_ticks:
                lvl.broken = True
                self.levels.append(Level(lvl.price_t, +1, "broken_high", idx))
            elif lvl.side == +1 and bar.close_t < lvl.price_t - self.cfg.touch_tolerance_ticks:
                lvl.broken = True
                self.levels.append(Level(lvl.price_t, -1, "broken_low", idx))
        self._prune_levels(idx)

    def _prune_levels(self, now: int) -> None:
        max_age = self.cfg.level_max_age_bars
        self.levels = [l for l in self.levels
                       if not l.broken
                       and now - l.created_at <= max_age
                       and l.touches < self.cfg.level_max_touches]

    # ---- lecture -----------------------------------------------------------
    def trend(self) -> int:
        highs = [s for s in self.swings if s.kind == +1][-self.cfg.trend_swings:]
        lows = [s for s in self.swings if s.kind == -1][-self.cfg.trend_swings:]
        if len(highs) < 2 or len(lows) < 2:
            return RANGE
        hh = highs[-1].price_t > highs[-2].price_t
        hl = lows[-1].price_t > lows[-2].price_t
        lh = highs[-1].price_t < highs[-2].price_t
        ll = lows[-1].price_t < lows[-2].price_t
        if hh and hl:
            return UP
        if lh and ll:
            return DOWN
        return RANGE

    def level_touched(self, lo_t: int, hi_t: int, side: int) -> Optional[Level]:
        """Niveau du côté `side` touché par l'extrême de barre [lo_t, hi_t]."""
        tol = self.cfg.touch_tolerance_ticks
        best: Optional[Level] = None
        for lvl in self.levels:
            if lvl.side != side or lvl.broken:
                continue
            if lo_t - tol <= lvl.price_t <= hi_t + tol:
                if best is None or abs(lvl.price_t - (lo_t if side > 0 else hi_t)) < \
                        abs(best.price_t - (lo_t if side > 0 else hi_t)):
                    best = lvl
        return best

    def last_swing(self, kind: int) -> Optional[Swing]:
        for s in reversed(self.swings):
            if s.kind == kind:
                return s
        return None
