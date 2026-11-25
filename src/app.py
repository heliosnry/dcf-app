"""
app.py — Interface Streamlit du DCF automatisé

Lancer :  streamlit run src/app.py
"""

import altair as alt
import pandas as pd
import streamlit as st

from datetime import date

from data import SPREADS, beta_blume, note_synthetique, parametres_pays
from dcf import (DonneesEntreprise, Hypotheses, ParametresWACC, dcf_inverse,
                 sensibilite_croissance_marge, sensibilite_wacc_g, valoriser, wacc)

st.set_page_config(page_title="DCF automatisé", page_icon="📈", layout="wide")


# ---------------------------------------------------------------------------
# Données de référence : Saint-Gobain d'après le rapport annuel (modèle Excel)
# ---------------------------------------------------------------------------

EXEMPLE_SG = {
    "nom": "Saint-Gobain (rapport annuel 2025)", "devise": "EUR", "beta": 1.428, "beta_yahoo": None,
    "logo": "https://www.google.com/s2/favicons?domain=saint-gobain.com&sz=128",
    "couverture": 5293 / 388, "note": "Baa2/BBB", "spread": 0.0111, "petite": False,
    "pays": parametres_pays("France"), "taux_is_source": "modèle Excel", "dettes_locatives": None,
    "date_cloture": pd.Timestamp("2025-12-31"),
    "decalage": max((date.today() - date(2025, 12, 31)).days / 365.25, 0.0),
    "dividendes": 0.0, "alertes": [], "devise_comptes": "EUR",
    "hypotheses": Hypotheses(croissance_ca=0.0125, marge_ebit=0.115, da_ca=0.04, capex_ca=0.0425,
                             bfr_ca=0.021, taux_is=0.26, g=0.02, horizon=5),
    "entreprise": DonneesEntreprise(ca_dernier=46483, bfr_dernier=968, annee_derniere=2025,
                                    dette_nette=10356, minoritaires=568, provisions_retraites=1444,
                                    nb_actions=489.9, cours=73.20),
    "historique": None, "ratios": None,
}


@st.cache_data(ttl=3600, show_spinner="Récupération des données Yahoo Finance…")
def charger(ticker: str) -> dict:
    from data import preparer
    d = preparer(ticker)
    m = d["marche"]
    return {"nom": m["nom"], "devise": m["devise"], "devise_comptes": m["devise_comptes"],
            "beta": d["beta"], "beta_yahoo": m["beta_yahoo"], "logo": m["logo"],
            "couverture": d["couverture"], "note": d["note"], "spread": d["spread"], "petite": d["petite"],
            "pays": d["pays"], "taux_is_source": d["taux_is_source"], "dettes_locatives": d["dettes_locatives"],
            "date_cloture": d["date_cloture"], "decalage": d["decalage"], "dividendes": d["dividendes"],
            "alertes": d["alertes"],
            "hypotheses": d["hypotheses"], "entreprise": d["entreprise"],
            "historique": d["historique"], "ratios": d["ratios"]}


def pct(x: float, dec: int = 1) -> str:
    return f"{x * 100:.{dec}f} %".replace(".", ",")


def nombre(x: float, dec: int = 0) -> str:
    return f"{x:,.{dec}f}".replace(",", " ").replace(".", ",")


# ---------------------------------------------------------------------------
# Barre latérale : choix de l'entreprise et paramètres de marché
# ---------------------------------------------------------------------------

st.sidebar.title("📈 DCF automatisé")
source = st.sidebar.radio("Source des données",
                          ["Ticker Yahoo Finance", "Exemple : Saint-Gobain (rapport annuel)"])

if source == "Ticker Yahoo Finance":
    ticker = st.sidebar.text_input("Ticker", value="SGO.PA",
                                   help="Exemples : SGO.PA, ML.PA, ORA.PA, AAPL").strip().upper()
    try:
        donnees = charger(ticker)
    except ValueError as e:
        st.warning(f"⚠ {e}")
        st.stop()
    except Exception as e:  # réseau, ticker inconnu…
        st.error(f"Impossible de récupérer les données pour « {ticker} » : {e}")
        st.stop()
