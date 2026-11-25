"""
Tests automatiques du moteur DCF.
Lancer depuis le dossier dcf-app :  pytest
Référence : le modèle Excel CP_Saint_Gobain.xlsx (scénario central).
"""

import pytest

from dcf import (
    DonneesEntreprise,
    Hypotheses,
    ParametresWACC,
    cout_fonds_propres,
    projeter_fcff,
    sensibilite_wacc_g,
    valeur_terminale,
    valoriser,
    wacc,
)

# --- Données Saint-Gobain (scénario central de l'Excel) ---------------------


@pytest.fixture
def hyp():
    """Hypothèses opérationnelles du scénario central de l'Excel."""
    return Hypotheses(
        croissance_ca=0.0125,
        marge_ebit=0.115,
        da_ca=0.04,
        capex_ca=0.0425,
        bfr_ca=0.021,
        taux_is=0.26,
        g=0.02,
        horizon=5,
    )


@pytest.fixture
def sg():
    """Données de Saint-Gobain (rapport annuel 2025)."""
    return DonneesEntreprise(
        ca_dernier=46483,
        bfr_dernier=968,
        annee_derniere=2025,
        dette_nette=10356,
        minoritaires=568,
        provisions_retraites=1444,
        nb_actions=489.9,
        cours=73.20,
    )


@pytest.fixture
def params():
    """Paramètres du WACC de l'Excel (OAT 4,83 %, prime 5,01 %)."""
    return ParametresWACC(
        taux_sans_risque=0.0483,
        beta=1.428,
        prime_risque=0.0501,
        kd=0.0594,
        taux_is=0.26,
        capitalisation=489.9 * 73.20,
        dette=10356,
    )


# --- 1. On retrouve les chiffres de l'Excel ---------------------------------


def test_ke(params):
    assert cout_fonds_propres(0.0483, 1.428, 0.0501) == pytest.approx(0.11984, abs=1e-5)


def test_wacc_excel(params):
    assert wacc(params) == pytest.approx(0.102838, abs=1e-5)


def test_fcff_2026_excel(hyp, sg):
    proj = projeter_fcff(hyp, sg)
    assert proj.loc[2026, "CA"] == pytest.approx(47064.04, abs=0.01)
    assert proj.loc[2026, "FCFF"] == pytest.approx(3867.14, abs=0.01)


def test_valeur_par_action_excel(hyp, sg, params):
    res = valoriser(hyp, sg, wacc(params))
    assert res["ve"] == pytest.approx(45649.3, abs=0.5)
    assert res["valeur_action"] == pytest.approx(67.93, abs=0.01)
    assert res["potentiel"] == pytest.approx(-0.0719, abs=1e-4)


# --- 2. Règles de cohérence (vraies pour toute entreprise) ------------------


def test_fcff_egal_formule(hyp, sg):
    """FCFF = NOPAT + D&A − Capex − ΔBFR (− loyers, nuls ici), chaque année."""
    p = projeter_fcff(hyp, sg)
    attendu = p["NOPAT"] + p["D&A"] - p["Capex"] - p["ΔBFR"]
    assert (p["FCFF"] - attendu).abs().max() < 1e-9


def test_horizon_respecte(hyp, sg):
    assert len(projeter_fcff(hyp, sg)) == hyp.horizon


def test_delta_bfr_nul_sans_croissance(hyp, sg):
    """Si le CA ne bouge pas et que le BFR de départ correspond au ratio,
    le ΔBFR doit être nul."""
    hyp.croissance_ca = 0.0
    sg.bfr_dernier = sg.ca_dernier * hyp.bfr_ca
    p = projeter_fcff(hyp, sg)
    assert p["ΔBFR"].abs().max() < 1e-9


def test_g_superieur_wacc_refuse():
    with pytest.raises(ValueError):
        valeur_terminale(4000, wacc_=0.09, g=0.10)


def test_part_vt_entre_0_et_1(hyp, sg, params):
    res = valoriser(hyp, sg, wacc(params))
    assert 0 < res["part_vt"] < 1


def test_wacc_plus_haut_valeur_plus_basse(hyp, sg):
    v1 = valoriser(hyp, sg, 0.09)["valeur_action"]
    v2 = valoriser(hyp, sg, 0.11)["valeur_action"]
    assert v1 > v2


