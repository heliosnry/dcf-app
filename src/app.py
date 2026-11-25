"""
app.py — Interface Streamlit du DCF automatisé

Lancer :  streamlit run src/app.py
"""

from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from data import (
    DATE_PRIMES,
    DATE_TAUX,
    PRIMES_ZONES,
    SPREADS,
    beta_blume,
    note_synthetique,
)
from dcf import (
    DonneesEntreprise,
    Hypotheses,
    ParametresWACC,
    dcf_inverse,
    reendetter_beta,
    sensibilite_croissance_marge,
    sensibilite_wacc_g,
    valoriser,
    wacc,
)
from export import exporter_excel

DOSSIER = Path(__file__).parent
st.set_page_config(
    page_title="DCF automatisé", page_icon=str(DOSSIER / "assets" / "logo.png"), layout="wide"
)
st.logo(
    str(DOSSIER / "assets" / "logo_texte.svg"),
    size="large",
    icon_image=str(DOSSIER / "assets" / "logo.svg"),
)

# Interface plus compacte
st.markdown(
    """
<style>
section[data-testid="stSidebar"] { width: 290px !important; }
section[data-testid="stSidebar"] p, section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] input, section[data-testid="stSidebar"] div[data-baseweb="select"] {
    font-size: 0.85rem; }
section[data-testid="stSidebar"] h2, section[data-testid="stSidebar"] h3 { font-size: 1rem; }
h1 { font-size: 1.8rem !important; }
[data-testid="stExpander"] summary p { font-size: 0.92rem; font-weight: 600; }
button[data-baseweb="tab"] p { font-size: 0.88rem; }
[data-testid="stMetricValue"] { font-size: 1.55rem; }
[data-testid="stMetricLabel"] p { font-size: 0.82rem; }
[data-testid="stWidgetLabel"] p { font-size: 0.85rem; }
.block-container { padding-top: 2.2rem; }
</style>
""",
    unsafe_allow_html=True,
)

SYMBOLES = {
    "EUR": "€",
    "USD": "$",
    "GBP": "£",
    "CHF": "CHF",
    "JPY": "¥",
    "SEK": "SEK",
    "DKK": "DKK",
    "NOK": "NOK",
    "CAD": "CA$",
}


# ---------------------------------------------------------------------------
# Données de référence : Saint-Gobain d'après le rapport annuel (modèle Excel)
# ---------------------------------------------------------------------------

# Paramètres de marché du modèle Excel, conservés tels quels pour retrouver 67,93 € :
# OAT (4,83 %) + prime totale France. L'appli, pour un ticker, prend le Bund (voir data.py).
PAYS_EXCEL = {
    "pays": "France",
    "connu": True,
    "rf": 0.0483,
    "devise_rf": "EUR",
    "rf_connu": True,
    "prime": 0.0501,
    "prime_pays": 0.0078,
    "impot": 0.2583,
    "zone": "Europe de l'Ouest",
}

EXEMPLE_SG = {
    "nom": "Saint-Gobain (rapport annuel 2025)",
    "devise": "EUR",
    "beta": 1.428,
    "beta_yahoo": None,
    "beta_stats": None,
    "beta_fiable": True,
    "beta_raison": "",
    "beta_secteur": None,
    "beta_defaut": 1.428,
    "logo": "https://www.google.com/s2/favicons?domain=saint-gobain.com&sz=128",
    "couverture": 5293 / 388,
    "note": "Baa2/BBB",
    "spread": 0.0111,
    "petite": False,
    "pays": PAYS_EXCEL,
    "taux_is_source": "modèle Excel",
    "dettes_locatives": 0.0,
    "loyers_estimes": 0.0,
    "loyers_importants": False,
    "dette_a_saisir": False,
    "dette_nette_moyenne": None,
    "minoritaires_marche": 568.0,
    "financement_captif": False,
    "roic_historique": None,
    "croissance_historique": 0.0125,
    "croissance_consensus": None,
    "date_cloture": pd.Timestamp("2025-12-31"),
    "decalage": max((date.today() - date(2025, 12, 31)).days / 365.25, 0.0),
    "dividendes": 0.0,
    "alertes": [],
    "devise_comptes": "EUR",
    "plus_bas_52s": 63.52,
    "plus_haut_52s": 91.30,
    "ebitda_dernier": 5293 + 2141,
    "ebitda_ttm": None,
    "comparables_defaut": "HOLN.SW, SIKA.SW, HEI.DE, KRX.IR, CRH",
    "hypotheses": Hypotheses(
        croissance_ca=0.0125,
        marge_ebit=0.115,
        da_ca=0.04,
        capex_ca=0.0425,
        bfr_ca=0.021,
        taux_is=0.26,
        g=0.02,
        horizon=5,
    ),
    "entreprise": DonneesEntreprise(
        ca_dernier=46483,
        bfr_dernier=968,
        annee_derniere=2025,
        dette_nette=10356,
        minoritaires=568,
        provisions_retraites=1444,
        nb_actions=489.9,
        cours=73.20,
    ),
    "historique": None,
    "ratios": None,
}


# Cache EN MÉMOIRE d'une heure : il limite les appels à Yahoo (qui bloque vite les serveurs
# partagés), tout en rafraîchissant les cours. Pas de persist="disk" : Streamlit ignore alors le
# ttl et les données ne seraient jamais mises à jour.
@st.cache_data(ttl=3600, show_spinner="Récupération des données Yahoo Finance…")
def charger(ticker: str) -> dict:
    """Prépare une entreprise depuis Yahoo (data.preparer) et renvoie un dict au même format
    qu'EXEMPLE_SG : données de marché, paramètres par défaut, alertes et comparables proposés."""
    from data import preparer

    d = preparer(ticker)
    m = d["marche"]
    cles = (
        "beta",
        "beta_stats",
        "beta_fiable",
        "beta_raison",
        "beta_secteur",
        "beta_defaut",
        "couverture",
        "note",
        "spread",
        "petite",
        "pays",
        "taux_is_source",
        "dettes_locatives",
        "loyers_estimes",
        "loyers_importants",
        "dette_a_saisir",
        "dette_nette_moyenne",
        "minoritaires_marche",
        "financement_captif",
        "roic_historique",
        "croissance_historique",
        "croissance_consensus",
        "date_cloture",
        "decalage",
        "dividendes",
        "alertes",
        "hypotheses",
        "entreprise",
        "historique",
        "ratios",
    )
    return {
        "nom": m["nom"],
        "devise": m["devise"],
        "devise_comptes": m["devise_comptes"],
        "beta_yahoo": m["beta_yahoo"],
        "logo": m["logo"],
        "plus_bas_52s": m["plus_bas_52s"],
        "plus_haut_52s": m["plus_haut_52s"],
        "ebitda_dernier": float(
            d["historique"].loc["EBIT"].iloc[-1] + d["historique"].loc["D&A"].iloc[-1]
        ),
        "ebitda_ttm": m["ebitda_ttm"],
        "comparables_defaut": ", ".join(chercher_comparables(ticker)),
        **{k: d[k] for k in cles},
    }


@st.cache_data(ttl=3600, show_spinner=False)
def taux_change(de: str, vers: str) -> float:
    """Taux de change du jour (combien de « vers » pour 1 « de »), mis en cache 1 h."""
    from data import _taux_de_change

    return _taux_de_change(de, vers)


@st.cache_data(ttl=3600, show_spinner="Recherche automatique des comparables…")
def _chercher_comparables_cache(ticker: str) -> list[str]:
    """Recherche automatique des comparables, mise en cache 1 h (échoue si la liste est vide)."""
    from data import comparables_auto

    trouves = comparables_auto(ticker)
    if not trouves:  # une liste vide n'est pas mise en cache : on réessaiera la prochaine fois
        raise ValueError("aucun comparable trouvé")
    return trouves


