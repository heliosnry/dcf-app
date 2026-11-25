"""
Tests de data.py sans accès réseau : correction de la dette nette, conversion de devise, note de
crédit, paramètres pays et bêta sectoriel.
Les chiffres reprennent les cas réels : Orange (dette Yahoo incomplète), Hermès (vraie trésorerie
nette), Saint-Gobain (cas normal) et STMicro (comptes en dollars).
"""

import pandas as pd
import pytest

from data import dette_nette_fiable


def bilan(dette_brute, locatives, treso, dette_nette_yahoo=None, annees=(2022, 2023, 2024, 2025)):
    """Mini-historique : seules les lignes utilisées par dette_nette_fiable."""
    n = len(annees)
    yahoo = dette_nette_yahoo if dette_nette_yahoo is not None else [float("nan")] * n
    df = pd.DataFrame(
        {
            "Dette financière retenue": [dette_brute] * n,
            "dont dettes locatives": [locatives] * n,
            "Trésorerie": [treso] * n,
            "Dette nette Yahoo": yahoo,
        },
        index=list(annees),
    ).T
    df.loc["Dette nette"] = df.loc["Dette financière retenue"] - df.loc["Trésorerie"]
    return df


def test_cas_normal_inchange():
    # Saint-Gobain : 950 d'intérêts pour 17 729 de dette, soit 5,4 % : cohérent
    h = bilan(17729, 3164, 7732)
    dn, alerte = dette_nette_fiable(h, 950, 0.06)
    assert dn == pytest.approx(17729 - 7732)
    assert alerte is None


def test_tresorerie_nette_coherente_conservee():
    # Hermès : trésorerie nette réelle, intérêts faibles → la dette nette négative est gardée
    h = bilan(2346, 2312, 12239)
    dn, alerte = dette_nette_fiable(h, 70, 0.05)
    assert dn < 0
    assert alerte is None


def test_dette_incomplete_reprend_la_dette_yahoo_de_la_meme_annee():
    # Dette brute incomplète, mais Yahoo publie une dette nette pour la même année : on la reprend
    h = bilan(
        7522, 7522, 15262, dette_nette_yahoo=[float("nan"), float("nan"), float("nan"), 22526]
    )
    dn, alerte = dette_nette_fiable(h, 1341, 0.0594)
    assert dn == pytest.approx(22526 + 7522)
    assert "2025" in alerte and "SAISIS" in alerte


def test_dette_yahoo_ancienne_refusee_cas_orange():
    # Orange : seule la dette de 2022 existe (31 547, AVANT la sortie de l'Espagne dans MasOrange).
    # La reprendre surestimait la dette de 9 Md€ : on la refuse et on demande la saisie.
    h = bilan(
        7522, 7522, 15262, dette_nette_yahoo=[31547, float("nan"), float("nan"), float("nan")]
    )
    dn, alerte = dette_nette_fiable(h, 1341, 0.0594)
    assert dn == pytest.approx(1341 / 0.0594 - 15262)
    assert "GROSSIÈRE" in alerte and "SAISIS" in alerte


def test_interets_absents_pas_de_fausse_alerte():
    # Apple : charges d'intérêts non publiées par Yahoo la dernière année (NaN). Avant la correction,
    # le taux implicite NaN déclenchait une fausse alerte et remplaçait 23 389 par 56 648.
    h = bilan(80111, 0, 56722)
    dn, alerte = dette_nette_fiable(h, float("nan"), 0.05)
    assert dn == pytest.approx(80111 - 56722)
    assert alerte is None


def test_loyers_de_la_meme_annee_seulement():
    # Les dettes locatives ajoutées doivent être celles de l'année de la dette (pas celles de 2023)
    h = bilan(7522, 7522, 15262, dette_nette_yahoo=[float("nan")] * 3 + [20000])
    h.loc["dont dettes locatives"] = [12842, float("nan"), float("nan"), float("nan")]
    dn, _ = dette_nette_fiable(h, 1341, 0.0594)
    assert dn == pytest.approx(20000)


