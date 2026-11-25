"""
dcf.py — Moteur de valorisation DCF (méthode FCFF)

Traduction en Python du modèle Excel « CP_Saint_Gobain.xlsx ».
Toutes les fonctions sont pures : elles reçoivent des nombres et renvoient
des nombres, sans aller chercher de données. La récupération des données
(yfinance) sera dans data.py, l'interface dans app.py.

Montants en millions d'euros, taux en décimal (0.02 = 2 %).
"""

from dataclasses import dataclass

import pandas as pd


# ---------------------------------------------------------------------------
# 1. Les entrées du modèle
# ---------------------------------------------------------------------------

@dataclass
class Hypotheses:
    """Hypothèses opérationnelles (onglet Hypothèses de l'Excel)."""
    croissance_ca: float      # croissance annuelle du CA
    marge_ebit: float         # EBIT / CA
    da_ca: float              # D&A / CA
    capex_ca: float           # Capex / CA
    bfr_ca: float             # BFR / CA (niveau, pas variation)
    taux_is: float            # taux d'impôt
    g: float                  # croissance à l'infini
    horizon: int = 5          # nombre d'années projetées
    loyers_ca: float = 0.0    # IFRS 16 : renouvellement des contrats de location / CA
    roic_terminal: float | None = None   # None = Gordon classique ; sinon formule ROIC
    mi_annee: bool = False    # convention de milieu d'année pour l'actualisation


@dataclass
class DonneesEntreprise:
    """Données historiques et de marché (onglet Données historique)."""
    ca_dernier: float         # CA de la dernière année publiée
    bfr_dernier: float        # BFR de la dernière année publiée
    annee_derniere: int       # ex. 2025
    dette_nette: float        # dettes locatives IFRS 16 comprises si Yahoo les inclut
    minoritaires: float
    provisions_retraites: float
    nb_actions: float         # en millions
    cours: float              # en euros


@dataclass
class ParametresWACC:
    """Paramètres du coût du capital."""
    taux_sans_risque: float   # TEC 10
    beta: float
    prime_risque: float       # prime de risque marché
    kd: float                 # coût de la dette avant impôt (TEC 10 + spread)
    taux_is: float
    capitalisation: float     # E
    dette: float              # D


# ---------------------------------------------------------------------------
# 2. Coût du capital
# ---------------------------------------------------------------------------

def cout_fonds_propres(rf: float, beta: float, prime: float) -> float:
    """MEDAF : Ke = rf + bêta × prime de risque."""
    return rf + beta * prime


def wacc(p: ParametresWACC) -> float:
    """WACC = E/(D+E) × Ke + D/(D+E) × Kd × (1 − t)."""
    ke = cout_fonds_propres(p.taux_sans_risque, p.beta, p.prime_risque)
    poids_e = p.capitalisation / (p.capitalisation + p.dette)
    poids_d = p.dette / (p.capitalisation + p.dette)
    return poids_e * ke + poids_d * p.kd * (1 - p.taux_is)


# ---------------------------------------------------------------------------
# 3. Projection des flux
# ---------------------------------------------------------------------------

def projeter_fcff(h: Hypotheses, d: DonneesEntreprise) -> pd.DataFrame:
    """
    Projette le compte de résultat simplifié et les FCFF sur l'horizon.
    Renvoie un tableau avec une ligne par année (comme l'onglet DCF).

    FCFF = NOPAT + D&A − Capex − ΔBFR
    """
    lignes = []
    ca_prec = d.ca_dernier
    bfr_prec = d.bfr_dernier

    for n in range(1, h.horizon + 1):
        ca = ca_prec * (1 + h.croissance_ca)
        ebit = ca * h.marge_ebit
        nopat = ebit * (1 - h.taux_is)
        da = ca * h.da_ca
        capex = ca * h.capex_ca
        bfr = ca * h.bfr_ca
        delta_bfr = bfr - bfr_prec
        loyers = ca * h.loyers_ca
        fcff = nopat + da - capex - delta_bfr - loyers

        lignes.append({
            "n": n,
            "annee": d.annee_derniere + n,
            "CA": ca,
            "EBIT": ebit,
            "NOPAT": nopat,
            "D&A": da,
            "Capex": capex,
            "BFR": bfr,
            "ΔBFR": delta_bfr,
            "Loyers IFRS 16": loyers,
            "FCFF": fcff,
        })
        ca_prec, bfr_prec = ca, bfr

    return pd.DataFrame(lignes).set_index("annee")


# ---------------------------------------------------------------------------
# 4. Valorisation
# ---------------------------------------------------------------------------