else:
    ticker = "SGO-exemple"
    donnees = EXEMPLE_SG

h0, e0 = donnees["hypotheses"], donnees["entreprise"]
cle = ticker  # pour remettre les curseurs à zéro quand on change d'entreprise

if st.sidebar.button("↺ Réinitialiser les valeurs", width="stretch",
                     help="Revenir aux valeurs par défaut pour cette entreprise"):
    for k in [k for k in st.session_state if str(k).endswith(f"_{cle}")]:
        del st.session_state[k]
    st.rerun()

st.sidebar.subheader("Coût du capital")
pays = donnees["pays"]
st.sidebar.caption(f"Pays : {pays['pays']}" + ("" if pays["connu"] else " (absent de la table : paramètres France)"))
rf = st.sidebar.number_input("Taux sans risque (%)", value=round(pays["rf"] * 100, 2), step=0.05,
                             key=f"rf_{cle}",
                             help="Taux d'État à 10 ans du pays (novembre 2026). France : TEC 10 · "
                                  "Allemagne : Bund · États-Unis : T-Note.") / 100
prime = st.sidebar.number_input("Prime de risque du marché (%)", value=round(pays["prime"] * 100, 2),
                                step=0.05, key=f"erp_{cle}",
                                help="Prime de risque totale du pays, Damodaran (janvier 2026)") / 100
notes = list(SPREADS)
note = st.sidebar.selectbox("Note de crédit", notes, index=notes.index(donnees["note"]), key=f"note_{cle}",
                            help="Par défaut : note synthétique de Damodaran, déduite de la couverture "
                                 "des intérêts (EBIT / charges d'intérêts). Remplace-la par la vraie note "
                                 "S&P ou Moody's si tu la connais.")
spread = SPREADS[note]
cv = donnees["couverture"]
table = "petites entreprises" if donnees.get("petite") else "grandes entreprises"
st.sidebar.caption((f"Couverture des intérêts : {cv:.1f}x → note synthétique "
                    f"{note_synthetique(cv, donnees.get('petite', False))[0]} (table {table}). "
                    if cv else "Charges d'intérêts indisponibles : BBB par défaut. ")
                   + f"Spread retenu : {pct(spread, 2)}")
type_beta = st.sidebar.radio("Bêta", ["Brut", "Ajusté (Blume)"], horizontal=True, key=f"tb_{cle}",
                             help="Blume : 0,67 × bêta brut + 0,33. Les bêtas tendent vers 1 avec le temps.")
beta_defaut = donnees["beta"] if type_beta == "Brut" else beta_blume(donnees["beta"])
beta = st.sidebar.number_input("Valeur du bêta", value=round(float(beta_defaut), 3), step=0.05,
                               format="%.3f", key=f"beta_{type_beta}_{cle}",
                               help="Calculé sur 5 ans en mensuel contre l'indice du pays")
if donnees.get("beta_yahoo"):
    st.sidebar.caption(f"Bêta affiché par Yahoo : {donnees['beta_yahoo']:.2f} "
                       "(calculé contre le S&P 500, même pour une action européenne)")

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
st.caption(f"Montants en millions de {donnees['devise']} · dernière clôture : "
           f"{donnees['date_cloture']:%d/%m/%Y}")
for alerte in donnees.get("alertes", []):
    st.warning(alerte, icon="⚠️")

croissance_defaut = min(max(h0.croissance_ca, 0.0), 0.05)   # bornée entre 0 % et 5 %