def chercher_comparables(ticker: str, forcer: bool = False) -> list[str]:
    """Comparables automatiques. Les échecs (erreur Yahoo, liste vide) ne sont PAS mis en cache,
    sinon une panne passagère bloquerait la recherche pendant 1 h. `forcer` relance la recherche."""
    if forcer:
        _chercher_comparables_cache.clear(ticker)
    try:
        return _chercher_comparables_cache(ticker)
    except Exception:
        return []


@st.cache_data(ttl=3600, show_spinner="Récupération des multiples des comparables…")
def charger_comparables(tickers: tuple[str, ...]) -> pd.DataFrame:
    """Multiples de marché des comparables (data.comparables), mis en cache 1 h."""
    from data import comparables

    return comparables(list(tickers))


def pct(x: float, dec: int = 1) -> str:
    """Pourcentage au format français : 0.1028 → « 10,3 % »."""
    return f"{x * 100:.{dec}f} %".replace(".", ",")


def nombre(x: float, dec: int = 0) -> str:
    """Nombre au format français : 45649.3 → « 45 649 »."""
    return f"{x:,.{dec}f}".replace(",", " ").replace(".", ",")


def multiple(x: float) -> str:
    """Multiple de valorisation : 8.43 → « 8,4x »."""
    return f"{x:.1f}x".replace(".", ",")


# ---------------------------------------------------------------------------
# Barre latérale : choix de l'entreprise et paramètres de marché
# ---------------------------------------------------------------------------

source = st.sidebar.radio(
    "Source des données",
    ["Ticker Yahoo Finance", "Exemple : Saint-Gobain (rapport annuel)"],
    key="source",
)

if source == "Ticker Yahoo Finance":
    ticker = (
        st.sidebar.text_input(
            "Ticker", value="SGO.PA", help="Exemples : SGO.PA, ML.PA, ORA.PA, AAPL"
        )
        .strip()
        .upper()
    )
    try:
        donnees = charger(ticker)
    except ValueError as e:
        st.warning(str(e), icon=":material/warning:")
        st.stop()
    except Exception as e:  # réseau, ticker inconnu, Yahoo qui bloque…
        st.error(f"Impossible de récupérer les données pour « {ticker} » : {e}")
        st.info(
            "Yahoo Finance limite le nombre de requêtes : réessaie dans quelques minutes, ou utilise "
            "l'exemple Saint-Gobain (barre latérale), qui fonctionne sans connexion."
        )
        st.stop()
else:
    ticker = "SGO-exemple"
    donnees = EXEMPLE_SG

exemple = donnees["historique"] is None
h0, e0 = donnees["hypotheses"], donnees["entreprise"]
cle = ticker  # pour remettre les curseurs à zéro quand on change d'entreprise

if st.sidebar.button(
    "Réinitialiser les valeurs",
    icon=":material/restart_alt:",
    width="stretch",
    help="Revenir aux valeurs par défaut pour cette entreprise",
):
    for k in [k for k in st.session_state if str(k).endswith(f"_{cle}")]:
        del st.session_state[k]
    st.rerun()

st.sidebar.subheader("Coût du capital")
pays = donnees["pays"]
st.sidebar.caption(
    f"Pays : {pays['pays']}"
    + ("" if pays["connu"] else " (absent de la table : prime France)")
    + (" · paramètres du modèle Excel (OAT)" if exemple else "")
)
rf = (
    st.sidebar.number_input(
        "Taux sans risque (%)",
        value=round(pays["rf"] * 100, 2),
        step=0.05,
        key=f"rf_{cle}",
        help=f"Taux d'État à 10 ans de la DEVISE des flux ({pays['devise_rf']}, {DATE_TAUX}). Euro : Bund "
        "allemand. L'OAT contient déjà un risque France : la prendre avec la prime pays compterait ce "
        "risque deux fois.",
    )
    / 100
)

pondere = st.sidebar.toggle(
    "Prime pondérée par zones d'activité",
    key=f"pz_{cle}",
    help="Le risque pays dépend de l'endroit où l'entreprise vend, pas de son siège. "
    "Renseigne la répartition du CA (rapport annuel, information sectorielle).",
)
if pondere:
    zones = [f"Pays du siège ({pays['pays']})"] + list(PRIMES_ZONES)
    rep = st.sidebar.data_editor(
        pd.DataFrame({"Zone": [zones[0]], "Poids (%)": [100.0]}),
        num_rows="dynamic",
        key=f"rep_{cle}",
        column_config={"Zone": st.column_config.SelectboxColumn(options=zones, required=True)},
        hide_index=True,
    )
    rep = rep.dropna()
    primes_zone = {zones[0]: pays["prime"], **PRIMES_ZONES}
    total_poids = rep["Poids (%)"].sum()
    prime = (
        float((rep["Zone"].map(primes_zone) * rep["Poids (%)"]).sum() / total_poids)
        if total_poids > 0
        else pays["prime"]
    )
    st.sidebar.caption(
        f"Prime pondérée : {pct(prime, 2)}"
        + (
            ""
            if abs(total_poids - 100) < 1e-9
            else f" (poids ramenés à 100 % ; total saisi {nombre(total_poids)} %)"
        )
    )
else:
    prime = (
        st.sidebar.number_input(
            "Prime de risque totale (%)",
            value=round(pays["prime"] * 100, 2),
            step=0.05,
            key=f"erp_{cle}",
            help=f"Prime d'un marché mature + prime de risque du pays (Damodaran, {DATE_PRIMES}). "
            f"Dont prime pays : {pct(pays['prime_pays'], 2)}.",
        )
        / 100
    )

notes = list(SPREADS)
note = st.sidebar.selectbox(
    "Note de crédit",
    notes,
    index=notes.index(donnees["note"]),
    key=f"note_{cle}",
    help="Par défaut : note synthétique de Damodaran, déduite de la couverture "
    "des intérêts (EBIT / charges d'intérêts, médiane sur 3 ans). Remplace-la "
    "par la vraie note S&P ou Moody's (site de l'entreprise, rubrique dette).",
)
spread = SPREADS[note]
cv = donnees["couverture"]
table = "petites entreprises" if donnees.get("petite") else "grandes entreprises"
st.sidebar.caption(
    (
        f"Couverture des intérêts : {nombre(cv, 1)}x → note synthétique "
        f"{note_synthetique(cv, donnees.get('petite', False))[0]} (table {table}). "
        if cv
        else "Charges d'intérêts indisponibles : BBB par défaut. "
    )
    + f"Spread retenu : {pct(spread, 2)}"
)

secteur = donnees.get("beta_secteur")
types_beta = ["Brut (régression)", "Ajusté (Blume)"] + (
    ["Sectoriel (Damodaran)"] if secteur else []
)
index_beta = 0 if donnees["beta_fiable"] or not secteur else 2
type_beta = st.sidebar.radio(
    "Bêta",
    types_beta,
    index=index_beta,
    key=f"tb_{cle}",
    help="Brut : régression mensuelle sur 5 ans contre l'indice du pays. "
    "Blume : 0,67 × bêta brut + 0,33. Sectoriel : bêta de l'activité "
    "(moyenne du secteur, sans dette) réendetté avec la dette de l'entreprise.",
)
if type_beta.startswith("Sectoriel"):
    beta_u = st.sidebar.number_input(
        "Bêta désendetté du secteur",
        value=round(secteur["beta_desendette"], 3),
        step=0.05,
        format="%.3f",
        key=f"bu_{cle}",
        help=f"Secteur Damodaran « {secteur['secteur']} », table {secteur['table']} "
        f"({DATE_PRIMES}), corrigé de la trésorerie.",
    )
    beta_saisi = None