def valeur_terminale(fcff_final: float, wacc_: float, g: float) -> float:
    """Gordon-Shapiro : VT = FCFF_N × (1 + g) / (WACC − g), en valeur de l'année N."""
    if g >= wacc_:
        raise ValueError(f"g ({g:.2%}) doit être inférieur au WACC ({wacc_:.2%})")
    return fcff_final * (1 + g) / (wacc_ - g)


def valeur_terminale_roic(nopat_final: float, wacc_: float, g: float, roic: float) -> float:
    """Formule des « value drivers » (McKinsey) :
    VT = NOPAT_N × (1 + g) × (1 − g / ROIC) / (WACC − g).
    Pour croître de g, l'entreprise doit réinvestir g / ROIC de son NOPAT :
    la croissance n'est plus « gratuite »."""
    if g >= wacc_:
        raise ValueError(f"g ({g:.2%}) doit être inférieur au WACC ({wacc_:.2%})")
    if roic <= 0:
        raise ValueError("Le ROIC terminal doit être positif.")
    return nopat_final * (1 + g) * (1 - g / roic) / (wacc_ - g)


def valoriser(h: Hypotheses, d: DonneesEntreprise, wacc_: float,
              decalage: float = 0.0, dividendes_par_action: float = 0.0) -> dict:
    """
    Calcule toute la chaîne : FCFF actualisés, VT, VE, fonds propres,
    valeur par action et potentiel. Renvoie un dictionnaire de résultats.

    decalage : années écoulées depuis la clôture (ex. 0.9 en novembre pour une clôture
               au 31/12). La VE est capitalisée au WACC jusqu'à aujourd'hui, et les
               dividendes versés depuis la clôture sont retirés (ils ont quitté l'entreprise).
    """
    proj = projeter_fcff(h, d)
    periode = proj["n"] - 0.5 if h.mi_annee else proj["n"]
    proj["Facteur d'actualisation"] = 1 / (1 + wacc_) ** periode
    proj["FCFF actualisé"] = proj["FCFF"] * proj["Facteur d'actualisation"]
    somme_fcff_actu = proj["FCFF actualisé"].sum()

    dernier = proj.iloc[-1]
    if h.roic_terminal is None:
        vt = valeur_terminale(dernier["FCFF"], wacc_, h.g)
    else:
        vt = valeur_terminale_roic(dernier["NOPAT"], wacc_, h.g, h.roic_terminal)
    vt_actu = vt / (1 + wacc_) ** (h.horizon - 0.5 if h.mi_annee else h.horizon)

    # ROIC implicite de la VT : g / taux de réinvestissement de la dernière année
    reinvesti = dernier["Capex"] - dernier["D&A"] + dernier["ΔBFR"] + dernier["Loyers IFRS 16"]
    taux_reinvestissement = (reinvesti / dernier["NOPAT"] if dernier["NOPAT"] > 0
                             else float("nan"))          # NOPAT nul ou négatif : ratio sans sens
    if h.roic_terminal is not None:
        roic_implicite = h.roic_terminal
    elif pd.isna(taux_reinvestissement):
        roic_implicite = float("nan")
    elif taux_reinvestissement > 0:
        roic_implicite = h.g / taux_reinvestissement
    else:
        roic_implicite = float("inf")   # croissance sans aucun réinvestissement

    ve_cloture = somme_fcff_actu + vt_actu
    ve = ve_cloture * (1 + wacc_) ** decalage
    passif = d.dette_nette + d.minoritaires + d.provisions_retraites
    fonds_propres = ve - passif - dividendes_par_action * d.nb_actions
    valeur_action = fonds_propres / d.nb_actions

    return {
        "projection": proj,
        "wacc": wacc_,
        "somme_fcff_actu": somme_fcff_actu,
        "vt": vt,
        "vt_actu": vt_actu,
        "ve_cloture": ve_cloture,
        "ve": ve,
        "part_vt": vt_actu / ve_cloture,
        "fonds_propres": fonds_propres,
        "valeur_action": valeur_action,
        "valeur_action_cloture": (ve_cloture - passif) / d.nb_actions,
        "potentiel": valeur_action / d.cours - 1,
        "taux_reinvestissement": taux_reinvestissement,
        "roic_implicite": roic_implicite,
    }


# ---------------------------------------------------------------------------
# 5. Sensibilité
# ---------------------------------------------------------------------------

def sensibilite_wacc_g(h: Hypotheses, d: DonneesEntreprise,
                       waccs: list[float], gs: list[float], **options) -> pd.DataFrame:
    """Tableau de la valeur par action : WACC en lignes, g en colonnes.
    options : decalage, dividendes_par_action (transmis à valoriser)."""
    tableau = {}
    for g in gs:
        h_g = Hypotheses(**{**h.__dict__, "g": g})
        tableau[g] = [valoriser(h_g, d, w, **options)["valeur_action"] for w in waccs]
    return pd.DataFrame(tableau, index=waccs)