def test_sensibilite_coherente(hyp, sg):
    """Dans le tableau : la valeur baisse quand le WACC monte, monte quand g monte,
    et la case centrale est égale à la valorisation directe."""
    waccs, gs = [0.093, 0.098, 0.103], [0.015, 0.02, 0.025]
    t = sensibilite_wacc_g(hyp, sg, waccs, gs)
    assert (t.diff(axis=0).iloc[1:] < 0).all().all()
    assert (t.diff(axis=1).iloc[:, 1:] > 0).all().all()
    assert t.loc[0.098, 0.02] == pytest.approx(valoriser(hyp, sg, 0.098)["valeur_action"])


# --- 3. Options de méthode (revue d'analyste) -------------------------------


def test_options_par_defaut_inchangees(hyp, sg, params):
    """Sans option, on retrouve exactement l'Excel."""
    res = valoriser(hyp, sg, wacc(params), decalage=0.0, dividendes_par_action=0.0)
    assert res["valeur_action"] == pytest.approx(67.93, abs=0.01)
    assert res["valeur_action_cloture"] == pytest.approx(res["valeur_action"])


def test_roic_implicite_saint_gobain(hyp, sg, params):
    """VT classique : 2 % de croissance avec 3 % du NOPAT réinvesti → ROIC implicite ≈ 62 %."""
    res = valoriser(hyp, sg, wacc(params))
    assert res["roic_implicite"] == pytest.approx(0.617, abs=0.005)


def test_vt_roic_formule(hyp, sg):
    from dcf import valeur_terminale_roic

    vt = valeur_terminale_roic(4000, wacc_=0.10, g=0.02, roic=0.12)
    assert vt == pytest.approx(4000 * 1.02 * (1 - 0.02 / 0.12) / 0.08)


def test_vt_roic_plus_prudente(hyp, sg, params):
    """Avec un ROIC terminal réaliste (12 %), la valeur baisse (≈ 59 €)."""
    w = wacc(params)
    hyp.roic_terminal = 0.12
    res = valoriser(hyp, sg, w)
    assert res["valeur_action"] == pytest.approx(59.2, abs=0.1)


def test_roic_egal_wacc_croissance_sans_valeur(hyp, sg):
    """Si ROIC = WACC, la croissance ne crée pas de valeur : VT = NOPAT_N+1 / WACC,
    quel que soit g."""
    hyp.roic_terminal = 0.10
    for g in (0.01, 0.03):
        res = valoriser(Hypotheses(**{**hyp.__dict__, "g": g}), sg, 0.10)
        nopat_suivant = res["projection"]["NOPAT"].iloc[-1] * (1 + g)
        assert res["vt"] == pytest.approx(nopat_suivant / 0.10)


def test_mi_annee_augmente_la_valeur(hyp, sg, params):
    w = wacc(params)
    base = valoriser(hyp, sg, w)["valeur_action"]
    hyp.mi_annee = True
    assert valoriser(hyp, sg, w)["valeur_action"] == pytest.approx(72.6, abs=0.1)
    assert valoriser(hyp, sg, w)["valeur_action"] > base


def test_date_de_valorisation(hyp, sg, params):
    """Ramener la VE à aujourd'hui (× (1 + WACC)^t) puis retirer le dividende versé."""
    w = wacc(params)
    res0 = valoriser(hyp, sg, w)
    res = valoriser(hyp, sg, w, decalage=0.5, dividendes_par_action=2.0)
    attendu = (res0["ve"] * (1 + w) ** 0.5 - 10356 - 568 - 1444) / 489.9 - 2.0
    assert res["valeur_action"] == pytest.approx(attendu)


def test_loyers_reduisent_fcff(hyp, sg):
    base = projeter_fcff(hyp, sg)["FCFF"]
    hyp.loyers_ca = 0.01
    avec = projeter_fcff(hyp, sg)
    assert ((base - avec["FCFF"]) - avec["CA"] * 0.01).abs().max() < 1e-9


# --- 4. Sensibilité croissance × marge et DCF inversé -----------------------


def test_sensibilite_croissance_marge_excel(hyp, sg, params):
    """Mêmes cases que le 2e tableau de sensibilité de l'Excel."""
    from dcf import sensibilite_croissance_marge

    t = sensibilite_croissance_marge(hyp, sg, wacc(params), [0.0125, 0.03], [0.115, 0.12])
    assert t.loc[0.0125, 0.115] == pytest.approx(67.93, abs=0.01)
    assert t.loc[0.0125, 0.12] == pytest.approx(72.12, abs=0.01)
    assert t.loc[0.03, 0.115] == pytest.approx(74.64, abs=0.01)


