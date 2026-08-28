# Méthodologie — backtest footprint / absorption (MES, Range 8 ticks)

## 1. Le problème posé

Backtester une lecture footprint (absorption sur LVN, absorption au retest d'une
structure) demande **des données tick avec le côté agresseur**. Ni un flux OHLC,
ni des barres agrégées ne suffisent : toute l'information est dans la
répartition bid/ask niveau de prix par niveau de prix.

Ce dépôt contient donc deux choses complémentaires :

| Composant | Rôle |
|---|---|
| `NinjaTrader8/` | La stratégie C# à exécuter sur **vos** données tick, dans le Strategy Analyzer avec Tick Replay |
| `backtest/` | Un moteur Python indépendant + un simulateur de marché, qui sert à **valider la logique** et à mesurer ce qu'elle détecte réellement |

Le moteur Python n'est pas un substitut à un backtest sur données réelles. Il
répond à une question différente, et plus fondamentale : *ce détecteur
d'absorption détecte-t-il vraiment des absorptions, et cette information
a-t-elle une valeur, indépendamment de la chance ?*

## 2. Le simulateur : un carnet, pas des bougies

Générer des bougies aléatoires puis y « peindre » du footprint produirait des
données qui valident n'importe quoi. Le générateur (`engine/synthetic.py`)
simule donc le mécanisme sous-jacent :

* chaque niveau de prix possède une **liquidité passive** ;
* le prix n'avance d'un tick que lorsque le volume agressif **cumulé à ce
  niveau, dans ce sens** dépasse cette liquidité ;
* cette consommation **persiste** entre les visites : une file d'ordres se vide
  progressivement, elle ne se réinitialise pas à chaque aller-retour du prix.

Trois propriétés en découlent, sans avoir eu besoin de les programmer :

* les niveaux à forte liquidité accumulent du volume → **HVN** ;
* les niveaux à faible liquidité sont traversés vite → **LVN** ;
* un gros ordre passif (« iceberg ») posé sur un niveau produit exactement la
  signature d'une absorption : le prix vient le chercher, des centaines de
  contrats s'exécutent contre lui, le prix ne passe pas.

Point important : **un ordre passif ne résiste que du côté où il est posé.**
Un iceberg à l'achat sur le plus bas encaisse les vendeurs agressifs mais
n'empêche pas les acheteurs de faire remonter le prix. C'est ce qui crée le
déséquilibre bid/ask caractéristique lu sur le footprint. La première version du
simulateur bloquait le prix dans les deux sens : le volume au niveau ressortait
à 50/50 et aucune absorption n'était lisible. C'est le bug qui a été corrigé.

### Trois jeux de données

| Mode | Tendances | Relief de liquidité (LVN) | Absorptions injectées |
|---|---|---|---|
| `structured` | oui | oui | oui |
| `placebo` | oui | oui | **non** |
| `null` | non | non | non |

Le mode `placebo` est le témoin décisif : marché génératif **identique**, avec
ses LVN et ses tendances, mais dont on a retiré la seule chose que la stratégie
prétend exploiter. Si la performance survit au placebo, c'est que l'edge ne
vient pas de l'absorption.

### Vérité terrain

Chaque absorption injectée est journalisée (`AbsorptionEvent`) avec le prix, le
volume absorbé et l'instant où elle devient **lisible** (et non l'instant où le
prix quitte le niveau). On peut donc mesurer, et pas seulement supposer, ce que
le détecteur attrape.

## 3. Barres Range

Règle appliquée : la barre se clôture dès qu'un tick ferait dépasser 8 ticks de
hauteur ; ce tick ouvre la barre suivante.

* chaque tick appartient à exactement une barre → **volumes footprint exacts** ;
* pas de gap artificiel entre barres.

NinjaTrader impose en plus la hauteur exacte de la barre et décale l'ouverture
d'un tick, ce qui crée de petits gaps. Les volumes par barre sont quasi
identiques, l'open/close peut différer d'un tick. Un test vérifie qu'aucun tick
n'est perdu : `sum(volumes de barres) == sum(tailles de ticks)`.

Statistiques obtenues sur le jeu `structured`, cohérentes avec un MES Range 8 :
~180 barres par séance, ~1 700 contrats par barre, ~50 points d'amplitude
quotidienne.

## 4. Détection de l'absorption — et l'erreur à ne pas commettre

Le premier critère naturel, « le volume à l'extrême est supérieur au volume
moyen par niveau de la barre », **ne détecte rien**. Dans une barre Range,
l'extrême est par construction moins échangé que le milieu : le prix n'y passe
qu'une fois. Mesure sur 1 000 barres : ce rapport vaut 0,78 en médiane, et son
maximum atteint à peine 2,3.

