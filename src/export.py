"""
export.py — Export du modèle en Excel, AVEC les formules

Le fichier produit recalcule tout seul : on peut modifier une hypothèse dans
l'onglet « Hypothèses » et voir la valeur par action changer, comme dans l'Excel
du cas pratique. Conventions : bleu = saisie, noir = calcul, vert = lien entre onglets.
"""

from io import BytesIO

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from dcf import DonneesEntreprise, Hypotheses, ParametresWACC

POLICE = "Arial"
BLEU, NOIR, VERT, BLANC, GRIS = "0000FF", "000000", "008000", "FFFFFF", "7F7F7F"
MARINE = "1F3864"
FOND_ENTETE = PatternFill("solid", fgColor=MARINE)
FOND_CLE = PatternFill("solid", fgColor="FFF2CC")
FOND_SOUS = PatternFill("solid", fgColor="D9E1F2")
TRAIT = Side(style="thin", color="BFBFBF")
CADRE = Border(left=TRAIT, right=TRAIT, top=TRAIT, bottom=TRAIT)

NUM = '#,##0;(#,##0);"-"'
PCT = "0.00%"
MONNAIE = "#,##0.00"  # montants par action (cours, valeur), quelle que soit la devise


def _cellule(ws, ref, valeur, couleur=NOIR, fmt=None, gras=False, fond=None):
    """Écrit une valeur ou une formule dans la cellule ref (ex. « C4 ») avec la police, la couleur,
    le format et le cadre du classeur. Renvoie la cellule."""
    c = ws[ref]
    c.value = valeur
    c.font = Font(name=POLICE, size=10, color=couleur, bold=gras)
    c.border = CADRE
    if fmt:
        c.number_format = fmt
    if fond:
        c.fill = fond
    return c


def _entete(ws, ref, texte):
    """Écrit un en-tête de tableau (blanc sur fond bleu marine) dans la cellule ref."""
    c = ws[ref]
    c.value = texte
    c.font = Font(name=POLICE, size=10, bold=True, color=BLANC)
    c.fill = FOND_ENTETE
    c.alignment = Alignment(horizontal="center")
    c.border = CADRE


def _titre(ws, ref, texte, taille=13):
    """Écrit le titre d'un onglet dans la cellule ref."""
    ws[ref] = texte
    ws[ref].font = Font(name=POLICE, size=taille, bold=True, color=MARINE)


