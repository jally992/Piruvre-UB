#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel.DataAnnotations;
using System.Linq;
using System.Windows.Media;
using NinjaTrader.Data;
using NinjaTrader.Gui;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.DrawingTools;
#endregion

// -----------------------------------------------------------------------------
//  FootprintAbsorptionMap — outil de VÉRIFICATION VISUELLE
//
//  Trace sur le graphique ce que la stratégie FootprintAbsorption « voit » :
//    * les LVN du profil de volume glissant (lignes pointillées) ;
//    * les barres d'absorption détectées (triangle + volume absorbé).
//
//  Sert à contrôler à l'œil, sur vos propres données, que la détection colle
//  à ce que vous lisez sur votre add-on footprint AVANT de lancer un backtest.
//  Le filtre de structure (pullback) n'est PAS repris ici : cet indicateur
//  montre l'absorption brute et son contexte LVN.
//
//  TICK REPLAY OBLIGATOIRE (Data Series -> Tick Replay = True).
// -----------------------------------------------------------------------------

namespace NinjaTrader.NinjaScript.Indicators
{
    public class FootprintAbsorptionMap : Indicator
    {
        private class FpBar
        {
            public int HighT = int.MinValue, LowT = int.MaxValue, CloseT;
            public long Volume;
            public readonly Dictionary<int, long[]> Ladder = new Dictionary<int, long[]>();

            public void Add(int p, long size, bool buy)
            {
                long[] lvl;
                if (!Ladder.TryGetValue(p, out lvl)) { lvl = new long[2]; Ladder[p] = lvl; }
                lvl[buy ? 1 : 0] += size;
                Volume += size;
            }

            public void Zone(int lo, int hi, out long bid, out long ask)
            {
                bid = 0; ask = 0;
                for (int p = lo; p <= hi; p++)
                {
                    long[] l;
                    if (Ladder.TryGetValue(p, out l)) { bid += l[0]; ask += l[1]; }
                }
            }

            public int Levels { get { return Ladder.Count; } }
            public double MeanPerLevel { get { return Levels == 0 ? 0 : (double)Volume / Levels; } }
            public double ClosePosition
            {
                get { return HighT == LowT ? 0.5 : (double)(CloseT - LowT) / (HighT - LowT); }
            }
        }

        private FpBar current = new FpBar();
        private readonly Queue<FpBar> hist = new Queue<FpBar>();
        private readonly Dictionary<int, long> profileVol = new Dictionary<int, long>();
        private readonly List<long> zoneLow = new List<long>();
        private readonly List<long> zoneHigh = new List<long>();
        private readonly List<long> barVols = new List<long>();
        private double lastBid = double.MinValue, lastAsk = double.MaxValue;
        private bool marketDataSeen, warned;

        [NinjaScriptProperty, Range(1, 10), Display(Name = "Niveaux formant l'extrême", GroupName = "Absorption", Order = 1)]
        public int ExtremeLevels { get; set; }

        [NinjaScriptProperty, Range(1.0, 10.0), Display(Name = "Volume zone / médiane glissante", GroupName = "Absorption", Order = 2)]
        public double VolumeMultiplier { get; set; }

        [NinjaScriptProperty, Range(0.5, 5.0), Display(Name = "Concentration sur l'extrême", GroupName = "Absorption", Order = 3)]
        public double ConcentrationMin { get; set; }

        [NinjaScriptProperty, Range(1.0, 5.0), Display(Name = "Volume de barre / médiane", GroupName = "Absorption", Order = 4)]
        public double BarVolumeRatio { get; set; }

        [NinjaScriptProperty, Range(0.0, 1.0), Display(Name = "Part du flux agressif piégé", GroupName = "Absorption", Order = 5)]
        public double DeltaRatio { get; set; }

        [NinjaScriptProperty, Range(0.0, 1.0), Display(Name = "Clôture du côté opposé", GroupName = "Absorption", Order = 6)]
        public double ClosePositionMax { get; set; }

