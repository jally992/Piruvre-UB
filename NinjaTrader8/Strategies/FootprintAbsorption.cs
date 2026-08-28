#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Linq;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.Gui.Tools;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.DrawingTools;
#endregion

// -----------------------------------------------------------------------------
//  FootprintAbsorption — MES / Range 8 ticks
//
//  Deux configurations, activables séparément :
//    A. Absorption au contact d'un LVN du profil de volume glissant
//    B. Pullback : absorption au retest d'un niveau structurel, dans le sens
//       de la tendance (séquence de swings)
//
//  PRÉ-REQUIS ABSOLU : Tick Replay doit être activé.
//    Strategy Analyzer -> case « Tick Replay »
//    Graphique         -> Data Series -> Tick Replay = True
//  Sans Tick Replay, OnMarketData n'est pas appelé sur l'historique : aucun
//  footprint n'est construit et la stratégie ne prend AUCUN trade (un message
//  est écrit dans la fenêtre Output).
//
//  La logique est le portage exact du moteur Python de backtest/ (mêmes seuils,
//  même ordre des opérations, même modèle d'exécution). Voir docs/METHODOLOGIE.md.
// -----------------------------------------------------------------------------

namespace NinjaTrader.NinjaScript.Strategies
{
    public class FootprintAbsorption : Strategy
    {
        #region Types internes

        /// <summary>Footprint d'une barre : volume bid/ask par niveau de prix (en ticks).</summary>
        private class FpBar
        {
            public int HighT, LowT, CloseT, OpenT;
            public long Volume;
            public DateTime Time;
            public readonly Dictionary<int, long[]> Ladder = new Dictionary<int, long[]>();

            public void Add(int priceT, long size, bool buyAggressor)
            {
                long[] lvl;
                if (!Ladder.TryGetValue(priceT, out lvl))
                {
                    lvl = new long[2];
                    Ladder[priceT] = lvl;
                }
                lvl[buyAggressor ? 1 : 0] += size;
                Volume += size;
            }

            public long BidAt(int p) { long[] l; return Ladder.TryGetValue(p, out l) ? l[0] : 0; }
            public long AskAt(int p) { long[] l; return Ladder.TryGetValue(p, out l) ? l[1] : 0; }

            public void ZoneVolume(int loT, int hiT, out long bid, out long ask)
            {
                bid = 0; ask = 0;
                for (int p = loT; p <= hiT; p++)
                {
                    long[] l;
                    if (Ladder.TryGetValue(p, out l)) { bid += l[0]; ask += l[1]; }
                }
            }

            public long Delta { get { return Ladder.Values.Sum(l => l[1] - l[0]); } }
            public int RangeT { get { return HighT - LowT; } }
            public int Levels { get { return Ladder.Count; } }
            public double MeanPerLevel { get { return Levels == 0 ? 0 : (double)Volume / Levels; } }
            public double ClosePosition
            {
                get { return RangeT == 0 ? 0.5 : (double)(CloseT - LowT) / RangeT; }
            }
        }

        private class Swing
        {
            public int BarIndex;
            public int PriceT;
            public int Kind;          // +1 sommet, -1 creux
        }

        private class Level
        {
            public int PriceT;
            public int Side;          // +1 support, -1 résistance
            public string Origin;
            public int CreatedAt;
            public bool Broken;
        }

        private class PendingSignal
        {
            public int Direction;     // +1 long, -1 short
            public int ExtremeT;
            public int StopT;
            public string Setup;
            public int BarIndex;
        }

        #endregion

        #region Champs

        private FpBar current;                       // footprint de la barre en construction
        private readonly List<FpBar> closedBars = new List<FpBar>();
        private readonly Queue<FpBar> profileHist = new Queue<FpBar>();
        private readonly Dictionary<int, long> profileVol = new Dictionary<int, long>();
        private readonly List<Swing> swings = new List<Swing>();
        private readonly List<Level> levels = new List<Level>();
        private readonly List<long> zoneLow = new List<long>();
        private readonly List<long> zoneHigh = new List<long>();
        private readonly List<long> barVols = new List<long>();
        private List<int> lvnCache = new List<int>();
        private bool lvnDirty = true;

