#!/usr/bin/env python3
"""Génère le rapport PDF simplifié (2 pages A4) du backtest footprint.

    python3 backtest/generer_rapport_pdf.py [chemin_de_sortie.pdf]
"""

import sys

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

ENCRE = colors.HexColor("#111820")
DOUX = colors.HexColor("#454F5B")
GRIS = colors.HexColor("#6B7682")
FILET = colors.HexColor("#D6DCE3")
FOND = colors.HexColor("#EEF1F5")
ASK = colors.HexColor("#1B6E9C")     # acheteur — accent
BID = colors.HexColor("#AE4832")     # vendeur

styles = getSampleStyleSheet()


def st(name, **kw):
    base = dict(fontName="Helvetica", fontSize=9.5, leading=13.5, textColor=DOUX,
                alignment=TA_LEFT, spaceAfter=6)
    base.update(kw)
    return ParagraphStyle(name, **base)


TITRE = st("titre", fontName="Helvetica-Bold", fontSize=20, leading=23,
           textColor=ENCRE, spaceAfter=4)
SOUS = st("sous", fontSize=10.5, leading=15, textColor=DOUX, spaceAfter=2)
EYEBROW = st("eyebrow", fontName="Helvetica-Bold", fontSize=7.5, leading=10,
             textColor=ASK, spaceAfter=5)
H2 = st("h2", fontName="Helvetica-Bold", fontSize=12.5, leading=15,
        textColor=ENCRE, spaceBefore=10, spaceAfter=5)
H3 = st("h3", fontName="Helvetica-Bold", fontSize=9.5, leading=13,
        textColor=ENCRE, spaceBefore=8, spaceAfter=2)
CORPS = st("corps")
PETIT = st("petit", fontSize=8.5, leading=12, textColor=GRIS)
LEGENDE = st("legende", fontSize=8, leading=11, textColor=GRIS, spaceAfter=10)


def table(donnees, largeurs, aligns=None, surligne=None, taille=8.8):
    t = Table(donnees, colWidths=largeurs, hAlign="LEFT")
    style = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), GRIS),
        ("FONT", (0, 1), (-1, -1), "Helvetica", taille),
        ("TEXTCOLOR", (0, 1), (-1, -1), ENCRE),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor("#B9C2CB")),
        ("LINEBELOW", (0, 1), (-1, -2), 0.4, FILET),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
    ]
    for col in (aligns or []):
        style.append(("ALIGN", (col, 0), (col, -1), "RIGHT"))
    if surligne is not None:
        style += [("BACKGROUND", (0, surligne), (-1, surligne), FOND),
                  ("FONT", (0, surligne), (-1, surligne), "Helvetica-Bold", taille)]
    t.setStyle(TableStyle(style))
    return t


def encadre(titre, texte, couleur=ASK):
    inner = [[Paragraph(f"<b>{titre}</b>", st("bt", fontName="Helvetica-Bold",
                                             fontSize=9.5, textColor=ENCRE, spaceAfter=3)),],
             [Paragraph(texte, st("bc", fontSize=9, leading=12.5))]]
    t = Table(inner, colWidths=[163 * mm], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), FOND),
        ("LINEBEFORE", (0, 0), (0, -1), 2.2, couleur),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (0, 0), 9),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 1),
        ("TOPPADDING", (0, -1), (-1, -1), 0),
    ]))
    return t