else:
    beta_u = None
    beta_defaut = donnees["beta"] if type_beta.startswith("Brut") else beta_blume(donnees["beta"])
    beta_saisi = st.sidebar.number_input(
        "Valeur du bêta",
        value=round(float(beta_defaut), 3),
        step=0.05,
        format="%.3f",
        key=f"beta_{type_beta}_{cle}",
        help="Calculé sur 5 ans en mensuel contre l'indice du pays",
    )
zone_beta = st.sidebar.empty()  # rempli après le calcul (bêta réendetté, fiabilité)

st.sidebar.subheader("Affichage")
devise_origine = donnees["devise"]
choix_devises = [devise_origine] + [
    d_ for d_ in ("EUR", "USD", "GBP", "CHF") if d_ != devise_origine
]
devise = st.sidebar.selectbox(
    "Devise des résultats",
    choix_devises,
    key=f"devise_{cle}",
    help="Conversion au taux de change du jour (Yahoo Finance). Les données "
    "saisies restent dans la devise de l'entreprise.",
)
fx = 1.0
if devise != devise_origine:
    try:
        fx = taux_change(devise_origine, devise)
        st.sidebar.caption(f"1 {devise_origine} = {fx:.4f} {devise}".replace(".", ","))
    except Exception:
        st.sidebar.warning(
            f"Taux de change indisponible : résultats affichés en {devise_origine}.",
            icon=":material/warning:",
        )
        devise = devise_origine
sym = SYMBOLES.get(devise, devise)

# ---------------------------------------------------------------------------
# Hypothèses (pré-remplies, modifiables)
# ---------------------------------------------------------------------------

t_logo, t_nom = st.columns([1, 12], vertical_alignment="center")
if donnees.get("logo"):
    try:
        t_logo.image(donnees["logo"], width=64)
    except Exception:
        pass
t_nom.title(donnees["nom"])
st.caption(
    f"Comptes en millions de {donnees['devise']} · dernière clôture : "
    f"{donnees['date_cloture']:%d/%m/%Y}"
)
for alerte in donnees.get("alertes", []):
    st.warning(alerte, icon=":material/warning:")

cle_croissance = f"cr_{cle}"
consensus = donnees.get("croissance_consensus")


def utiliser_consensus():
    """Bouton « consensus » : remplace la croissance par celle attendue par les analystes."""
    st.session_state[cle_croissance] = round(min(max(consensus, -0.05), 0.10) * 100, 2)


with st.expander("Hypothèses opérationnelles", expanded=True):
    c1, c2, c3, c4 = st.columns(4)
    # value n'est passé qu'à la création : ensuite la valeur vit dans session_state
    # (sinon Streamlit signale un conflit quand le bouton « consensus » la modifie)
    defaut_cr = (
        {} if cle_croissance in st.session_state else {"value": round(h0.croissance_ca * 100, 2)}
    )
    croissance = (
        c1.slider("Croissance du CA (%/an)", -5.0, 10.0, step=0.25, key=cle_croissance, **defaut_cr)
        / 100
    )
    marge = (
        c1.slider("Marge EBIT (%)", 0.0, 40.0, round(h0.marge_ebit * 100, 2), 0.25, key=f"mg_{cle}")
        / 100
    )
    da = c2.slider("D&A / CA (%)", 0.0, 20.0, round(h0.da_ca * 100, 2), 0.25, key=f"da_{cle}") / 100
    capex = (
        c2.slider("Capex / CA (%)", 0.0, 20.0, round(h0.capex_ca * 100, 2), 0.25, key=f"cx_{cle}")
        / 100
    )
    bfr = (
        c3.slider("BFR / CA (%)", -20.0, 40.0, round(h0.bfr_ca * 100, 2), 0.5, key=f"bfr_{cle}")
        / 100
    )
    taux_is = (
        c3.slider("Taux d'impôt (%)", 0.0, 40.0, round(h0.taux_is * 100, 1), 0.5, key=f"is_{cle}")
        / 100
    )
    g = (
        c4.slider(
            "Croissance à l'infini g (%)", 0.0, 4.0, round(h0.g * 100, 2), 0.25, key=f"g_{cle}"
        )
        / 100
    )
    horizon = c4.slider("Horizon (années)", 3, 10, h0.horizon, key=f"hz_{cle}")
    transition = c4.slider(
        "Années de transition",
        0,
        10,
        0,
        key=f"tr_{cle}",
        help="Modèle à 3 phases : après l'horizon, la croissance glisse en ligne droite "
        "vers g. Utile pour une entreprise en forte croissance (passer de 15 % à 2 % "
        "du jour au lendemain n'est pas réaliste).",
    )
    infos = []
    if not exemple:
        infos.append(
            f"Croissance historique moyenne : {pct(donnees['croissance_historique'])} "
            "(valeur par défaut bornée entre 0 % et 5 %)"
        )
    if consensus is not None:
        infos.append(
            f"consensus des analystes : {pct(consensus)} (inclut les acquisitions annoncées)"
        )
    if infos:
        c1.caption("ℹ " + " · ".join(infos) + ".")
    if consensus is not None:
        c1.button(
            "Utiliser le consensus",
            on_click=utiliser_consensus,
            key=f"bc_{cle}",
            icon=":material/groups:",
        )
    st.caption(f"Taux d'impôt par défaut : {donnees.get('taux_is_source', '')}.")

with st.expander("Méthode", expanded=True):
    c1, c2, c3 = st.columns(3)
    methode_vt = c1.radio(
        "Valeur terminale",
        ["Gordon sur le FCFF", "Formule ROIC (McKinsey)"],
        index=0 if exemple else 1,
        key=f"vt_{cle}",
        help="Gordon : VT = FCFF × (1 + g) / (WACC − g) : prolonge à l'infini le Capex et "
        "les D&A de la dernière année. Formule ROIC : l'entreprise réinvestit g / ROIC "
        "de son NOPAT pour croître de g. La croissance n'est plus « gratuite ».",
    )
    ecart_roic = None
    if methode_vt == "Formule ROIC (McKinsey)":
        roic_hist = donnees.get("roic_historique")
        # Point de départ : 2 points au-dessus du WACC (avantage concurrentiel modéré qui persiste)
        ecart_roic = (
            c1.slider(
                "Écart ROIC − WACC des nouveaux investissements (pt)",
                0.0,
                15.0,
                2.0,
                0.5,
                key=f"roic_{cle}",
                help="À long terme, la concurrence ramène la rentabilité des NOUVEAUX "
                "investissements vers le coût du capital. 0 = la croissance ne crée "
                "plus de valeur. Une marque forte (luxe) justifie un écart durable.",
            )
            / 100
        )
        if roic_hist is not None:
            c1.caption(f"ROIC historique (dernier exercice) : {pct(roic_hist)}.")
    mi_annee = c2.checkbox(
        "Convention de milieu d'année",
        value=False,
        key=f"mid_{cle}",
        help="Les flux arrivent tout au long de l'année : on les actualise à mi-année "
        "(pratique courante en banque d'affaires).",
    )
    ifrs16 = c2.radio(
        "Loyers IFRS 16",
        ["Loyers = dette", "Loyers = charge"],
        key=f"ifrs_{cle}",
        help="Deux traitements corrects, mais UN seul à la fois. « Dette » : on retire du FCFF "
        "le renouvellement des contrats (comme un Capex) et les dettes locatives de la VE. "
        "« Charge » : on retire les loyers payés du FCFF et les dettes locatives ne sont "
        "PAS retirées. Retirer loyers ET dette locative = double compte.",
    )
    # Dans les DEUX traitements, il faut retirer quelque chose du FCFF : les D&A contiennent
    # l'amortissement des biens loués, mais le Capex n'inclut pas les nouveaux contrats de location.
    #  - loyers = dette  : on retire le renouvellement des droits d'utilisation (« Capex locatif »)
    #                      et la dette locative est retirée de la VE ;
    #  - loyers = charge : on retire les loyers payés et la dette locative n'est PAS retirée.
    libelle_loyers = (
        "Renouvellement des contrats de location / CA (%)"
        if ifrs16 == "Loyers = dette"
        else "Loyers payés / CA (%)"
    )
    loyers = (
        c2.slider(
            libelle_loyers,
            0.0,
            10.0,
            round(donnees.get("loyers_estimes", 0.0) * 100, 2),
            0.05,
            key=f"ly_{cle}",
            help="Par défaut : dette locative / 5 (durée moyenne des contrats ≈ 5 ans). Mieux : "
            "« remboursement des dettes locatives » du tableau des flux.",
        )
        / 100
    )
    a_date = c3.checkbox(
        "Valoriser à la date du jour",
        value=not exemple,
        key=f"date_{cle}",
        help="Sans cette option, la valeur est calculée à la date de clôture : la "
        "comparer au cours du jour est incohérent. Avec, la VE est capitalisée au "
        "WACC jusqu'à aujourd'hui et les dividendes versés depuis sont retirés.",
    )
    dividendes = 0.0
    if a_date:
        dividendes = c3.number_input(
            f"Dividendes versés depuis le {donnees['date_cloture']:%d/%m/%Y} (par action)",
            value=float(round(donnees["dividendes"], 2)),
            step=0.1,
            key=f"div_{cle}",
        )
        if dividendes == 0:
            c3.caption(
                "ℹ Aucun dividende saisi : la valeur est surestimée si un dividende a été versé "
                "depuis la clôture."
            )

