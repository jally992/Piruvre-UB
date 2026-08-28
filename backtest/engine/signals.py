"""Détection de l'absorption et construction des deux setups.

Setup A — « Absorption sur LVN »
    Une barre vient chercher un LVN du profil glissant. À son extrême, un fort
    volume agressif se fait absorber (le prix ne va pas plus loin) et la barre
    clôture du côté opposé -> retournement attendu depuis le LVN.

Setup B — « Pullback sur structure »
    Le marché est en tendance ; le repli vient retester un niveau structurel
    (ancien swing, ou résistance cassée devenue support). Même signature
    d'absorption au contact -> reprise attendue dans le sens de la tendance.
"""

from collections import deque
from dataclasses import dataclass
from statistics import median
from typing import Deque, Optional

from .bars import FootprintBar
from .config import AbsorptionConfig, FootprintConfig
from .profile import Lvn, RollingProfile
from .structure import Level, StructureTracker, UP, DOWN, RANGE

LONG, SHORT = 1, -1


class ZoneBaseline:
    """Médiane glissante du volume des zones extrêmes (haut et bas séparément)."""

    def __init__(self, cfg: AbsorptionConfig):
        self.cfg = cfg
        self.lows: Deque[int] = deque(maxlen=cfg.baseline_bars)
        self.highs: Deque[int] = deque(maxlen=cfg.baseline_bars)
        self.vols: Deque[int] = deque(maxlen=cfg.baseline_bars)

    def update(self, bar: FootprintBar) -> None:
        n = self.cfg.extreme_levels
        lo_b, lo_a = bar.volume_in_range(bar.low_t, bar.low_t + n - 1)
        hi_b, hi_a = bar.volume_in_range(bar.high_t - n + 1, bar.high_t)
        self.lows.append(lo_b + lo_a)
        self.highs.append(hi_b + hi_a)
        self.vols.append(bar.volume)

    @property
    def ready(self) -> bool:
        return len(self.lows) >= max(10, self.cfg.baseline_bars // 3)

    def ref_low(self) -> float:
        return max(1.0, median(self.lows))

    def ref_high(self) -> float:
        return max(1.0, median(self.highs))

    def ref_bar_volume(self) -> float:
        return max(1.0, median(self.vols))


@dataclass
class Absorption:
    direction: int          # LONG si absorption des vendeurs au plus bas
    extreme_t: int          # niveau absorbé (plus bas pour un long)
    zone_lo_t: int
    zone_hi_t: int
    zone_volume: int
    aggressor_share: float  # part du volume agressif "piégé" dans la zone
    volume_ratio: float     # volume zone / médiane glissante des zones extrêmes
    concentration: float    # volume zone / volume moyen par niveau de la barre
    bar_delta: int
    close_position: float


@dataclass
class Signal:
    bar_index: int
    ts_open: object         # horodatage de la barre de signal (attribution)
    ts_close: object
    direction: int
    absorption: Absorption
    at_lvn: Optional[Lvn]
    at_level: Optional[Level]
    trend: int
    setup: str              # 'lvn' | 'structure' | 'lvn+structure'
    stop_t: int
    stacked: int = 0        # déséquilibres empilés dans le sens du trade
    confirmed: bool = False


def detect_absorption(bar: FootprintBar, cfg: AbsorptionConfig,
                      baseline: ZoneBaseline) -> Optional[Absorption]:
    """Cherche une absorption à l'un des deux extrêmes de la barre."""
    if bar.volume < cfg.min_bar_volume or bar.n_levels < cfg.extreme_levels + 1:
        return None
    if not baseline.ready:
        return None
    if bar.volume < cfg.bar_volume_ratio * baseline.ref_bar_volume():
        return None   # barre sans activité anormale : rien à absorber

    mean_lvl = bar.mean_volume_per_level()
    if mean_lvl <= 0:
        return None
    expected = mean_lvl * cfg.extreme_levels
    cpos = bar.close_position()

    # --- absorption des vendeurs au plus bas -> signal LONG ---
    lo_hi = bar.low_t + cfg.extreme_levels - 1
    bid_v, ask_v = bar.volume_in_range(bar.low_t, lo_hi)
    zone_v = bid_v + ask_v
    if zone_v > 0:
        share = bid_v / zone_v
        ratio = zone_v / baseline.ref_low()
        conc = zone_v / expected
        if (ratio >= cfg.volume_multiplier
                and conc >= cfg.concentration_min
                and share >= cfg.delta_ratio
                and cpos >= 1.0 - cfg.close_position
                and (not cfg.require_opposite_delta or bar.delta < 0)):
            return Absorption(LONG, bar.low_t, bar.low_t, lo_hi, zone_v,
                              share, ratio, conc, bar.delta, cpos)

    # --- absorption des acheteurs au plus haut -> signal SHORT ---
    hi_lo = bar.high_t - cfg.extreme_levels + 1
    bid_v, ask_v = bar.volume_in_range(hi_lo, bar.high_t)
    zone_v = bid_v + ask_v
    if zone_v > 0:
        share = ask_v / zone_v
        ratio = zone_v / baseline.ref_high()
        conc = zone_v / expected
        if (ratio >= cfg.volume_multiplier
                and conc >= cfg.concentration_min
                and share >= cfg.delta_ratio
                and cpos <= cfg.close_position
                and (not cfg.require_opposite_delta or bar.delta > 0)):
            return Absorption(SHORT, bar.high_t, hi_lo, bar.high_t, zone_v,
                              share, ratio, conc, bar.delta, cpos)

    return None



def stacked_imbalances(bar: FootprintBar, direction: int, cfg: FootprintConfig) -> int:
    """Nombre max de déséquilibres diagonaux consécutifs dans le sens donné.

    Diagonale classique : ask[p] contre bid[p - 1 tick].
    Utilisé comme filtre de qualité optionnel / statistique de reporting.
    """
    if not bar.ladder:
        return 0
    best = run = 0
    for p in range(bar.low_t, bar.high_t + 1):
        if direction == LONG:
            up, dn = bar.ask_at(p), bar.bid_at(p - 1)
        else:
            up, dn = bar.bid_at(p), bar.ask_at(p + 1)
        hit = (up >= cfg.imbalance_min_volume
               and up >= cfg.imbalance_ratio * max(dn, 1))
        run = run + 1 if hit else 0
        best = max(best, run)
    return best


def build_signal(bar: FootprintBar, profile: RollingProfile, structure: StructureTracker,
                 baseline: ZoneBaseline, cfg) -> Optional[Signal]:
    """Assemble un signal à partir d'une absorption + contexte (LVN / structure).

    `profile` et `structure` doivent refléter l'état AVANT la barre courante.
    """
    absorption = detect_absorption(bar, cfg.absorption, baseline)
    if absorption is None:
        return None

    tol_lvn = cfg.profile.lvn_touch_tolerance_ticks
    at_lvn = None
    if cfg.setups.enable_lvn_absorption and profile.ready:
        at_lvn = profile.lvn_touched_by(absorption.zone_lo_t, absorption.zone_hi_t, tol_lvn)

    trend = structure.trend()
    at_level = None
    if cfg.setups.enable_structure_pullback:
        side = +1 if absorption.direction == LONG else -1
        aligned = (not cfg.structure.require_trend_alignment
                   or (absorption.direction == LONG and trend == UP)
                   or (absorption.direction == SHORT and trend == DOWN))
        if aligned:
            at_level = structure.level_touched(
                absorption.zone_lo_t, absorption.zone_hi_t, side)

    if at_lvn is None and at_level is None:
        return None

    setup = "lvn+structure" if (at_lvn and at_level) else ("lvn" if at_lvn else "structure")

    buf = cfg.trade.stop_buffer_ticks
    stop_t = (absorption.extreme_t - buf if absorption.direction == LONG
              else absorption.extreme_t + buf)

    return Signal(bar_index=bar.index, ts_open=bar.ts_open, ts_close=bar.ts_close,
                  direction=absorption.direction,
                  absorption=absorption, at_lvn=at_lvn, at_level=at_level,
                  trend=trend, setup=setup, stop_t=stop_t)


def confirms(signal: Signal, next_bar: FootprintBar) -> bool:
    """La barre suivante ne doit pas casser l'extrême absorbé."""
    if signal.direction == LONG:
        return next_bar.low_t >= signal.absorption.extreme_t
    return next_bar.high_t <= signal.absorption.extreme_t
