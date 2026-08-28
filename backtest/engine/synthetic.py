"""Générateur de flux de ticks synthétique.

Le générateur ne dessine PAS des bougies : il simule un carnet simplifié où
chaque niveau de prix possède une *liquidité passive*. Le prix n'avance d'un
tick que lorsque le volume agressif cumulé à ce niveau dépasse cette liquidité.

Conséquences (voulues) :
  * les niveaux à forte liquidité accumulent du volume  -> HVN ;
  * les niveaux à faible liquidité sont traversés vite  -> LVN ;
  * un « iceberg » (liquidité massive et temporaire) posé sur un niveau produit
    exactement la signature d'une absorption : gros volume agressif dans un sens,
    prix qui ne passe pas, puis retournement avec probabilité `p_revert`.

Trois modes :
  * `structured` : régimes de tendance, liquidité hétérogène, icebergs posés sur
    les niveaux clés -> le marché contient réellement des absorptions ;
  * `placebo`    : EXACTEMENT le même marché (mêmes LVN, mêmes tendances, même
    relief de liquidité) mais SANS aucun iceberg. La stratégie y trouve des
    barres à gros volume sur LVN par pur hasard : c'est le témoin qui isole
    l'apport réel de l'absorption ;
  * `null`       : marche aléatoire, liquidité uniforme, aucun iceberg
                   -> ni structure ni absorption. Témoin négatif extrême.

Les icebergs suivis d'un retournement sont journalisés (`AbsorptionEvent`) :
ils servent de vérité terrain pour mesurer précision et rappel du détecteur.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from random import Random
from typing import Dict, List, Optional, Tuple

from .bars import BUY, SELL, Tick


@dataclass
class AbsorptionEvent:
    """Vérité terrain : une absorption réellement présente dans les données.

    `ts` est l'instant où l'absorption devient VISIBLE (le volume absorbé
    dépasse le seuil), pas l'instant où le prix quitte le niveau : c'est à ce
    moment-là qu'un opérateur peut la lire sur son footprint.
    """
    ts: datetime
    price_t: int
    direction: int      # sens du retournement attendu (+1 = hausse)
    volume: int
    reverted: bool


@dataclass
class GenParams:
    days: int = 40
    trades_per_day: int = 60000
    start_price: float = 5000.0
    tick_size: float = 0.25
    base_liquidity: float = 90.0     # volume agressif à consommer pour avancer d'un tick
    liquidity_noise: float = 0.75    # amplitude du relief de liquidité (0 = plat)
    liquidity_cell_ticks: int = 7    # granularité du relief
    trade_size_mean: float = 8.0
    momentum: float = 0.030          # auto-corrélation du sens des agressions
    regime_switch_p: float = 0.00012  # proba de changement de régime par trade
    drift_strength: float = 0.060    # biais directionnel en régime tendanciel
    drift_halflife_trades: int = 6000  # le régime s'essouffle tout seul
    iceberg_p: float = 0.012         # proba de poser un iceberg par mouvement de prix
    iceberg_mult: Tuple[float, float] = (12.0, 30.0)
    iceberg_revert_p: float = 0.62   # proba de retournement après absorption
    iceberg_ttl_trades: int = 600    # durée de vie une fois le niveau atteint
    iceberg_arm_timeout: int = 4000  # annulé si le prix ne revient pas le chercher
    iceberg_min_absorbed: int = 700  # volume à absorber pour que ce soit une vraie absorption
    session_open: Tuple[int, int] = (9, 30)
    session_close: Tuple[int, int] = (16, 0)


class MarketSim:
    def __init__(self, params: GenParams, seed: int, mode: str = "structured"):
        if mode not in ("structured", "placebo", "null"):
            raise ValueError("mode doit être 'structured', 'placebo' ou 'null'")
        self.p = params
        self.mode = mode
        self.rng = Random(seed)
        self._liq_cache: Dict[int, float] = {}
        self._cell_seed = self.rng.getrandbits(31)

    # ---- relief de liquidité (bruit de valeur lissé, déterministe) ----------
    def _cell_value(self, cell: int) -> float:
        r = Random((cell * 0x9E3779B1) ^ self._cell_seed)
        return r.random()

    def _liquidity(self, price_t: int) -> float:
        if self.mode == "null":
            return self.p.base_liquidity
        cached = self._liq_cache.get(price_t)
        if cached is not None:
            return cached
        cell_size = self.p.liquidity_cell_ticks
        c, frac = divmod(price_t, cell_size)
        w = frac / cell_size
        v = self._cell_value(c) * (1 - w) + self._cell_value(c + 1) * w
        # v dans [0,1] -> facteur multiplicatif centré sur 1
        factor = 1.0 + self.p.liquidity_noise * (2 * v - 1)
        val = max(6.0, self.p.base_liquidity * factor)
        self._liq_cache[price_t] = val
        return val

    # ---- génération --------------------------------------------------------
    def generate(self, start_day: datetime) -> Tuple[List[Tick], List[AbsorptionEvent]]:
        p = self.p
        ticks: List[Tick] = []
        events: List[AbsorptionEvent] = []
        price_t = int(round(p.start_price / p.tick_size))

        drift = 0.0
        last_dir = BUY
        # Pression agressive consommée, PAR NIVEAU DE PRIX et par sens : une file
        # d'ordres passifs se vide progressivement, elle ne se réinitialise pas
        # à chaque aller-retour du prix. C'est ce qui permet à un gros ordre
        # passif d'absorber des centaines de contrats visite après visite.
        pressure: Dict[Tuple[int, int], float] = {}
        iceberg_price: Optional[int] = None
        iceberg_mult = 1.0
        iceberg_ttl = 0
        iceberg_age = 0
        iceberg_vol = 0
        iceberg_seen_ts = None   # instant où l'absorption devient lisible
        iceberg_dir = 0
        recent_extremes: List[int] = []

        day = start_day
        for _ in range(p.days):
            if day.weekday() >= 5:
                day += timedelta(days=1)
                continue
            open_ts = day.replace(hour=p.session_open[0], minute=p.session_open[1],
                                  second=0, microsecond=0)
            close_ts = day.replace(hour=p.session_close[0], minute=p.session_close[1],
                                   second=0, microsecond=0)
            span = (close_ts - open_ts).total_seconds()
            drift = 0.0            # chaque séance repart sans biais hérité
            n = p.trades_per_day
            decay = 0.5 ** (1.0 / max(1, p.drift_halflife_trades))
            # cadence en U : plus dense à l'ouverture et à la clôture
            for k in range(n):
                u = k / n
                shape = u + 0.16 * (u - u * u) * (1 if u > 0.5 else -1)
                ts = open_ts + timedelta(seconds=min(span, max(0.0, shape * span)))

                drift *= decay
                if self.mode != "null" and self.rng.random() < p.regime_switch_p:
                    drift = self.rng.choice([-1.0, 0.0, 1.0]) * p.drift_strength

                bias = 0.5 + drift * 0.5 + (p.momentum * 0.5 if last_dir == BUY else -p.momentum * 0.5)
                direction = BUY if self.rng.random() < bias else SELL
                last_dir = direction
                size = 1 + int(self.rng.expovariate(1.0 / max(0.6, p.trade_size_mean - 1)))

                ticks.append(Tick(ts=ts, price_ticks=price_t, size=size, aggressor=direction))
                key = (price_t, direction)
                consumed = pressure.get(key, 0.0) + size
                pressure[key] = consumed

                liq = self._liquidity(price_t)
                attacking = False
                if iceberg_price is not None:
                    iceberg_age += 1
                    if iceberg_age > p.iceberg_arm_timeout and iceberg_price != price_t:
                        iceberg_price = None   # l'ordre passif est retiré, jamais touché
                if iceberg_price == price_t:
                    # Un ordre passif ne résiste QUE du côté où il est posé :
                    # les agressions qui viennent le chercher butent dessus,
                    # celles qui vont dans l'autre sens font partir le prix.
                    attack_dir = BUY if iceberg_dir > 0 else SELL
                    if direction == attack_dir:
                        attacking = True
                        liq *= iceberg_mult
                        iceberg_vol += size
                        if iceberg_seen_ts is None and iceberg_vol >= p.iceberg_min_absorbed:
                            iceberg_seen_ts = ts
                        iceberg_ttl -= 1
                        if iceberg_ttl <= 0:
                            iceberg_price = None   # l'iceberg se retire, épuisé

                if consumed >= liq:
                    move = 1 if direction == BUY else -1
                    if iceberg_price == price_t:
                        if attacking:
                            # l'iceberg a cédé : pas d'absorption, le prix passe
                            events.append(AbsorptionEvent(iceberg_seen_ts or ts, price_t,
                                                          -iceberg_dir, iceberg_vol, False))
                            iceberg_price = None
                        elif iceberg_vol >= p.iceberg_min_absorbed:
                            # le prix repart du niveau après y avoir absorbé du volume
                            reverted = self.rng.random() < p.iceberg_revert_p
                            events.append(AbsorptionEvent(iceberg_seen_ts or ts, price_t,
                                                          -iceberg_dir, iceberg_vol, reverted))
                            if reverted:
                                drift = -iceberg_dir * p.drift_strength * 1.6
                            iceberg_price = None
                    pressure[key] = 0.0        # la file de ce niveau a été balayée
                    price_t += move
                    recent_extremes.append(price_t)
                    del recent_extremes[:-400]
                    if len(pressure) > 4000:   # purge des niveaux devenus lointains
                        pressure = {k: v for k, v in pressure.items()
                                    if abs(k[0] - price_t) < 300}

                    if (self.mode == "structured" and iceberg_price is None
                            and self.rng.random() < p.iceberg_p
                            and len(recent_extremes) > 50):
                        target = self._pick_key_level(price_t, recent_extremes, move)
                        if target is not None:
                            iceberg_price = target
                            iceberg_dir = move
                            iceberg_mult = self.rng.uniform(*p.iceberg_mult)
                            iceberg_ttl = p.iceberg_ttl_trades
                            iceberg_age = 0
                            iceberg_vol = 0
                            iceberg_seen_ts = None
            day += timedelta(days=1)
        return ticks, events

    def _pick_key_level(self, price_t: int, extremes: List[int], move: int) -> Optional[int]:
        """Pose l'iceberg soit sur un creux de liquidité (LVN), soit sur un extrême récent."""
        if self.rng.random() < 0.5:
            lo, hi = (price_t + 2, price_t + 14) if move > 0 else (price_t - 14, price_t - 2)
            band = range(lo, hi)
            if not band:
                return None
            return min(band, key=self._liquidity)     # futur LVN
        window = extremes[-300:]
        cand = max(window) if move > 0 else min(window)
        return cand if abs(cand - price_t) <= 40 else None


def generate(seed: int = 7, days: int = 40, mode: str = "structured",
             params: Optional[GenParams] = None,
             start_day: Optional[datetime] = None):
    p = params or GenParams(days=days)
    p.days = days
    sim = MarketSim(p, seed=seed, mode=mode)
    return sim.generate(start_day or datetime(2024, 1, 2, 0, 0))