with st.expander("Hypothèses opérationnelles", expanded=True):
    c1, c2, c3, c4 = st.columns(4)
    croissance = c1.slider("Croissance du CA (%/an)", -5.0, 10.0, round(croissance_defaut * 100, 2), 0.25,
                           key=f"cr_{cle}") / 100
    marge = c1.slider("Marge EBIT (%)", 0.0, 40.0, round(h0.marge_ebit * 100, 2), 0.25, key=f"mg_{cle}") / 100
    da = c2.slider("D&A / CA (%)", 0.0, 20.0, round(h0.da_ca * 100, 2), 0.25, key=f"da_{cle}") / 100
    capex = c2.slider("Capex / CA (%)", 0.0, 20.0, round(h0.capex_ca * 100, 2), 0.25, key=f"cx_{cle}") / 100
    bfr = c3.slider("BFR / CA (%)", -20.0, 40.0, round(h0.bfr_ca * 100, 2), 0.5, key=f"bfr_{cle}") / 100
    taux_is = c3.slider("Taux d'impôt (%)", 0.0, 40.0, round(h0.taux_is * 100, 1), 0.5, key=f"is_{cle}") / 100
    g = c4.slider("Croissance à l'infini g (%)", 0.0, 4.0, round(h0.g * 100, 2), 0.25, key=f"g_{cle}") / 100
    horizon = c4.slider("Horizon (années)", 3, 10, h0.horizon, key=f"hz_{cle}")
    loyers = c4.slider("Loyers IFRS 16 / CA (%)", 0.0, 10.0, 0.0, 0.25, key=f"ly_{cle}",
                       help="Renouvellement des contrats de location (≈ amortissement des droits "
                            "d'utilisation, voir l'annexe du rapport annuel). À renseigner pour la "
                            "distribution, l'hôtellerie, les compagnies aériennes…") / 100
    if croissance_defaut != h0.croissance_ca:
        st.caption(f"ℹ Croissance historique moyenne : {pct(h0.croissance_ca)}. "
                   "Valeur par défaut bornée entre 0 % et 5 %.")
    st.caption(f"Taux d'impôt par défaut : {donnees.get('taux_is_source', '')}.")

with st.expander("Méthode", expanded=True):
    c1, c2, c3 = st.columns(3)
    methode_vt = c1.radio("Valeur terminale", ["Gordon sur le FCFF", "Formule ROIC (McKinsey)"],
                          key=f"vt_{cle}",
                          help="Gordon : VT = FCFF × (1 + g) / (WACC − g). La formule ROIC impose que la "
                               "croissance soit financée : FCFF = NOPAT × (1 − g / ROIC).")
    roic_t = None
    if methode_vt == "Formule ROIC (McKinsey)":
        roic_t = c1.slider("ROIC des nouveaux investissements (%)", 4.0, 40.0, 12.0, 0.5, key=f"roic_{cle}",
                           help="À long terme, la concurrence ramène le ROIC vers le coût du capital. "
                                "ROIC = WACC : la croissance ne crée plus de valeur.") / 100
    mi_annee = c2.checkbox("Convention de milieu d'année", value=False, key=f"mid_{cle}",
                           help="Les flux arrivent tout au long de l'année : on les actualise à mi-année "
                                "(pratique courante en banque d'affaires).")
    a_date = c3.checkbox("Valoriser à la date du jour", value=donnees["historique"] is not None,
                         key=f"date_{cle}",
                         help="Sans cette option, la valeur est calculée à la date de clôture : la "
                              "comparer au cours du jour est incohérent. Avec, la VE est capitalisée au "
                              "WACC jusqu'à aujourd'hui et les dividendes versés depuis sont retirés.")
    dividendes = 0.0
    if a_date:
        dividendes = c3.number_input(f"Dividendes versés depuis le {donnees['date_cloture']:%d/%m/%Y} "
                                     "(par action)", value=float(round(donnees["dividendes"], 2)),
                                     step=0.1, key=f"div_{cle}")
        if dividendes == 0:
            c3.caption("ℹ Aucun dividende saisi : la valeur est surestimée si un dividende a été versé "
                       "depuis la clôture.")