def sensibilite_croissance_marge(h: Hypotheses, d: DonneesEntreprise, wacc_: float,
                                 croissances: list[float], marges: list[float],
                                 **options) -> pd.DataFrame:
    """Tableau de la valeur par action : croissance du CA en lignes, marge EBIT en colonnes."""
    tableau = {}
    for m in marges:
        tableau[m] = [valoriser(Hypotheses(**{**h.__dict__, "croissance_ca": c, "marge_ebit": m}),
                                d, wacc_, **options)["valeur_action"] for c in croissances]
    return pd.DataFrame(tableau, index=croissances)


# ---------------------------------------------------------------------------
# 6. DCF inversé : quelle hypothèse justifie le cours actuel ?
# ---------------------------------------------------------------------------

BORNES_INVERSE = {"croissance_ca": (-0.10, 0.30), "marge_ebit": (0.0, 0.60), "wacc": (None, 0.30)}


def dcf_inverse(h: Hypotheses, d: DonneesEntreprise, wacc_: float, parametre: str,
                cible: float | None = None, tolerance: float = 1e-6, **options) -> float | None:
    """
    Cherche la valeur du paramètre (« croissance_ca », « marge_ebit » ou « wacc ») pour laquelle
    la valeur par action égale la cible (le cours, par défaut), toutes les autres hypothèses
    restant inchangées. Méthode de la dichotomie : la valeur par action varie dans un seul sens
    avec chacun de ces paramètres. Renvoie None si la solution sort des bornes plausibles.
    """
    cible = d.cours if cible is None else cible
    bas, haut = BORNES_INVERSE[parametre]
    if parametre == "wacc":
        bas = h.g + 0.005                                  # le WACC doit rester > g

    def ecart(x: float) -> float:
        if parametre == "wacc":
            return valoriser(h, d, x, **options)["valeur_action"] - cible
        h_x = Hypotheses(**{**h.__dict__, parametre: x})
        return valoriser(h_x, d, wacc_, **options)["valeur_action"] - cible

    e_bas, e_haut = ecart(bas), ecart(haut)
    if e_bas * e_haut > 0:                                 # pas de solution dans les bornes
        return None
    for _ in range(200):
        milieu = (bas + haut) / 2
        e_milieu = ecart(milieu)
        if abs(e_milieu) < tolerance or haut - bas < 1e-10:
            return milieu
        if e_bas * e_milieu < 0:
            haut = milieu
        else:
            bas, e_bas = milieu, e_milieu
    return (bas + haut) / 2


# ---------------------------------------------------------------------------
# Test rapide : doit redonner les chiffres de l'Excel Saint-Gobain
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    hyp = Hypotheses(
        croissance_ca=0.0125, marge_ebit=0.115, da_ca=0.04, capex_ca=0.0425,
        bfr_ca=0.021, taux_is=0.26, g=0.02, horizon=5,
    )
    sg = DonneesEntreprise(
        ca_dernier=46483, bfr_dernier=968, annee_derniere=2025,
        dette_nette=10356, minoritaires=568, provisions_retraites=1444,
        nb_actions=489.9, cours=73.20,
    )
    params = ParametresWACC(
        taux_sans_risque=0.0483, beta=1.428, prime_risque=0.0501,
        kd=0.0483 + 0.0111, taux_is=0.26,
        capitalisation=489.9 * 73.20, dette=10356,
    )

    w = wacc(params)
    res = valoriser(hyp, sg, w)

    pd.options.display.float_format = "{:,.1f}".format
    print(res["projection"][["CA", "EBIT", "NOPAT", "D&A", "Capex", "ΔBFR", "FCFF"]])
    print()
    print(f"WACC              : {w:.3%}")
    print(f"VE                : {res['ve']:,.0f} M€")
    print(f"Part de la VT     : {res['part_vt']:.1%}")
    print(f"Valeur par action : {res['valeur_action']:.2f} €   (Excel : 67,93 €)")
    print(f"Potentiel         : {res['potentiel']:.1%}")
    print()
    sens = sensibilite_wacc_g(hyp, sg, [0.093, 0.098, 0.103, 0.108, 0.113],
                              [0.015, 0.0175, 0.02, 0.0225, 0.025])
    sens.index = [f"WACC {x:.2%}" for x in sens.index]
    sens.columns = [f"g {x:.2%}" for x in sens.columns]
    pd.options.display.float_format = "{:.2f}".format
    print("Sensibilité (lignes = WACC, colonnes = g) :")
    print(sens)
