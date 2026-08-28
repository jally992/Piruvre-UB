# Extraire vos données et backtester dessus

## Il n'y a rien à extraire de l'add-on footprint

C'est le point à comprendre avant tout le reste : **un add-on footprint ne stocke
aucune donnée qui lui soit propre.** Il ne fait que recalculer, en direct, une
agrégation du flux de transactions :

> pour chaque trade → à quel prix, quel volume, et exécuté au bid ou à l'ask ?

Tout le contenu d'une cellule de footprint tient dans ces trois informations. Le
« volume au bid » d'un niveau, c'est la somme des trades exécutés à ce prix du
côté vendeur. Rien de plus.

Ce flux est accessible à n'importe quel NinjaScript via `OnMarketData`. Il est
donc inutile — et beaucoup plus fragile — d'essayer de récupérer l'affichage de
l'add-on : on va chercher la matière première, et le footprint est reconstruit
à l'identique côté backtest.

## Méthode recommandée : l'exporteur fourni

`NinjaTrader8/Indicators/TickDataExporter.cs` écrit exactement ce qu'il faut.

1. **Charger les données tick.** Tools → Historical Data → Load, sur
   l'instrument et la période voulus. Sans historique tick, il n'y a rien à
   exporter.
2. **Ouvrir un graphique** de l'instrument, avec **Tick Replay activé**
   (Data Series → Tick Replay = `True`). La période du graphique n'a aucune
   importance pour l'export.
3. **Ajouter l'indicateur `TickDataExporter`**, renseigner le dossier de sortie
   (`C:\ticks` par défaut).
4. L'export se fait pendant le chargement de l'historique. Le nombre de ticks
   écrits s'affiche dans la fenêtre Output (New → Output).

Format produit, un fichier par séance :

```
timestamp;price;volume;aggressor;bid;ask
2025-03-14 09:31:02.417;5024.25;3;B;5024.00;5024.25
2025-03-14 09:31:02.980;5024.00;7;S;5024.00;5024.25
```

`aggressor` vaut `B` (trade à l'ask ou au-dessus), `S` (au bid ou en dessous) ou
`?` dans le cas rare d'une exécution entre les deux — le lecteur Python retombe
alors sur le bid/ask pour trancher.

### Volumétrie

Comptez environ **50 Mo par séance et par million de trades**. Le MES tourne
autour de 200 000 à 500 000 trades par séance : une vingtaine de séances tient
sans problème sur un disque ordinaire. L'option « Un fichier par séance » évite
de se retrouver avec un fichier unique ingérable.

Vingt séances suffisent pour un premier verdict sur la fréquence des signaux ;
comptez plutôt trois à six mois pour juger d'une espérance.

## Backtester sur ces fichiers

```bash
python3 backtest/run_backtest.py --ticks "C:/ticks/MES_*.csv"
python3 backtest/run_backtest.py --ticks "C:/ticks/MES_*.csv" --csv trades_reels.csv
```

Le moteur reconstruit les barres Range et le footprint à partir des ticks, puis
sort le même rapport que sur données simulées — moins la section « qualité de
détection », qui n'a de sens que là où l'on sait par construction où sont les
absorptions.

Le lecteur est tolérant sur le format : il détecte le séparateur (`;`, `,`, tab),
lit l'ordre des colonnes dans l'en-tête, accepte plusieurs formats de date, et
se contente de `bid`/`ask` s'il n'y a pas de colonne `aggressor`. Les fichiers
`.gz` sont lus directement.

Pour un autre instrument, ajustez la valeur du tick et sa valeur monétaire :

```bash
python3 backtest/run_backtest.py --ticks "CL_*.csv" \
    --set instrument.tick_size=0.01 --set instrument.tick_value=10 \
    --set bar.range_ticks=5
```

## Autre voie : l'export natif de NinjaTrader

Tools → Historical Data → onglet Export permet de sortir les séries `Last`,
`Bid` et `Ask` — mais dans **trois fichiers séparés**, sans côté agresseur. Il
faut alors les fusionner sur l'horodatage pour reconstituer, pour chaque trade,
le bid et l'ask en vigueur. C'est faisable, mais l'exporteur ci-dessus fait ce
travail au bon endroit, là où NinjaTrader connaît déjà la réponse.

## Ce qu'il faut vérifier une fois les vrais chiffres sortis

Trois repères, dans cet ordre :

1. **Le nombre de barres par séance.** Sur MES Range 8, attendez-vous à un ordre
   de grandeur de 100 à 250. Très en dessous, la période Range n'est pas la
   bonne.
2. **Le nombre de signaux par jour.** Le moteur en produit ~1,5 sur données
   simulées. Beaucoup plus, le seuil `absorption.volume_multiplier` est trop
   permissif pour votre instrument ; beaucoup moins, il est trop strict.
3. **La part de trades à agresseur indéterminé.** Le rapport l'affiche et
   avertit au-delà de 10 %. Un taux élevé signifie que le bid/ask n'a pas été
   correctement capturé, et le footprint qui en découle ne vaut rien.

Et surtout : les seuils livrés sont calibrés sur le simulateur. Sur vos données,
c'est le balayage qui décide.

```bash
python3 backtest/run_backtest.py --ticks "MES_*.csv" \
    --sweep absorption.volume_multiplier=1.5:4.0:0.5
```