sym_origine = SYMBOLES.get(donnees["devise"], donnees["devise"])
with st.expander(
    f"Passage aux fonds propres (en M{sym_origine}, à vérifier dans le rapport annuel)",
    expanded=donnees.get("dette_a_saisir", False),
):
    locatives_defaut = float(donnees.get("dettes_locatives") or 0.0)
    moyenne = donnees.get("dette_nette_moyenne")
    c1, c2, c3, c4 = st.columns(4)
    base_dn = "Clôture"
    if moyenne:
        base_dn = c1.radio(
            "Dette nette retenue",
            ["Clôture", "Moyenne 12 mois"],
            horizontal=True,
            key=f"bdn_{cle}",
            help="Moyenne des bilans intermédiaires : neutralise la saisonnalité du BFR.",
        )
    dn_totale_defaut = moyenne["moyenne"] if base_dn.startswith("Moyenne") else e0.dette_nette
    dette_financiere = c1.number_input(
        "Dette nette financière (hors loyers)",
        value=float(round(dn_totale_defaut - locatives_defaut)),
        step=100.0,
        key=f"dn_{base_dn}_{cle}",
        help="Endettement financier net publié : emprunts + obligations − trésorerie, SANS les dettes locatives.",
    )
    dettes_locatives = c1.number_input(
        "Dettes locatives IFRS 16",
        value=float(round(locatives_defaut)),
        step=100.0,
        key=f"loc_{cle}",
        help="Retirées de la VE seulement si « Loyers = dette ».",
    )
    dette_verifiee = True
    if donnees.get("dette_a_saisir"):
        dette_verifiee = c1.checkbox(
            "J'ai saisi la dette du rapport annuel",
            key=f"dv_{cle}",
            help="La dette Yahoo est incomplète : tant que cette case n'est pas cochée, "
            "l'appli ne conclut pas.",
        )
    type_mino = c2.radio(
        "Minoritaires",
        ["Valeur comptable", "Prix de marché"],
        horizontal=True,
        key=f"tm_{cle}",
        help="Prix de marché : valeur comptable × (capitalisation / capitaux propres du "
        "groupe). Les minoritaires valent plus que leur valeur comptable si le groupe "
        "vaut plus que ses fonds propres.",
    )
    mino_defaut = (
        donnees.get("minoritaires_marche", e0.minoritaires)
        if type_mino == "Prix de marché"
        else e0.minoritaires
    )
    minoritaires = c2.number_input(
        "Intérêts minoritaires",
        value=float(round(mino_defaut)),
        step=10.0,
        key=f"mi_{type_mino}_{cle}",
    )
    provisions = c2.number_input(
        "Provisions retraites",
        value=float(round(e0.provisions_retraites)),
        step=10.0,
        key=f"pr_{cle}",
        help="Engagements nets de retraite (passif non courant).",
    )
    participations = c3.number_input(
        "Participations mises en équivalence (+)",
        value=float(round(e0.participations)),
        step=10.0,
        key=f"pa_{cle}",
        help="Valeur comptable des sociétés associées (leurs profits ne sont pas "
        "dans l'EBIT d'exploitation) : elles s'AJOUTENT à la VE. Remplace par "
        "la valeur de marché si la société est cotée.",
    )
    autres_dettes = c3.number_input(
        "Autres dettes : hybrides, affacturage… (−)",
        value=float(round(e0.autres_dettes)),
        step=10.0,
        key=f"ad_{cle}",
        help="Obligations hybrides (comptées en capitaux propres mais remboursables), "
        "affacturage déconsolidant, convertibles non dilutives, litiges.",
    )
    nb_actions = c4.number_input(
        "Nombre d'actions (M)", value=float(round(e0.nb_actions, 1)), step=1.0, key=f"na_{cle}"
    )
    actions_dilutives = c4.number_input(
        "Actions dilutives (M)",
        value=float(round(e0.actions_dilutives, 1)),
        step=0.5,
        key=f"ad2_{cle}",
        help="Stock-options, actions gratuites, convertibles : écart entre "
        "actions diluées et actions de base (compte de résultat).",
    )

# ---------------------------------------------------------------------------
# Calcul
# ---------------------------------------------------------------------------

# Un seul traitement IFRS 16 : les dettes locatives ne sont retirées que si les loyers sont une dette
dette_nette_origine = dette_financiere + (dettes_locatives if ifrs16 == "Loyers = dette" else 0.0)

# Conversion dans la devise d'affichage (le DCF est linéaire dans les montants)
cours = e0.cours * fx
dette_nette = dette_nette_origine * fx
minoritaires, provisions = minoritaires * fx, provisions * fx
participations, autres_dettes = participations * fx, autres_dettes * fx
ebitda0 = donnees["ebitda_dernier"] * fx
# Pour les multiples : EBITDA de même source, même définition et même période (12 derniers mois)
# que celui des comparables. À défaut, EBIT + D&A du dernier exercice. Utilisé dans TOUS les onglets.
ebitda_comp = (donnees.get("ebitda_ttm") or donnees["ebitda_dernier"]) * fx
bas_52s = donnees["plus_bas_52s"] * fx if donnees.get("plus_bas_52s") else None
haut_52s = donnees["plus_haut_52s"] * fx if donnees.get("plus_haut_52s") else None
dividendes *= fx

capitalisation = nb_actions * cours
dette_wacc = max(dette_nette, 0.0) + autres_dettes  # les hybrides sont financés comme de la dette

# Bêta retenu : saisi, ou sectoriel réendetté avec la structure financière de l'entreprise
textes_beta = []
if beta_u is not None:
    beta = reendetter_beta(beta_u, dette_wacc / capitalisation, taux_is)
    textes_beta.append(
        f"Bêta réendetté retenu : {nombre(beta, 2)} = {nombre(beta_u, 2)} × (1 + (1 − "
        f"{pct(taux_is, 0)}) × D/E de {pct(dette_wacc / capitalisation, 0)})."
    )
else:
    beta = beta_saisi
