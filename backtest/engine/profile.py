"""Profil de volume glissant : POC, value area, et détection des LVN.

Un LVN (Low Volume Node) est un niveau de prix où le marché a peu échangé :
le prix y a "glissé". On les cherche comme minima locaux du profil, sous un
pourcentage du volume du POC.
"""

from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Tuple

from .bars import FootprintBar
from .config import ProfileConfig


@dataclass(frozen=True)
class Lvn:
    price_t: int
    volume: int
    strength: float  # 1 - vol/vol_poc  (1.0 = niveau totalement vierge)


class RollingProfile:
    """Profil de volume sur les `lookback_bars` dernières barres clôturées."""

    def __init__(self, cfg: ProfileConfig):
        self.cfg = cfg
        self.hist: Deque[FootprintBar] = deque()
        self.vol_at: Dict[int, int] = {}
        self._lvns: Optional[List[Lvn]] = None
        self._dirty = True

    # ---- maintenance -------------------------------------------------------
    def add_bar(self, bar: FootprintBar) -> None:
        for price_t, (b, a) in bar.ladder.items():
            self.vol_at[price_t] = self.vol_at.get(price_t, 0) + b + a
        self.hist.append(bar)
        while len(self.hist) > self.cfg.lookback_bars:
            old = self.hist.popleft()
            for price_t, (b, a) in old.ladder.items():
                left = self.vol_at.get(price_t, 0) - (b + a)
                if left > 0:
                    self.vol_at[price_t] = left
                else:
                    self.vol_at.pop(price_t, None)
        self._dirty = True

    @property
    def ready(self) -> bool:
        return len(self.hist) >= self.cfg.warmup_bars

    @property
    def bars_seen(self) -> int:
        return len(self.hist)

    # ---- statistiques ------------------------------------------------------
    def bounds(self) -> Optional[Tuple[int, int]]:
        if not self.vol_at:
            return None
        return min(self.vol_at), max(self.vol_at)

    def poc(self) -> Optional[int]:
        if not self.vol_at:
            return None
        return max(self.vol_at, key=self.vol_at.get)

    def value_area(self) -> Optional[Tuple[int, int]]:
        """(VAL, VAH) par expansion symétrique autour du POC."""
        if not self.vol_at:
            return None
        total = sum(self.vol_at.values())
        target = total * self.cfg.value_area_pct
        poc = self.poc()
        lo = hi = poc
        acc = self.vol_at.get(poc, 0)
        lo_min, hi_max = self.bounds()
        while acc < target and (lo > lo_min or hi < hi_max):
            v_down = self.vol_at.get(lo - 1, 0) if lo > lo_min else -1
            v_up = self.vol_at.get(hi + 1, 0) if hi < hi_max else -1
            if v_up >= v_down:
                hi += 1
                acc += max(v_up, 0)
            else:
                lo -= 1
                acc += max(v_down, 0)
        return lo, hi

    # ---- LVN ---------------------------------------------------------------
    def lvns(self) -> List[Lvn]:
        if self._dirty:
            self._lvns = self._scan_lvns()
            self._dirty = False
        return self._lvns

    def _scan_lvns(self) -> List[Lvn]:
        if not self.vol_at:
            return []
        lo, hi = self.bounds()
        poc_vol = self.vol_at[self.poc()]
        if poc_vol <= 0:
            return []
        threshold = poc_vol * self.cfg.lvn_max_pct_of_poc
        w = self.cfg.lvn_local_window_ticks

        # Les bords du profil ne sont pas des LVN exploitables : on les exclut.
        candidates: List[Lvn] = []
        for p in range(lo + w, hi - w + 1):
            v = self.vol_at.get(p, 0)
            if v >= threshold:
                continue
            if any(self.vol_at.get(q, 0) < v for q in range(p - w, p + w + 1)):
                continue  # pas un minimum local
            candidates.append(Lvn(price_t=p, volume=v, strength=1.0 - v / poc_vol))

        # Fusion des LVN contigus : on garde le plus creux de chaque grappe.
        merged: List[Lvn] = []
        for lvn in candidates:
            if merged and lvn.price_t - merged[-1].price_t <= self.cfg.lvn_min_separation_ticks:
                if lvn.volume < merged[-1].volume:
                    merged[-1] = lvn
                continue
            merged.append(lvn)
        return merged

    def nearest_lvn(self, price_t: int, tolerance_ticks: int) -> Optional[Lvn]:
        best = None
        for lvn in self.lvns():
            d = abs(lvn.price_t - price_t)
            if d <= tolerance_ticks and (best is None or d < abs(best.price_t - price_t)):
                best = lvn
        return best

    def lvn_touched_by(self, lo_t: int, hi_t: int, tolerance_ticks: int) -> Optional[Lvn]:
        """LVN traversé ou frôlé par l'intervalle [lo_t, hi_t] (extrême de barre)."""
        best = None
        for lvn in self.lvns():
            if lo_t - tolerance_ticks <= lvn.price_t <= hi_t + tolerance_ticks:
                if best is None or lvn.volume < best.volume:
                    best = lvn
        return best