@pytest.mark.parametrize(
    "parametre, attendu", [("croissance_ca", 0.0263), ("marge_ebit", 0.1213), ("wacc", 0.0984)]
)
def test_dcf_inverse_saint_gobain(hyp, sg, params, parametre, attendu):
    """Ce que le cours de 73,20 € suppose (conclusion de l'Excel)."""
    from dcf import dcf_inverse

    x = dcf_inverse(hyp, sg, wacc(params), parametre)
    assert x == pytest.approx(attendu, abs=0.0005)


def test_dcf_inverse_retrouve_le_cours(hyp, sg, params):
    from dcf import dcf_inverse

    w = wacc(params)
    m = dcf_inverse(hyp, sg, w, "marge_ebit")
    v = valoriser(Hypotheses(**{**hyp.__dict__, "marge_ebit": m}), sg, w)["valeur_action"]
    assert v == pytest.approx(sg.cours, abs=0.001)


def test_dcf_inverse_hors_bornes(hyp, sg, params):
    """Un cours impossible à atteindre renvoie None au lieu d'un chiffre absurde."""
    from dcf import dcf_inverse

    assert dcf_inverse(hyp, sg, wacc(params), "croissance_ca", cible=10_000) is None


# --- 5. Revue de méthode du 25/11/2026 ---------------------------------------


def test_options_nouvelles_neutres_par_defaut(hyp, sg, params):
    """Transition, participations, autres dettes et dilution à 0 : on retrouve l'Excel."""
    res = valoriser(Hypotheses(**{**hyp.__dict__, "annees_transition": 0}), sg, wacc(params))
    assert res["valeur_action"] == pytest.approx(67.93, abs=0.01)


def test_transition_glisse_vers_g(hyp, sg):
    """Phase 2 : la croissance passe en ligne droite de 5 % à g = 2 % sur 3 ans (4,25 / 3,5 / 2,75)."""
    from dcf import croissance_annee

    h = Hypotheses(**{**hyp.__dict__, "croissance_ca": 0.05, "annees_transition": 3})
    p = projeter_fcff(h, sg)
    assert len(p) == 8
    assert list(p["Croissance"].round(4)) == [0.05] * 5 + [0.0425, 0.035, 0.0275]
    assert croissance_annee(h, 9) == pytest.approx(0.02)  # rejoint g juste après la transition


def test_transition_actualise_la_vt_a_la_bonne_annee(hyp, sg):
    """Avec 8 années projetées, la VT est actualisée sur 8 ans, pas sur l'horizon de 5 ans."""
    h = Hypotheses(**{**hyp.__dict__, "annees_transition": 3})
    res = valoriser(h, sg, 0.10)
    assert res["vt_actu"] == pytest.approx(res["vt"] / 1.10**8)


def test_pont_fonds_propres_complet(hyp, sg, params):
    """Participations ajoutées, autres dettes retirées, division par les actions diluées."""
    from dataclasses import replace

    w = wacc(params)
    base = valoriser(hyp, sg, w)
    d = replace(sg, participations=800, autres_dettes=300, actions_dilutives=4.1)
    res = valoriser(hyp, d, w)
    assert res["fonds_propres"] == pytest.approx(base["fonds_propres"] + 800 - 300)
    assert res["valeur_action"] == pytest.approx(res["fonds_propres"] / (489.9 + 4.1))


def test_hamada_aller_retour():
    """Désendetter puis réendetter avec la même structure redonne le bêta de départ."""
    from dcf import desendetter_beta, reendetter_beta

    bu = desendetter_beta(1.2, 0.5, 0.25)
    assert bu == pytest.approx(1.2 / (1 + 0.75 * 0.5))
    assert reendetter_beta(bu, 0.5, 0.25) == pytest.approx(1.2)
    # Orange : secteur télécom 0,447, D/E 42 %, impôt 25,8 % → ≈ 0,59
    assert reendetter_beta(0.447, 0.42, 0.258) == pytest.approx(0.586, abs=0.005)


def test_vt_roic_egale_wacc_neutralise_la_croissance(hyp, sg):
    """Normalisation de l'année terminale : si ROIC = WACC, la valeur ne dépend plus de g
    (la croissance coûte exactement ce qu'elle rapporte)."""
    h1 = Hypotheses(**{**hyp.__dict__, "roic_terminal": 0.09, "g": 0.01})
    h2 = Hypotheses(**{**hyp.__dict__, "roic_terminal": 0.09, "g": 0.03})
    assert valoriser(h1, sg, 0.09)["vt"] / 1.01 == pytest.approx(
        valoriser(h2, sg, 0.09)["vt"] / 1.03
    )