with st.expander("Passage aux fonds propres (à vérifier dans le rapport annuel)"):
    c1, c2, c3, c4 = st.columns(4)
    dette_nette = c1.number_input("Dette nette", value=float(round(e0.dette_nette)), step=100.0, key=f"dn_{cle}")
    if donnees.get("dettes_locatives"):
        c1.caption(f"dont dettes locatives IFRS 16 : {nombre(donnees['dettes_locatives'])}")
    minoritaires = c2.number_input("Intérêts minoritaires", value=float(round(e0.minoritaires)), step=10.0,
                                   key=f"mi_{cle}")
    provisions = c3.number_input("Provisions retraites", value=float(round(e0.provisions_retraites)), step=10.0,
                                 key=f"pr_{cle}", help="Absentes de Yahoo Finance : à saisir à la main")
    nb_actions = c4.number_input("Nombre d'actions (M)", value=float(round(e0.nb_actions, 1)), step=1.0,
                                 key=f"na_{cle}")

# ---------------------------------------------------------------------------
# Calcul
# ---------------------------------------------------------------------------

hyp = Hypotheses(croissance_ca=croissance, marge_ebit=marge, da_ca=da, capex_ca=capex,
                 bfr_ca=bfr, taux_is=taux_is, g=g, horizon=horizon, loyers_ca=loyers,
                 roic_terminal=roic_t, mi_annee=mi_annee)
options = {"decalage": donnees["decalage"] if a_date else 0.0, "dividendes_par_action": dividendes}
ent = DonneesEntreprise(ca_dernier=e0.ca_dernier, bfr_dernier=e0.bfr_dernier,
                        annee_derniere=e0.annee_derniere, dette_nette=dette_nette,
                        minoritaires=minoritaires, provisions_retraites=provisions,
                        nb_actions=nb_actions, cours=e0.cours)
capitalisation = nb_actions * e0.cours


def wacc_pour(b: float) -> float:
    """WACC avec les paramètres de la barre latérale et le bêta b."""
    return wacc(ParametresWACC(taux_sans_risque=rf, beta=b, prime_risque=prime, kd=rf + spread,
                               taux_is=taux_is, capitalisation=capitalisation,
                               dette=max(dette_nette, 0.0)))


w = wacc_pour(beta)

try:
    res = valoriser(hyp, ent, w, **options)
except ValueError as e:
    st.error(f"⚠ {e}")
    st.stop()

proj = res["projection"]
pot = res["potentiel"]
reco = "ACHAT" if pot > 0.15 else "VENTE" if pot < -0.10 else "CONSERVER"

# ---------------------------------------------------------------------------
# Résultats
# ---------------------------------------------------------------------------

st.divider()
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Valeur par action", f"{nombre(res['valeur_action'], 2)} {donnees['devise']}")
c2.metric("Cours actuel", f"{nombre(e0.cours, 2)} {donnees['devise']}")
c3.metric("Potentiel", pct(pot), delta=pct(pot), delta_color="normal")
c4.metric("WACC", pct(w, 2))
c5.metric("Recommandation", reco)
st.caption(f"Valeur au {donnees['date_cloture']:%d/%m/%Y} : "
           f"{nombre(res['valeur_action_cloture'], 2)} {donnees['devise']}"
           + (f" · ramenée à aujourd'hui ({nombre(options['decalage'], 2)} an après la clôture)"
              if a_date else " · ⚠ valeur à la date de clôture, comparée au cours du jour"))

if res["fonds_propres"] < 0:
    st.error("La valeur des fonds propres est négative : la dette nette est probablement surestimée "
             "(activité de financement consolidée, comme la banque de Renault ?). Corrige-la ci-dessus.")

onglets = st.tabs(["Projection", "Sensibilité", "DCF inversé", "Scénarios", "Contrôles", "Historique"])