stats = donnees.get("beta_stats")
if stats:
    bas, haut = stats["beta"] - 2 * stats["erreur_type"], stats["beta"] + 2 * stats["erreur_type"]
    textes_beta.append(
        f"Régression : {nombre(stats['beta'], 2)} (R² {pct(stats['r2'], 0)}, intervalle à 95 % "
        f"{nombre(bas, 2)} à {nombre(haut, 2)}, {stats['n']} mois)"
        + ("." if donnees["beta_fiable"] else " → peu fiable.")
    )
if donnees.get("beta_yahoo"):
    textes_beta.append(f"Yahoo (contre le S&P 500) : {nombre(donnees['beta_yahoo'], 2)}.")
if textes_beta:
    zone_beta.caption(" ".join(textes_beta))


def parametres_wacc_pour(b: float) -> ParametresWACC:
    """Paramètres du coût du capital de la barre latérale, avec le bêta b."""
    return ParametresWACC(
        taux_sans_risque=rf,
        beta=b,
        prime_risque=prime,
        kd=rf + spread,
        taux_is=taux_is,
        capitalisation=capitalisation,
        dette=dette_wacc,
    )


def wacc_pour(b: float) -> float:
    """WACC avec les paramètres de la barre latérale et le bêta b."""
    return wacc(parametres_wacc_pour(b))


w = wacc_pour(beta)
roic_t = None if ecart_roic is None else w + ecart_roic

hyp = Hypotheses(
    croissance_ca=croissance,
    marge_ebit=marge,
    da_ca=da,
    capex_ca=capex,
    bfr_ca=bfr,
    taux_is=taux_is,
    g=g,
    horizon=horizon,
    loyers_ca=loyers,
    roic_terminal=roic_t,
    mi_annee=mi_annee,
    annees_transition=transition,
)
options = {"decalage": donnees["decalage"] if a_date else 0.0, "dividendes_par_action": dividendes}
ent = DonneesEntreprise(
    ca_dernier=e0.ca_dernier * fx,
    bfr_dernier=e0.bfr_dernier * fx,
    annee_derniere=e0.annee_derniere,
    dette_nette=dette_nette,
    minoritaires=minoritaires,
    provisions_retraites=provisions,
    nb_actions=nb_actions,
    cours=cours,
    participations=participations,
    autres_dettes=autres_dettes,
    actions_dilutives=actions_dilutives,
)
passif = dette_nette + minoritaires + provisions + autres_dettes - participations
nb_dilue = nb_actions + actions_dilutives

try:
    res = valoriser(hyp, ent, w, **options)
except ValueError as e:
    st.error(str(e), icon=":material/error:")
    st.stop()

proj = res["projection"]
pot = res["potentiel"]

# --- Contrôles (calculés AVANT la recommandation : elle en dépend) ---
ve_marche = capitalisation + passif
mult_marche, mult_dcf = ve_marche / ebitda_comp, res["ve_cloture"] / ebitda_comp
ebitda_1 = proj["EBIT"].iloc[0] + proj["D&A"].iloc[0]
ebitda_n = proj["EBIT"].iloc[-1] + proj["D&A"].iloc[-1]
croissance_sans_invest = (capex - da) > 0.03 and croissance <= 0.01  # modèle incohérent : bloquant
# (contrôle, valeur, règle, OK, bloquant) — un contrôle bloquant empêche toute recommandation
controles = [
    ("g < WACC", pct(w - g, 2), "> 0", w > g, True),
    (
        "Part de la VT dans la VE",
        pct(res["part_vt"]),
        "60 % à 80 %",
        0.6 <= res["part_vt"] <= 0.8,
        False,
    ),
    (
        "VE / EBITDA année 1",
        multiple(res["ve_cloture"] / ebitda_1),
        "5x à 15x",
        5 <= res["ve_cloture"] / ebitda_1 <= 15,
        False,
    ),
    (
        "Multiple de sortie VT / EBITDA final",
        multiple(res["vt"] / ebitda_n),
        "5x à 15x",
        5 <= res["vt"] / ebitda_n <= 15,
        False,
    ),
    ("Capex + loyers ≥ D&A", pct(capex + loyers - da, 2), "≥ 0", capex + loyers >= da, False),
    (
        "Croissance cohérente avec le Capex",
        "non" if croissance_sans_invest else "oui",
        "pas de Capex de croissance sans croissance",
        not croissance_sans_invest,
        True,
    ),
    (
        "ROIC implicite de la VT",
        "infini"
        if res["roic_implicite"] == float("inf")
        else "n.d."
        if pd.isna(res["roic_implicite"])
        else pct(res["roic_implicite"]),
        "≤ 25 %",
        bool(res["roic_implicite"] <= 0.25),
        False,
    ),
    ("Fonds propres positifs", nombre(res["fonds_propres"]), "> 0", res["fonds_propres"] > 0, True),
    (
        "VE / EBITDA du DCF vs marché",
        f"{multiple(mult_dcf)} vs {multiple(mult_marche)}",
        "écart ≤ 30 %",
        abs(mult_dcf / mult_marche - 1) <= 0.30,
        False,
    ),
    ("Écart valeur / cours", pct(pot), "entre −40 % et +40 %", abs(pot) <= 0.40, False),
]
if stats:
    controles.append(
        (
            "Bêta utilisé fiable",
            "sectoriel" if beta_u is not None else ("oui" if donnees["beta_fiable"] else "non"),
            "R² ≥ 20 % et 0,6 ≤ β ≤ 1,6, ou sectoriel",
            beta_u is not None or donnees["beta_fiable"],
            False,
        )
    )
if donnees.get("dette_a_saisir"):
    controles.append(
        (
            "Dette nette vérifiée",
            "oui" if dette_verifiee else "non",
            "saisie du rapport annuel",
            dette_verifiee,
            True,
        )
    )
if donnees.get("financement_captif"):
    controles.append(
        ("Pas de banque captive consolidée", "banque captive", "DCF industriel seul", False, True)
    )
df_ctl = pd.DataFrame(controles, columns=["Contrôle", "Valeur", "Règle", "OK", "Bloquant"])
nb_ko = int((~df_ctl["OK"]).sum())
bloquants = df_ctl.loc[~df_ctl["OK"] & df_ctl["Bloquant"], "Contrôle"].tolist()
if bloquants or nb_ko >= 4:
    reco = "NON CONCLUANTE"
else:
    reco = "ACHAT" if pot > 0.15 else "VENTE" if pot < -0.10 else "CONSERVER"

# ---------------------------------------------------------------------------
# Résultats
# ---------------------------------------------------------------------------

st.divider()
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Valeur par action", f"{nombre(res['valeur_action'], 2)} {sym}")
c2.metric("Cours actuel", f"{nombre(cours, 2)} {sym}")
c3.metric("Potentiel", pct(pot), delta=pct(pot), delta_color="normal")
c4.metric("WACC", pct(w, 2))
c5.metric("Recommandation", reco)
st.caption(
    f"Valeur au {donnees['date_cloture']:%d/%m/%Y} : "
    f"{nombre(res['valeur_action_cloture'], 2)} {sym}"
    + (
        f" · ramenée à aujourd'hui ({nombre(options['decalage'], 2)} an après la clôture)"
        if a_date
        else " · attention : valeur à la date de clôture, comparée au cours du jour"
    )
    + (f" · {nombre(nb_dilue, 1)} M actions diluées" if actions_dilutives else "")
)

if reco == "NON CONCLUANTE":
    st.error(
        "Pas de recommandation : "
        + (
            ", ".join(bloquants) + " (contrôle bloquant)"
            if bloquants
            else f"{nb_ko} contrôles en échec"
        )
        + ". Corrige les données avant de conclure (onglet Contrôles).",
        icon=":material/block:",
    )