        private double lastBid = double.MinValue;
        private double lastAsk = double.MaxValue;
        private bool marketDataSeen;
        private bool tickReplayWarned;

        private PendingSignal pendingConfirm;         // attend la barre de confirmation
        private PendingSignal pendingEntry;           // entrée à l'ouverture suivante
        private int cooldownUntilBar = -1;
        private int entryBar = -1;
        private int tradeExtremeT;
        private int tradeDirection;
        private double stopPrice, targetPrice;
        private bool exitsPlaced, breakevenDone;
        private string activeSetup = "";

        #endregion

        #region Paramètres

        [NinjaScriptProperty, Display(Name = "Setup A : absorption sur LVN", Order = 1, GroupName = "1 Setups")]
        public bool EnableLvnSetup { get; set; }

        [NinjaScriptProperty, Display(Name = "Setup B : pullback sur structure", Order = 2, GroupName = "1 Setups")]
        public bool EnableStructureSetup { get; set; }

        [NinjaScriptProperty, Range(1, int.MaxValue), Display(Name = "Contrats", Order = 3, GroupName = "1 Setups")]
        public int Contracts { get; set; }

        [NinjaScriptProperty, Range(1, 10), Display(Name = "Niveaux formant l'extrême", Order = 1, GroupName = "2 Absorption")]
        public int ExtremeLevels { get; set; }

        [NinjaScriptProperty, Range(10, 500), Display(Name = "Profondeur de la référence glissante (barres)", Order = 2, GroupName = "2 Absorption")]
        public int BaselineBars { get; set; }

        [NinjaScriptProperty, Range(1.0, 10.0), Display(Name = "Volume zone / médiane glissante", Order = 3, GroupName = "2 Absorption")]
        public double VolumeMultiplier { get; set; }

        [NinjaScriptProperty, Range(0.5, 5.0), Display(Name = "Concentration sur l'extrême", Order = 4, GroupName = "2 Absorption")]
        public double ConcentrationMin { get; set; }

        [NinjaScriptProperty, Range(1.0, 5.0), Display(Name = "Volume de barre / médiane", Order = 5, GroupName = "2 Absorption")]
        public double BarVolumeRatio { get; set; }

        [NinjaScriptProperty, Range(0.0, 1.0), Display(Name = "Part du flux agressif piégé", Order = 6, GroupName = "2 Absorption")]
        public double DeltaRatio { get; set; }

        [NinjaScriptProperty, Range(0.0, 1.0), Display(Name = "Clôture du côté opposé (0.40 = 40 %)", Order = 7, GroupName = "2 Absorption")]
        public double ClosePositionMax { get; set; }

        [NinjaScriptProperty, Display(Name = "Exiger la confirmation de la barre suivante", Order = 8, GroupName = "2 Absorption")]
        public bool ConfirmNextBar { get; set; }

        [NinjaScriptProperty, Range(50, 2000), Display(Name = "Profondeur du profil (barres)", Order = 1, GroupName = "3 Profil / LVN")]
        public int ProfileLookback { get; set; }

        [NinjaScriptProperty, Range(0.05, 0.9), Display(Name = "LVN : volume max en % du POC", Order = 2, GroupName = "3 Profil / LVN")]
        public double LvnMaxPctOfPoc { get; set; }

        [NinjaScriptProperty, Range(1, 20), Display(Name = "LVN : fenêtre de minimum local (ticks)", Order = 3, GroupName = "3 Profil / LVN")]
        public int LvnWindowTicks { get; set; }

        [NinjaScriptProperty, Range(0, 20), Display(Name = "LVN : tolérance de contact (ticks)", Order = 4, GroupName = "3 Profil / LVN")]
        public int LvnTouchTicks { get; set; }

        [NinjaScriptProperty, Range(1, 20), Display(Name = "Pivot : barres à gauche", Order = 1, GroupName = "4 Structure")]
        public int SwingLeft { get; set; }