with onglets[0]:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Somme des FCFF actualisés", nombre(res["somme_fcff_actu"]))
    c2.metric("VT actualisée", nombre(res["vt_actu"]))
    c3.metric("Valeur d'entreprise", nombre(res["ve"]))
    c4.metric("Fonds propres", nombre(res["fonds_propres"]))
    tableau = proj.drop(columns=["n"]).T
    tableau.columns = [str(c) for c in tableau.columns]
    st.dataframe(tableau.style.format(lambda x: nombre(x, 3) if abs(x) < 2 else nombre(x)),
                 width="stretch")
    graph = (proj[["FCFF", "FCFF actualisé"]].reset_index()
             .melt(id_vars="annee", var_name="Série", value_name="Montant"))
    graph["annee"] = graph["annee"].astype(str)
    base = alt.Chart(graph).encode(
        x=alt.X("annee:N", title=None, axis=alt.Axis(labelAngle=0)),
        y=alt.Y("Montant:Q", title=f"M{donnees['devise']}", scale=alt.Scale(zero=False)),
        color=alt.Color("Série:N", scale=alt.Scale(range=["#1F3864", "#9DB4D9"]),
                        legend=alt.Legend(orient="top", title=None)),
    )
    courbes = base.mark_line(point=alt.OverlayMarkDef(size=70), strokeWidth=2.5)
    etiquettes = base.mark_text(dy=-12, fontSize=11).encode(text=alt.Text("Montant:Q", format=",.0f"))
    st.altair_chart((courbes + etiquettes).properties(height=320), width="stretch")
    st.caption("L'axe ne part pas de zéro pour rendre visibles les écarts d'une année à l'autre. "
               "L'écart entre les deux courbes montre l'effet de l'actualisation.")
    st.caption("FCFF = NOPAT + D&A − Capex − ΔBFR − loyers IFRS 16 · "
               + ("VT = FCFF final × (1 + g) / (WACC − g)" if roic_t is None
                  else "VT = NOPAT final × (1 + g) × (1 − g / ROIC) / (WACC − g)"))

with onglets[1]:
    waccs = [round(w + d, 4) for d in (-0.01, -0.005, 0, 0.005, 0.01)]
    gs = [round(g + d, 4) for d in (-0.005, -0.0025, 0, 0.0025, 0.005)]
    gs = [x for x in gs if x < min(waccs)]
    sens = sensibilite_wacc_g(hyp, ent, waccs, gs, **options)
    sens.index = [f"WACC {pct(x, 2)}" for x in sens.index]
    sens.columns = [f"g {pct(x, 2)}" for x in sens.columns]

    def couleur(v):
        return ("background-color:#C6EFCE;color:#006100" if v >= e0.cours
                else "background-color:#FFC7CE;color:#9C0006")

    st.markdown("**Valeur par action selon le WACC et g** (vert : au-dessus du cours)")
    st.dataframe(sens.style.map(couleur).format(lambda x: nombre(x, 2)), width="stretch")

    croissances = [round(croissance + d, 4) for d in (-0.02, -0.01, -0.005, 0, 0.005, 0.01, 0.02)]
    marges = [round(marge + d, 4) for d in (-0.01, -0.005, 0, 0.005, 0.01) if marge + d > 0]
    sens2 = sensibilite_croissance_marge(hyp, ent, w, croissances, marges, **options)
    sens2.index = [f"Croissance {pct(x, 2)}" for x in sens2.index]
    sens2.columns = [f"Marge {pct(x, 2)}" for x in sens2.columns]
    st.markdown("**Valeur par action selon la croissance du CA et la marge EBIT**")
    st.dataframe(sens2.style.map(couleur).format(lambda x: nombre(x, 2)), width="stretch")
    st.caption("Compare l'effet d'un point de marge et d'un point de croissance : "
               "c'est souvent la marge qui pèse le plus.")