elif abs(pot) > 0.40:
    st.warning(
        f"Valeur très éloignée du cours ({pct(pot)}) : avant de conclure que le marché se trompe, "
        "vérifie la dette nette, le bêta, le Capex et les loyers (onglet Contrôles).",
        icon=":material/search:",
    )
if beta < 0.6:
    st.warning(
        f"Bêta très faible ({nombre(beta, 2)}) : le coût des fonds propres ({pct(rf + beta * prime)}) est "
        "probablement sous-estimé. Préfère le bêta sectoriel (Damodaran).",
        icon=":material/warning:",
    )
if res["fonds_propres"] < 0:
    st.error(
        "La valeur des fonds propres est négative : la dette nette est probablement surestimée "
        "(activité de financement consolidée, comme la banque de Renault ?). Corrige-la ci-dessus."
    )

onglets = st.tabs(
    [
        "Projection",
        "Sensibilité",
        "DCF inversé",
        "Scénarios",
        "Comparables",
        "Football field",
        "Contrôles",
        "Historique",
    ]
)

with onglets[0]:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Somme des FCFF actualisés", nombre(res["somme_fcff_actu"]))
    c2.metric("VT actualisée", nombre(res["vt_actu"]))
    c3.metric("Valeur d'entreprise", nombre(res["ve"]))
    c4.metric("Fonds propres", nombre(res["fonds_propres"]))
    tableau = proj.drop(columns=["n"]).T
    tableau.columns = [str(c) for c in tableau.columns]
    st.dataframe(
        tableau.style.format(lambda x: nombre(x, 3) if abs(x) < 2 else nombre(x)), width="stretch"
    )
    graphe = (
        proj[["FCFF", "FCFF actualisé"]]
        .reset_index()
        .melt(id_vars="annee", var_name="Série", value_name="Montant")
    )
    graphe["annee"] = graphe["annee"].astype(str)
    base = alt.Chart(graphe).encode(
        x=alt.X("annee:N", title=None, axis=alt.Axis(labelAngle=0)),
        y=alt.Y("Montant:Q", title=f"M{sym}", scale=alt.Scale(zero=False)),
        color=alt.Color(
            "Série:N",
            scale=alt.Scale(range=["#1F3864", "#9DB4D9"]),
            legend=alt.Legend(orient="top", title=None),
        ),
        tooltip=[
            alt.Tooltip("Série:N"),
            alt.Tooltip("annee:N", title="Année"),
            alt.Tooltip("Montant:Q", title=f"Montant (M{sym})", format=",.0f"),
        ],
    )
    courbes = base.mark_line(point=alt.OverlayMarkDef(size=70), strokeWidth=2.5)
    etiquettes = base.mark_text(dy=-12, fontSize=11, color="#8A8F98").encode(
        text=alt.Text("Montant:Q", format=",.0f")
    )
    st.altair_chart((courbes + etiquettes).properties(height=320), width="stretch")
    st.caption(
        "L'axe ne part pas de zéro pour rendre visibles les écarts d'une année à l'autre. "
        "L'écart entre les deux courbes montre l'effet de l'actualisation."
    )
    st.caption(
        "FCFF = NOPAT + D&A − Capex − ΔBFR − loyers IFRS 16 · "
        + (
            "VT = FCFF final × (1 + g) / (WACC − g)"
            if roic_t is None
            else f"VT = NOPAT final × (1 + g) × (1 − g / ROIC) / (WACC − g), ROIC = {pct(roic_t)}"
        )
        + (f" · {transition} années de transition vers g" if transition else "")
    )
    st.caption(
        f"Pont VE → fonds propres : VE {nombre(res['ve'])} − dette nette {nombre(dette_nette)} − "
        f"minoritaires {nombre(minoritaires)} − retraites {nombre(provisions)} − autres dettes "
        f"{nombre(autres_dettes)} + participations {nombre(participations)} − dividendes versés "
        f"{nombre(dividendes * nb_actions)} = {nombre(res['fonds_propres'])}"
    )

with onglets[1]:
    waccs = [round(w + d, 4) for d in (-0.01, -0.005, 0, 0.005, 0.01)]
    gs = [round(g + d, 4) for d in (-0.005, -0.0025, 0, 0.0025, 0.005)]
    gs = [x for x in gs if x < min(waccs)]
    sens = sensibilite_wacc_g(hyp, ent, waccs, gs, **options)
    sens.index = [f"WACC {pct(x, 2)}" for x in sens.index]
    sens.columns = [f"g {pct(x, 2)}" for x in sens.columns]

    def couleur(v):
        """Vert si la valeur dépasse le cours, rouge sinon."""
        return (
            "background-color:#C6EFCE;color:#006100"
            if v >= cours
            else "background-color:#FFC7CE;color:#9C0006"
        )

    st.markdown("**Valeur par action selon le WACC et g** (vert : au-dessus du cours)")
    st.dataframe(sens.style.map(couleur).format(lambda x: nombre(x, 2)), width="stretch")

    croissances = [round(croissance + d, 4) for d in (-0.02, -0.01, -0.005, 0, 0.005, 0.01, 0.02)]
    marges = [round(marge + d, 4) for d in (-0.01, -0.005, 0, 0.005, 0.01) if marge + d > 0]
    sens2 = sensibilite_croissance_marge(hyp, ent, w, croissances, marges, **options)
    sens2.index = [f"Croissance {pct(x, 2)}" for x in sens2.index]
    sens2.columns = [f"Marge {pct(x, 2)}" for x in sens2.columns]
    st.markdown("**Valeur par action selon la croissance du CA et la marge EBIT**")
    st.dataframe(sens2.style.map(couleur).format(lambda x: nombre(x, 2)), width="stretch")
    st.caption(
        "Compare l'effet d'un point de marge et d'un point de croissance : "
        "c'est souvent la marge qui pèse le plus."
        + (" Le ROIC terminal reste fixé à sa valeur centrale." if roic_t is not None else "")
    )

with onglets[2]:
    st.markdown(
        f"**Que faut-il supposer pour justifier le cours de {nombre(cours, 2)} "
        f"{sym} ?** Chaque ligne fait varier un seul paramètre, "
        "les autres restant à leur valeur retenue."
    )
    inverses = {
        "Croissance du CA": (dcf_inverse(hyp, ent, w, "croissance_ca", **options), croissance),
        "Marge EBIT": (dcf_inverse(hyp, ent, w, "marge_ebit", **options), marge),
        "WACC": (dcf_inverse(hyp, ent, w, "wacc", **options), w),
    }
    colonnes = st.columns(3)
    for col, (nom, (implicite, retenu)) in zip(colonnes, inverses.items()):
        if implicite is None:
            col.metric(f"{nom} implicite", "hors bornes")
        else:
            ecart = (implicite - retenu) * 100
            col.metric(
                f"{nom} implicite",
                pct(implicite, 2),
                delta=f"{ecart:+.2f} pt vs retenu ({pct(retenu, 2)})".replace(".", ","),
                delta_color="off",
            )
    phrases = []
    if inverses["Croissance du CA"][0] is not None:
        phrases.append(f"une croissance du CA de {pct(inverses['Croissance du CA'][0], 1)} par an")
    if inverses["Marge EBIT"][0] is not None:
        phrases.append(f"une marge EBIT de {pct(inverses['Marge EBIT'][0], 1)}")
    if inverses["WACC"][0] is not None:
        phrases.append(f"un WACC de {pct(inverses['WACC'][0], 1)}")
    if phrases:
        st.info(
            "Le cours actuel suppose " + ", ou ".join(phrases) + ". "
            "À toi de juger si c'est réaliste au regard de l'historique et de la guidance."
        )
    st.caption(
        "Méthode : recherche par dichotomie de la valeur du paramètre pour laquelle la valeur "
        "par action égale le cours."
    )

