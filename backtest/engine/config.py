"""Paramètres du backtest footprint / absorption.

Valeurs par défaut calibrées pour MES (Micro E-mini S&P 500) en Range 8 ticks.
Toutes les valeurs sont modifiables en CLI (voir run_backtest.py --set cle=valeur).
"""

from dataclasses import dataclass, field, fields, asdict


@dataclass
class InstrumentConfig:
    symbol: str = "MES"
    tick_size: float = 0.25          # 1 tick = 0.25 pt
    tick_value: float = 1.25         # $ par tick et par contrat
    commission_per_side: float = 0.62  # $ par contrat et par côté (aller OU retour)
    slippage_ticks: float = 1.0      # ticks perdus à l'entrée ET à la sortie marché


@dataclass
class BarConfig:
    range_ticks: int = 8             # hauteur de la barre range, en ticks


@dataclass
class FootprintConfig:
    """Détection des déséquilibres diagonaux (bid/ask imbalance)."""
    imbalance_ratio: float = 3.0     # ask[p] >= ratio * bid[p-1tick]
    imbalance_min_volume: int = 8    # volume plancher pour qu'un niveau compte
    stacked_min: int = 3             # nb de déséquilibres consécutifs = "stacked"


@dataclass
class ProfileConfig:
    """Profil de volume glissant -> détection des LVN."""
    lookback_bars: int = 400         # profondeur du profil glissant (barres range)
    warmup_bars: int = 150           # pas de trade avant ce nombre de barres
    lvn_max_pct_of_poc: float = 0.30  # LVN si volume < 30 % du volume du POC
    lvn_local_window_ticks: int = 3  # minimum local sur +/- N ticks
    lvn_min_separation_ticks: int = 4  # fusionne les LVN trop proches
    lvn_touch_tolerance_ticks: int = 2  # distance max barre <-> LVN pour un "contact"
    value_area_pct: float = 0.70     # pour VAH/VAL (reporting + filtre optionnel)


@dataclass
class StructureConfig:
    """Swings et pullbacks."""
    swing_left: int = 3              # barres à gauche pour valider un pivot
    swing_right: int = 3             # barres à droite (=> latence de confirmation)
    max_swings_kept: int = 40
    touch_tolerance_ticks: int = 3   # distance max barre <-> niveau structurel
    trend_swings: int = 4            # nb de swings examinés pour la tendance
    require_trend_alignment: bool = True  # pullback uniquement dans le sens du trend
    level_max_age_bars: int = 600    # un niveau structurel plus vieux est ignoré
    level_max_touches: int = 3       # au-delà, le niveau est considéré usé


@dataclass
class AbsorptionConfig:
    """Cœur du signal : absorption au contact d'un niveau.

    Attention au dénominateur : dans une barre Range, l'extrême est par
    construction MOINS échangé que le milieu de la barre (le prix n'y passe
    qu'une fois). Comparer le volume de l'extrême au volume moyen par niveau
    de la même barre ne détecte donc quasiment rien. La référence utilisée
    ici est la **médiane glissante du volume des zones extrêmes** des barres
    précédentes : on cherche un extrême anormalement chargé *par rapport à
    ce que fait habituellement le marché*.
    """
    extreme_levels: int = 3          # nb de niveaux de prix formant "l'extrême"
    baseline_bars: int = 50          # profondeur de la médiane glissante de référence
    volume_multiplier: float = 2.5   # vol. zone >= mult * médiane glissante
    concentration_min: float = 1.15  # vol. zone >= x * (vol. moyen/niveau × n niveaux)
    bar_volume_ratio: float = 1.4    # volume de barre >= x * médiane glissante
    min_bar_volume: int = 200        # barre trop maigre = pas de signal
    delta_ratio: float = 0.50        # part du volume agressif dans le sens "piégé"
    close_position: float = 0.40     # clôture dans les 40 % opposés à l'extrême
    require_opposite_delta: bool = False  # delta global contre le sens du trade
                                          # (mesuré peu discriminant, cf. docs)
    confirm_next_bar: bool = True    # la barre suivante ne casse pas l'extrême


@dataclass
class TradeConfig:
    contracts: int = 1
    stop_buffer_ticks: int = 2       # au-delà de l'extrême absorbé
    min_stop_ticks: int = 4
    max_stop_ticks: int = 24         # au-delà, signal rejeté (risque trop large)
    target_r_multiple: float = 2.0
    breakeven_at_r: float = 1.0      # 0 = désactivé
    breakeven_offset_ticks: int = 1
    max_bars_in_trade: int = 60      # time stop
    one_position_at_a_time: bool = True
    cooldown_bars_after_exit: int = 3


@dataclass
class SetupsConfig:
    enable_lvn_absorption: bool = True
    enable_structure_pullback: bool = True


@dataclass
class SessionConfig:
    """Filtre horaire, en heure de l'échange (US/Eastern dans les données générées)."""
    start_hhmm: int = 940            # pas de trade sur les 10 premières minutes
    end_hhmm: int = 1550             # ni sur la dernière 10 min
    flat_at_hhmm: int = 1555         # liquidation forcée


@dataclass
class Config:
    instrument: InstrumentConfig = field(default_factory=InstrumentConfig)
    bar: BarConfig = field(default_factory=BarConfig)
    footprint: FootprintConfig = field(default_factory=FootprintConfig)
    profile: ProfileConfig = field(default_factory=ProfileConfig)
    structure: StructureConfig = field(default_factory=StructureConfig)
    absorption: AbsorptionConfig = field(default_factory=AbsorptionConfig)
    trade: TradeConfig = field(default_factory=TradeConfig)
    setups: SetupsConfig = field(default_factory=SetupsConfig)
    session: SessionConfig = field(default_factory=SessionConfig)

    def set_path(self, dotted: str, raw: str) -> None:
        """config.set_path('absorption.volume_multiplier', '2.5')"""
        section, _, key = dotted.partition(".")
        if not key:
            raise KeyError(f"paramètre attendu sous la forme section.cle, reçu {dotted!r}")
        if section not in {f.name for f in fields(self)}:
            raise KeyError(f"section inconnue: {section}")
        sub = getattr(self, section)
        current = getattr(sub, key)  # lève AttributeError si la clé n'existe pas
        if isinstance(current, bool):
            value = raw.strip().lower() in {"1", "true", "yes", "oui", "on"}
        elif isinstance(current, int):
            value = int(raw)
        elif isinstance(current, float):
            value = float(raw)
        else:
            value = raw
        setattr(sub, key, value)

    def to_dict(self) -> dict:
        return asdict(self)