with onglets[2]:
    st.markdown(f"**Que faut-il supposer pour justifier le cours de {nombre(e0.cours, 2)} "
                f"{donnees['devise']} ?** Chaque ligne fait varier un seul paramètre, "
                "les autres restant à leur valeur retenue.")
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
            col.metric(f"{nom} implicite", pct(implicite, 2),
                       delta=f"{ecart:+.2f} pt vs retenu ({pct(retenu, 2)})".replace(".", ","),
                       delta_color="off")
    phrases = []
    if inverses["Croissance du CA"][0] is not None:
        phrases.append(f"une croissance du CA de {pct(inverses['Croissance du CA'][0], 1)} par an")
    if inverses["Marge EBIT"][0] is not None:
        phrases.append(f"une marge EBIT de {pct(inverses['Marge EBIT'][0], 1)}")
    if inverses["WACC"][0] is not None:
        phrases.append(f"un WACC de {pct(inverses['WACC'][0], 1)}")
    if phrases:
        st.info("Le cours actuel suppose " + ", ou ".join(phrases) + ". "
                "À toi de juger si c'est réaliste au regard de l'historique et de la guidance.")
    st.caption("Méthode : recherche par dichotomie de la valeur du paramètre pour laquelle la valeur "
               "par action égale le cours.")

with onglets[3]:
    st.markdown("**Écarts par rapport au scénario central** (en points, modifiables). "
                "Le scénario central reprend les curseurs ci-dessus.")
    ecarts_defaut = pd.DataFrame(
        {"Pessimiste": [-1.25, -1.0, -0.5, 0.0], "Optimiste": [2.25, 1.0, 0.0,
                                                               round(beta_blume(beta) - beta, 2)]},
        index=["Croissance du CA (pt)", "Marge EBIT (pt)", "g (pt)", "Bêta"])
    ecarts = st.data_editor(ecarts_defaut, key=f"scen_{cle}", width="stretch")
    p1, p2, p3 = st.columns(3)
    poids = {"Pessimiste": p1.number_input("Poids pessimiste (%)", 0, 100, 25, 5, key=f"pp_{cle}"),
             "Central": p2.number_input("Poids central (%)", 0, 100, 50, 5, key=f"pc_{cle}"),
             "Optimiste": p3.number_input("Poids optimiste (%)", 0, 100, 25, 5, key=f"po_{cle}")}

    lignes = []
    for nom in ("Pessimiste", "Central", "Optimiste"):
        e = ecarts[nom] if nom != "Central" else pd.Series(0.0, index=ecarts.index)
        h_s = Hypotheses(**{**hyp.__dict__,
                            "croissance_ca": croissance + e["Croissance du CA (pt)"] / 100,
                            "marge_ebit": marge + e["Marge EBIT (pt)"] / 100,
                            "g": g + e["g (pt)"] / 100})
        b_s = beta + e["Bêta"]
        w_s = wacc_pour(b_s)
        try:
            v = valoriser(h_s, ent, w_s, **options)["valeur_action"]
        except ValueError:
            v = float("nan")
        lignes.append({"Scénario": nom, "Croissance": pct(h_s.croissance_ca, 2),
                       "Marge EBIT": pct(h_s.marge_ebit, 2), "g": pct(h_s.g, 2),
                       "Bêta": f"{b_s:.2f}".replace(".", ","), "WACC": pct(w_s, 2),
                       "Valeur": v, "Potentiel": v / e0.cours - 1, "Poids": poids[nom] / 100})
    scen = pd.DataFrame(lignes)
    total_poids = scen["Poids"].sum()
    st.dataframe(scen.style.format({"Valeur": lambda x: nombre(x, 2), "Potentiel": lambda x: pct(x),
                                    "Poids": lambda x: pct(x, 0)}),
                 hide_index=True, width="stretch")
    if abs(total_poids - 1) > 1e-9:
        st.warning(f"Le total des poids fait {pct(total_poids, 0)} au lieu de 100 %.")
    else:
        v_pond = float((scen["Valeur"] * scen["Poids"]).sum())
        c1, c2 = st.columns(2)
        c1.metric("Valeur pondérée", f"{nombre(v_pond, 2)} {donnees['devise']}")
        c2.metric("Potentiel pondéré", pct(v_pond / e0.cours - 1))
        barres = alt.Chart(scen).mark_bar(size=60).encode(
            x=alt.X("Scénario:N", sort=["Pessimiste", "Central", "Optimiste"], title=None,
                    axis=alt.Axis(labelAngle=0)),
            y=alt.Y("Valeur:Q", title=f"Valeur par action ({donnees['devise']})"),
            color=alt.Color("Scénario:N", legend=None,
                            scale=alt.Scale(domain=["Pessimiste", "Central", "Optimiste"],
                                            range=["#C0504D", "#1F3864", "#4F9A57"])))
        texte = barres.mark_text(dy=-8, fontSize=12).encode(
            text=alt.Text("Valeur:Q", format=",.2f"), color=alt.value("#333"))
        regle = alt.Chart(pd.DataFrame({"Cours": [e0.cours]})).mark_rule(
            strokeDash=[6, 4], color="#7F7F7F").encode(y="Cours:Q")
        st.altair_chart((barres + texte + regle).properties(height=300), width="stretch")
        st.caption("Ligne pointillée : cours actuel.")