        [NinjaScriptProperty, Range(1, 20), Display(Name = "Pivot : barres à droite", Order = 2, GroupName = "4 Structure")]
        public int SwingRight { get; set; }

        [NinjaScriptProperty, Range(0, 20), Display(Name = "Tolérance de retest (ticks)", Order = 3, GroupName = "4 Structure")]
        public int StructureTouchTicks { get; set; }

        [NinjaScriptProperty, Display(Name = "Pullback uniquement dans le sens de la tendance", Order = 4, GroupName = "4 Structure")]
        public bool RequireTrendAlignment { get; set; }

        [NinjaScriptProperty, Range(0, 20), Display(Name = "Marge du stop sous l'extrême (ticks)", Order = 1, GroupName = "5 Gestion")]
        public int StopBufferTicks { get; set; }

        [NinjaScriptProperty, Range(1, 100), Display(Name = "Stop minimum (ticks)", Order = 2, GroupName = "5 Gestion")]
        public int MinStopTicks { get; set; }

        [NinjaScriptProperty, Range(1, 200), Display(Name = "Stop maximum (ticks)", Order = 3, GroupName = "5 Gestion")]
        public int MaxStopTicks { get; set; }

        [NinjaScriptProperty, Range(0.1, 10.0), Display(Name = "Objectif (multiple de R)", Order = 4, GroupName = "5 Gestion")]
        public double TargetR { get; set; }

        [NinjaScriptProperty, Range(0.0, 10.0), Display(Name = "Breakeven à (R) — 0 = désactivé", Order = 5, GroupName = "5 Gestion")]
        public double BreakevenR { get; set; }

        [NinjaScriptProperty, Range(1, 500), Display(Name = "Sortie au bout de N barres", Order = 6, GroupName = "5 Gestion")]
        public int MaxBarsInTrade { get; set; }

        [NinjaScriptProperty, Range(0, 20), Display(Name = "Pause après une sortie (barres)", Order = 7, GroupName = "5 Gestion")]
        public int CooldownBars { get; set; }

        [NinjaScriptProperty, Range(0, 235959), Display(Name = "Début de plage (HHmmss)", Order = 1, GroupName = "6 Séance")]
        public int StartTime { get; set; }

        [NinjaScriptProperty, Range(0, 235959), Display(Name = "Fin de plage (HHmmss)", Order = 2, GroupName = "6 Séance")]
        public int EndTime { get; set; }

        [NinjaScriptProperty, Range(0, 235959), Display(Name = "Liquidation forcée (HHmmss)", Order = 3, GroupName = "6 Séance")]
        public int FlatTime { get; set; }

        [NinjaScriptProperty, Display(Name = "Dessiner les signaux et les LVN", Order = 1, GroupName = "7 Affichage")]
        public bool DrawSignals { get; set; }

        #endregion

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "Absorption footprint sur LVN et pullbacks de structure (Range bars, Tick Replay requis).";
                Name = "FootprintAbsorption";
                Calculate = Calculate.OnBarClose;
                EntriesPerDirection = 1;
                EntryHandling = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy = true;
                ExitOnSessionCloseSeconds = 60;
                IsFillLimitOnTouch = false;
                MaximumBarsLookBack = MaximumBarsLookBack.Infinite;
                OrderFillResolution = OrderFillResolution.High;
                OrderFillResolutionType = BarsPeriodType.Tick;
                OrderFillResolutionValue = 1;
                Slippage = 1;                    // 1 tick, cohérent avec le backtest Python
                StartBehavior = StartBehavior.WaitUntilFlat;
                TimeInForce = TimeInForce.Day;
                TraceOrders = false;
                RealtimeErrorHandling = RealtimeErrorHandling.StopCancelClose;
                StopTargetHandling = StopTargetHandling.PerEntryExecution;
                BarsRequiredToTrade = 200;
                IsInstantiatedOnEachOptimizationIteration = true;

                EnableLvnSetup = true;
                EnableStructureSetup = true;
                Contracts = 1;