La référence utilisée est donc la **médiane glissante du volume des zones
extrêmes des 50 barres précédentes** : on cherche un extrême anormalement chargé
par rapport à ce que fait habituellement le marché.

### Calibrage sur la vérité terrain

Distributions mesurées (3 tirages × 25 jours), sur les barres dont le plus bas
coïncide à ±1 tick avec une absorption réellement injectée, contre toutes les
autres :

| Feature | Absorption (médiane) | Bruit (médiane / p90) | Verdict |
|---|---|---|---|
| volume zone / médiane glissante | **3,69** | 0,99 / 2,14 | très discriminant |
| volume barre / médiane glissante | **1,86** | 1,00 / 2,01 | discriminant |
| concentration sur l'extrême | **1,52** | 0,87 / 1,42 | discriminant |
| part du flux agressif piégé | 0,53 | 0,47 / 0,62 | **faible** |
| delta de barre opposé | 71 % des cas | 49 % des cas | **faible** |

Deux critères « évidents » dans la littérature footprint se révèlent donc peu
discriminants ici. Le seuil initial `part du flux agressif ≥ 0,55` éliminait à
lui seul **65 % des vraies absorptions**. Il est ramené à 0,50, et l'exigence de
delta de barre opposé est désactivée par défaut (`require_opposite_delta`).

Filtre retenu (`ratio ≥ 2,5`, `concentration ≥ 1,15`, `volume barre ≥ 1,4×`,
`part ≥ 0,50`, clôture dans les 40 % opposés) : **rappel ~29 %, précision ~20 %**
au niveau de la barre, pour un taux de base de 0,52 %. Soit un rapport de
vraisemblance d'environ **40×**.

## 5. LVN

Profil de volume glissant sur 400 barres. Un LVN est un prix qui est
simultanément :