with onglets[4]:
    ebitda_1 = proj["EBIT"].iloc[0] + proj["D&A"].iloc[0]
    ebitda_n = proj["EBIT"].iloc[-1] + proj["D&A"].iloc[-1]
    controles = [
        ("g < WACC", pct(w - g, 2), "> 0", w > g),
        ("Part de la VT dans la VE", pct(res["part_vt"]), "60 % à 80 %", 0.6 <= res["part_vt"] <= 0.8),
        ("VE / EBITDA année 1", f"{res['ve_cloture'] / ebitda_1:.1f}x", "5x à 15x", 5 <= res["ve_cloture"] / ebitda_1 <= 15),
        ("Multiple de sortie VT / EBITDA final", f"{res['vt'] / ebitda_n:.1f}x", "5x à 15x",
         5 <= res["vt"] / ebitda_n <= 15),
        ("Capex ≥ D&A", pct(capex - da, 2), "≥ 0", capex >= da),
        ("ROIC implicite de la VT", "infini" if res["roic_implicite"] == float("inf")
         else "n.d." if pd.isna(res["roic_implicite"]) else pct(res["roic_implicite"]),
         "≤ 25 %", bool(res["roic_implicite"] <= 0.25)),
        ("Fonds propres positifs", nombre(res["fonds_propres"]), "> 0", res["fonds_propres"] > 0),
    ]
    df_ctl = pd.DataFrame(controles, columns=["Contrôle", "Valeur", "Règle", "OK"])
    df_ctl["Statut"] = df_ctl["OK"].map({True: "✅ OK", False: "⚠ À vérifier"})
    st.dataframe(df_ctl.drop(columns="OK"), hide_index=True, width="stretch")
    nb_ok = int(df_ctl["OK"].sum())
    (st.success if nb_ok == len(df_ctl) else st.warning)(f"{nb_ok} / {len(df_ctl)} contrôles OK")

with onglets[5]:
    if donnees["historique"] is None:
        st.info("Exemple construit à partir du rapport annuel : voir le fichier Excel du projet.")
    else:
        hist, rat = donnees["historique"].copy(), donnees["ratios"].copy()
        hist.attrs, rat.attrs = {}, {}                   # métadonnées internes, non affichables
        hist.columns = [str(c) for c in hist.columns]   # années + « Moyenne » : tout en texte
        rat.columns = [str(c) for c in rat.columns]
        st.markdown("**Données historiques (Yahoo Finance)**")
        st.dataframe(hist.style.format(lambda x: nombre(x)), width="stretch")
        st.markdown("**Ratios**")
        st.dataframe(rat.style.format(lambda x: pct(x) if pd.notna(x) else "–"),
                     width="stretch")
        st.caption("Les définitions de Yahoo (EBIT, D&A, BFR) peuvent différer du rapport annuel.")

st.divider()
st.caption("Projet personnel à visée pédagogique — ne constitue pas un conseil en investissement.")