with onglets[3]:
    st.markdown(
        "**Écarts par rapport au scénario central** (en points, modifiables). "
        "Le scénario central reprend les curseurs ci-dessus."
    )
    st.caption(
        "Par défaut, les scénarios sont symétriques : avec des poids symétriques, la valeur pondérée "
        "reste proche du central. Des écarts asymétriques reviennent à prendre parti sans le dire."
    )
    ecarts_defaut = pd.DataFrame(
        {"Pessimiste": [-1.5, -1.0, -0.25, 0.10], "Optimiste": [1.5, 1.0, 0.25, -0.10]},
        index=["Croissance du CA (pt)", "Marge EBIT (pt)", "g (pt)", "Bêta"],
    )
    ecarts = st.data_editor(ecarts_defaut, key=f"scen_{cle}", width="stretch")
    p1, p2, p3 = st.columns(3)
    poids = {
        "Pessimiste": p1.number_input("Poids pessimiste (%)", 0, 100, 25, 5, key=f"pp_{cle}"),
        "Central": p2.number_input("Poids central (%)", 0, 100, 50, 5, key=f"pc_{cle}"),
        "Optimiste": p3.number_input("Poids optimiste (%)", 0, 100, 25, 5, key=f"po_{cle}"),
    }

    lignes = []
    for nom in ("Pessimiste", "Central", "Optimiste"):
        e = ecarts[nom] if nom != "Central" else pd.Series(0.0, index=ecarts.index)
        h_s = Hypotheses(
            **{
                **hyp.__dict__,
                "croissance_ca": croissance + e["Croissance du CA (pt)"] / 100,
                "marge_ebit": marge + e["Marge EBIT (pt)"] / 100,
                "g": g + e["g (pt)"] / 100,
            }
        )
        b_s = beta + e["Bêta"]
        w_s = wacc_pour(b_s)
        try:
            v = valoriser(h_s, ent, w_s, **options)["valeur_action"]
        except ValueError:
            v = float("nan")
        lignes.append(
            {
                "Scénario": nom,
                "Croissance": pct(h_s.croissance_ca, 2),
                "Marge EBIT": pct(h_s.marge_ebit, 2),
                "g": pct(h_s.g, 2),
                "Bêta": nombre(b_s, 2),
                "WACC": pct(w_s, 2),
                "Valeur": v,
                "Potentiel": v / cours - 1,
                "Poids": poids[nom] / 100,
            }
        )
    scen = pd.DataFrame(lignes)
    total_poids = scen["Poids"].sum()
    st.dataframe(
        scen.style.format(
            {
                "Valeur": lambda x: nombre(x, 2),
                "Potentiel": lambda x: pct(x),
                "Poids": lambda x: pct(x, 0),
            }
        ),
        hide_index=True,
        width="stretch",
    )
    if abs(total_poids - 1) > 1e-9:
        st.warning(f"Le total des poids fait {pct(total_poids, 0)} au lieu de 100 %.")
    else:
        v_pond = float((scen["Valeur"] * scen["Poids"]).sum())
        c1, c2 = st.columns(2)
        c1.metric("Valeur pondérée", f"{nombre(v_pond, 2)} {sym}")
        c2.metric("Potentiel pondéré", pct(v_pond / cours - 1))
        barres = (
            alt.Chart(scen)
            .mark_bar(size=60)
            .encode(
                x=alt.X(
                    "Scénario:N",
                    sort=["Pessimiste", "Central", "Optimiste"],
                    title=None,
                    axis=alt.Axis(labelAngle=0),
                ),
                y=alt.Y("Valeur:Q", title=f"Valeur par action ({sym})"),
                color=alt.Color(
                    "Scénario:N",
                    legend=None,
                    scale=alt.Scale(
                        domain=["Pessimiste", "Central", "Optimiste"],
                        range=["#C0504D", "#1F3864", "#4F9A57"],
                    ),
                ),
                tooltip=[
                    alt.Tooltip("Scénario:N"),
                    alt.Tooltip("Valeur:Q", title=f"Valeur ({sym})", format=",.2f"),
                    alt.Tooltip("Potentiel:Q", format="+.1%"),
                ],
            )
        )
        texte = barres.mark_text(dy=-8, fontSize=12).encode(
            text=alt.Text("Valeur:Q", format=",.2f"), color=alt.value("#8A8F98")
        )
        regle = (
            alt.Chart(pd.DataFrame({"Cours": [cours]}))
            .mark_rule(strokeDash=[6, 4], color="#7F7F7F")
            .encode(
                y="Cours:Q", tooltip=[alt.Tooltip("Cours:Q", title=f"Cours ({sym})", format=",.2f")]
            )
        )
        st.altair_chart((barres + texte + regle).properties(height=300), width="stretch")
        st.caption("Ligne pointillée : cours actuel.")

with onglets[4]:
    st.markdown(
        "**Valorisation par les multiples de comparables** : on applique à l'EBITDA de "
        "l'entreprise le multiple VE / EBITDA auquel le marché valorise des entreprises similaires."
    )
    cle_comp = f"comp_{cle}"
    st.session_state.setdefault(cle_comp, donnees["comparables_defaut"])
    if st.button(
        "Rechercher automatiquement",
        icon=":material/search:",
        key=f"auto_{cle}",
        help="Même secteur d'activité (classification Yahoo), même zone géographique, "
        "taille la plus proche.",
    ):
        trouves = chercher_comparables("SGO.PA" if exemple else ticker, forcer=True)
        if trouves:
            st.session_state[cle_comp] = ", ".join(trouves)
            st.rerun()
        else:
            st.warning("Aucun comparable trouvé automatiquement : saisis-les à la main.")
    saisie = st.text_input(
        "Tickers des comparables (séparés par des virgules)",
        key=cle_comp,
        placeholder="ex. HOLN.SW, SIKA.SW, HEI.DE",
        help="Vérifie toujours la liste : la classification sectorielle de Yahoo "
        "n'est pas parfaite. 4 à 6 entreprises suffisent.",
    )
    tickers_comp = tuple(t.strip().upper() for t in saisie.split(",") if t.strip())
    comp, val_multiples = None, None
    if tickers_comp:
        try:
            comp = charger_comparables(tickers_comp)
        except Exception as e:
            st.error(f"Impossible de récupérer les comparables : {e}")
    if comp is not None and comp["VE / EBITDA"].notna().any():
        mult = comp["VE / EBITDA"].dropna()

        def par_action(m_):
            """Valeur par action obtenue en appliquant le multiple m_ à l'EBITDA."""
            return (m_ * ebitda_comp - passif) / nb_dilue

        stats_mult = {
            "1er quartile": mult.quantile(0.25),
            "Médiane": mult.median(),
            "3e quartile": mult.quantile(0.75),
        }
        val_multiples = {k: par_action(v) for k, v in stats_mult.items()}
        st.dataframe(
            comp.style.format(
                {
                    c: lambda x: multiple(x) if pd.notna(x) else "–"
                    for c in ("VE / EBITDA", "VE / CA", "PER")
                }
            ),
            hide_index=True,
            width="stretch",
        )
        c1, c2, c3 = st.columns(3)
        for col, (k, v) in zip((c1, c2, c3), stats_mult.items()):
            col.metric(
                f"VE / EBITDA — {k}",
                multiple(v),
                delta=f"{nombre(val_multiples[k], 2)} {sym} par action",
                delta_color="off",
            )
        if len(mult) < 4:
            st.warning(
                f"Seulement {len(mult)} multiple(s) exploitable(s) : les quartiles n'ont pas de sens "
                "statistique. Ajoute des comparables.",
                icon=":material/warning:",
            )
        if stats_mult["3e quartile"] > 1.6 * stats_mult["1er quartile"]:
            st.warning(
                "Multiples très dispersés (le 3e quartile dépasse de plus de 60 % le 1er) : les "
                "comparables ne sont sans doute pas homogènes (activités, croissance, marges, "
                "éléments exceptionnels comme une scission). Vérifie la liste.",
                icon=":material/warning:",
            )
        st.info(
            f"L'entreprise cote {multiple(mult_marche)} son EBITDA, ton DCF la valorise "
            f"{multiple(mult_dcf)}, la médiane des comparables est à {multiple(stats_mult['Médiane'])}. "
            "Un écart durable avec les comparables (décote ou prime) s'explique en général par la "
            "croissance, les marges, le risque ou un mix d'activités différent : c'est à l'analyste "
            "de juger s'il est justifié."
        )
        st.caption(
            f"EBITDA utilisé (ici et dans Contrôles) : {nombre(ebitda_comp)} "
            + (
                "(Yahoo, 12 derniers mois, même définition que les comparables)"
                if donnees.get("ebitda_ttm")
                else f"(EBIT + D&A {e0.annee_derniere})"
            )
            + " · valeur par action = (multiple × EBITDA − dette nette − minoritaires − retraites − "
            "autres dettes + participations) / actions diluées. Multiples Yahoo sur 12 mois, "
            "corrigés quand la devise des comptes diffère de la devise de cotation."
        )
    elif tickers_comp:
        st.warning("Aucun multiple VE / EBITDA exploitable pour ces tickers.")