def pied(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(FILET)
    canvas.setLineWidth(0.5)
    canvas.line(23 * mm, 15 * mm, A4[0] - 23 * mm, 15 * mm)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(GRIS)
    canvas.drawString(23 * mm, 10.5 * mm,
                      "Backtest footprint / absorption — MES Range 8 ticks — données simulées")
    canvas.drawRightString(A4[0] - 23 * mm, 10.5 * mm, "Page %d/2" % doc.page)
    canvas.restoreState()


def construire(sortie):
    doc = SimpleDocTemplate(sortie, pagesize=A4,
                            leftMargin=23 * mm, rightMargin=23 * mm,
                            topMargin=20 * mm, bottomMargin=22 * mm,
                            title="Backtest absorption LVN et structure",
                            author="Backtest footprint MES")
    L = 163 * mm
    s = []

    # ---------------------------------------------------------------- page 1
    s.append(Paragraph("BACKTEST — MES — RANGE 8 TICKS", EYEBROW))
    s.append(Paragraph("L’absorption sur LVN et en pullback tient-elle debout ?", TITRE))
    s.append(Paragraph(
        "Deux configurations testées : absorption au contact d’un nœud de faible volume (LVN), "
        "et absorption au retest d’un niveau structurel dans le sens de la tendance.", SOUS))
    s.append(Spacer(1, 12))

    s.append(table(
        [["LE TEST", ""],
         ["Échantillon", "360 séances simulées, 64 343 barres range"],
         ["Position", "1 contrat MES, une position à la fois"],
         ["Coûts facturés", "1 tick de slippage à l’entrée et au stop, 1,24 $ de commissions A/R"],
         ["Gestion", "stop 2 ticks sous le niveau absorbé, objectif 2 R, breakeven à +1 R"]],
        [34 * mm, L - 34 * mm]))
    s.append(Spacer(1, 4))

    s.append(Paragraph("Le résultat qui compte", H2))
    s.append(Paragraph(
        "Le jeu <b>placebo</b> est généré exactement comme le premier — mêmes tendances, mêmes LVN, "
        "même relief de liquidité — mais privé de la seule chose que la stratégie prétend exploiter.",
        CORPS))
    s.append(table(
        [["JEU DE DONNÉES", "SIGNAUX/J", "TRADES", "RÉUSSITE", "PF", "ESPÉRANCE"],
         ["Avec absorptions", "1,80", "520", "41,5 %", "1,68", "+0,41 R"],
         ["Placebo — sans absorption", "0,06", "20", "20,0 %", "0,75", "−0,33 R"],
         ["Marche aléatoire", "0,01", "4", "0 %", "0,00", "−0,87 R"]],
        [52 * mm, 21 * mm, 19 * mm, 21 * mm, 16 * mm, L - 129 * mm],
        aligns=[1, 2, 3, 4, 5], surligne=1))
    s.append(Paragraph(
        "Retirer les absorptions divise le nombre de signaux par 28 et fait passer l’espérance "
        "sous zéro. Le déclencheur n’est donc pas « une grosse barre sur un LVN » — configuration "
        "que le placebo produit tout autant.", LEGENDE))

    s.append(Paragraph("Par configuration", H2))
    s.append(table(
        [["SETUP", "TRADES", "RÉUSSITE", "PF", "ESPÉRANCE"],
         ["Pullback sur structure", "108", "47,2 %", "2,23", "+0,571 R"],
         ["Absorption sur LVN", "320", "40,9 %", "1,64", "+0,386 R"],
         ["Les deux à la fois", "92", "37,0 %", "1,31", "+0,291 R"],
         ["Ensemble", "520", "41,5 %", "1,68", "+0,408 R"]],
        [62 * mm, 22 * mm, 24 * mm, 18 * mm, L - 126 * mm],
        aligns=[1, 2, 3, 4], surligne=4))
    s.append(Paragraph(
        "Le cumul des deux contextes fait moins bien que chacun séparément : un niveau à la fois "
        "LVN et structure a déjà été travaillé.", LEGENDE))

    s.append(Paragraph("Comment les trades se terminent", H2))
    s.append(table(
        [["SORTIE", "TRADES", "RÉSULTAT NET"],
         ["Objectif 2 R atteint", "207", "+6 558 $"],
         ["Stop", "191", "−3 873 $"],
         ["Breakeven, après +1 R", "110", "−136 $"]],
        [62 * mm, 24 * mm, L - 86 * mm], aligns=[1, 2]))
    s.append(Paragraph(
        "Les 110 sorties à breakeven sont la vraie question de réglage : un cinquième des trades "
        "neutralisés à +1 R. Les 12 trades restants sortent en fin de séance (+201 $).", LEGENDE))

    s.append(KeepTogether(encadre(
        "Contrôle de détection",
        "Les absorptions sont placées sciemment dans les données : on sait où elles sont. "
        "<b>48 % des trades</b> se déclenchent sur un niveau où un ordre passif a réellement absorbé "
        "du volume — taux de base : 0,5 % des barres.")))

    s.append(PageBreak())

    # ---------------------------------------------------------------- page 2
    s.append(Paragraph("CE QU’IL FAUT RETENIR", EYEBROW))
    s.append(Paragraph("Trois réglages à revoir", TITRE))
    s.append(Spacer(1, 10))

    s.append(Paragraph("1. Les LVN les plus creux sont les moins bons", H3))
    s.append(Paragraph(
        "L’espérance décroît avec la virginité du niveau. Le balayage du seuil de définition va "
        "dans le même sens : 0,35–0,40 du POC fait mieux que 0,15. Un vide total, le prix le "
        "traverse ; c’est le vide relatif, encore disputé, qui retient.", CORPS))
    s.append(table(
        [["PROFONDEUR DU LVN", "TRADES", "RÉUSSITE", "ESPÉRANCE"],
         ["Pas de LVN (pullback pur)", "108", "47,2 %", "+0,571 R"],
         ["LVN modéré", "182", "41,8 %", "+0,430 R"],
         ["LVN profond", "136", "37,5 %", "+0,305 R"],
         ["Niveau quasi vierge", "59", "37,3 %", "+0,176 R"]],
        [62 * mm, 22 * mm, 24 * mm, L - 108 * mm], aligns=[1, 2, 3]))

    s.append(Paragraph("2. Exiger 55 % de flux agressif piégé supprime 65 % des vraies absorptions", H3))
    s.append(Paragraph(
        "Le critère le plus intuitif — « il faut une nette domination vendeuse au plus bas » — est "
        "celui qui coûte le plus cher. Mesuré contre les absorptions réelles, il est presque non "
        "discriminant (0,53 contre 0,47 sur le reste du marché). Même constat pour l’exigence d’un "
        "delta de barre opposé. Seuil ramené à 0,50, delta opposé désactivé.", CORPS))

    s.append(Paragraph("3. Attention au dénominateur du volume", H3))
    s.append(Paragraph(
        "Comparer le volume de l’extrême au volume moyen des niveaux de la même barre <b>ne détecte "
        "rien</b> : dans une barre Range, l’extrême est par construction peu échangé, le prix n’y "
        "passe qu’une fois (rapport médian mesuré : 0,78). La référence doit être la médiane "
        "glissante des zones extrêmes des barres précédentes.", CORPS))
    s.append(Paragraph(
        "Bonne nouvelle : ce seuil n’est pas critique. De 1,5× à 4,0×, le facteur de profit reste "
        "entre 1,60 et 1,87 pendant que le nombre de trades passe de 573 à 326. Il pilote la "
        "fréquence, pas la qualité.", CORPS))

    s.append(Paragraph("Ce que ce backtest ne prouve pas", H2))
    s.append(Paragraph(
        "Il établit que la logique est implémentable, sans lecture du futur, et que ce qu’elle "
        "exploite est bien l’absorption. Il n’établit <b>rien</b> sur la rentabilité future :", CORPS))
    for ligne in [
        "les données sont simulées : la performance dépend directement de la fréquence des "
        "absorptions injectées et de leur probabilité de retournement (62 %, un paramètre choisi) ;",
        "les six tirages indépendants sont tous positifs, mais s’étalent de +0,20 R à +0,73 R : "
        "le sens du résultat est stable, son amplitude ne l’est pas ;",
        "l’espérance monte jusqu’à 3,5 R dans le balayage de l’objectif, mais c’est un artefact du "
        "simulateur — à ne surtout pas transposer tel quel ;",
        "spread variable, files d’attente réelles, annonces macro et gaps de séance sont absents "
        "du modèle.",
    ]:
        s.append(Paragraph("•&nbsp;&nbsp;" + ligne,
                           st("puce", fontSize=9, leading=12.5, leftIndent=10, spaceAfter=4)))

    s.append(KeepTogether(encadre(
        "La suite, sur vos données",
        "<b>1.</b> Graphique MES Range 8 avec <b>Tick Replay activé</b> — sans lui, aucun footprint "
        "n’est reconstruit et la stratégie ne prend aucun trade.<br/>"
        "<b>2.</b> Poser l’indicateur <i>FootprintAbsorptionMap</i> et vérifier à l’œil que les "
        "détections correspondent à votre add-on footprint.<br/>"
        "<b>3.</b> Strategy Analyzer, <i>Order Fill Resolution</i> = High (1 tick). Repère de "
        "contrôle : environ 1,5 signal par jour. Dix fois plus ou dix fois moins trahit une erreur "
        "de configuration.", BID)))

    doc.build(s, onFirstPage=pied, onLaterPages=pied)
    return sortie


if __name__ == "__main__":
    chemin = sys.argv[1] if len(sys.argv) > 1 else "docs/rapport-backtest-absorption.pdf"
    print("PDF écrit :", construire(chemin))