def exporter_excel(
    nom: str,
    devise: str,
    h: Hypotheses,
    d: DonneesEntreprise,
    p: ParametresWACC,
    spread: float,
    decalage: float = 0.0,
    dividendes_par_action: float = 0.0,
    tableaux: dict[str, pd.DataFrame] | None = None,
) -> bytes:
    """Construit le classeur Excel du DCF, avec ses formules.

    Reçoit les mêmes objets que dcf.valoriser (hypothèses, entreprise, paramètres du WACC), le spread,
    les options de date de valorisation et, en option, des tableaux de l'appli (sensibilités,
    scénarios) copiés en valeurs. Renvoie le fichier en octets (pour st.download_button)."""
    wb = Workbook()

    # ------------------------------------------------------------------ Hypothèses
    ws = wb.active
    ws.title = "Hypothèses"
    ws.sheet_view.showGridLines = False
    _titre(ws, "B2", f"{nom} — hypothèses (montants en millions de {devise})")

    lignes = [
        (4, "Croissance du CA", h.croissance_ca, PCT),
        (5, "Marge EBIT", h.marge_ebit, PCT),
        (6, "D&A / CA", h.da_ca, PCT),
        (7, "Capex / CA", h.capex_ca, PCT),
        (8, "BFR / CA", h.bfr_ca, PCT),
        (9, "Loyers IFRS 16 / CA", h.loyers_ca, PCT),
        (10, "Taux d'impôt", h.taux_is, PCT),
        (11, "Croissance à l'infini (g)", h.g, PCT),
        (12, "Horizon (années) — structure fixée à l'export", h.horizon, "0"),
        (13, "Valeur terminale : 1 = Gordon, 2 = ROIC", 2 if h.roic_terminal else 1, "0"),
        (14, "ROIC terminal (si méthode 2)", h.roic_terminal or 0.12, PCT),
        (15, "Convention mi-année : 1 = oui, 0 = non", 1 if h.mi_annee else 0, "0"),
        (16, "Années écoulées depuis la clôture", decalage, "0.00"),
        (17, "Dividendes versés depuis la clôture (par action)", dividendes_par_action, MONNAIE),
        (19, "Taux sans risque", p.taux_sans_risque, PCT),
        (20, "Bêta", p.beta, "0.000"),
        (21, "Prime de risque", p.prime_risque, PCT),
        (22, "Spread de crédit", spread, PCT),
        (27, "CA de la dernière année publiée", d.ca_dernier, NUM),
        (28, "BFR de la dernière année publiée", d.bfr_dernier, NUM),
        (29, "Dette nette", d.dette_nette, NUM),
        (30, "Intérêts minoritaires", d.minoritaires, NUM),
        (31, "Provisions retraites", d.provisions_retraites, NUM),
        (32, "Nombre d'actions (millions)", d.nb_actions, "#,##0.0"),
        (33, "Cours actuel", d.cours, MONNAIE),
    ]
    _entete(ws, "B3", "Hypothèse")
    _entete(ws, "C3", "Valeur")
    _entete(ws, "B26", "Données de l'entreprise")
    _entete(ws, "C26", "Valeur")
    _entete(ws, "B18", "Coût du capital")
    _entete(ws, "C18", "Valeur")
    for r, lib, val, fmt in lignes:
        _cellule(ws, f"B{r}", lib)
        _cellule(ws, f"C{r}", val, BLEU, fmt)

    calculs = [
        (23, "Kd = taux sans risque + spread", "=C19+C22", PCT),
        (24, "Ke = rf + bêta × prime (MEDAF)", "=C19+C20*C21", PCT),
        (35, "Capitalisation E", "=C32*C33", NUM),
        (36, "Dette D = max(dette nette ; 0) + autres dettes", "=MAX(C29,0)+C43", NUM),
        (
            37,
            "WACC = E/(D+E) × Ke + D/(D+E) × Kd × (1 − t)",
            "=C35/(C35+C36)*C24+C36/(C35+C36)*C23*(1-C10)",
            PCT,
        ),
    ]
    for r, lib, f, fmt in calculs:
        _cellule(ws, f"B{r}", lib)
        _cellule(ws, f"C{r}", f, NOIR, fmt)
    for ref in ("B37", "C37"):
        ws[ref].fill = FOND_CLE
        ws[ref].font = Font(name=POLICE, size=10, bold=True)

    # Compléments : phase de transition et pont VE → fonds propres
    _entete(ws, "B40", "Compléments")
    _entete(ws, "C40", "Valeur")
    complements = [
        (
            41,
            "Années de transition (croissance → g) — structure fixée à l'export",
            h.annees_transition,
            "0",
        ),
        (42, "Participations mises en équivalence (+)", d.participations, NUM),
        (43, "Autres dettes : hybrides, affacturage… (−)", d.autres_dettes, NUM),
        (44, "Actions dilutives (millions)", d.actions_dilutives, "#,##0.0"),
    ]
    for r, lib, val, fmt in complements:
        _cellule(ws, f"B{r}", lib)
        _cellule(ws, f"C{r}", val, BLEU, fmt)
    ws["B46"] = "Bleu : saisie modifiable · Noir : calcul · Vert : lien vers un autre onglet."
    ws["B46"].font = Font(name=POLICE, size=9, italic=True, color=GRIS)
    ws.column_dimensions["A"].width = 2
    ws.column_dimensions["B"].width = 48
    ws.column_dimensions["C"].width = 14

    # ------------------------------------------------------------------ DCF
    dc = wb.create_sheet("DCF")
    dc.sheet_view.showGridLines = False
    _titre(dc, "B2", f"DCF {nom} — projection des flux (M{devise})")
    H = "Hypothèses!"
    n = h.horizon + h.annees_transition
    cols = [get_column_letter(3 + i) for i in range(n)]
    der = cols[-1]

    _entete(dc, "B3", "n")
    _entete(dc, "B4", "Année")
    for i, c in enumerate(cols):
        _entete(dc, f"{c}3", i + 1)
        _entete(dc, f"{c}4", d.annee_derniere + i + 1)
    libelles = {
        5: "CA",
        6: "EBIT",
        7: "NOPAT",
        8: "D&A",
        9: "Capex",
        10: "BFR",
        11: "ΔBFR",
        12: "Loyers IFRS 16",
        13: "FCFF",
        14: "Facteur d'actualisation",
        15: "FCFF actualisé",
        16: "Croissance du CA de l'année",
    }
    for r, lib in libelles.items():
        _cellule(dc, f"B{r}", lib, gras=r in (5, 13, 15))

    for i, c in enumerate(cols):
        prec = cols[i - 1] if i else None
        # Phase 1 : croissance retenue ; phase 2 : glissement linéaire vers g
        croissance = (
            f"=IF({c}3<={H}$C$12,{H}$C$4,{H}$C$4+({H}$C$11-{H}$C$4)*({c}3-{H}$C$12)/({H}$C$41+1))"
        )
        _cellule(dc, f"{c}16", croissance, VERT, PCT)
        ca = f"={H}$C$27*(1+{c}16)" if i == 0 else f"={prec}5*(1+{c}16)"
        delta_bfr = f"={c}10-{H}$C$28" if i == 0 else f"={c}10-{prec}10"
        formules = {
            5: (ca, VERT if i == 0 else NOIR),
            6: (f"={c}5*{H}$C$5", VERT),
            7: (f"={c}6*(1-{H}$C$10)", VERT),
            8: (f"={c}5*{H}$C$6", VERT),
            9: (f"={c}5*{H}$C$7", VERT),
            10: (f"={c}5*{H}$C$8", VERT),
            11: (delta_bfr, VERT if i == 0 else NOIR),
            12: (f"={c}5*{H}$C$9", VERT),
            13: (f"={c}7+{c}8-{c}9-{c}11-{c}12", NOIR),
            14: (f"=1/(1+{H}$C$37)^({c}3-0.5*{H}$C$15)", VERT),
            15: (f"={c}13*{c}14", NOIR),
        }
        for r, (f, coul) in formules.items():
            _cellule(dc, f"{c}{r}", f, coul, "0.000" if r == 14 else NUM, gras=r in (5, 13, 15))
    for c in ["B"] + cols:
        dc[f"{c}13"].fill = FOND_SOUS

    g, w = f"{H}$C$11", f"{H}$C$37"
    valo = [
        (18, "Somme des FCFF actualisés", f"=SUM(C15:{der}15)", NUM),
        (
            19,
            "Valeur terminale (année N)",
            f"=IF({H}$C$13=2,{der}7*(1+{g})*(1-{g}/{H}$C$14)/({w}-{g}),{der}13*(1+{g})/({w}-{g}))",
            NUM,
        ),
        (20, "VT actualisée", f"=C19/(1+{w})^({der}3-0.5*{H}$C$15)", NUM),
        (21, "Valeur d'entreprise à la clôture", "=C18+C20", NUM),
        (22, "Valeur d'entreprise à date", f"=C21*(1+{w})^{H}$C$16", NUM),
        (
            23,
            "Valeur des fonds propres",
            f"=C22-{H}$C$29-{H}$C$30-{H}$C$31-{H}$C$43+{H}$C$42-{H}$C$17*{H}$C$32",
            NUM,
        ),
        (24, "Valeur par action (diluée)", f"=C23/({H}$C$32+{H}$C$44)", MONNAIE),
        (25, "Potentiel vs cours", f"=C24/{H}$C$33-1", "0.0%"),
        (26, "Part de la VT dans la VE", "=C20/C21", "0.0%"),
    ]
    _entete(dc, "B17", "Valorisation")
    _entete(dc, "C17", f"M{devise}")
    for r, lib, f, fmt in valo:
        _cellule(dc, f"B{r}", lib)
        _cellule(dc, f"C{r}", f, VERT if "Hypothèses!" in f else NOIR, fmt)
    for r in (24, 25):
        for ref in (f"B{r}", f"C{r}"):
            dc[ref].fill = FOND_CLE
            dc[ref].font = Font(
                name=POLICE,
                size=11,
                bold=True,
                color=dc[ref].font.color.rgb if dc[ref].font.color else NOIR,
            )
    dc["B28"] = (
        "FCFF = NOPAT + D&A − Capex − ΔBFR − loyers · VT Gordon = FCFF N × (1+g)/(WACC−g) · "
        "VT ROIC = NOPAT N × (1+g) × (1 − g/ROIC)/(WACC−g) · Fonds propres = VE − dette nette − "
        "minoritaires − retraites − autres dettes + participations − dividendes versés"
    )
    dc["B28"].font = Font(name=POLICE, size=9, italic=True, color=GRIS)
    dc.column_dimensions["A"].width = 2
    dc.column_dimensions["B"].width = 32
    for c in cols:
        dc.column_dimensions[c].width = 12

    # ------------------------------------------------------------------ Tableaux de l'appli
    if tableaux:
        tb = wb.create_sheet("Résultats de l'appli")
        tb.sheet_view.showGridLines = False
        _titre(tb, "B2", "Tableaux calculés par l'application (valeurs figées)")
        ligne = 4
        for titre, df in tableaux.items():
            tb.cell(ligne, 2, titre).font = Font(name=POLICE, size=11, bold=True, color=MARINE)
            ligne += 1
            tb.cell(ligne, 2, "").border = CADRE
            for j, col in enumerate(df.columns):
                _entete(tb, f"{get_column_letter(3 + j)}{ligne}", str(col))
            for i, (etiquette, valeurs) in enumerate(df.iterrows()):
                _cellule(tb, f"B{ligne + 1 + i}", str(etiquette), gras=True)
                for j, v in enumerate(valeurs):
                    en_pct = str(df.columns[j]) in (
                        "Potentiel",
                        "Poids",
                    )  # ratios, pas des montants
                    fmt = ("0.0%" if en_pct else MONNAIE) if isinstance(v, (int, float)) else None
                    _cellule(tb, f"{get_column_letter(3 + j)}{ligne + 1 + i}", v, NOIR, fmt)
            ligne += len(df) + 3
        tb.column_dimensions["A"].width = 2
        tb.column_dimensions["B"].width = 22
        for j in range(3, 12):
            tb.column_dimensions[get_column_letter(j)].width = 15

    wb.active = 1
    tampon = BytesIO()
    wb.save(tampon)
    return tampon.getvalue()
