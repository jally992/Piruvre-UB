"""Tests du moteur. Exécution : python3 -m unittest discover -s backtest/tests -v"""

import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from engine.backtest import Backtester
from engine.bars import BUY, SELL, FootprintBar, RangeBarBuilder, Tick, build_range_bars
from engine.config import Config
from engine.profile import RollingProfile
from engine.signals import LONG, SHORT, ZoneBaseline, detect_absorption, stacked_imbalances
from engine.csv_ticks import TickFormatError, read_ticks
from engine.structure import StructureTracker
from engine.synthetic import generate

T0 = datetime(2024, 1, 2, 10, 0)


def mk_ticks(seq, start=T0, step=1):
    """seq = [(prix_ticks, taille, agresseur), ...]"""
    return [Tick(start + timedelta(seconds=i * step), p, s, a)
            for i, (p, s, a) in enumerate(seq)]


def mk_bar(index, ladder, ts=T0, open_t=None, close_t=None):
    lo, hi = min(ladder), max(ladder)
    bar = FootprintBar(index=index, ts_open=ts, ts_close=ts + timedelta(minutes=1),
                       open_t=open_t if open_t is not None else lo,
                       high_t=hi, low_t=lo,
                       close_t=close_t if close_t is not None else hi)
    for price, (b, a) in ladder.items():
        bar.ladder[price] = [b, a]
        bar.volume += b + a
    return bar


class TestRangeBars(unittest.TestCase):
    def test_hauteur_bornee_et_volume_conserve(self):
        ticks, _ = generate(seed=11, days=2)
        bars = build_range_bars(ticks, 8, lambda a, b: a.ts.date() != b.ts.date())
        self.assertGreater(len(bars), 50)
        for bar in bars:
            self.assertLessEqual(bar.range_t, 8, "une barre dépasse le range demandé")
        self.assertEqual(sum(b.volume for b in bars), sum(t.size for t in ticks),
                         "des ticks ont été perdus ou comptés deux fois")

    def test_index_continu(self):
        ticks, _ = generate(seed=12, days=2)
        bars = build_range_bars(ticks, 8, lambda a, b: a.ts.date() != b.ts.date())
        self.assertEqual([b.index for b in bars], list(range(len(bars))))

    def test_cloture_de_seance(self):
        ticks = mk_ticks([(100, 1, BUY), (101, 1, BUY)])
        ticks += mk_ticks([(101, 1, BUY)], start=T0 + timedelta(days=1))
        bars = build_range_bars(ticks, 8, lambda a, b: a.ts.date() != b.ts.date())
        self.assertEqual(len(bars), 2, "la barre doit être coupée au changement de séance")

    def test_ladder_bid_ask(self):
        builder = RangeBarBuilder(8)
        for t in mk_ticks([(100, 5, SELL), (100, 3, BUY), (101, 2, BUY)]):
            builder.add(t)
        bar = builder.force_close()
        self.assertEqual(bar.bid_at(100), 5)
        self.assertEqual(bar.ask_at(100), 3)
        self.assertEqual(bar.delta, (3 - 5) + 2)
        self.assertEqual(bar.poc_t, 100)
        self.assertEqual(bar.volume, 10)