with onglets[5]:
    st.markdown(
        "**Football field** : les fourchettes de valeur par action obtenues avec chaque méthode, "
        "comparées au cours actuel."
    )
    barres_ff = [
        ("DCF — sensibilité WACC × g", float(sens.min().min()), float(sens.max().max())),
        ("DCF — scénarios", float(scen["Valeur"].min()), float(scen["Valeur"].max())),
    ]
    if val_multiples:
        barres_ff.append(
            (
                "Comparables — VE / EBITDA (Q1 à Q3)",
                val_multiples["1er quartile"],
                val_multiples["3e quartile"],
            )
        )
    if bas_52s:
        barres_ff.append(("Cours — plus bas / plus haut 52 semaines", bas_52s, haut_52s))
    fourchettes = pd.DataFrame(barres_ff, columns=["Méthode", "Bas", "Haut"])
    ordre = list(fourchettes["Méthode"])
    axe_y = alt.Y("Méthode:N", sort=ordre, title=None, axis=alt.Axis(labelLimit=260))
    barres = (
        alt.Chart(fourchettes)
        .mark_bar(height=26, color="#9DB4D9")
        .encode(
            x=alt.X("Bas:Q", title=f"Valeur par action ({sym})", scale=alt.Scale(zero=False)),
            x2="Haut:Q",
            y=axe_y,
            tooltip=[
                alt.Tooltip("Méthode:N"),
                alt.Tooltip("Bas:Q", title=f"Bas ({sym})", format=",.2f"),
                alt.Tooltip("Haut:Q", title=f"Haut ({sym})", format=",.2f"),
            ],
        )
    )
    txt_bas = (
        alt.Chart(fourchettes)
        .mark_text(align="right", dx=-5, fontSize=11, color="#8A8F98")
        .encode(x="Bas:Q", y=axe_y, text=alt.Text("Bas:Q", format=",.1f"))
    )
    txt_haut = (
        alt.Chart(fourchettes)
        .mark_text(align="left", dx=5, fontSize=11, color="#8A8F98")
        .encode(x="Haut:Q", y=axe_y, text=alt.Text("Haut:Q", format=",.1f"))
    )
    reperes = pd.DataFrame(
        {"Repère": ["Cours actuel", "DCF central"], "Valeur": [cours, res["valeur_action"]]}
    )
    lignes_ref = (
        alt.Chart(reperes)
        .mark_rule(strokeWidth=2, strokeDash=[6, 4])
        .encode(
            x="Valeur:Q",
            tooltip=[
                alt.Tooltip("Repère:N"),
                alt.Tooltip("Valeur:Q", title=f"Valeur ({sym})", format=",.2f"),
            ],
            color=alt.Color(
                "Repère:N",
                scale=alt.Scale(range=["#C0504D", "#1F3864"]),
                legend=alt.Legend(orient="top", title=None),
            ),
        )
    )
    st.altair_chart(
        (barres + txt_bas + txt_haut + lignes_ref).properties(height=60 + 55 * len(fourchettes)),
        width="stretch",
    )
    st.caption(
        "Chaque barre va de la valeur la plus basse à la plus haute de la méthode. "
        "Les méthodes qui se recoupent autour du cours suggèrent une action correctement valorisée."
    )

with onglets[6]:
    df_ctl["Statut"] = [
        ("OK" if ok else "Bloquant" if bl else "À vérifier")
        for ok, bl in zip(df_ctl["OK"], df_ctl["Bloquant"])
    ]
    st.dataframe(
        df_ctl.drop(columns=["OK", "Bloquant"]).style.map(
            lambda v: (
                "color:#2E8B57;font-weight:600"
                if v == "OK"
                else "color:#C0392B;font-weight:600"
                if v == "Bloquant"
                else "color:#D9822B;font-weight:600"
            ),
            subset=["Statut"],
        ),
        hide_index=True,
        width="stretch",
    )
    nb_ok = len(df_ctl) - nb_ko
    (st.success if nb_ko == 0 else st.warning)(f"{nb_ok} / {len(df_ctl)} contrôles OK")
    st.caption(
        "Règle de recommandation : aucune recommandation si un contrôle bloquant échoue ou si "
        "4 contrôles ou plus sont à vérifier. Multiples calculés avec le même EBITDA que l'onglet "
        "Comparables."
    )

with onglets[7]:
    if exemple:
        st.info("Exemple construit à partir du rapport annuel : voir le fichier Excel du projet.")
    else:
        hist, rat = donnees["historique"].copy(), donnees["ratios"].copy()
        hist.attrs, rat.attrs = {}, {}  # métadonnées internes, non affichables
        hist.columns = [str(c) for c in hist.columns]  # années + « Moyenne » : tout en texte
        rat.columns = [str(c) for c in rat.columns]
        st.markdown("**Données historiques (Yahoo Finance)**")
        st.dataframe(hist.style.format(lambda x: nombre(x)), width="stretch")
        st.markdown("**Ratios**")
        st.dataframe(rat.style.format(lambda x: pct(x) if pd.notna(x) else "–"), width="stretch")
        st.caption(
            "EBIT = résultat d'exploitation de Yahoo (« Operating Income », hors éléments "
            "exceptionnels). La ligne « EBIT Yahoo (avec exceptionnels) » montre l'écart. "
            "Les définitions de Yahoo peuvent différer du rapport annuel."
        )

# ---------------------------------------------------------------------------
# Export Excel (avec les formules)
# ---------------------------------------------------------------------------

st.sidebar.divider()
sens_export = sens.copy()
scen_export = scen.set_index("Scénario")[["Valeur", "Potentiel", "Poids"]]
fichier = exporter_excel(
    donnees["nom"],
    devise,
    hyp,
    ent,
    parametres_wacc_pour(beta),
    spread,
    options["decalage"],
    options["dividendes_par_action"],
    tableaux={
        "Sensibilité WACC × g": sens_export,
        "Sensibilité croissance × marge": sens2,
        "Scénarios": scen_export,
    },
)
st.sidebar.download_button(
    "Télécharger le modèle Excel",
    icon=":material/download:",
    data=fichier,
    file_name=f"DCF_{ticker.replace('.', '_')}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    width="stretch",
    help="Le classeur contient les formules : modifie une hypothèse, tout se recalcule.",
)

st.divider()
st.caption("Projet personnel à visée pédagogique — ne constitue pas un conseil en investissement.")
