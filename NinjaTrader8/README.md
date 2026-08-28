# Installation dans NinjaTrader 8

## 1. Copier les fichiers

| Fichier | Destination |
|---|---|
| `Strategies/FootprintAbsorption.cs` | `Documents\NinjaTrader 8\bin\Custom\Strategies\` |
| `Indicators/FootprintAbsorptionMap.cs` | `Documents\NinjaTrader 8\bin\Custom\Indicators\` |

Puis, dans NinjaTrader : **New → NinjaScript Editor → F5** (Compile).

## 2. Activer Tick Replay — obligatoire

Sans Tick Replay, `OnMarketData` n'est jamais appelé sur l'historique : aucun
footprint n'est reconstruit, et **aucun trade n'est pris**. La stratégie l'écrit
alors dans la fenêtre Output (`New → Output`).

* **Graphique** : Data Series → *Tick Replay* = `True`
* **Strategy Analyzer** : cocher *Tick Replay* dans les paramètres du backtest

Tick Replay exige que les données tick soient téléchargées pour la période
testée (Tools → Historical Data → Load).

## 3. Vérifier à l'œil avant de backtester

1. Graphique MES, type **Range**, valeur **8**.
2. Ajouter l'indicateur `FootprintAbsorptionMap`.
3. Comparer les triangles avec ce que montre votre add-on footprint sur les
   mêmes barres.
   * trop de triangles → augmenter `Volume zone / médiane glissante` (2,5 → 3,0)
   * aucun triangle → le baisser (2,5 → 2,0), et vérifier Tick Replay

Les LVN apparaissent en pointillés gris. Ils sont recalculés toutes les 20 barres
pour rester lisibles.

## 4. Backtester

**Strategy Analyzer** → `FootprintAbsorption` :

| Réglage | Valeur |
|---|---|
| Bars type / value | Range / 8 |
| Tick Replay | coché |
| Order Fill Resolution | High — 1 tick |
| Slippage | 1 tick |
| Commission | votre barème (≈ 1,24 $ A/R sur MES) |
| Min bars required | 200 |

## 5. Contrôle de cohérence

Deux repères pour savoir si le moteur tourne correctement :

* **~1,5 signal par jour** avec les réglages par défaut. Dix fois plus ou dix
  fois moins trahit un problème de configuration.
* La stratégie compare son footprint reconstruit au volume de chaque barre et
  signale dans la fenêtre Output tout écart supérieur à 5 % — symptôme de
  données tick incomplètes.

## 6. Paramètres

Ils sont regroupés en six sections dans la fenêtre de propriétés. Les valeurs par
défaut sont celles calibrées dans `docs/METHODOLOGIE.md` ; les deux qui méritent
d'être ajustés en premier sur vos données sont **`Volume zone / médiane
glissante`** (sélectivité de l'absorption) et **`LVN : volume max en % du POC`**
(nombre de LVN retenus).