class TestProfile(unittest.TestCase):
    def _profile(self, vols):
        cfg = Config().profile
        cfg.warmup_bars = 1
        cfg.lvn_local_window_ticks = 2
        prof = RollingProfile(cfg)
        for i, (price, v) in enumerate(vols):
            prof.add_bar(mk_bar(i, {price: [v // 2, v - v // 2]}))
        return prof

    def test_lvn_sur_un_creux_de_volume(self):
        vols = [(p, 100) for p in range(0, 10)]
        vols += [(p, 5) for p in range(10, 13)]      # creux marqué
        vols += [(p, 100) for p in range(13, 23)]
        prof = self._profile(vols)
        lvns = [l.price_t for l in prof.lvns()]
        self.assertTrue(any(10 <= p <= 12 for p in lvns), f"LVN non détecté : {lvns}")
        self.assertNotIn(5, lvns, "un HVN ne doit pas être signalé comme LVN")

    def test_poc_et_value_area(self):
        prof = self._profile([(p, 10) for p in range(0, 20)] + [(10, 500)])
        self.assertEqual(prof.poc(), 10)
        val, vah = prof.value_area()
        self.assertLessEqual(val, 10)
        self.assertGreaterEqual(vah, 10)

    def test_eviction_du_lookback(self):
        cfg = Config().profile
        cfg.lookback_bars = 3
        cfg.warmup_bars = 1
        prof = RollingProfile(cfg)
        for i in range(10):
            prof.add_bar(mk_bar(i, {i: [10, 10]}))
        self.assertEqual(prof.bars_seen, 3)
        self.assertEqual(sorted(prof.vol_at), [7, 8, 9], "le profil garde des barres évincées")


class TestStructure(unittest.TestCase):
    def test_pivot_detecte_et_confirme_en_retard(self):
        cfg = Config().structure
        cfg.swing_left = cfg.swing_right = 2
        tracker = StructureTracker(cfg)
        prices = [10, 11, 15, 11, 10, 9, 8]       # sommet à l'index 2
        for i, p in enumerate(prices):
            tracker.add_bar(mk_bar(i, {p: [5, 5]}))
        highs = [s for s in tracker.swings if s.kind == +1]
        self.assertTrue(highs, "aucun swing high détecté")
        self.assertEqual(highs[0].index, 2)
        self.assertEqual(highs[0].confirmed_at, 4,
                         "le pivot ne doit pas être connu avant swing_right barres")

    def test_tendance_haussiere(self):
        cfg = Config().structure
        cfg.swing_left = cfg.swing_right = 1
        tracker = StructureTracker(cfg)
        for i, p in enumerate([10, 8, 12, 9, 14, 11, 17, 20]):
            tracker.add_bar(mk_bar(i, {p: [5, 5]}))
        self.assertEqual(tracker.trend(), 1)


class TestAbsorption(unittest.TestCase):
    def _baseline(self, cfg, level_volume=100):
        bl = ZoneBaseline(cfg.absorption)
        for i in range(cfg.absorption.baseline_bars):
            bl.update(mk_bar(i, {p: [level_volume // 2, level_volume // 2]
                                 for p in range(100, 109)}))
        return bl

    def test_absorption_acheteuse_au_plus_bas(self):
        cfg = Config()
        bl = self._baseline(cfg)
        ladder = {p: [50, 50] for p in range(101, 109)}
        ladder[100] = [900, 120]      # vendeurs massivement absorbés sur le plus bas
        bar = mk_bar(999, ladder, close_t=108)
        found = detect_absorption(bar, cfg.absorption, bl)
        self.assertIsNotNone(found)
        self.assertEqual(found.direction, LONG)
        self.assertEqual(found.extreme_t, 100)
        self.assertGreater(found.aggressor_share, 0.6)

    def test_absorption_vendeuse_au_plus_haut(self):
        cfg = Config()
        bl = self._baseline(cfg)
        ladder = {p: [50, 50] for p in range(100, 108)}
        ladder[108] = [120, 900]
        bar = mk_bar(999, ladder, close_t=100)
        found = detect_absorption(bar, cfg.absorption, bl)
        self.assertIsNotNone(found)
        self.assertEqual(found.direction, SHORT)
        self.assertEqual(found.extreme_t, 108)

    def test_pas_de_signal_sans_anomalie_de_volume(self):
        cfg = Config()
        bl = self._baseline(cfg)
        bar = mk_bar(999, {p: [50, 50] for p in range(100, 109)}, close_t=108)
        self.assertIsNone(detect_absorption(bar, cfg.absorption, bl))

    def test_pas_de_signal_si_cloture_du_mauvais_cote(self):
        cfg = Config()
        bl = self._baseline(cfg)
        ladder = {p: [50, 50] for p in range(101, 109)}
        ladder[100] = [900, 120]
        bar = mk_bar(999, ladder, close_t=100)   # clôture SUR le plus bas
        self.assertIsNone(detect_absorption(bar, cfg.absorption, bl))

    def test_stacked_imbalances(self):
        cfg = Config().footprint
        ladder = {100: [10, 10], 101: [2, 60], 102: [2, 60], 103: [2, 60]}
        bar = mk_bar(1, ladder, close_t=103)
        self.assertGreaterEqual(stacked_imbalances(bar, LONG, cfg), 3)
        self.assertEqual(stacked_imbalances(bar, SHORT, cfg), 0)


class TestExecution(unittest.TestCase):
    """Vérifie le compte exact d'un trade gagnant et la règle du stop prioritaire."""

    def _run_with_trade(self, bars, cfg):
        bt = Backtester(cfg)
        return bt.run(bars)

    def test_pnl_dun_objectif_atteint(self):
        """Un objectif touché doit rapporter exactement 2R moins les commissions."""
        cfg = Config()
        cfg.trade.breakeven_at_r = 0        # on isole le calcul du take-profit
        bt = Backtester(cfg)
        from engine.backtest import Position, Trade
        trade = Trade(setup="test", direction=LONG, entry_index=0, entry_ts=T0,
                      entry_t=100.0, stop_t=94.0, target_t=112.0, risk_ticks=6.0)
        bt.position = Position(trade=trade)
        bt._manage_position(mk_bar(1, {p: [10, 10] for p in range(99, 115)}))
        done = bt.result.trades[0]
        self.assertEqual(done.reason, "objectif")
        self.assertAlmostEqual(done.pnl_ticks, 12.0)
        self.assertAlmostEqual(done.r_multiple, 2.0)
        self.assertAlmostEqual(done.pnl_usd, 12 * 1.25 - 1.24)

    def test_stop_prioritaire_si_les_deux_sont_touches(self):
        cfg = Config()
        cfg.absorption.confirm_next_bar = False
        bt = Backtester(cfg)
        from engine.backtest import Position, Trade
        trade = Trade(setup="test", direction=LONG, entry_index=0, entry_ts=T0,
                      entry_t=100.0, stop_t=96.0, target_t=108.0, risk_ticks=4.0)
        bt.position = Position(trade=trade)
        bar = mk_bar(1, {p: [10, 10] for p in range(95, 110)})
        bt._manage_position(bar)
        self.assertEqual(bt.result.trades[0].reason, "stop")
        self.assertEqual(bt.result.ambiguous_bars, 1)
        self.assertLess(bt.result.trades[0].pnl_usd, 0)


class TestPasDeLookAhead(unittest.TestCase):
    """Test central : rejouer un préfixe des barres doit donner EXACTEMENT les
    mêmes trades que le backtest complet tronqué. Toute utilisation d'une
    information future ferait diverger les deux."""

    def test_prefixe_identique(self):
        cfg = Config()
        ticks, _ = generate(seed=21, days=12)
        bars = build_range_bars(ticks, 8, lambda a, b: a.ts.date() != b.ts.date())
        complet = Backtester(cfg).run(bars)
        coupe = int(len(bars) * 0.6)
        partiel = Backtester(cfg).run(bars[:coupe])
        self.assertGreater(len(partiel.trades), 3, "échantillon trop petit pour conclure")
        for a, b in zip(partiel.trades, complet.trades):
            if a.exit_index >= coupe - 1:
                break     # trade tronqué par la fin des données : normal
            self.assertEqual((a.entry_index, a.entry_t, a.stop_t, a.exit_index, a.reason),
                             (b.entry_index, b.entry_t, b.stop_t, b.exit_index, b.reason))


class TestLectureCsvReel(unittest.TestCase):
    """Chemin des données réelles : export NinjaTrader -> ticks du moteur."""

    def _fichier(self, contenu):
        import tempfile
        chemin = os.path.join(tempfile.mkdtemp(), "ticks.csv")
        with open(chemin, "w") as fh:
            fh.write(contenu)
        return chemin

    EXPORT = ("timestamp;price;volume;aggressor;bid;ask\n"
              "2025-03-14 09:31:02.417;5024.25;3;B;5024.00;5024.25\n"
              "2025-03-14 09:31:02.980;5024.00;7;S;5024.00;5024.25\n")

    def test_lecture_format_exporteur(self):
        ticks, stats = read_ticks(self._fichier(self.EXPORT), 0.25)
        self.assertEqual(stats["ticks"], 2)
        self.assertEqual([t.aggressor for t in ticks], [BUY, SELL])
        self.assertEqual([t.price_ticks for t in ticks], [20097, 20096])
        self.assertEqual([t.size for t in ticks], [3, 7])

    def test_agresseur_deduit_du_bid_ask(self):
        contenu = ("time,price,size,bid,ask\n"
                   "2025-03-14 09:31:02,5024.25,3,5024.00,5024.25\n"
                   "2025-03-14 09:31:03,5024.00,4,5024.00,5024.25\n")
        ticks, _ = read_ticks(self._fichier(contenu), 0.25)
        self.assertEqual([t.aggressor for t in ticks], [BUY, SELL],
                         "sans colonne agresseur, il doit être déduit du bid/ask")

    def test_ticks_tries_par_horodatage(self):
        contenu = ("timestamp;price;volume;aggressor\n"
                   "2025-03-14 09:31:05.000;5024.25;1;B\n"
                   "2025-03-14 09:31:01.000;5024.00;1;S\n")
        ticks, _ = read_ticks(self._fichier(contenu), 0.25)
        self.assertLess(ticks[0].ts, ticks[1].ts)

    def test_lignes_illisibles_ignorees_sans_planter(self):
        contenu = self.EXPORT + "ligne;cassée\n2025-03-14 09:31:04.000;abc;2;B;1;2\n"
        ticks, stats = read_ticks(self._fichier(contenu), 0.25)
        self.assertEqual(stats["ticks"], 2)
        self.assertEqual(stats["ignorees"], 2)

    def test_colonnes_insuffisantes_rejetees(self):
        with self.assertRaises(TickFormatError):
            read_ticks(self._fichier("timestamp;price;volume\n2025-03-14 09:31:02;5024;3\n"), 0.25)

    def test_pipeline_complet_depuis_un_export(self):
        """Un export réel doit traverser tout le moteur jusqu'aux trades."""
        source, _ = generate(seed=7, days=4)
        lignes = ["timestamp;price;volume;aggressor;bid;ask"]
        for t in source:
            prix = t.price_ticks * 0.25
            bid, ask = (prix - 0.25, prix) if t.aggressor == SELL else (prix, prix + 0.25)
            lignes.append("%s;%.2f;%d;%s;%.2f;%.2f" % (
                t.ts.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], prix, t.size,
                "B" if t.aggressor == BUY else "S", bid, ask))
        ticks, stats = read_ticks(self._fichier("\n".join(lignes) + "\n"), 0.25)
        self.assertEqual(stats["ticks"], len(source))
        self.assertEqual(sum(t.size for t in ticks), sum(t.size for t in source))

        bars = build_range_bars(ticks, 8, lambda a, b: a.ts.date() != b.ts.date())
        self.assertGreater(len(bars), 100)
        Backtester(Config()).run(bars)   # doit s'exécuter sans erreur


class TestGenerateur(unittest.TestCase):
    def test_deterministe(self):
        a, ea = generate(seed=5, days=2)
        b, eb = generate(seed=5, days=2)
        self.assertEqual(len(a), len(b))
        self.assertEqual([t.price_ticks for t in a[:500]], [t.price_ticks for t in b[:500]])
        self.assertEqual(len(ea), len(eb))

    def test_placebo_sans_absorption(self):
        _, events = generate(seed=5, days=5, mode="placebo")
        self.assertEqual(events, [], "le placebo ne doit contenir aucune absorption")

    def test_mode_null_sans_relief(self):
        ticks, events = generate(seed=5, days=3, mode="null")
        self.assertEqual(events, [])
        self.assertGreater(len(ticks), 1000)


if __name__ == "__main__":
    unittest.main()
