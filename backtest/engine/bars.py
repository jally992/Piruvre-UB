"""Ticks, barres Range et footprint (ladder bid/ask par niveau de prix).

Tous les prix sont manipulés en **entiers de ticks** à l'intérieur du moteur
(price_ticks = round(prix / tick_size)) : pas d'erreur de flottant sur les
comparaisons de niveaux, ce qui est indispensable pour un footprint.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Iterable, Tuple

BUY = 1    # agressif à l'achat  -> volume "ask"
SELL = -1  # agressif à la vente -> volume "bid"


@dataclass(slots=True)
class Tick:
    ts: datetime
    price_ticks: int
    size: int
    aggressor: int  # BUY / SELL


@dataclass
class FootprintBar:
    index: int
    ts_open: datetime
    ts_close: datetime
    open_t: int
    high_t: int
    low_t: int
    close_t: int
    volume: int = 0
    # niveau de prix (en ticks) -> [volume vendeur agressif, volume acheteur agressif]
    ladder: Dict[int, List[int]] = field(default_factory=dict)

    # ---- helpers footprint -------------------------------------------------
    def bid_at(self, price_t: int) -> int:
        lvl = self.ladder.get(price_t)
        return lvl[0] if lvl else 0

    def ask_at(self, price_t: int) -> int:
        lvl = self.ladder.get(price_t)
        return lvl[1] if lvl else 0

    def vol_at(self, price_t: int) -> int:
        lvl = self.ladder.get(price_t)
        return lvl[0] + lvl[1] if lvl else 0

    @property
    def delta(self) -> int:
        return sum(a - b for b, a in self.ladder.values())

    @property
    def range_t(self) -> int:
        return self.high_t - self.low_t

    @property
    def n_levels(self) -> int:
        return len(self.ladder)

    @property
    def poc_t(self) -> int:
        """Niveau de prix le plus échangé de la barre."""
        return max(self.ladder, key=lambda p: (self.vol_at(p), -abs(p - self.close_t)))

    def close_position(self) -> float:
        """0.0 = clôture sur le plus bas, 1.0 = clôture sur le plus haut."""
        if self.range_t == 0:
            return 0.5
        return (self.close_t - self.low_t) / self.range_t

    def volume_in_range(self, lo_t: int, hi_t: int) -> Tuple[int, int]:
        """(volume bid, volume ask) cumulés sur [lo_t, hi_t] inclus."""
        b = a = 0
        for p in range(lo_t, hi_t + 1):
            lvl = self.ladder.get(p)
            if lvl:
                b += lvl[0]
                a += lvl[1]
        return b, a

    def mean_volume_per_level(self) -> float:
        return self.volume / self.n_levels if self.n_levels else 0.0


class RangeBarBuilder:
    """Construit des barres Range à partir d'un flux de ticks.

    Règle appliquée : la barre se clôture dès qu'un tick ferait dépasser
    `range_ticks` de hauteur ; ce tick ouvre la barre suivante. Chaque tick
    appartient donc à exactement une barre (volumes footprint exacts, pas de
    gap artificiel). NinjaTrader force en plus la hauteur exacte de la barre
    et décale l'ouverture d'un tick : les barres y sont donc quasi identiques
    en volume mais l'open/close peut différer d'un tick. Voir docs/METHODOLOGIE.md.
    """

    def __init__(self, range_ticks: int):
        self.range_ticks = range_ticks
        self.current: Optional[FootprintBar] = None
        self._next_index = 0

    def _open_bar(self, tick: Tick) -> None:
        self.current = FootprintBar(
            index=self._next_index,
            ts_open=tick.ts,
            ts_close=tick.ts,
            open_t=tick.price_ticks,
            high_t=tick.price_ticks,
            low_t=tick.price_ticks,
            close_t=tick.price_ticks,
        )
        self._next_index += 1

    def _apply(self, tick: Tick) -> None:
        bar = self.current
        p = tick.price_ticks
        if p > bar.high_t:
            bar.high_t = p
        if p < bar.low_t:
            bar.low_t = p
        bar.close_t = p
        bar.ts_close = tick.ts
        bar.volume += tick.size
        lvl = bar.ladder.get(p)
        if lvl is None:
            lvl = [0, 0]
            bar.ladder[p] = lvl
        lvl[1 if tick.aggressor == BUY else 0] += tick.size

    def add(self, tick: Tick) -> Optional[FootprintBar]:
        """Ajoute un tick. Retourne la barre clôturée le cas échéant."""
        if self.current is None:
            self._open_bar(tick)
            self._apply(tick)
            return None

        bar = self.current
        new_high = max(bar.high_t, tick.price_ticks)
        new_low = min(bar.low_t, tick.price_ticks)
        if new_high - new_low > self.range_ticks:
            closed = bar
            self._open_bar(tick)
            self._apply(tick)
            return closed

        self._apply(tick)
        return None

    def force_close(self) -> Optional[FootprintBar]:
        """Clôture la barre en cours (fin de séance / fin de fichier)."""
        closed, self.current = self.current, None
        return closed


def build_range_bars(ticks: Iterable[Tick], range_ticks: int,
                     session_break) -> List[FootprintBar]:
    """`session_break(prev_tick, tick) -> bool` force une clôture de barre."""
    builder = RangeBarBuilder(range_ticks)
    bars: List[FootprintBar] = []
    prev: Optional[Tick] = None
    for tick in ticks:
        if prev is not None and session_break(prev, tick):
            closed = builder.force_close()
            if closed is not None:
                bars.append(closed)
        closed = builder.add(tick)
        if closed is not None:
            bars.append(closed)
        prev = tick
    closed = builder.force_close()
    if closed is not None:
        bars.append(closed)
    return bars
