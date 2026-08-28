#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using System.Linq;
using System.Windows.Media;
using NinjaTrader.Data;
using NinjaTrader.Gui;
using NinjaTrader.Gui.Chart;
using NinjaTrader.Gui.Tools;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.DrawingTools;
#endregion

// -----------------------------------------------------------------------------
//  FootprintAbsorptionMap — outil de VERIFICATION VISUELLE et de CALIBRAGE
//
//    * marque les absorptions detectees (fleche + volume absorbe)
//    * trace les LVN du profil de volume glissant (pointilles gris)
//    * mode Diagnostic : ecrit dans la fenetre Output, barre par barre, les
//      mesures et le critere qui a bloque. C'est ce qui permet de regler les
//      seuils sur VOS donnees au lieu de deviner.
//
//  Les marqueurs sont des textes et non des figures : les figures de
//  NinjaTrader se dimensionnent sur la largeur des barres et deviennent
//  illisibles sur un graphique footprint zoome.
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

        // Mesures d'un extreme de barre, conservees meme quand un critere echoue :
        // c'est ce qui permet au diagnostic de dire POURQUOI ca n'a pas passe.
        private class SideEval
        {
            public long Zone, Bid, Ask;
            public double Ratio, Conc, Share, CPos;
            public bool Ok;
            public string Fail = "";
        }

        private FpBar current = new FpBar();
        private readonly Queue<FpBar> hist = new Queue<FpBar>();
        private readonly Dictionary<int, long> profileVol = new Dictionary<int, long>();
        private readonly List<long> zoneLow = new List<long>();
        private readonly List<long> zoneHigh = new List<long>();
        private readonly List<long> barVols = new List<long>();
        private double lastBid = double.MinValue, lastAsk = double.MaxValue;
        private bool marketDataSeen, warned;
        private int signalCount;
        private readonly HashSet<string> drawnLvns = new HashSet<string>();

        [NinjaScriptProperty, Range(1, 10), Display(Name = "Niveaux formant l'extreme", GroupName = "Absorption", Order = 1)]
        public int ExtremeLevels { get; set; }

        [NinjaScriptProperty, Range(1.0, 10.0), Display(Name = "Volume zone / mediane glissante", GroupName = "Absorption", Order = 2)]
        public double VolumeMultiplier { get; set; }

        [NinjaScriptProperty, Range(0.3, 5.0), Display(Name = "Concentration sur l'extreme", GroupName = "Absorption", Order = 3)]
        public double ConcentrationMin { get; set; }

        [NinjaScriptProperty, Range(0.5, 5.0), Display(Name = "Volume de barre / mediane", GroupName = "Absorption", Order = 4)]
        public double BarVolumeRatio { get; set; }

        [NinjaScriptProperty, Range(0.0, 1.0), Display(Name = "Part du flux agressif piege", GroupName = "Absorption", Order = 5)]
        public double DeltaRatio { get; set; }

        [NinjaScriptProperty, Range(0.0, 1.0), Display(Name = "Cloture du cote oppose", GroupName = "Absorption", Order = 6)]
        public double ClosePositionMax { get; set; }

        [NinjaScriptProperty, Range(50, 2000), Display(Name = "Profondeur du profil (barres)", GroupName = "Profil", Order = 1)]
        public int ProfileLookback { get; set; }

        [NinjaScriptProperty, Range(0.05, 0.9), Display(Name = "LVN : volume max en % du POC", GroupName = "Profil", Order = 2)]
        public double LvnMaxPctOfPoc { get; set; }

        [NinjaScriptProperty, Range(1, 20), Display(Name = "LVN : fenetre de minimum local", GroupName = "Profil", Order = 3)]
        public int LvnWindowTicks { get; set; }

        [NinjaScriptProperty, Display(Name = "Tracer les LVN", GroupName = "Affichage", Order = 1)]
        public bool ShowLvn { get; set; }

        [NinjaScriptProperty, Range(6, 20), Display(Name = "Taille du texte", GroupName = "Affichage", Order = 2)]
        public int FontSize { get; set; }

        [NinjaScriptProperty, Display(Name = "Diagnostic dans la fenetre Output", GroupName = "Diagnostic", Order = 1)]
        public bool Diagnostic { get; set; }

        [NinjaScriptProperty, Range(1.0, 10.0), Display(Name = "Diagnostic : ne montrer qu'au-dela de ce ratio", GroupName = "Diagnostic", Order = 2)]
        public double DiagnosticMinRatio { get; set; }

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name = "FootprintAbsorptionMap";
                Description = "Marque les absorptions, trace les LVN, et explique dans Output pourquoi une barre est rejetee.";
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
                FontSize = 11;
                Diagnostic = false;
                DiagnosticMinRatio = 1.5;
            }
            else if (State == State.Terminated)
            {
                if (Diagnostic)
                    Print(string.Format("[FAM] termine — {0} signal(s) sur {1} barres analysees.",
                                        signalCount, hist.Count));
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
                    Print("FootprintAbsorptionMap : activez TICK REPLAY sur la serie de donnees.");
                }
                return;
            }

            FpBar bar = current;
            current = new FpBar();
            if (bar.Volume == 0) return;
            Reconcile(bar);
            if (bar.Volume == 0) return;

            if (hist.Count >= Math.Min(150, ProfileLookback) && zoneLow.Count >= 15)
            {
                double volRef = Median(barVols);
                double volRatio = bar.Volume / volRef;
                bool volOk = volRatio >= BarVolumeRatio && bar.Levels >= ExtremeLevels + 1;

                SideEval lo = EvalSide(bar, true);
                SideEval hi = EvalSide(bar, false);

                int dir = 0;
                SideEval win = null;
                if (volOk)
                {
                    if (lo.Ok) { dir = +1; win = lo; }
                    else if (hi.Ok) { dir = -1; win = hi; }
                }

                if (dir != 0)
                {
                    signalCount++;
                    // Marqueur en TEXTE : taille fixe, ne deborde pas sur les
                    // barres voisines comme le font les figures de NinjaTrader.
                    string tag = "fam" + CurrentBar;
                    string glyphe = dir > 0 ? "\u25B2" : "\u25BC";   // triangle haut / bas
                    Draw.Text(this, tag, false,
                              glyphe + " " + win.Zone.ToString(CultureInfo.InvariantCulture),
                              0,
                              dir > 0 ? Low[0] - 2 * TickSize : High[0] + 2 * TickSize,
                              dir > 0 ? -8 : 8,
                              dir > 0 ? Brushes.DodgerBlue : Brushes.OrangeRed,
                              new SimpleFont("Arial", FontSize) { Bold = true },
                              System.Windows.TextAlignment.Center,
                              Brushes.Transparent, Brushes.Transparent, 0);
                }

                if (Diagnostic && (dir != 0 || lo.Ratio >= DiagnosticMinRatio || hi.Ratio >= DiagnosticMinRatio))
                    Print(string.Format(
                        "[FAM] {0:HH:mm:ss} vol={1} ({2:0.00}x{3}) | BAS z={4} r={5:0.00} c={6:0.00} part={7:0.00} clot={8:0.00} -> {9} | HAUT z={10} r={11:0.00} c={12:0.00} part={13:0.00} clot={14:0.00} -> {15}{16}",
                        Time[0], bar.Volume, volRatio, volOk ? "" : " REJET VOLUME BARRE",
                        lo.Zone, lo.Ratio, lo.Conc, lo.Share, lo.CPos, lo.Ok ? "OK" : lo.Fail,
                        hi.Zone, hi.Ratio, hi.Conc, hi.Share, hi.CPos, hi.Ok ? "OK" : hi.Fail,
                        dir != 0 ? "   ==> SIGNAL" : ""));

                if (ShowLvn && CurrentBar % 20 == 0)
                    DrawLvns();
            }

            ProfileAdd(bar);
            BaselineAdd(bar);
        }

        // Evalue un extreme de barre. Toutes les mesures sont calculees AVANT
        // les tests, pour que le diagnostic puisse les afficher meme en cas de rejet.
        private SideEval EvalSide(FpBar bar, bool low)
        {
            SideEval r = new SideEval();
            long bid, ask;
            if (low) bar.Zone(bar.LowT, bar.LowT + ExtremeLevels - 1, out bid, out ask);
            else bar.Zone(bar.HighT - ExtremeLevels + 1, bar.HighT, out bid, out ask);
            r.Bid = bid; r.Ask = ask; r.Zone = bid + ask;
            r.CPos = bar.ClosePosition;
            if (r.Zone <= 0) { r.Fail = "zone vide"; return r; }

            double expected = bar.MeanPerLevel * ExtremeLevels;
            r.Ratio = r.Zone / Median(low ? zoneLow : zoneHigh);
            r.Conc = expected > 0 ? r.Zone / expected : 0;
            r.Share = (double)(low ? bid : ask) / r.Zone;

            if (r.Ratio < VolumeMultiplier) { r.Fail = "ratio"; return r; }
            if (r.Conc < ConcentrationMin) { r.Fail = "concentr"; return r; }
            if (r.Share < DeltaRatio) { r.Fail = "part"; return r; }
            if (low ? r.CPos < 1.0 - ClosePositionMax : r.CPos > ClosePositionMax)
            { r.Fail = "cloture"; return r; }

            r.Ok = true;
            return r;
        }

        // Le tick qui fait depasser le range est recu par OnMarketData avant la
        // cloture de barre : on renvoie vers la barre suivante tout niveau hors
        // du High/Low reel, pour que le footprint colle exactement a la barre.
        private void Reconcile(FpBar bar)
        {
            int hiT = (int)Math.Round(High[0] / TickSize);
            int loT = (int)Math.Round(Low[0] / TickSize);
            var outside = bar.Ladder.Keys.Where(p => p > hiT || p < loT).ToList();
            foreach (int p in outside)
            {
                long[] lvl = bar.Ladder[p];
                bar.Ladder.Remove(p);
                bar.Volume -= lvl[0] + lvl[1];
                if (lvl[0] > 0) current.Add(p, lvl[0], false);
                if (lvl[1] > 0) current.Add(p, lvl[1], true);
                if (p > current.HighT) current.HighT = p;
                if (p < current.LowT) current.LowT = p;
                current.CloseT = p;
            }
            if (bar.Ladder.Count == 0) { bar.Volume = 0; return; }
            bar.HighT = bar.Ladder.Keys.Max();
            bar.LowT = bar.Ladder.Keys.Min();
            bar.CloseT = Math.Min(bar.HighT, Math.Max(bar.LowT, (int)Math.Round(Close[0] / TickSize)));
        }

        private void DrawLvns()
        {
            if (profileVol.Count == 0) return;
            int lo = profileVol.Keys.Min(), hi = profileVol.Keys.Max();
            long poc = profileVol.Values.Max();
            if (poc <= 0) return;
            double threshold = poc * LvnMaxPctOfPoc;
            int w = LvnWindowTicks;

            // On ne redessine que les LVN : RemoveDrawObjects() effacerait aussi
            // les marqueurs d'absorption deja poses sur les barres passees.
            var fresh = new HashSet<string>();
            for (int p = lo + w; p <= hi - w; p++)
            {
                long v = VolAt(p);
                if (v >= threshold) continue;
                bool isMin = true;
                for (int q = p - w; q <= p + w && isMin; q++)
                    if (VolAt(q) < v) isMin = false;
                if (!isMin) continue;
                string tag = "lvn" + p;
                fresh.Add(tag);
                Draw.HorizontalLine(this, tag, false, p * TickSize,
                                    Brushes.Gray, DashStyleHelper.Dot, 1);
            }
            foreach (string stale in drawnLvns)
                if (!fresh.Contains(stale)) RemoveDrawObject(stale);
            drawnLvns.Clear();
            foreach (string tag in fresh) drawnLvns.Add(tag);
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