        [NinjaScriptProperty, Range(50, 2000), Display(Name = "Profondeur du profil (barres)", GroupName = "Profil", Order = 1)]
        public int ProfileLookback { get; set; }

        [NinjaScriptProperty, Range(0.05, 0.9), Display(Name = "LVN : volume max en % du POC", GroupName = "Profil", Order = 2)]
        public double LvnMaxPctOfPoc { get; set; }

        [NinjaScriptProperty, Range(1, 20), Display(Name = "LVN : fenêtre de minimum local", GroupName = "Profil", Order = 3)]
        public int LvnWindowTicks { get; set; }

        [NinjaScriptProperty, Display(Name = "Tracer les LVN", GroupName = "Affichage", Order = 1)]
        public bool ShowLvn { get; set; }

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name = "FootprintAbsorptionMap";
                Description = "Marque les absorptions et les LVN vus par la stratégie FootprintAbsorption.";
                Calculate = Calculate.OnBarClose;
                IsOverlay = true;
                DisplayInDataBox = false;
                DrawOnPricePanel = true;
                PaintPriceMarkers = false;

                ExtremeLevels = 3;
                VolumeMultiplier = 2.5;
                ConcentrationMin = 1.15;
                BarVolumeRatio = 1.4;
                DeltaRatio = 0.50;
                ClosePositionMax = 0.40;
                ProfileLookback = 400;
                LvnMaxPctOfPoc = 0.30;
                LvnWindowTicks = 3;
                ShowLvn = true;
            }
        }

        protected override void OnMarketData(MarketDataEventArgs e)
        {
            if (e.MarketDataType == MarketDataType.Bid) { lastBid = e.Price; return; }
            if (e.MarketDataType == MarketDataType.Ask) { lastAsk = e.Price; return; }
            if (e.MarketDataType != MarketDataType.Last || CurrentBar < 0) return;

            marketDataSeen = true;
            bool buy;
            if (lastAsk < double.MaxValue && e.Price >= lastAsk) buy = true;
            else if (lastBid > double.MinValue && e.Price <= lastBid) buy = false;
            else buy = (lastBid > double.MinValue && lastAsk < double.MaxValue)
                       ? (e.Price - lastBid) >= (lastAsk - e.Price)
                       : e.Price >= Close[0];

            int p = (int)Math.Round(e.Price / TickSize);
            if (p > current.HighT) current.HighT = p;
            if (p < current.LowT) current.LowT = p;
            current.CloseT = p;
            current.Add(p, (long)e.Volume, buy);
        }

        protected override void OnBarUpdate()
        {
            if (CurrentBar < 1) return;
            if (!marketDataSeen)
            {
                if (!warned && CurrentBar > 20)
                {
                    warned = true;
                    Print("FootprintAbsorptionMap : activez TICK REPLAY sur la série de données.");
                }
                return;
            }

            FpBar bar = current;
            current = new FpBar();
            if (bar.Volume == 0) return;

            if (hist.Count >= Math.Min(150, ProfileLookback) && zoneLow.Count >= 15)
            {
                int dir = Detect(bar);
                if (dir != 0)
                {
                    long bid, ask;
                    if (dir > 0) bar.Zone(bar.LowT, bar.LowT + ExtremeLevels - 1, out bid, out ask);
                    else bar.Zone(bar.HighT - ExtremeLevels + 1, bar.HighT, out bid, out ask);

                    Draw.TriangleUp(this, "abs" + CurrentBar, false,
                                    0, dir > 0 ? Low[0] - 3 * TickSize : High[0] + 3 * TickSize,
                                    dir > 0 ? Brushes.DodgerBlue : Brushes.OrangeRed);
                    Draw.Text(this, "absTxt" + CurrentBar, (bid + ask).ToString(),
                              0, dir > 0 ? Low[0] - 6 * TickSize : High[0] + 6 * TickSize,
                              dir > 0 ? Brushes.DodgerBlue : Brushes.OrangeRed);
                }
                if (ShowLvn && CurrentBar % 20 == 0)
                    DrawLvns();
            }

            ProfileAdd(bar);
            BaselineAdd(bar);
        }

        private int Detect(FpBar bar)
        {
            if (bar.Volume < BarVolumeRatio * Median(barVols) || bar.Levels < ExtremeLevels + 1)
                return 0;
            double expected = bar.MeanPerLevel * ExtremeLevels;
            if (expected <= 0) return 0;
            double cpos = bar.ClosePosition;
            long bid, ask;

            bar.Zone(bar.LowT, bar.LowT + ExtremeLevels - 1, out bid, out ask);
            long zone = bid + ask;
            if (zone > 0 && zone >= VolumeMultiplier * Median(zoneLow) && zone >= ConcentrationMin * expected
                && (double)bid / zone >= DeltaRatio && cpos >= 1.0 - ClosePositionMax)
                return +1;

            bar.Zone(bar.HighT - ExtremeLevels + 1, bar.HighT, out bid, out ask);
            zone = bid + ask;
            if (zone > 0 && zone >= VolumeMultiplier * Median(zoneHigh) && zone >= ConcentrationMin * expected
                && (double)ask / zone >= DeltaRatio && cpos <= ClosePositionMax)
                return -1;

            return 0;
        }

        private void DrawLvns()
        {
            if (profileVol.Count == 0) return;
            int lo = profileVol.Keys.Min(), hi = profileVol.Keys.Max();
            long poc = profileVol.Values.Max();
            if (poc <= 0) return;
            double threshold = poc * LvnMaxPctOfPoc;
            int w = LvnWindowTicks;
            RemoveDrawObjects();

            for (int p = lo + w; p <= hi - w; p++)
            {
                long v = VolAt(p);
                if (v >= threshold) continue;
                bool isMin = true;
                for (int q = p - w; q <= p + w && isMin; q++)
                    if (VolAt(q) < v) isMin = false;
                if (!isMin) continue;
                Draw.HorizontalLine(this, "lvn" + p, false, p * TickSize,
                                    Brushes.Gray, DashStyleHelper.Dot, 1);
            }
        }

        private long VolAt(int p) { long v; return profileVol.TryGetValue(p, out v) ? v : 0; }

        private void ProfileAdd(FpBar bar)
        {
            foreach (var kv in bar.Ladder)
            {
                long v;
                profileVol.TryGetValue(kv.Key, out v);
                profileVol[kv.Key] = v + kv.Value[0] + kv.Value[1];
            }
            hist.Enqueue(bar);
            while (hist.Count > ProfileLookback)
            {
                FpBar old = hist.Dequeue();
                foreach (var kv in old.Ladder)
                {
                    long v;
                    if (!profileVol.TryGetValue(kv.Key, out v)) continue;
                    long left = v - kv.Value[0] - kv.Value[1];
                    if (left > 0) profileVol[kv.Key] = left; else profileVol.Remove(kv.Key);
                }
            }
        }

        private void BaselineAdd(FpBar bar)
        {
            long bid, ask;
            bar.Zone(bar.LowT, bar.LowT + ExtremeLevels - 1, out bid, out ask);
            zoneLow.Add(bid + ask);
            bar.Zone(bar.HighT - ExtremeLevels + 1, bar.HighT, out bid, out ask);
            zoneHigh.Add(bid + ask);
            barVols.Add(bar.Volume);
            Trim(zoneLow); Trim(zoneHigh); Trim(barVols);
        }

        private void Trim(List<long> l) { if (l.Count > 50) l.RemoveRange(0, l.Count - 50); }

        private static double Median(List<long> values)
        {
            if (values.Count == 0) return 1.0;
            var copy = new List<long>(values);
            copy.Sort();
            int n = copy.Count;
            double m = (n % 2 == 1) ? copy[n / 2] : (copy[n / 2 - 1] + copy[n / 2]) / 2.0;
            return Math.Max(1.0, m);
        }
    }
}
