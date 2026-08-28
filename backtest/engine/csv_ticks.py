"""Lecture d'un export de ticks réels (CSV) vers les objets Tick du moteur.

Format attendu — celui produit par `NinjaTrader8/Indicators/TickDataExporter.cs` :

    timestamp;price;volume;aggressor;bid;ask
    2025-03-14 09:31:02.417;5024.25;3;B;5024.00;5024.25

Le lecteur est tolérant : il détecte le séparateur, lit l'ordre des colonnes
dans l'en-tête, et accepte plusieurs formats d'horodatage. Les colonnes bid/ask
sont facultatives ; l'agresseur peut être absent s'il peut être déduit du
bid/ask.
"""

import csv
import gzip
import io
import os
from datetime import datetime
from typing import Iterator, List, Optional, Tuple

from .bars import BUY, SELL, Tick

FORMATS_DATE = (
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y%m%d %H%M%S %f",      # export natif NinjaTrader
    "%Y%m%d %H%M%S",
    "%d/%m/%Y %H:%M:%S.%f",
    "%m/%d/%Y %H:%M:%S.%f",
)

ALIAS = {
    "timestamp": "ts", "time": "ts", "datetime": "ts", "date": "ts", "horodatage": "ts",
    "price": "price", "prix": "price", "last": "price",
    "volume": "volume", "size": "volume", "qty": "volume", "quantity": "volume",
    "aggressor": "side", "side": "side", "agresseur": "side", "direction": "side",
    "bid": "bid", "ask": "ask", "offer": "ask",
}

ACHETEUR = {"b", "buy", "ask", "a", "1", "+1", "up", "acheteur"}
VENDEUR = {"s", "sell", "bid", "-1", "0", "down", "vendeur"}


class TickFormatError(ValueError):
    pass


def parse_ts(raw: str) -> datetime:
    raw = raw.strip()
    for fmt in FORMATS_DATE:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    raise TickFormatError(f"horodatage non reconnu : {raw!r}")


def _open(path: str):
    if path.endswith(".gz"):
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", errors="replace")
    return open(path, "r", encoding="utf-8", errors="replace", newline="")


def _sniff(header_line: str) -> str:
    for sep in (";", ",", "\t", "|"):
        if sep in header_line:
            return sep
    raise TickFormatError("séparateur de colonnes introuvable dans l'en-tête")


def _columns(header: List[str]) -> dict:
    cols = {}
    for i, name in enumerate(header):
        key = ALIAS.get(name.strip().lower().lstrip("﻿"))
        if key and key not in cols:
            cols[key] = i
    for needed in ("ts", "price", "volume"):
        if needed not in cols:
            raise TickFormatError(
                f"colonne « {needed} » absente de l'en-tête : {header}")
    if "side" not in cols and not {"bid", "ask"} <= set(cols):
        raise TickFormatError(
            "impossible de déterminer l'agresseur : il faut soit une colonne "
            "aggressor, soit les colonnes bid et ask")
    return cols


def _side(row: List[str], cols: dict, price: float) -> Optional[int]:
    if "side" in cols:
        raw = row[cols["side"]].strip().lower()
        if raw in ACHETEUR:
            return BUY
        if raw in VENDEUR:
            return SELL
        if raw not in ("?", ""):
            return None
    if "bid" in cols and "ask" in cols:
        try:
            bid = float(row[cols["bid"]])
            ask = float(row[cols["ask"]])
        except (ValueError, IndexError):
            return None
        if price >= ask:
            return BUY
        if price <= bid:
            return SELL
        # trade entre les deux : on retient le côté le plus proche
        return BUY if (price - bid) >= (ask - price) else SELL
    return None


def read_ticks(path: str, tick_size: float,
               drop_unclassified: bool = False) -> Tuple[List[Tick], dict]:
    """Retourne (ticks, statistiques). Les ticks sont triés par horodatage.

    `drop_unclassified` : par défaut les trades dont l'agresseur est inconnu
    (exécutions « mid ») sont répartis en alternance pour ne pas biaiser le
    delta ; les jeter fausserait les volumes du footprint.
    """
    stats = {"lignes": 0, "ignorees": 0, "indetermines": 0, "fichier": os.path.basename(path)}
    ticks: List[Tick] = []
    alterne = BUY

    with _open(path) as fh:
        first = fh.readline()
        if not first:
            raise TickFormatError(f"fichier vide : {path}")
        sep = _sniff(first)
        cols = _columns(next(csv.reader([first], delimiter=sep)))

        for row in csv.reader(fh, delimiter=sep):
            if not row or len(row) <= max(cols.values()):
                stats["ignorees"] += 1
                continue
            stats["lignes"] += 1
            try:
                ts = parse_ts(row[cols["ts"]])
                price = float(row[cols["price"]])
                volume = int(float(row[cols["volume"]]))
            except (ValueError, TickFormatError):
                stats["ignorees"] += 1
                continue
            if volume <= 0:
                stats["ignorees"] += 1
                continue

            side = _side(row, cols, price)
            if side is None:
                stats["indetermines"] += 1
                if drop_unclassified:
                    continue
                side, alterne = alterne, -alterne
            ticks.append(Tick(ts=ts, price_ticks=int(round(price / tick_size)),
                              size=volume, aggressor=side))

    ticks.sort(key=lambda t: t.ts)
    stats["ticks"] = len(ticks)
    if ticks:
        stats["debut"] = ticks[0].ts
        stats["fin"] = ticks[-1].ts
        stats["volume"] = sum(t.size for t in ticks)
    return ticks, stats


def read_many(paths: Iterator[str], tick_size: float,
              drop_unclassified: bool = False) -> Tuple[List[Tick], List[dict]]:
    tous: List[Tick] = []
    rapports: List[dict] = []
    for path in paths:
        ticks, stats = read_ticks(path, tick_size, drop_unclassified)
        tous.extend(ticks)
        rapports.append(stats)
    tous.sort(key=lambda t: t.ts)
    return tous, rapports
