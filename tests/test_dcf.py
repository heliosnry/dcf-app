"""
Tests automatiques du moteur DCF.
Lancer depuis le dossier dcf-app :  pytest
Référence : le modèle Excel CP_Saint_Gobain.xlsx (scénario central).
"""

import pytest

from dcf import (DonneesEntreprise, Hypotheses, ParametresWACC,
                 cout_fonds_propres, projeter_fcff, sensibilite_wacc_g,
                 valeur_terminale, valoriser, wacc)


# --- Données Saint-Gobain (scénario central de l'Excel) ---------------------

@pytest.fixture
def hyp():
    return Hypotheses(croissance_ca=0.0125, marge_ebit=0.115, da_ca=0.04,
                      capex_ca=0.0425, bfr_ca=0.021, taux_is=0.26, g=0.02, horizon=5)


@pytest.fixture
def sg():
    return DonneesEntreprise(ca_dernier=46483, bfr_dernier=968, annee_derniere=2025,
                             dette_nette=10356, minoritaires=568, provisions_retraites=1444,
                             nb_actions=489.9, cours=73.20)


@pytest.fixture
def params():
    return ParametresWACC(taux_sans_risque=0.0483, beta=1.428, prime_risque=0.0501,
                          kd=0.0594, taux_is=0.26, capitalisation=489.9 * 73.20, dette=10356)


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
    """FCFF = NOPAT + D&A − Capex − ΔBFR, chaque année."""
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