                ExtremeLevels = 3;
                BaselineBars = 50;
                VolumeMultiplier = 2.5;
                ConcentrationMin = 1.15;
                BarVolumeRatio = 1.4;
                DeltaRatio = 0.50;
                ClosePositionMax = 0.40;
                ConfirmNextBar = true;

                ProfileLookback = 400;
                LvnMaxPctOfPoc = 0.30;
                LvnWindowTicks = 3;
                LvnTouchTicks = 2;

                SwingLeft = 3;
                SwingRight = 3;
                StructureTouchTicks = 3;
                RequireTrendAlignment = true;

                StopBufferTicks = 2;
                MinStopTicks = 4;
                MaxStopTicks = 24;
                TargetR = 2.0;
                BreakevenR = 1.0;
                MaxBarsInTrade = 60;
                CooldownBars = 3;

                StartTime = 94000;
                EndTime = 155000;
                FlatTime = 155500;

                DrawSignals = true;
            }
            else if (State == State.Configure)
            {
                if (BarsPeriod != null && BarsPeriod.BarsPeriodType != BarsPeriodType.Range)
                    Print("FootprintAbsorption : cette stratégie est conçue pour des barres Range "
                          + "(8 ticks sur MES). Type actuel : " + BarsPeriod.BarsPeriodType);
            }
            else if (State == State.DataLoaded)
            {
                current = NewBar();
            }
        }

        #region Collecte du footprint

        protected override void OnMarketData(MarketDataEventArgs e)
        {
            if (e.MarketDataType == MarketDataType.Bid) { lastBid = e.Price; return; }
            if (e.MarketDataType == MarketDataType.Ask) { lastAsk = e.Price; return; }
            if (e.MarketDataType != MarketDataType.Last || CurrentBar < 0)
                return;

            marketDataSeen = true;

            // Classification de l'agresseur : au-dessus/au niveau de l'offre = acheteur,
            // au-dessous/au niveau de la demande = vendeur. Entre les deux (trade
            // « mid »), on retient le côté le plus proche.
            bool buyAggressor;
            if (lastAsk < double.MaxValue && e.Price >= lastAsk) buyAggressor = true;
            else if (lastBid > double.MinValue && e.Price <= lastBid) buyAggressor = false;
            else buyAggressor = (lastBid > double.MinValue && lastAsk < double.MaxValue)
                                ? (e.Price - lastBid) >= (lastAsk - e.Price)
                                : e.Price >= Close[0];

            int priceT = PriceToTicks(e.Price);
            if (current.Volume == 0)
            {
                current.OpenT = current.HighT = current.LowT = priceT;
            }
            if (priceT > current.HighT) current.HighT = priceT;
            if (priceT < current.LowT) current.LowT = priceT;
            current.CloseT = priceT;
            current.Time = e.Time;
            current.Add(priceT, (long)e.Volume, buyAggressor);
        }

        private FpBar NewBar() { return new FpBar { HighT = int.MinValue, LowT = int.MaxValue }; }

        private int PriceToTicks(double price) { return (int)Math.Round(price / TickSize); }
        private double TicksToPrice(int t) { return Instrument.MasterInstrument.RoundToTickSize(t * TickSize); }

        #endregion

        protected override void OnBarUpdate()
        {
            if (BarsInProgress != 0 || CurrentBar < 1)
                return;

            if (!marketDataSeen)
            {
                if (!tickReplayWarned && CurrentBar > 20)
                {
                    tickReplayWarned = true;
                    Print("FootprintAbsorption : aucun tick reçu par OnMarketData. "
                          + "Activez TICK REPLAY sur la série de données, sinon aucun "
                          + "footprint ne peut être construit et aucun trade ne sera pris.");
                }
                return;
            }

            // ----- clôture du footprint de la barre qui vient de se terminer -----
            FpBar bar = current;
            current = NewBar();
            if (bar.Volume == 0)
                return;
            bar.Time = Time[0];
            closedBars.Add(bar);

            // 1. entrée éventuelle décidée à la barre précédente (fill à l'ouverture)
            TryEnter(bar);
            // 2. gestion de la position en cours
            ManagePosition(bar);
            // 3. confirmation d'un signal en attente
            HandleConfirmation(bar);
            // 4. recherche d'un nouveau signal, sur l'état ANTÉRIEUR à cette barre
            Detect(bar);
            // 5. mise à jour des états (profil, structure, référence de volume)
            ProfileAdd(bar);
            StructureAdd(bar);
            BaselineAdd(bar);
        }

        #region Profil de volume et LVN

        private void ProfileAdd(FpBar bar)
        {
            foreach (var kv in bar.Ladder)
            {
                long v;
                profileVol.TryGetValue(kv.Key, out v);
                profileVol[kv.Key] = v + kv.Value[0] + kv.Value[1];
            }
            profileHist.Enqueue(bar);
            while (profileHist.Count > ProfileLookback)
            {
                FpBar old = profileHist.Dequeue();
                foreach (var kv in old.Ladder)
                {
                    long v;
                    if (!profileVol.TryGetValue(kv.Key, out v)) continue;
                    long left = v - kv.Value[0] - kv.Value[1];
                    if (left > 0) profileVol[kv.Key] = left; else profileVol.Remove(kv.Key);
                }
            }
            lvnDirty = true;
        }

        private bool ProfileReady { get { return profileHist.Count >= Math.Min(150, ProfileLookback); } }

        private List<int> Lvns()
        {
            if (!lvnDirty) return lvnCache;
            lvnDirty = false;
            lvnCache = new List<int>();
            if (profileVol.Count == 0) return lvnCache;

            int lo = int.MaxValue, hi = int.MinValue;
            long pocVol = 0;
            foreach (var kv in profileVol)
            {
                if (kv.Key < lo) lo = kv.Key;
                if (kv.Key > hi) hi = kv.Key;
                if (kv.Value > pocVol) pocVol = kv.Value;
            }
            if (pocVol <= 0) return lvnCache;
            double threshold = pocVol * LvnMaxPctOfPoc;
            int w = LvnWindowTicks;

            int lastKept = int.MinValue;
            long lastVol = 0;
            for (int p = lo + w; p <= hi - w; p++)
            {
                long v = VolAt(p);
                if (v >= threshold) continue;
                bool isMin = true;
                for (int q = p - w; q <= p + w && isMin; q++)
                    if (VolAt(q) < v) isMin = false;
                if (!isMin) continue;

                if (lastKept != int.MinValue && p - lastKept <= 4)
                {
                    if (v < lastVol) { lvnCache[lvnCache.Count - 1] = p; lastKept = p; lastVol = v; }
                    continue;
                }
                lvnCache.Add(p);
                lastKept = p; lastVol = v;
            }
            return lvnCache;
        }

        private long VolAt(int priceT) { long v; return profileVol.TryGetValue(priceT, out v) ? v : 0; }

        /// <summary>LVN traversé ou frôlé par l'intervalle donné ; -1 si aucun.</summary>
        private int LvnTouchedBy(int loT, int hiT)
        {
            int best = -1; long bestVol = long.MaxValue;
            foreach (int p in Lvns())
                if (p >= loT - LvnTouchTicks && p <= hiT + LvnTouchTicks && VolAt(p) < bestVol)
                {
                    best = p; bestVol = VolAt(p);
                }
            return best;
        }

        #endregion

        #region Structure

        private void StructureAdd(FpBar bar)
        {
            int idx = closedBars.Count - 1;

            // pivot confirmé avec SwingRight barres de retard (aucun look-ahead)
            int i = idx - SwingRight;
            if (i >= SwingLeft)
            {
                FpBar cand = closedBars[i];
                bool isHigh = true, isLow = true;
                for (int k = i - SwingLeft; k < i; k++)
                {
                    if (closedBars[k].HighT >= cand.HighT) isHigh = false;
                    if (closedBars[k].LowT <= cand.LowT) isLow = false;
                }
                for (int k = i + 1; k <= i + SwingRight; k++)
                {
                    if (closedBars[k].HighT > cand.HighT) isHigh = false;
                    if (closedBars[k].LowT < cand.LowT) isLow = false;
                }
                if (isHigh) AddSwing(new Swing { BarIndex = i, PriceT = cand.HighT, Kind = +1 }, idx);
                if (isLow) AddSwing(new Swing { BarIndex = i, PriceT = cand.LowT, Kind = -1 }, idx);
            }

            // une résistance cassée devient un support, et réciproquement
            var promoted = new List<Level>();
            foreach (Level l in levels)
            {
                if (l.Broken) continue;
                if (l.Side == -1 && bar.CloseT > l.PriceT + StructureTouchTicks)
                {
                    l.Broken = true;
                    promoted.Add(new Level { PriceT = l.PriceT, Side = +1, Origin = "broken_high", CreatedAt = idx });
                }
                else if (l.Side == +1 && bar.CloseT < l.PriceT - StructureTouchTicks)
                {
                    l.Broken = true;
                    promoted.Add(new Level { PriceT = l.PriceT, Side = -1, Origin = "broken_low", CreatedAt = idx });
                }
            }
            levels.AddRange(promoted);
            levels.RemoveAll(l => l.Broken || idx - l.CreatedAt > 600);
        }

        private void AddSwing(Swing s, int now)
        {
            swings.Add(s);
            if (swings.Count > 40) swings.RemoveAt(0);
            levels.Add(new Level
            {
                PriceT = s.PriceT,
                Side = s.Kind == -1 ? +1 : -1,
                Origin = s.Kind == -1 ? "swing_low" : "swing_high",
                CreatedAt = now
            });
        }

        private int Trend()
        {
            var highs = swings.Where(s => s.Kind == +1).ToList();
            var lows = swings.Where(s => s.Kind == -1).ToList();
            if (highs.Count < 2 || lows.Count < 2) return 0;
            bool hh = highs[highs.Count - 1].PriceT > highs[highs.Count - 2].PriceT;
            bool hl = lows[lows.Count - 1].PriceT > lows[lows.Count - 2].PriceT;
            bool lh = highs[highs.Count - 1].PriceT < highs[highs.Count - 2].PriceT;
            bool ll = lows[lows.Count - 1].PriceT < lows[lows.Count - 2].PriceT;
            if (hh && hl) return +1;
            if (lh && ll) return -1;
            return 0;
        }

        private int LevelTouched(int loT, int hiT, int side)
        {
            int best = int.MinValue;
            foreach (Level l in levels)
            {
                if (l.Side != side || l.Broken) continue;
                if (l.PriceT < loT - StructureTouchTicks || l.PriceT > hiT + StructureTouchTicks) continue;
                int anchor = side > 0 ? loT : hiT;
                if (best == int.MinValue || Math.Abs(l.PriceT - anchor) < Math.Abs(best - anchor))
                    best = l.PriceT;
            }
            return best;
        }

        #endregion

        #region Référence glissante de volume

        private void BaselineAdd(FpBar bar)
        {
            long bid, ask;
            bar.ZoneVolume(bar.LowT, bar.LowT + ExtremeLevels - 1, out bid, out ask);
            zoneLow.Add(bid + ask);
            bar.ZoneVolume(bar.HighT - ExtremeLevels + 1, bar.HighT, out bid, out ask);
            zoneHigh.Add(bid + ask);
            barVols.Add(bar.Volume);
            Trim(zoneLow); Trim(zoneHigh); Trim(barVols);
        }

        private void Trim(List<long> l) { if (l.Count > BaselineBars) l.RemoveRange(0, l.Count - BaselineBars); }

        private bool BaselineReady { get { return zoneLow.Count >= Math.Max(10, BaselineBars / 3); } }

        private static double Median(List<long> values)
        {
            if (values.Count == 0) return 1.0;
            var copy = new List<long>(values);
            copy.Sort();
            int n = copy.Count;
            double m = (n % 2 == 1) ? copy[n / 2] : (copy[n / 2 - 1] + copy[n / 2]) / 2.0;
            return Math.Max(1.0, m);
        }

        #endregion

        #region Détection

        private void Detect(FpBar bar)
        {
            if (!ProfileReady || !BaselineReady || CurrentBar < BarsRequiredToTrade)
                return;
            int now = ToTime(Time[0]);
            if (now < StartTime || now > EndTime)
                return;
            if (bar.Volume < BarVolumeRatio * Median(barVols) || bar.Levels < ExtremeLevels + 1)
                return;

            double meanLevel = bar.MeanPerLevel;
            if (meanLevel <= 0) return;
            double expected = meanLevel * ExtremeLevels;
            double cpos = bar.ClosePosition;

            int direction = 0, extremeT = 0, zoneLoT = 0, zoneHiT = 0;
            long bid, ask;

            // absorption des vendeurs sur le plus bas -> long
            bar.ZoneVolume(bar.LowT, bar.LowT + ExtremeLevels - 1, out bid, out ask);
            long zone = bid + ask;
            if (zone > 0
                && zone >= VolumeMultiplier * Median(zoneLow)
                && zone >= ConcentrationMin * expected
                && (double)bid / zone >= DeltaRatio
                && cpos >= 1.0 - ClosePositionMax)
            {
                direction = 1; extremeT = bar.LowT; zoneLoT = bar.LowT; zoneHiT = bar.LowT + ExtremeLevels - 1;
            }
            else
            {
                // absorption des acheteurs sur le plus haut -> short
                bar.ZoneVolume(bar.HighT - ExtremeLevels + 1, bar.HighT, out bid, out ask);
                zone = bid + ask;
                if (zone > 0
                    && zone >= VolumeMultiplier * Median(zoneHigh)
                    && zone >= ConcentrationMin * expected
                    && (double)ask / zone >= DeltaRatio
                    && cpos <= ClosePositionMax)
                {
                    direction = -1; extremeT = bar.HighT; zoneLoT = bar.HighT - ExtremeLevels + 1; zoneHiT = bar.HighT;
                }
            }
            if (direction == 0) return;

            // --- contexte : LVN et / ou niveau structurel ---
            int lvn = EnableLvnSetup ? LvnTouchedBy(zoneLoT, zoneHiT) : -1;
            int trend = Trend();
            int level = int.MinValue;
            if (EnableStructureSetup)
            {
                bool aligned = !RequireTrendAlignment || trend == direction;
                if (aligned) level = LevelTouched(zoneLoT, zoneHiT, direction);
            }
            if (lvn < 0 && level == int.MinValue) return;

            string setup = (lvn >= 0 && level != int.MinValue) ? "LVN+STRUCT" : (lvn >= 0 ? "LVN" : "STRUCT");

            if (DrawSignals)
            {
                Draw.Dot(this, "abs" + CurrentBar, false, 0,
                         direction > 0 ? Low[0] - 2 * TickSize : High[0] + 2 * TickSize,
                         direction > 0 ? System.Windows.Media.Brushes.DodgerBlue
                                       : System.Windows.Media.Brushes.OrangeRed);
                if (lvn >= 0)
                    Draw.Line(this, "lvn" + CurrentBar, false, 3, TicksToPrice(lvn), 0, TicksToPrice(lvn),
                              System.Windows.Media.Brushes.Gray, DashStyleHelper.Dot, 1);
            }

            var sig = new PendingSignal
            {
                Direction = direction,
                ExtremeT = extremeT,
                StopT = direction > 0 ? extremeT - StopBufferTicks : extremeT + StopBufferTicks,
                Setup = setup,
                BarIndex = CurrentBar
            };

            bool busy = Position.MarketPosition != MarketPosition.Flat
                        || pendingEntry != null || CurrentBar <= cooldownUntilBar;
            if (busy) return;

            if (ConfirmNextBar) pendingConfirm = sig;
            else pendingEntry = sig;
        }

        private void HandleConfirmation(FpBar bar)
        {
            PendingSignal sig = pendingConfirm;
            pendingConfirm = null;
            if (sig == null) return;
            bool ok = sig.Direction > 0 ? bar.LowT >= sig.ExtremeT : bar.HighT <= sig.ExtremeT;
            if (ok) pendingEntry = sig;
        }

        #endregion

        #region Exécution

        private void TryEnter(FpBar bar)
        {
            PendingSignal sig = pendingEntry;
            pendingEntry = null;
            if (sig == null || Position.MarketPosition != MarketPosition.Flat)
                return;

            // Contrôle du risque à partir du prix courant : le fill réel sera
            // l'ouverture de la barre suivante, l'écart est absorbé par Slippage.
            double risk = Math.Abs(Close[0] - TicksToPrice(sig.StopT)) / TickSize;
            if (risk < MinStopTicks || risk > MaxStopTicks)
                return;

            tradeDirection = sig.Direction;
            tradeExtremeT = sig.ExtremeT;
            activeSetup = sig.Setup;
            exitsPlaced = false;
            breakevenDone = false;
            entryBar = CurrentBar;

            if (sig.Direction > 0) EnterLong(Contracts, "ABS_" + sig.Setup);
            else EnterShort(Contracts, "ABS_" + sig.Setup);
        }

        private void ManagePosition(FpBar bar)
        {
            if (Position.MarketPosition == MarketPosition.Flat)
            {
                exitsPlaced = false;
                return;
            }

            double entry = Position.AveragePrice;
            bool isLong = Position.MarketPosition == MarketPosition.Long;

            if (!exitsPlaced)
            {
                // Le stop se place derrière le niveau réellement absorbé ; il n'est
                // resserré que s'il est plus proche que le minimum autorisé.
                stopPrice = TicksToPrice(tradeExtremeT + (isLong ? -StopBufferTicks : StopBufferTicks));
                double risk = Math.Max(Math.Abs(entry - stopPrice), MinStopTicks * TickSize);
                stopPrice = isLong ? entry - risk : entry + risk;
                targetPrice = isLong ? entry + TargetR * risk : entry - TargetR * risk;
                exitsPlaced = true;
            }

            // passage à breakeven
            if (BreakevenR > 0 && !breakevenDone)
            {
                double risk = Math.Abs(entry - stopPrice);
                double mfe = isLong ? High[0] - entry : entry - Low[0];
                if (risk > 0 && mfe >= BreakevenR * risk)
                {
                    stopPrice = isLong ? entry + TickSize : entry - TickSize;
                    breakevenDone = true;
                }
            }

            int now = ToTime(Time[0]);
            if (now >= FlatTime || CurrentBar - entryBar >= MaxBarsInTrade)
            {
                if (isLong) ExitLong(Contracts, "SORTIE", "ABS_" + activeSetup);
                else ExitShort(Contracts, "SORTIE", "ABS_" + activeSetup);
                cooldownUntilBar = CurrentBar + CooldownBars;
                return;
            }

            // les ordres de sortie sont resoumis à chaque barre : NinjaTrader met
            // simplement à jour l'ordre existant portant le même nom de signal.
            if (isLong)
            {
                ExitLongStopMarket(Contracts, Instrument.MasterInstrument.RoundToTickSize(stopPrice),
                                   "STOP", "ABS_" + activeSetup);
                ExitLongLimit(Contracts, Instrument.MasterInstrument.RoundToTickSize(targetPrice),
                              "TP", "ABS_" + activeSetup);
            }
            else
            {
                ExitShortStopMarket(Contracts, Instrument.MasterInstrument.RoundToTickSize(stopPrice),
                                    "STOP", "ABS_" + activeSetup);
                ExitShortLimit(Contracts, Instrument.MasterInstrument.RoundToTickSize(targetPrice),
                               "TP", "ABS_" + activeSetup);
            }
        }

        protected override void OnPositionUpdate(Position position, double averagePrice,
                                                 int quantity, MarketPosition marketPosition)
        {
            if (marketPosition == MarketPosition.Flat)
            {
                exitsPlaced = false;
                breakevenDone = false;
                cooldownUntilBar = CurrentBar + CooldownBars;
            }
        }

        #endregion
    }
}
