# Footprint / Absorption — MES Range 8 ticks

Backtest d'une lecture footprint sur deux configurations :

* **Setup A — absorption sur LVN** : le prix vient chercher un nœud de faible
  volume, s'y fait absorber, et repart.
* **Setup B — pullback sur structure** : dans une tendance établie, le repli
  vient retester un niveau structurel et l'absorption y signe la reprise.

Le dépôt contient la stratégie NinjaTrader 8 à lancer sur vos données tick, et
un moteur de backtest Python indépendant qui valide la logique et mesure ce
qu'elle détecte réellement.

```
NinjaTrader8/
  Strategies/FootprintAbsorption.cs      stratégie complète (Tick Replay requis)
  Indicators/FootprintAbsorptionMap.cs   contrôle visuel : absorptions + LVN
backtest/
  engine/       moteur : barres range, footprint, profil/LVN, structure, exécution
  tests/        20 tests, dont un test anti look-ahead
  run_backtest.py
  resultats/    sortie de l'étude complète
docs/METHODOLOGIE.md                     comment c'est construit et ce que ça vaut
```

## Utilisation

Aucune dépendance : Python 3.8+ suffit (bibliothèque standard uniquement).

```bash
# étude complète : marché avec absorptions, placebo, marche aléatoire
python3 backtest/run_backtest.py --days 60 --seeds 6 --mode all

# un paramètre à la fois
python3 backtest/run_backtest.py --set trade.target_r_multiple=1.5 \
                                 --set absorption.volume_multiplier=3.0

# balayage d'un paramètre
python3 backtest/run_backtest.py --sweep absorption.volume_multiplier=1.5:4.0:0.5

# journal détaillé des trades
python3 backtest/run_backtest.py --csv trades.csv

# tests
python3 -m unittest discover -s backtest/tests -v
```

Tous les paramètres sont dans `backtest/engine/config.py` et pilotables en ligne
de commande via `--set section.cle=valeur`.

## Résultats sur données simulées

360 séances simulées, 1 contrat MES, 1 tick de slippage, 1,24 $ de commissions
aller-retour.

| Jeu de données | Signaux/jour | Trades | Réussite | PF | Espérance |
|---|---|---|---|---|---|
| **structured** (avec absorptions) | 1,86 | 529 | 41,2 % | **1,68** | **+0,40 R** |
| **placebo** (mêmes LVN, mêmes tendances, sans absorption) | 0,067 | 21 | 23,8 % | 0,99 | −0,22 R |
| **null** (marche aléatoire) | 0,011 | 4 | 0 % | 0,00 | −0,87 R |

Le placebo est le résultat qui compte : sur un marché génératif **identique**
auquel on a seulement retiré les absorptions, la stratégie ne trouve plus que
28 fois moins de signaux et son espérance retombe à zéro. Ce qu'elle exploite
est donc bien le phénomène visé, et non la structure de sortie à 2 R.

Contrôle de détection contre la vérité terrain : **~50 %** des trades se
déclenchent sur un niveau où un ordre passif a réellement absorbé du volume
(taux de base : 0,5 % des barres).

> ⚠️ Ces chiffres mesurent la **qualité de la logique**, pas une performance
> attendue sur le marché réel. La rentabilité du jeu `structured` dépend
> directement de la fréquence des absorptions injectées et de leur probabilité
> de retournement (62 %, paramètre choisi). Ils ne constituent en aucun cas une
> prévision. Lire `docs/METHODOLOGIE.md`, section 10.

## Enseignements utiles pour le réglage

* **Comparer le volume de l'extrême au volume moyen de la barre ne fonctionne
  pas** : dans une barre Range, l'extrême est par construction peu échangé
  (rapport médian 0,78). La référence doit être une médiane glissante des zones
  extrêmes des barres précédentes.
* **Deux critères classiques sont peu discriminants** : exiger un delta de barre
  opposé, et exiger plus de 55 % de flux agressif piégé. Ce dernier seuil
  éliminait 65 % des vraies absorptions ; il est ramené à 0,50.
* Les barres Range rendent la simulation d'exécution nettement plus fiable que
  les barres temps : sur 529 trades, stop et objectif n'ont été touchés dans la
  même barre qu'**une seule fois**.

## Passer sur vos données

1. Graphique MES `Range 8`, **Tick Replay activé** — sans lui, aucun footprint
   n'est construit et la stratégie n'ouvre aucune position (elle l'écrit dans la
   fenêtre Output).
2. Poser `FootprintAbsorptionMap` et vérifier à l'œil que les détections
   correspondent à ce que montre votre add-on footprint.
3. Strategy Analyzer → `FootprintAbsorption`, `Order Fill Resolution = High
   (1 tick)`, commissions et slippage renseignés.
4. Comparer le nombre de signaux par jour à celui du moteur Python (~1,5/jour) :
   un écart d'un ordre de grandeur trahit une erreur de configuration.

Le code C# n'a pas pu être compilé dans cet environnement (les assemblies
NinjaTrader ne sont pas publiques) : il est à compiler dans l'éditeur NinjaScript.
