#region Using declarations
using System;
using System.Globalization;
using System.IO;
using System.ComponentModel.DataAnnotations;
using System.Text;
using NinjaTrader.Data;
using NinjaTrader.Gui;
using NinjaTrader.Gui.Chart;
using NinjaTrader.Gui.Tools;
using NinjaTrader.NinjaScript;
#endregion

// -----------------------------------------------------------------------------
//  TickDataExporter — exporte le flux de ticks en CSV, avec le côté agresseur
//
//  Il n'y a rien à extraire d'un add-on footprint : celui-ci ne stocke aucune
//  donnée propre, il recalcule tout à partir du flux de transactions. C'est donc
//  ce flux qu'on exporte ici, à la source.
//
//  Une ligne par transaction :
//      horodatage;prix;volume;agresseur;bid;ask
//      2025-03-14 09:31:02.417;5024.25;3;B;5024.00;5024.25
//
//  L'agresseur vaut B (acheteur : trade à l'ask ou au-dessus), S (vendeur :
//  trade au bid ou en dessous) ou ? (entre les deux, cas rare).
//
//  UTILISATION
//    1. Graphique de l'instrument voulu, période quelconque (Range 8 par ex.),
//       avec TICK REPLAY ACTIVÉ et l'historique tick chargé sur la plage voulue.
//    2. Ajouter cet indicateur, renseigner le dossier de sortie.
//    3. Le fichier est écrit pendant le chargement de l'historique. Le nombre de
//       ticks exportés est affiché dans la fenêtre Output (New -> Output).
//
//  Volumétrie : comptez ~1,5 million de lignes par séance sur ES, ~50 Mo par jour.
//  Sur MES c'est bien moins. Exportez séance par séance si le fichier devient
//  ingérable (paramètre « Un fichier par séance »).
// -----------------------------------------------------------------------------

namespace NinjaTrader.NinjaScript.Indicators
{
    public class TickDataExporter : Indicator
    {
        private StreamWriter writer;
        private string currentDay = "";
        private long exported;
        private double lastBid = double.MinValue;
        private double lastAsk = double.MaxValue;
        private bool marketDataSeen, warned;

        [NinjaScriptProperty, Display(Name = "Dossier de sortie", Order = 1, GroupName = "Export")]
        public string OutputFolder { get; set; }

        [NinjaScriptProperty, Display(Name = "Un fichier par séance", Order = 2, GroupName = "Export")]
        public bool OneFilePerSession { get; set; }

        [NinjaScriptProperty, Display(Name = "Écrire bid et ask", Order = 3, GroupName = "Export")]
        public bool IncludeQuotes { get; set; }

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name = "TickDataExporter";
                Description = "Exporte les ticks (prix, volume, côté agresseur) en CSV pour un backtest externe.";
                Calculate = Calculate.OnBarClose;
                IsOverlay = true;
                DisplayInDataBox = false;
                PaintPriceMarkers = false;
                OutputFolder = @"C:\ticks";
                OneFilePerSession = true;
                IncludeQuotes = true;
            }
            else if (State == State.Terminated)
            {
                CloseWriter();
                if (exported > 0)
                    Print(string.Format("TickDataExporter : {0} ticks exportés dans {1}",
                                        exported, OutputFolder));
            }
        }

        private void CloseWriter()
        {
            if (writer == null) return;
            writer.Flush();
            writer.Dispose();
            writer = null;
        }

        private void OpenWriter(DateTime ts)
        {
            string day = ts.ToString("yyyyMMdd");
            if (writer != null && (!OneFilePerSession || day == currentDay))
                return;

            CloseWriter();
            currentDay = day;

            if (!Directory.Exists(OutputFolder))
                Directory.CreateDirectory(OutputFolder);

            string instrument = Instrument.MasterInstrument.Name;
            string name = OneFilePerSession
                ? string.Format("{0}_{1}.csv", instrument, day)
                : string.Format("{0}_ticks.csv", instrument);

            string path = Path.Combine(OutputFolder, name);
            bool fresh = !File.Exists(path) || OneFilePerSession;
            writer = new StreamWriter(path, append: !fresh, encoding: Encoding.ASCII);
            if (fresh)
                writer.WriteLine(IncludeQuotes
                    ? "timestamp;price;volume;aggressor;bid;ask"
                    : "timestamp;price;volume;aggressor");
        }

        protected override void OnMarketData(MarketDataEventArgs e)
        {
            if (e.MarketDataType == MarketDataType.Bid) { lastBid = e.Price; return; }
            if (e.MarketDataType == MarketDataType.Ask) { lastAsk = e.Price; return; }
            if (e.MarketDataType != MarketDataType.Last) return;

            marketDataSeen = true;
            OpenWriter(e.Time);
            if (writer == null) return;

            // Classification de l'agresseur, exactement comme le fait un footprint :
            // au-dessus ou au niveau de l'offre = acheteur, au niveau ou sous la
            // demande = vendeur. Entre les deux, on ne tranche pas.
            char side;
            if (lastAsk < double.MaxValue && e.Price >= lastAsk) side = 'B';
            else if (lastBid > double.MinValue && e.Price <= lastBid) side = 'S';
            else side = '?';

            var sb = new StringBuilder(72);
            sb.Append(e.Time.ToString("yyyy-MM-dd HH:mm:ss.fff", CultureInfo.InvariantCulture));
            sb.Append(';').Append(e.Price.ToString("0.########", CultureInfo.InvariantCulture));
            sb.Append(';').Append(((long)e.Volume).ToString(CultureInfo.InvariantCulture));
            sb.Append(';').Append(side);
            if (IncludeQuotes)
            {
                sb.Append(';');
                if (lastBid > double.MinValue)
                    sb.Append(lastBid.ToString("0.########", CultureInfo.InvariantCulture));
                sb.Append(';');
                if (lastAsk < double.MaxValue)
                    sb.Append(lastAsk.ToString("0.########", CultureInfo.InvariantCulture));
            }
            writer.WriteLine(sb.ToString());

            if (++exported % 50000 == 0)
                writer.Flush();
        }

        protected override void OnBarUpdate()
        {
            if (marketDataSeen || warned || CurrentBar < 20) return;
            warned = true;
            Print("TickDataExporter : aucun tick reçu. Activez TICK REPLAY sur la série de "
                  + "données (Data Series -> Tick Replay = True) et vérifiez que l'historique "
                  + "tick est bien chargé pour la période affichée.");
        }
    }
}
