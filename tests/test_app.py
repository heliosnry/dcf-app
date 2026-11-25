"""Test de l'interface (sans réseau) : l'exemple Saint-Gobain s'affiche sans erreur dans tous les
onglets et redonne la valeur de référence de l'Excel."""

from pathlib import Path

import pytest

APP = str(Path(__file__).parents[1] / "src" / "app.py")


@pytest.fixture(scope="module")
def appli():
    """Appli lancée une seule fois pour tout le fichier, sur l'exemple Saint-Gobain."""
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP, default_timeout=180)
    # On choisit l'exemple AVANT le premier passage : aucun appel à Yahoo
    at.session_state["source"] = "Exemple : Saint-Gobain (rapport annuel)"
    return at.run()


def test_exemple_sans_erreur(appli):
    assert not appli.exception
    assert not appli.error


def test_exemple_redonne_l_excel(appli):
    metriques = {m.label: m.value for m in appli.metric}
    assert metriques["Valeur par action"] == "67,93 €"
    assert metriques["WACC"] == "10,28 %"


def test_tous_les_onglets_s_affichent(appli):
    assert len(appli.tabs) == 8
    assert any(m.label == "Valeur pondérée" for m in appli.metric)  # Scénarios
    assert any("implicite" in m.label for m in appli.metric)  # DCF inversé