def test_dette_incomplete_sans_historique_estimee_par_les_interets():
    h = bilan(7522, 7522, 15262)
    dn, alerte = dette_nette_fiable(h, 1341, 0.05)
    assert dn == pytest.approx(1341 / 0.05 - 15262)
    assert alerte is not None


def test_dette_yahoo_trop_ancienne_ignoree():
    # Dette nette Yahoo de 2020 pour une clôture 2025 : plus de 3 ans, on ne la reprend pas
    h = bilan(
        7522,
        7522,
        15262,
        dette_nette_yahoo=[31547] + [float("nan")] * 5,
        annees=(2020, 2021, 2022, 2023, 2024, 2025),
    )
    dn, _ = dette_nette_fiable(h, 1341, 0.05)
    assert dn == pytest.approx(1341 / 0.05 - 15262)


def test_conversion_ne_touche_pas_les_actions():
    # STMicro : comptes en dollars convertis en euros, mais 888,8 M d'actions restent 888,8 M
    from data import convertir

    h = pd.DataFrame(
        {2025: [10000.0, 888.8, 900.0]}, index=["CA", "Actions en circulation", "Actions moyennes"]
    )
    c = convertir(h, 0.9)
    assert c.loc["CA", 2025] == pytest.approx(9000)
    assert c.loc["Actions en circulation", 2025] == pytest.approx(888.8)
    assert c.loc["Actions moyennes", 2025] == pytest.approx(900.0)


def test_couverture_mediane_trois_ans():
    # Une seule mauvaise année ne doit pas faire basculer la note de crédit
    from data import couverture_interets

    h = pd.DataFrame(
        {2023: [3000.0, 300.0], 2024: [3200.0, 320.0], 2025: [-500.0, 330.0]},
        index=["EBIT", "Charges d'intérêts"],
    )
    assert couverture_interets(h) == pytest.approx(10.0)


def test_taux_sans_risque_de_la_devise_et_prime_du_pays():
    # France en euros : Bund (pas l'OAT) + prime France (qui contient déjà le risque pays)
    from data import PRIME_MATURE, TAUX_SANS_RISQUE, parametres_pays

    fr = parametres_pays("France", "EUR")
    assert fr["rf"] == TAUX_SANS_RISQUE["EUR"]
    assert fr["prime"] == pytest.approx(PRIME_MATURE + fr["prime_pays"], abs=1e-4)
    assert fr["prime_pays"] > 0
    # Même pays, autre devise : le taux sans risque suit la devise, la prime suit le pays
    us = parametres_pays("France", "USD")
    assert us["rf"] == TAUX_SANS_RISQUE["USD"] and us["prime"] == fr["prime"]
    # Pays inconnu : prime France par défaut, signalée
    assert parametres_pays("Atlantide", "EUR")["connu"] is False
    # Devise sans taux enregistré : signalée
    assert parametres_pays("Switzerland", "CHF")["rf_connu"] is False


def test_beta_sectoriel_et_fiabilite():
    from data import beta_fiable, beta_sectoriel

    telecom = beta_sectoriel("Telecom Services", "France")
    assert telecom["table"] == "Europe" and 0.2 < telecom["beta_desendette"] < 1.0
    assert beta_sectoriel("Secteur imaginaire", "France") is None
    # Orange : bêta 0,33 avec un R² de 6 % → peu fiable
    ok, raison = beta_fiable({"beta": 0.33, "r2": 0.06})
    assert not ok and "R²" in raison
    assert beta_fiable({"beta": 1.1, "r2": 0.5})[0]


def test_estimation_jamais_negative():
    # Intérêts modestes face à une grosse trésorerie : l'estimation est ramenée à 0
    h = bilan(100, 100, 50000)
    dn, _ = dette_nette_fiable(h, 200, 0.05)
    assert dn == 0.0
