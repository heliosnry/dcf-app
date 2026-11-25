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


@dataclass
class DonneesEntreprise:
    """Données historiques et de marché (onglet Données historique)."""
    ca_dernier: float         # CA de la dernière année publiée
    bfr_dernier: float        # BFR de la dernière année publiée
    annee_derniere: int       # ex. 2025
    dette_nette: float
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
        fcff = nopat + da - capex - delta_bfr

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


def valoriser(h: Hypotheses, d: DonneesEntreprise, wacc_: float) -> dict:
    """
    Calcule toute la chaîne : FCFF actualisés, VT, VE, fonds propres,
    valeur par action et potentiel. Renvoie un dictionnaire de résultats.
    """
    proj = projeter_fcff(h, d)
    proj["Facteur d'actualisation"] = 1 / (1 + wacc_) ** proj["n"]
    proj["FCFF actualisé"] = proj["FCFF"] * proj["Facteur d'actualisation"]

    somme_fcff_actu = proj["FCFF actualisé"].sum()
    vt = valeur_terminale(proj["FCFF"].iloc[-1], wacc_, h.g)
    vt_actu = vt / (1 + wacc_) ** h.horizon

    ve = somme_fcff_actu + vt_actu
    fonds_propres = ve - d.dette_nette - d.minoritaires - d.provisions_retraites
    valeur_action = fonds_propres / d.nb_actions

    return {
        "projection": proj,
        "wacc": wacc_,
        "somme_fcff_actu": somme_fcff_actu,
        "vt": vt,
        "vt_actu": vt_actu,
        "ve": ve,
        "part_vt": vt_actu / ve,
        "fonds_propres": fonds_propres,
        "valeur_action": valeur_action,
        "potentiel": valeur_action / d.cours - 1,
    }


# ---------------------------------------------------------------------------
# 5. Sensibilité
# ---------------------------------------------------------------------------

def sensibilite_wacc_g(h: Hypotheses, d: DonneesEntreprise,
                       waccs: list[float], gs: list[float]) -> pd.DataFrame:
    """Tableau de la valeur par action : WACC en lignes, g en colonnes."""
    tableau = {}
    for g in gs:
        h_g = Hypotheses(**{**h.__dict__, "g": g})
        tableau[g] = [valoriser(h_g, d, w)["valeur_action"] for w in waccs]
    return pd.DataFrame(tableau, index=waccs)


# ---------------------------------------------------------------------------
# Test rapide : doit redonner les chiffres de l'Excel Saint-Gobain
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    hyp = Hypotheses(
        croissance_ca=0.115, marge_ebit=0.105, da_ca=0.04, capex_ca=0.0425,
        bfr_ca=0.021, taux_is=0.26, g=0.02, horizon=5,
    )
    sg = DonneesEntreprise(
        ca_dernier=46483, bfr_dernier=968, annee_derniere=2025,
        dette_nette=10356, minoritaires=568, provisions_retraites=1444,
        nb_actions=489.9, cours=73.20,
    )
    params = ParametresWACC(
        taux_sans_risque=0.0483, beta=1.328, prime_risque=0.0501,
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