* sous 30 % du volume du POC ;
* un minimum local sur ±3 ticks ;
* hors des bords du profil (un bord n'est pas un LVN exploitable).

Les LVN contigus sont fusionnés (le plus creux gagne). Le contact est validé si
la zone extrême de la barre passe à ±2 ticks du LVN.

## 6. Structure et pullbacks

* pivot = plus haut/plus bas sur 3 barres à gauche et 3 à droite ;
* un pivot n'est utilisable qu'après ses 3 barres de droite — c'est une latence
  réelle, pas un détail : l'ignorer est la source de look-ahead la plus
  fréquente dans ce type de stratégie ;
* une résistance cassée devient un support (et réciproquement) ;
* tendance = séquence des deux derniers sommets et des deux derniers creux
  (HH+HL = haussière, LH+LL = baissière, sinon range) ;
* le setup pullback n'est armé que dans le sens de la tendance
  (`require_trend_alignment`).

## 7. Règles de trade

| Élément | Règle |
|---|---|
| Signal | absorption détectée au contact d'un LVN et/ou d'un niveau structurel |
| Confirmation | la barre suivante ne casse pas l'extrême absorbé |
| Entrée | au marché à l'ouverture de la barre d'après |
| Stop | 2 ticks derrière le niveau absorbé (rejet si < 4 ou > 24 ticks) |
| Objectif | 2 R |
| Breakeven | à +1 R, stop déplacé à +1 tick |
| Time stop | 60 barres |
| Séance | 09h40–15h50 ET, liquidation à 15h55 |
| Filtres | une position à la fois, pause de 3 barres après une sortie |

## 8. Modèle d'exécution (volontairement pessimiste)

* entrée au marché dégradée d'**1 tick** de slippage ;
* stop au marché dégradé d'1 tick ; objectif en limite, sans amélioration ;
* **1,24 $ de commissions aller-retour** par contrat (MES) ;
* si stop et objectif sont touchés dans la même barre, on suppose le stop.

Ce dernier point mérite d'être quantifié plutôt que promis : le compteur
`ambiguous_bars` du rapport indique combien de fois le cas s'est produit. Sur
520 trades : **1 fois**. Une barre Range de 8 ticks ne peut pratiquement pas
contenir à la fois un stop et un objectif distants de 2 R — c'est un avantage
méthodologique réel des barres Range sur les barres temps pour ce type de test.

## 9. Absence de look-ahead

Trois protections, dont une testée automatiquement :

1. **Ordre des opérations.** Le signal de la barre *i* est calculé sur l'état du
   profil, de la structure et de la référence de volume **avant** que la barre
   *i* n'y soit intégrée. Le volume de la barre en cours ne peut donc pas
   « boucher » le LVN qu'elle vient tester.
2. **Latence des pivots.** Un pivot n'existe qu'après ses barres de droite.
3. **Test de préfixe** (`TestPasDeLookAhead`) : rejouer le backtest sur les 60 %
   premières barres doit produire **exactement** les mêmes trades que le
   backtest complet tronqué au même endroit. Toute lecture d'une information
   future ferait diverger les deux séries.

## 10. Ce que cette étude prouve — et ce qu'elle ne prouve pas

**Elle prouve :**

* que la logique est implémentable, déterministe et sans look-ahead ;
* que le détecteur identifie bien des absorptions réelles, et non du bruit à
  gros volume : **48 %** des trades se déclenchent sur un niveau où un ordre
  passif a effectivement absorbé du volume, contre un taux de base de 0,5 %, et
  33 % sur une absorption effectivement suivie d'un retournement ;
* que **sans absorption dans les données, la stratégie ne trade quasiment
  pas** : 1,80 signal/jour en `structured` contre 0,064 en `placebo` — soit
  28 fois moins — et l'espérance y passe sous zéro (−0,33 R). L'edge mesuré
  vient bien du phénomène ciblé, pas de la structure de sortie 2 R ;
* que le résultat ne tient pas à un seuil ajusté au millimètre : de 1,5× à 4,0×
  la médiane glissante, le facteur de profit reste entre 1,60 et 1,87.

**Elle ne prouve pas :**

* que ces chiffres se retrouveront sur le MES réel. La rentabilité observée en
  mode `structured` dépend directement de la fréquence des absorptions injectées
  et de leur probabilité de retournement (62 % ici, un paramètre choisi). Ce
  nombre n'est **pas** une prévision de performance ;
* que les seuils calibrés ici sont optimaux sur données réelles. Ils sont
  optimaux *sur ce simulateur*. Ils constituent un point de départ raisonné, à
  re-valider sur vos données ;
* rien sur la microstructure absente du modèle : spread variable, files
  d'attente réelles, spoofing, annonces macro, gaps de séance ;
* rien sur l'objectif optimal. Le balayage donne une espérance croissante
  jusqu'à 3,5 R, mais c'est une propriété du simulateur — la dérive qui suit une
  absorption y persiste par construction. Sur données réelles, cette courbe est
  la première à re-mesurer.

Amplitude du résultat, tirage par tirage (6 marchés indépendants de 60 séances) :
espérance de **+0,20 R à +0,73 R**, tous positifs. Le sens du résultat est
stable ; son amplitude ne l'est pas. Sur 85 trades par tirage, un écart de ce
type est attendu — c'est aussi l'ordre de grandeur d'incertitude qu'il faut
garder en tête en lisant le chiffre global.

Le chemin correct est donc : **valider la logique ici → la faire tourner dans
NinjaTrader sur vos données tick → comparer les deux**.

## 11. Ce que le journal des trades apprend

`python3 backtest/analyse_trades.py` ventile les 520 trades par critère
(sortie complète dans `backtest/resultats/analyse_trades.txt`) :

| Profondeur du LVN | n | Réussite | Espérance |
|---|---|---|---|
| pas de LVN (pullback pur) | 108 | 47,2 % | **+0,571 R** |
| LVN modéré (0,75–0,90) | 182 | 41,8 % | +0,430 R |
| LVN profond (0,90–0,97) | 136 | 37,5 % | +0,305 R |
| niveau quasi vierge (≥ 0,97) | 59 | 37,3 % | **+0,176 R** |

Résultat contre-intuitif et cohérent avec le balayage du seuil LVN (0,35–0,40 du
POC fait mieux que 0,15) : **plus le nœud est vide, moins l'absorption y tient**.
Un vide total, le prix le traverse ; c'est le vide relatif, encore disputé, qui
retient. La force de l'absorption, elle, joue dans le sens attendu : +0,41 R
entre 2,5× et 3,5× la médiane, +0,63 R au-delà de 8×.

## 12. Passer sur données réelles

1. Ouvrir un graphique MES, `Range 8`, **Tick Replay activé** (Data Series →
   Tick Replay = True).
2. Poser `FootprintAbsorptionMap` : contrôler à l'œil, sur quelques séances, que
   les triangles tombent bien sur ce que votre add-on footprint montre comme
   absorption. Ajuster `VolumeMultiplier` si la détection est trop bavarde ou
   trop muette.
3. Strategy Analyzer → `FootprintAbsorption`, Tick Replay coché, période Range 8,
   `Order Fill Resolution = High (1 tick)`, commissions et slippage renseignés.
4. Comparer le nombre de signaux par jour à celui du moteur Python (~1,5/jour).
   Un écart d'un ordre de grandeur signale un problème de configuration — le
   plus souvent Tick Replay non actif : la stratégie l'écrit alors dans la
   fenêtre Output.
