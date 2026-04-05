"""
Eurostat Explorer
=================
Dashboard open-source per ricerca, download e analisi statistica di dati Eurostat.
Nessuna API key richiesta — i dati Eurostat sono pubblici e gratuiti.

Avvio locale  : python eurostat.py
Produzione    : gunicorn eurostat:server --workers 2 --timeout 120
Deploy online : Render / Railway / Hugging Face Spaces / Fly.io
"""

import io, json, math, warnings, urllib.request, urllib.parse
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from dash import Dash, html, dcc, Output, Input, State, ALL, no_update, callback_context
from dash.exceptions import PreventUpdate
import statsmodels.api as sm
from statsmodels.tsa.stattools import adfuller, acf as _acf_fn, pacf as _pacf_fn
from statsmodels.tsa.statespace.sarimax import SARIMAX
from scipy import stats as sp_stats

warnings.filterwarnings("ignore")

# ═══════════════════════════════════════════════════════════════════════════════
# COSTANTI
# ═══════════════════════════════════════════════════════════════════════════════

EUROSTAT_BASE = "https://ec.europa.eu/eurostat/api/dissemination"

GEO_OPTIONS = [
    {"label": "Area Euro (EA20)",  "value": "EA20"},
    {"label": "EU27 (2020)",       "value": "EU27_2020"},
    {"label": "Germania",          "value": "DE"},
    {"label": "Francia",           "value": "FR"},
    {"label": "Italia",            "value": "IT"},
    {"label": "Spagna",            "value": "ES"},
    {"label": "Paesi Bassi",       "value": "NL"},
    {"label": "Belgio",            "value": "BE"},
    {"label": "Austria",           "value": "AT"},
    {"label": "Portogallo",        "value": "PT"},
    {"label": "Finlandia",         "value": "FI"},
    {"label": "Grecia",            "value": "GR"},
    {"label": "Irlanda",           "value": "IE"},
    {"label": "Slovacchia",        "value": "SK"},
    {"label": "Slovenia",          "value": "SI"},
    {"label": "Lettonia",          "value": "LV"},
    {"label": "Lituania",          "value": "LT"},
    {"label": "Estonia",           "value": "EE"},
    {"label": "Lussemburgo",       "value": "LU"},
    {"label": "Malta",             "value": "MT"},
    {"label": "Cipro",             "value": "CY"},
    {"label": "Polonia",           "value": "PL"},
    {"label": "Svezia",            "value": "SE"},
    {"label": "Danimarca",         "value": "DK"},
    {"label": "Repubblica Ceca",   "value": "CZ"},
    {"label": "Ungheria",          "value": "HU"},
    {"label": "Romania",           "value": "RO"},
    {"label": "Bulgaria",          "value": "BG"},
    {"label": "Croazia",           "value": "HR"},
    {"label": "USA",               "value": "US"},
    {"label": "Regno Unito",       "value": "UK"},
    {"label": "Svizzera",          "value": "CH"},
    {"label": "Norvegia",          "value": "NO"},
    {"label": "Giappone",          "value": "JP"},
]

COLORS = [
    "#1f77b4","#d62728","#2ca02c","#ff7f0e","#9467bd",
    "#8c564b","#e377c2","#17becf","#bcbd22","#7f7f7f",
    "#aec7e8","#ffbb78","#98df8a","#ff9896","#c5b0d5",
]

TR_OPTIONS = [
    {"label": "Livelli (originale)",     "value": "levels"},
    {"label": "Δ% Anno su Anno (YoY)",   "value": "yoy"},
    {"label": "Logaritmo naturale",      "value": "log"},
    {"label": "Δ Logaritmo (mom)",       "value": "dlog"},
    {"label": "Prima differenza (Δ)",    "value": "diff"},
    {"label": "Differenza stagionale 12","value": "sdiff"},
]

LAG_OPTIONS = [{"label": f"L{k}", "value": k} for k in range(13)]

# ═══════════════════════════════════════════════════════════════════════════════
# FUNZIONI API EUROSTAT
# ═══════════════════════════════════════════════════════════════════════════════

def _fetch_json(url: str, timeout: int = 35) -> dict | None:
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "EurostatExplorer/2.0 (public dashboard)"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"  HTTP error [{url[:90]}]: {e}")
        return None


def search_datasets(query: str) -> list[dict]:
    """
    Cerca dataset nel catalogo Eurostat per parola chiave.
    Prova prima l'API di ricerca, poi fa fallback sul TOC completo.
    Ritorna lista di dict {code, title, lastUpdate, type}.
    """
    q = urllib.parse.quote(query.strip())

    # ── Tentativo 1: API di ricerca diretta ──────────────────────────────────
    url = f"{EUROSTAT_BASE}/catalogue/datasets?lang=en&query={q}&limit=60"
    data = _fetch_json(url, timeout=20)
    if data is not None:
        items = data if isinstance(data, list) else data.get("dataset",
                data.get("results", data.get("datasets", [])))
        results = []
        for item in items:
            code  = item.get("code", item.get("id", ""))
            title = item.get("title", item.get("label", code))
            if isinstance(title, dict):
                title = title.get("en", next(iter(title.values()), code))
            lu = item.get("lastUpdate", item.get("dataEnd", ""))
            if code:
                results.append({"code": code, "title": str(title), "lastUpdate": lu})
        if results:
            return results

    # ── Tentativo 2: TOC completo (filtra in Python) ──────────────────────────
    toc_url = f"{EUROSTAT_BASE}/catalogue/toc/json?lang=en"
    toc = _fetch_json(toc_url, timeout=40)
    if toc is None:
        return []

    results = []
    kws = query.lower().split()

    def _walk(node):
        if isinstance(node, list):
            for item in node:
                _walk(item)
        elif isinstance(node, dict):
            code  = node.get("code", "")
            title = node.get("title", node.get("label", ""))
            if isinstance(title, dict):
                title = title.get("en", "")
            ntype = node.get("type", "")
            if code and ntype in ("dataset", "table") and all(k in (code + " " + title).lower() for k in kws):
                lu = node.get("lastUpdate", node.get("dataEnd", ""))
                results.append({"code": code, "title": str(title), "lastUpdate": lu})
            for child in node.get("children", []):
                _walk(child)

    _walk(toc)
    return results[:60]


def get_dataset_metadata(code: str) -> dict | None:
    """
    Recupera le dimensioni e le categorie di un dataset Eurostat.
    Ritorna: {dim_id: {label: str, categories: {cat_code: cat_label}}}
    Fa due tentativi con geo diversi per massimizzare le probabilità di risposta.
    """
    for geo_hint in ["DE", "EU27_2020", "EA20", "FR"]:
        url = f"{EUROSTAT_BASE}/statistics/1.0/data/{code}?geo={geo_hint}&lang=en"
        raw = _fetch_json(url, timeout=30)
        if raw and "dimension" in raw:
            dims_raw = raw["dimension"]
            ids      = raw.get("id", list(dims_raw.keys()))
            result   = {}
            for dim_id in ids:
                dim_info = dims_raw.get(dim_id, {})
                label    = dim_info.get("label", dim_id)
                cats_raw = dim_info.get("category", {})
                index    = cats_raw.get("index", {})
                labels   = cats_raw.get("label", {})
                if isinstance(index, dict):
                    ordered = sorted(index.items(), key=lambda x: x[1])
                    cats = {k: labels.get(k, k) for k, _ in ordered}
                elif isinstance(index, list):
                    cats = {k: labels.get(k, k) for k in index}
                else:
                    cats = {}
                result[dim_id] = {"label": label, "categories": cats}
            if result:
                return result
    return None


def download_series(code: str, dim_filters: dict, geo: str) -> pd.Series | None:
    """
    Scarica una serie temporale dal dataset Eurostat.
    dim_filters: {dim_code: category_code} per ogni dimensione non-geo non-time.
    """
    all_params = {**dim_filters, "geo": geo}
    qstr = "&".join(f"{k}={v}" for k, v in all_params.items())
    url  = f"{EUROSTAT_BASE}/statistics/1.0/data/{code}?{qstr}&lang=en"
    raw  = _fetch_json(url, timeout=35)
    if raw is None:
        return None
    try:
        ids    = raw["id"]
        sizes  = raw["size"]
        dims   = raw["dimension"]
        values = raw["value"]
        if "time" not in ids:
            return None
        t_idx     = ids.index("time")
        time_cats = list(dims["time"]["category"]["index"].keys())
        stride    = 1
        for s in sizes[t_idx + 1:]:
            stride *= s
        result = {}
        for i, tcat in enumerate(time_cats):
            v = values.get(str(i * stride))
            if v is not None:
                result[tcat] = float(v)
        if not result:
            return None
        s = pd.Series(result).sort_index()
        # ── Parse formati data Eurostat ──────────────────────────────────────
        sample = str(s.index[0])
        if "-Q" in sample:
            s.index = pd.PeriodIndex(s.index, freq="Q").to_timestamp()
            full = pd.date_range(s.index.min(), s.index.max(), freq="MS")
            s = s.reindex(full).ffill()
        elif len(sample) == 7 and "M" in sample[4:5]:
            s.index = pd.to_datetime(s.index, format="%YM%m")
        elif len(sample) == 4 and sample.isdigit():
            s.index = pd.to_datetime(s.index, format="%Y")
            full = pd.date_range(s.index.min(), s.index.max(), freq="MS")
            s = s.reindex(full).ffill()
        else:
            s.index = pd.to_datetime(s.index, errors="coerce")
            s = s.dropna()
        return s.sort_index().dropna()
    except Exception as e:
        print(f"  Parse [{code}]: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# TRASFORMAZIONI
# ═══════════════════════════════════════════════════════════════════════════════

def apply_transform(s: pd.Series, tr: str) -> pd.Series:
    s = s.dropna()
    if tr == "yoy":
        return s.pct_change(12).mul(100).dropna()
    elif tr == "log":
        s = s[s > 0]
        return np.log(s).dropna()
    elif tr == "dlog":
        s = s[s > 0]
        return np.log(s).diff().dropna()
    elif tr == "diff":
        return s.diff().dropna()
    elif tr == "sdiff":
        return s.diff(12).dropna()
    return s


def tr_label(name: str, tr: str) -> str:
    m = {"yoy": "YoY%", "log": "log", "dlog": "Δlog", "diff": "Δ", "sdiff": "Δ12"}
    sfx = m.get(tr, "")
    return f"{sfx}({name})" if sfx else name


def _pstar(p: float) -> str:
    if p < 0.001: return "***"
    if p < 0.01:  return "**"
    if p < 0.05:  return "*"
    if p < 0.10:  return "·"
    return ""


def empty_fig(msg: str = "") -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        paper_bgcolor="white", plot_bgcolor="#f8f9fa",
        xaxis={"visible": False}, yaxis={"visible": False},
        annotations=[{"text": msg, "xref": "paper", "yref": "paper",
                       "x": 0.5, "y": 0.5, "showarrow": False,
                       "font": {"size": 13, "color": "#888"}}],
        margin={"t": 30, "b": 20, "l": 20, "r": 20},
    )
    return fig


def _make_table(rows: list[list], header_color: str = "#1565c0") -> html.Table:
    """Costruisce una html.Table da lista di righe (prima riga = intestazione)."""
    if not rows:
        return html.Div()
    head = rows[0]
    body = rows[1:]
    th_style = {"background": header_color, "color": "white", "padding": "5px 8px",
                 "font-size": "10px", "font-weight": "bold", "white-space": "nowrap",
                 "border": "1px solid #c8d8e4"}
    td_style = {"padding": "4px 8px", "font-size": "10px", "border": "1px solid #dee2e6",
                 "white-space": "nowrap"}
    alt_style = {**td_style, "background": "#f8f9fa"}
    return html.Table([
        html.Thead(html.Tr([html.Th(h, style=th_style) for h in head])),
        html.Tbody([
            html.Tr([html.Td(str(c), style=(td_style if i % 2 == 0 else alt_style))
                     for c in row])
            for i, row in enumerate(body)
        ])
    ], style={"border-collapse": "collapse", "width": "100%",
               "font-family": "monospace", "margin-top": "8px"})


# ═══════════════════════════════════════════════════════════════════════════════
# DASH APP — LAYOUT
# ═══════════════════════════════════════════════════════════════════════════════

app = Dash(
    __name__,
    suppress_callback_exceptions=True,
    title="Eurostat Explorer",
    meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}],
)
server = app.server  # expose Flask for gunicorn

_CARD = {"background": "#fff", "border": "1px solid #dee2e6", "border-radius": "6px",
         "padding": "14px 16px", "margin-bottom": "12px",
         "box-shadow": "0 1px 3px rgba(0,0,0,.06)"}
_HDR  = {"font-size": "11px", "font-weight": "700", "color": "#1a3a5c",
         "background": "#eaf4fb", "padding": "5px 10px", "border-radius": "3px",
         "margin-bottom": "10px", "letter-spacing": ".4px"}
_BTN  = {"background": "#1565c0", "color": "white", "border": "none",
         "padding": "8px 18px", "border-radius": "4px", "cursor": "pointer",
         "font-size": "12px", "font-weight": "bold", "width": "100%"}
_BTN_G = {**_BTN, "background": "#2e7d32"}
_LBL  = {"font-size": "10px", "color": "#333", "display": "block", "margin-bottom": "4px"}
_STS  = {"font-size": "10px", "color": "#555", "margin-top": "5px", "font-style": "italic"}


def _tab_search():
    """Tab ① — Ricerca & Selezione dataset."""
    return html.Div([
        html.Div("①  RICERCA DATASET EUROSTAT", style=_HDR),
        html.P("Digita parole chiave in italiano o inglese (es. 'GDP', 'inflation', 'prezzi', "
               "'unemployment', 'trade'). I dati Eurostat sono gratuiti e non richiedono registrazione.",
               style={"font-size": "11px", "color": "#555", "margin-bottom": "12px"}),

        # ── Barra di ricerca ──────────────────────────────────────────────────
        html.Div([
            dcc.Input(id="search-query", type="text",
                      placeholder="es. GDP, inflation, HICP, unemployment...",
                      debounce=False,
                      style={"flex": "1", "font-size": "12px", "padding": "8px 12px",
                             "border": "1px solid #ced4da", "border-radius": "4px 0 0 4px",
                             "outline": "none"}),
            html.Button("🔍  Cerca", id="btn-search", n_clicks=0,
                        style={**_BTN, "width": "auto", "border-radius": "0 4px 4px 0",
                               "padding": "8px 20px"}),
        ], style={"display": "flex", "margin-bottom": "8px"}),
        html.Div(id="search-status", style=_STS),

        # ── Risultati ricerca ─────────────────────────────────────────────────
        html.Div(id="search-results-container",
                 style={"max-height": "340px", "overflow-y": "auto",
                        "border": "1px solid #dee2e6", "border-radius": "4px",
                        "margin-bottom": "16px", "background": "#fafafa"}),

        html.Hr(style={"margin": "16px 0"}),

        # ── Dataset selezionato ───────────────────────────────────────────────
        html.Div("②  DATASET SELEZIONATO", style=_HDR),
        html.Div(id="selected-dataset-info",
                 children="Clicca su un risultato per selezionare il dataset.",
                 style={"font-size": "11px", "color": "#777",
                        "font-style": "italic", "margin-bottom": "12px"}),

        html.Div([
            html.Button("🔄  Carica dimensioni", id="btn-load-dims", n_clicks=0,
                        style={**_BTN, "width": "auto", "padding": "7px 18px"}),
            html.Div(id="dims-status", style={**_STS, "margin-left": "12px",
                                              "display": "inline-block"}),
        ], style={"display": "flex", "align-items": "center", "margin-bottom": "12px"}),

        # ── Pannello dimensioni ───────────────────────────────────────────────
        html.Div(id="dims-panel",
                 style={"background": "#f0f4fa", "border": "1px solid #aed6f1",
                        "border-radius": "6px", "padding": "12px",
                        "margin-bottom": "14px"}),

        # ── Selezione geo + download ──────────────────────────────────────────
        html.Div([
            html.Div([
                html.Label("Paese / Area geografica:", style=_LBL),
                dcc.Dropdown(id="dl-geo", options=GEO_OPTIONS, value="EA20",
                             clearable=False,
                             style={"font-size": "11px", "margin-bottom": "8px"}),
            ], style={"flex": "1", "margin-right": "12px"}),
            html.Div([
                html.Label("Etichetta personalizzata (opzionale):", style=_LBL),
                dcc.Input(id="dl-label", type="text", placeholder="es. PIL Reale Italia",
                          style={"width": "100%", "font-size": "11px", "padding": "5px 8px",
                                 "border": "1px solid #ced4da", "border-radius": "3px",
                                 "margin-bottom": "8px"}),
            ], style={"flex": "1"}),
        ], style={"display": "flex"}),

        html.Button("⬇  Scarica e aggiungi ai dati", id="btn-download", n_clicks=0,
                    style=_BTN_G),
        html.Div(id="download-status", style=_STS),
    ], style={"padding": "16px", "max-width": "900px", "margin": "0 auto"})


def _tab_data():
    """Tab ② — Dati scaricati e grafici."""
    return html.Div([
        html.Div([
            # ── Sidebar serie ─────────────────────────────────────────────────
            html.Div([
                html.Div("Serie disponibili", style={**_HDR, "margin-bottom": "6px"}),
                html.Div(id="data-series-list",
                         children="Nessuna serie. Scarica dati dal tab ① Ricerca.",
                         style={"font-size": "10px", "color": "#888",
                                "font-style": "italic"}),
                html.Hr(style={"margin": "10px 0"}),
                html.Div([
                    html.Button("✔ Tutte", id="data-sel-all", n_clicks=0,
                                style={"font-size": "9px", "padding": "2px 8px",
                                       "margin-right": "4px", "cursor": "pointer",
                                       "border": "1px solid #ccc", "border-radius": "3px"}),
                    html.Button("✘ Nessuna", id="data-sel-none", n_clicks=0,
                                style={"font-size": "9px", "padding": "2px 8px",
                                       "cursor": "pointer", "border": "1px solid #ccc",
                                       "border-radius": "3px"}),
                ], style={"display": "flex", "margin-bottom": "10px"}),
                html.Hr(style={"margin": "8px 0"}),
                html.Label("Trasformazione:", style=_LBL),
                dcc.RadioItems(id="data-transform", options=TR_OPTIONS, value="levels",
                               style={"font-size": "10px"},
                               inputStyle={"margin-right": "3px"},
                               labelStyle={"display": "block", "margin-bottom": "3px"}),
                html.Hr(style={"margin": "8px 0"}),
                html.Button("🗑  Svuota tutto", id="btn-clear-data", n_clicks=0,
                            style={**_BTN, "background": "#c62828",
                                   "font-size": "10px", "padding": "5px 10px"}),
            ], style={"width": "210px", "min-width": "200px", "padding": "12px",
                      "border-right": "1px solid #ddd", "background": "#fafafa",
                      "overflow-y": "auto", "height": "calc(100vh - 120px)"}),

            # ── Area grafici ──────────────────────────────────────────────────
            html.Div([
                # Slider range
                html.Div([
                    dcc.RangeSlider(id="data-slider", min=0, max=1, value=[0, 1],
                                    marks={}, step=86400*30,
                                    tooltip={"placement": "bottom", "always_visible": False}),
                    html.Div(id="data-slider-label",
                             style={"font-size": "9px", "color": "#666",
                                    "text-align": "center", "margin-top": "2px"}),
                ], style={"padding": "8px 28px 4px"}),

                dcc.Loading(type="circle", children=[
                    dcc.Graph(id="chart-data-main",
                              figure=empty_fig("Scarica dati dal tab ① Ricerca"),
                              style={"height": "45vh"},
                              config={"responsive": True, "scrollZoom": True,
                                      "toImageButtonOptions": {"format": "png", "scale": 2}}),
                ]),
                html.Div([
                    html.Button("📥  Esporta CSV", id="btn-export-csv", n_clicks=0,
                                style={**_BTN, "width": "auto", "padding": "6px 14px",
                                       "font-size": "11px", "margin": "8px 16px 0"}),
                    dcc.Download(id="download-csv"),
                ]),
                html.Div(id="data-table-container",
                         style={"margin": "8px 16px 0", "overflow-x": "auto",
                                "max-height": "35vh", "overflow-y": "auto"}),
            ], style={"flex": "1", "min-width": "0", "overflow-y": "auto",
                      "height": "calc(100vh - 120px)"}),
        ], style={"display": "flex"}),
    ])


def _tab_arima():
    """Tab ③ — ARIMA / SARIMA — workflow Box-Jenkins."""
    CARD = _CARD

    def _num(label, id_, val, mn, mx):
        return html.Div([
            html.Label(label, style={**_LBL, "width": "130px", "margin-bottom": 0,
                                     "flex-shrink": "0"}),
            dcc.Input(id=id_, type="number", value=val, min=mn, max=mx, step=1,
                      style={"width": "55px", "font-size": "11px", "padding": "2px 4px",
                             "border": "1px solid #ced4da", "border-radius": "3px"}),
        ], style={"display": "flex", "align-items": "center", "gap": "8px", "margin-bottom": "5px"})

    sidebar = html.Div([
        html.Div("Serie", style={**_LBL, "font-weight": "700", "color": "#b71c1c",
                                  "background": "#ffebee", "padding": "4px 8px",
                                  "border-radius": "3px", "margin-bottom": "6px"}),
        dcc.Dropdown(id="arima-series", options=[], placeholder="Seleziona serie...",
                     style={"font-size": "10px", "margin-bottom": "10px"}),
        html.Label("Trasformazione:", style=_LBL),
        dcc.RadioItems(id="arima-transform", options=TR_OPTIONS, value="log",
                       style={"font-size": "10px"},
                       inputStyle={"margin-right": "3px"},
                       labelStyle={"display": "block", "margin-bottom": "3px"}),
        html.Hr(style={"margin": "8px 0"}),
        html.Label("Detrend:", style=_LBL),
        dcc.RadioItems(id="arima-detrend",
                       options=[{"label": " Nessuno",   "value": "none"},
                                {"label": " MA 12",     "value": "ma12"},
                                {"label": " HP filter", "value": "hp"},
                                {"label": " Diff sag.", "value": "sdiff"}],
                       value="none", style={"font-size": "10px"},
                       inputStyle={"margin-right": "3px"},
                       labelStyle={"display": "block", "margin-bottom": "3px"}),
        html.Hr(style={"margin": "8px 0"}),
        html.Button("①  Analizza serie", id="btn-arima-step1", n_clicks=0,
                    style={**_BTN, "margin-bottom": "6px"}),
        html.Button("②  ACF / PACF / ADF", id="btn-arima-step2", n_clicks=0,
                    style={**_BTN, "background": "#2e7d32", "margin-bottom": "6px"}),
        html.Hr(style={"margin": "8px 0"}),
        html.Div("Ordini ARIMA", style={**_LBL, "font-weight": "700",
                                         "color": "#6a1b9a"}),
        _num("p (AR):", "ar-p", 1, 0, 10),
        _num("d (diff):", "ar-d", 0, 0, 3),
        _num("q (MA):", "ar-q", 0, 0, 10),
        html.Hr(style={"margin": "6px 0"}),
        dcc.Checklist(id="arima-seasonal", options=[{"label": " Stagionale (SARIMA)", "value": "on"}],
                      value=[], style={"font-size": "10px"}, inputStyle={"margin-right": "3px"}),
        html.Div(id="arima-seasonal-box", children=[
            _num("P (AR stag.):", "ar-P", 1, 0, 5),
            _num("D (diff stag.):", "ar-D", 1, 0, 2),
            _num("Q (MA stag.):", "ar-Q", 1, 0, 5),
            _num("s (stagione):", "ar-s", 12, 2, 52),
        ], style={"display": "none", "margin-top": "4px"}),
        html.Hr(style={"margin": "8px 0"}),
        _num("Forecast (mesi):", "arima-fc-steps", 24, 1, 120),
        html.Button("③  Stima & Prevedi", id="btn-arima-fit", n_clicks=0,
                    style={**_BTN, "background": "#6a1b9a", "margin-top": "6px"}),
        html.Div(id="arima-status", style=_STS),
    ], style={"width": "230px", "min-width": "220px", "padding": "12px",
              "border-right": "1px solid #ddd", "background": "#fafafa",
              "overflow-y": "auto", "height": "calc(100vh - 120px)"})

    main = html.Div([
        dcc.Loading(type="circle", children=[
            html.Div(id="arima-step1-out"),
            html.Div(id="arima-step2-out"),
            html.Div(id="arima-fit-out"),
        ]),
    ], style={"flex": "1", "min-width": "0", "overflow-y": "auto",
              "height": "calc(100vh - 120px)", "padding": "12px"})

    return html.Div([sidebar, main], style={"display": "flex"})


def _tab_adl():
    """Tab ④ — Regressione ADL + IRF."""
    sidebar = html.Div([
        html.Div("① Y — Variabile dipendente", style={**_LBL, "font-weight": "700",
                  "color": "#b71c1c", "background": "#ffebee",
                  "padding": "4px 8px", "border-radius": "3px", "margin-bottom": "6px"}),
        dcc.Dropdown(id="adl-y", options=[], placeholder="Seleziona Y...",
                     style={"font-size": "10px", "margin-bottom": "6px"}),
        dcc.RadioItems(id="adl-y-tr", options=TR_OPTIONS, value="yoy",
                       style={"font-size": "10px"},
                       inputStyle={"margin-right": "3px"},
                       labelStyle={"display": "block", "margin-bottom": "2px"}),
        html.Hr(style={"margin": "8px 0"}),
        html.Div("② Lag AR di Y:", style={**_LBL, "font-weight": "700", "color": "#1a3a5c"}),
        dcc.Dropdown(id="adl-ar", options=LAG_OPTIONS, value=[], multi=True,
                     placeholder="nessuno",
                     style={"font-size": "10px", "margin-bottom": "8px"}),
        html.Hr(style={"margin": "8px 0"}),
        html.Div("③ Variabili X (scegli trasformazione e lag):",
                 style={**_LBL, "font-weight": "700", "color": "#1a3a5c"}),
        html.Div(id="adl-x-panel",
                 style={"max-height": "260px", "overflow-y": "auto",
                        "border": "1px solid #dee2e6", "border-radius": "4px",
                        "padding": "6px", "background": "#f8f9fa",
                        "margin-bottom": "8px"}),
        html.Hr(style={"margin": "8px 0"}),
        html.Label("Errori standard:", style=_LBL),
        dcc.RadioItems(id="adl-cov",
                       options=[{"label": " OLS standard", "value": "nonrobust"},
                                {"label": " HC3 (robusto)",  "value": "HC3"},
                                {"label": " HAC Newey-West", "value": "HAC"}],
                       value="HC3", style={"font-size": "10px"},
                       inputStyle={"margin-right": "3px"},
                       labelStyle={"display": "block", "margin-bottom": "2px"}),
        dcc.Checklist(id="adl-const",
                      options=[{"label": " Includi costante", "value": "const"}],
                      value=["const"], style={"font-size": "10px", "margin-top": "5px"},
                      inputStyle={"margin-right": "3px"}),
        html.Hr(style={"margin": "8px 0"}),
        html.Button("▶  Stima modello", id="btn-adl-run", n_clicks=0, style=_BTN_G),
        html.Div(id="adl-status", style=_STS),
    ], style={"width": "240px", "min-width": "230px", "padding": "12px",
              "border-right": "1px solid #ddd", "background": "#fafafa",
              "overflow-y": "auto", "height": "calc(100vh - 120px)"})

    main = html.Div([
        dcc.Loading(type="circle", children=[
            html.Div(id="adl-equation",
                     style={"font-family": "monospace", "font-size": "11px",
                            "background": "#f8f9fa", "border": "1px solid #dee2e6",
                            "border-radius": "4px", "padding": "10px 14px",
                            "margin-bottom": "8px", "white-space": "pre-wrap",
                            "color": "#1a3a5c"}),
            html.Div(id="adl-stats-out", style={"margin-bottom": "8px"}),
            html.Div(id="adl-coef-out",  style={"margin-bottom": "8px"}),
            dcc.Graph(id="chart-adl-fit",
                      figure=empty_fig("Stima il modello"),
                      style={"height": "35vh"},
                      config={"responsive": True, "scrollZoom": True}),
            dcc.Graph(id="chart-adl-irf",
                      figure=empty_fig("IRF — 12 mesi"),
                      style={"height": "42vh"},
                      config={"responsive": True}),
            dcc.Graph(id="chart-adl-resid",
                      figure=empty_fig("Residui"),
                      style={"height": "28vh"},
                      config={"responsive": True}),
        ]),
    ], style={"flex": "1", "min-width": "0", "overflow-y": "auto",
              "height": "calc(100vh - 120px)", "padding": "8px"})

    return html.Div([sidebar, main], style={"display": "flex"})


def _tab_info():
    """Tab ⑤ — Guida all'uso."""
    def _section(title, items):
        return html.Div([
            html.H4(title, style={"color": "#1565c0", "margin-bottom": "6px"}),
            html.Ul([html.Li(i, style={"margin-bottom": "4px", "font-size": "13px"})
                     for i in items]),
        ], style={**_CARD})

    return html.Div([
        html.H2("Guida a Eurostat Explorer",
                style={"color": "#1a3a5c", "margin-bottom": "16px"}),
        html.P("Dashboard open-source per accedere liberamente ai dati Eurostat, "
               "eseguire analisi statistiche e costruire previsioni econometriche.",
               style={"font-size": "13px", "color": "#555", "margin-bottom": "20px"}),
        _section("① Ricerca & Download", [
            "Cerca dataset con parole chiave (italiano o inglese): 'GDP', 'inflazione', 'prezzi', 'lavoro'...",
            "Clicca su un risultato per selezionarlo → carica le dimensioni del dataset.",
            "Per ogni dimensione (es. unit, freq, na_item) scegli la categoria desiderata.",
            "Seleziona il paese/area geografica e clicca 'Scarica e aggiungi ai dati'.",
            "Puoi aggiungere più serie anche da dataset diversi.",
        ]),
        _section("② Dati & Grafici", [
            "Visualizza tutte le serie scaricate sovraimposte in un grafico interattivo.",
            "Scegli la trasformazione: livelli, YoY%, logaritmo, differenza.",
            "Usa lo slider per selezionare il periodo di analisi.",
            "Esporta i dati in CSV con un click.",
        ]),
        _section("③ ARIMA / SARIMA", [
            "Seleziona una serie e una trasformazione per renderla stazionaria.",
            "Passo ①: visualizza la serie originale, trasformata e detrend.",
            "Passo ②: analizza ACF, PACF, periodogramma e test ADF — suggerisce p, d, q.",
            "Passo ③: stima il modello SARIMA e genera la previsione con intervallo di confidenza 95%.",
            "Modifica manualmente gli ordini p, d, q, P, D, Q, s nella sidebar.",
        ]),
        _section("④ Modello ADL & IRF", [
            "Seleziona la variabile dipendente Y (e la sua trasformazione).",
            "Aggiungi i lag AR di Y (se il processo è autoregressivo).",
            "Per ogni variabile X seleziona la trasformazione e i lag specifici (L0=contemporaneo, L1=ritardo 1 mese...).",
            "Ottieni: equazione stimata, R², diagnostiche (DW, JB, BP), tabella coefficienti con p-value e VIF.",
            "Impulse Response Function (IRF): impatto di +1σ di ogni X su Y nel tempo.",
        ]),
        html.Div([
            html.H4("Fonti & Crediti", style={"color": "#1565c0"}),
            html.P("Dati: © Eurostat — licenza Creative Commons (CC BY 4.0). "
                   "Dashboard: open-source, nessuna chiave API richiesta.",
                   style={"font-size": "12px", "color": "#666"}),
            html.P("Per deployment online: gunicorn eurostat:server --workers 2 --timeout 120",
                   style={"font-family": "monospace", "font-size": "11px",
                          "background": "#f8f9fa", "padding": "6px 10px",
                          "border-radius": "4px", "color": "#333"}),
        ], style=_CARD),
    ], style={"max-width": "860px", "margin": "20px auto", "padding": "0 16px"})


# ── Overlay caricamento ────────────────────────────────────────────────────────
_overlay = html.Div([
    html.Div([
        html.Div("⏳", style={"font-size": "52px", "margin-bottom": "10px"}),
        html.Div(id="eur-loading-title", children="Download in corso...",
                 style={"font-size": "20px", "font-weight": "bold", "color": "white",
                        "margin-bottom": "6px"}),
        html.Div(id="eur-loading-src", children="",
                 style={"font-size": "13px", "color": "#90caf9", "margin-bottom": "18px"}),
        html.Div([
            html.Div(id="eur-progress-bar",
                     style={"width": "0%", "height": "100%",
                            "background": "linear-gradient(90deg,#1a5276,#42a5f5)",
                            "border-radius": "6px", "transition": "width 0.35s ease"}),
        ], style={"width": "380px", "height": "13px", "background": "rgba(255,255,255,.15)",
                  "border-radius": "7px", "overflow": "hidden", "margin-bottom": "10px"}),
        html.Div(id="eur-progress-pct", children="0%",
                 style={"font-size": "26px", "font-weight": "bold", "color": "#90caf9",
                        "margin-bottom": "4px"}),
        html.Div(id="eur-progress-detail", children="Connessione a Eurostat...",
                 style={"font-size": "11px", "color": "#aaa", "font-style": "italic"}),
    ], style={"display": "flex", "flex-direction": "column", "align-items": "center",
              "background": "rgba(10,20,40,0.95)", "border-radius": "16px",
              "padding": "50px 60px", "box-shadow": "0 8px 40px rgba(0,0,0,.6)"}),
], id="eur-loading-overlay",
   style={"display": "none", "position": "fixed", "top": "0", "left": "0",
          "width": "100%", "height": "100%", "background": "rgba(0,0,0,0.75)",
          "z-index": "9999", "align-items": "center", "justify-content": "center"})

dcc.Interval(id="eur-tick", interval=350, disabled=True, n_intervals=0)

# ── Stores ─────────────────────────────────────────────────────────────────────
_stores = [
    dcc.Store(id="store-search-results"),
    dcc.Store(id="store-selected-code"),
    dcc.Store(id="store-dims-meta"),
    dcc.Store(id="store-series", storage_type="session"),
    dcc.Store(id="store-loading-state", data={"active": False}),
    dcc.Store(id="store-arima-series"),
]

# ── Layout principale ──────────────────────────────────────────────────────────
app.layout = html.Div([
    *_stores,
    dcc.Interval(id="eur-tick", interval=350, disabled=True, n_intervals=0),
    _overlay,

    # ── Header ──────────────────────────────────────────────────────────────
    html.Div([
        html.Div([
            html.Span("🇪🇺", style={"font-size": "24px", "margin-right": "10px"}),
            html.Span("Eurostat Explorer",
                      style={"font-size": "18px", "font-weight": "bold",
                             "color": "#1a3a5c", "letter-spacing": "0.5px"}),
            html.Span(" — dati economici europei liberi e gratuiti",
                      style={"font-size": "12px", "color": "#888", "margin-left": "10px"}),
        ], style={"display": "flex", "align-items": "center"}),
        html.A("eurostat.ec.europa.eu", href="https://ec.europa.eu/eurostat",
               target="_blank",
               style={"font-size": "11px", "color": "#1565c0",
                      "text-decoration": "none"}),
    ], style={"display": "flex", "justify-content": "space-between", "align-items": "center",
              "padding": "10px 20px", "background": "#eaf4fb",
              "border-bottom": "2px solid #1565c0"}),

    # ── Tabs ────────────────────────────────────────────────────────────────
    dcc.Tabs(id="main-tabs", value="tab-search", children=[
        dcc.Tab(label="🔍  Ricerca",    value="tab-search",  children=[_tab_search()]),
        dcc.Tab(label="📊  Dati",       value="tab-data",    children=[_tab_data()]),
        dcc.Tab(label="〜  ARIMA",      value="tab-arima",   children=[_tab_arima()]),
        dcc.Tab(label="📐  ADL",        value="tab-adl",     children=[_tab_adl()]),
        dcc.Tab(label="ℹ  Guida",      value="tab-info",    children=[_tab_info()]),
    ], style={"font-size": "12px"}),
])


# ═══════════════════════════════════════════════════════════════════════════════
# CALLBACKS — TAB RICERCA
# ═══════════════════════════════════════════════════════════════════════════════

@app.callback(
    Output("store-search-results", "data"),
    Output("search-status",        "children"),
    Output("search-results-container", "children"),
    Input("btn-search",  "n_clicks"),
    State("search-query","value"),
    prevent_initial_call=True,
)
def do_search(n, query):
    if not query or not query.strip():
        return no_update, "⚠ Inserisci almeno una parola chiave.", no_update
    results = search_datasets(query.strip())
    if not results:
        return [], f"❌ Nessun risultato per '{query}'. Prova termini in inglese.", html.Div(
            f"Nessun risultato. Prova 'GDP', 'unemployment', 'HICP', 'trade'...",
            style={"padding": "16px", "color": "#888", "font-size": "12px"})

    rows = []
    for r in results:
        rows.append(html.Div([
            html.Span(r["code"],
                      style={"font-family": "monospace", "font-size": "11px",
                             "font-weight": "bold", "color": "#1565c0",
                             "min-width": "120px", "display": "inline-block"}),
            html.Span(r["title"][:80] + ("…" if len(r["title"]) > 80 else ""),
                      style={"font-size": "11px", "color": "#333", "flex": "1"}),
            html.Span(r.get("lastUpdate", ""),
                      style={"font-size": "10px", "color": "#aaa",
                             "min-width": "80px", "text-align": "right"}),
            html.Button("Seleziona", id={"type": "btn-select-ds", "index": r["code"]},
                        n_clicks=0,
                        style={"font-size": "9px", "padding": "2px 8px",
                               "background": "#1565c0", "color": "white",
                               "border": "none", "border-radius": "3px",
                               "cursor": "pointer", "margin-left": "8px"}),
        ], style={"display": "flex", "align-items": "center", "padding": "6px 10px",
                  "border-bottom": "1px solid #eee", "gap": "8px",
                  "cursor": "pointer"}))

    return results, f"✅ {len(results)} dataset trovati per '{query}'", rows


@app.callback(
    Output("store-selected-code",   "data"),
    Output("selected-dataset-info", "children"),
    Input({"type": "btn-select-ds", "index": ALL}, "n_clicks"),
    State("store-search-results", "data"),
    prevent_initial_call=True,
)
def select_dataset(clicks, results):
    ctx = callback_context
    if not ctx.triggered or not results:
        raise PreventUpdate
    prop = ctx.triggered[0]["prop_id"]
    try:
        code = json.loads(prop.split(".")[0])["index"]
    except Exception:
        raise PreventUpdate
    hit = next((r for r in results if r["code"] == code), None)
    if not hit:
        raise PreventUpdate
    info = html.Div([
        html.B(f"{hit['code']}  ", style={"color": "#1565c0", "font-size": "13px"}),
        html.Span(hit["title"], style={"font-size": "12px"}),
        html.Br(),
        html.A(f"Vedi su Eurostat →",
               href=f"https://ec.europa.eu/eurostat/web/products-datasets/-/{hit['code']}",
               target="_blank",
               style={"font-size": "10px", "color": "#1565c0"}),
    ])
    return code, info


@app.callback(
    Output("store-dims-meta", "data"),
    Output("dims-status",     "children"),
    Output("dims-panel",      "children"),
    Input("btn-load-dims",    "n_clicks"),
    State("store-selected-code", "data"),
    prevent_initial_call=True,
)
def load_dimensions(n, code):
    if not code:
        return no_update, "⚠ Seleziona prima un dataset.", no_update
    meta = get_dataset_metadata(code)
    if meta is None:
        return no_update, f"❌ Impossibile caricare le dimensioni di {code}.", no_update

    controls = []
    for dim_id, dim_info in meta.items():
        if dim_id in ("geo", "time"):
            continue
        cats = dim_info["categories"]
        options = [{"label": f"{k} — {v[:60]}", "value": k} for k, v in cats.items()]
        default = list(cats.keys())[0] if cats else None
        controls.append(html.Div([
            html.Label(f"{dim_info['label']} ({dim_id})",
                       style={**_LBL, "font-weight": "600", "color": "#1a3a5c"}),
            dcc.Dropdown(
                id={"type": "dim-selector", "index": dim_id},
                options=options, value=default, clearable=False,
                style={"font-size": "10px", "margin-bottom": "8px"},
            ),
        ]))

    if not controls:
        controls = [html.P("Nessuna dimensione da configurare (dataset semplice).",
                           style={"font-size": "11px", "color": "#777"})]

    panel = html.Div([
        html.Div(f"Dataset: {code} — {len(controls)} dimensioni",
                 style={"font-size": "11px", "font-weight": "bold",
                        "color": "#1a3a5c", "margin-bottom": "10px"}),
        *controls,
    ])
    return meta, f"✅ {len(meta)} dimensioni caricate per {code}", panel


# ── Clientside: mostra overlay al click Download ──────────────────────────────
app.clientside_callback(
    """
    function(n) {
        if (!n) return window.dash_clientside.no_update;
        return {"active": true};
    }
    """,
    Output("store-loading-state", "data"),
    Input("btn-download", "n_clicks"),
    prevent_initial_call=True,
)


@app.callback(
    Output("eur-loading-overlay", "style"),
    Output("eur-loading-title",   "children"),
    Output("eur-loading-src",     "children"),
    Output("eur-tick",            "disabled"),
    Output("eur-tick",            "n_intervals"),
    Input("store-loading-state",  "data"),
    prevent_initial_call=True,
)
def toggle_overlay(state):
    _hidden = {"display": "none"}
    if state and state.get("active"):
        return ({"display": "flex", "position": "fixed", "top": "0", "left": "0",
                 "width": "100%", "height": "100%", "background": "rgba(0,0,0,0.75)",
                 "z-index": "9999", "align-items": "center", "justify-content": "center"},
                "Download Eurostat in corso...",
                "Connessione ai server Eurostat...",
                False, 0)
    return _hidden, "", "", True, 0


@app.callback(
    Output("eur-progress-pct",    "children"),
    Output("eur-progress-bar",    "style"),
    Output("eur-progress-detail", "children"),
    Input("eur-tick",             "n_intervals"),
    prevent_initial_call=True,
)
def tick_progress(n):
    pct = min(int(95 * (1 - math.exp(-n * 0.09))), 93)
    detail = ("Connessione..." if pct < 20 else
              "Download serie temporale..." if pct < 50 else
              "Parsing dati..." if pct < 75 else "Quasi pronto...")
    bar = {"width": f"{pct}%", "height": "100%",
           "background": "linear-gradient(90deg,#1a5276,#42a5f5)",
           "border-radius": "6px", "transition": "width 0.35s ease"}
    return f"{pct}%", bar, detail


@app.callback(
    Output("store-series",         "data"),
    Output("download-status",      "children"),
    Output("store-loading-state",  "data", allow_duplicate=True),
    Input("btn-download",          "n_clicks"),
    State("store-selected-code",   "data"),
    State("store-dims-meta",       "data"),
    State({"type": "dim-selector", "index": ALL}, "value"),
    State({"type": "dim-selector", "index": ALL}, "id"),
    State("dl-geo",                "value"),
    State("dl-label",              "value"),
    State("store-series",          "data"),
    prevent_initial_call=True,
)
def do_download(n, code, meta, dim_vals, dim_ids, geo, label, existing):
    _done = {"active": False}
    if not code:
        return no_update, "⚠ Seleziona prima un dataset.", _done
    if not geo:
        return no_update, "⚠ Seleziona un paese.", _done

    # Build dimension filters
    dim_filters = {}
    for val, did in zip(dim_vals, dim_ids):
        key = did["index"]
        if key not in ("geo", "time") and val:
            dim_filters[key] = val

    series = download_series(code, dim_filters, geo)
    if series is None or series.empty:
        return no_update, f"❌ Nessun dato per {code} / {geo} con i filtri selezionati.", _done

    # Build series name
    series_name = label.strip() if label and label.strip() else (
        f"{code}/{geo} — " + ", ".join(f"{k}={v}" for k, v in dim_filters.items()))
    series_name = series_name[:80]

    # Add to store
    store = existing or {}
    store[series_name] = {
        "data": series.to_json(date_format="iso", orient="split"),
        "code": code,
        "geo":  geo,
    }
    n_obs = len(series)
    d1 = series.index.min().strftime("%Y-%m")
    d2 = series.index.max().strftime("%Y-%m")
    msg = f"✅  '{series_name}'  |  {n_obs} obs  |  {d1} → {d2}"
    return store, msg, _done


# ═══════════════════════════════════════════════════════════════════════════════
# CALLBACKS — TAB DATI
# ═══════════════════════════════════════════════════════════════════════════════

def _load_all_series(store: dict) -> dict[str, pd.Series]:
    """Legge lo store e ritorna dict {name: pd.Series}."""
    out = {}
    for name, info in (store or {}).items():
        try:
            s = pd.read_json(io.StringIO(info["data"]), typ="series")
            s.index = pd.to_datetime(s.index)
            s.name = name
            out[name] = s.sort_index().dropna()
        except Exception:
            pass
    return out


def _slider_params(series_dict: dict) -> tuple:
    idx = pd.DatetimeIndex([])
    for s in series_dict.values():
        idx = idx.union(s.index)
    if idx.empty:
        return 0, 1, [0, 1], {}
    mn = int(idx.min().timestamp())
    mx = int(idx.max().timestamp())
    step = 5
    marks = {int(pd.Timestamp(yr, 1, 1).timestamp()): str(yr)
             for yr in range(idx.min().year, idx.max().year + 1, step)}
    return mn, mx, [mn, mx], marks


@app.callback(
    Output("data-series-list", "children"),
    Output("data-slider",      "min"),
    Output("data-slider",      "max"),
    Output("data-slider",      "value"),
    Output("data-slider",      "marks"),
    Input("store-series",      "data"),
    prevent_initial_call=False,
)
def refresh_series_list(store):
    series_dict = _load_all_series(store)
    if not series_dict:
        return ("Nessuna serie. Scarica dati dal tab ① Ricerca.",
                0, 1, [0, 1], {})
    mn, mx, val, marks = _slider_params(series_dict)
    rows = []
    for i, name in enumerate(series_dict):
        color = COLORS[i % len(COLORS)]
        rows.append(html.Div([
            html.Span("■", style={"color": color, "margin-right": "5px", "font-size": "12px"}),
            dcc.Checklist(
                id={"type": "data-check", "index": name},
                options=[{"label": f" {name[:38]}{'…' if len(name)>38 else ''}",
                          "value": name}],
                value=[name],
                style={"font-size": "10px", "display": "inline"},
                inputStyle={"margin-right": "3px"},
            ),
        ], style={"display": "flex", "align-items": "center", "margin-bottom": "5px"}))
    return rows, mn, mx, val, marks


@app.callback(
    Output("data-slider-label", "children"),
    Input("data-slider", "value"),
)
def data_slider_label(val):
    if not val or (val[1] - val[0]) < 86400:
        return ""
    s = pd.to_datetime(val[0], unit="s").strftime("%b %Y")
    e = pd.to_datetime(val[1], unit="s").strftime("%b %Y")
    return f"📅  {s}  →  {e}"


@app.callback(
    Output({"type": "data-check", "index": ALL}, "value"),
    Input("data-sel-all",  "n_clicks"),
    Input("data-sel-none", "n_clicks"),
    State({"type": "data-check", "index": ALL}, "id"),
    prevent_initial_call=True,
)
def data_sel_desel(a, b, ids):
    ctx = callback_context
    if not ctx.triggered:
        raise PreventUpdate
    if "data-sel-none" in ctx.triggered[0]["prop_id"]:
        return [[] for _ in ids]
    return [[i["index"]] for i in ids]


@app.callback(
    Output("chart-data-main",     "figure"),
    Output("data-table-container","children"),
    Input("store-series",         "data"),
    Input("data-slider",          "value"),
    Input({"type": "data-check", "index": ALL}, "value"),
    Input("data-transform",       "value"),
    prevent_initial_call=False,
)
def update_data_chart(store, slider_val, checks, tr):
    series_dict = _load_all_series(store)
    if not series_dict:
        return empty_fig("Nessuna serie scaricata"), html.Div()

    selected = [v[0] for v in (checks or []) if v]
    if not selected:
        return empty_fig("Seleziona almeno una serie"), html.Div()

    t0 = pd.to_datetime(slider_val[0], unit="s").normalize() if slider_val else None
    t1 = pd.to_datetime(slider_val[1], unit="s").normalize() if slider_val else None

    fig = go.Figure()
    table_data = {}
    for i, name in enumerate(selected):
        if name not in series_dict:
            continue
        s = apply_transform(series_dict[name], tr or "levels")
        if t0 and t1:
            s = s.loc[t0:t1]
        if s.empty:
            continue
        lbl = tr_label(name[:40], tr or "levels")
        color = COLORS[i % len(COLORS)]
        fig.add_trace(go.Scatter(x=s.index, y=s.values, name=lbl,
                                  line=dict(color=color, width=1.8),
                                  hovertemplate=f"{lbl}: %{{y:.4f}}<extra></extra>"))
        table_data[lbl] = s

    tr_titles = {"levels": "Valori originali", "yoy": "Δ% Anno su Anno",
                 "log": "Logaritmo", "dlog": "Δ Logaritmo", "diff": "Prima differenza",
                 "sdiff": "Differenza stagionale"}
    fig.update_layout(
        title=dict(text=f"Serie Eurostat — {tr_titles.get(tr, tr)}",
                   font=dict(size=12, color="#1a3a5c"), x=0.01),
        hovermode="x unified",
        margin=dict(t=45, b=30, l=55, r=20),
        paper_bgcolor="white", plot_bgcolor="#f8f9fa",
        legend=dict(orientation="h", y=1.02, font=dict(size=9)),
    )
    fig.add_hline(y=0, line_color="#bbb", line_width=0.7, line_dash="dot")

    # Tabella ultimi 12 valori
    if table_data:
        df_tbl = pd.DataFrame(table_data).tail(24).iloc[::-1]
        df_tbl.index = df_tbl.index.strftime("%Y-%m")
        header = ["Data"] + list(df_tbl.columns)
        rows_tbl = [[idx] + [f"{v:.4f}" if pd.notna(v) else "—"
                              for v in row] for idx, row in df_tbl.iterrows()]
        table = _make_table([header] + rows_tbl)
    else:
        table = html.Div()

    return fig, table


@app.callback(
    Output("store-series", "data", allow_duplicate=True),
    Input("btn-clear-data", "n_clicks"),
    prevent_initial_call=True,
)
def clear_data(n):
    return {}


@app.callback(
    Output("download-csv", "data"),
    Input("btn-export-csv", "n_clicks"),
    State("store-series",   "data"),
    State("data-transform", "value"),
    prevent_initial_call=True,
)
def export_csv(n, store, tr):
    series_dict = _load_all_series(store)
    if not series_dict:
        raise PreventUpdate
    frames = {}
    for name, s in series_dict.items():
        frames[tr_label(name, tr or "levels")] = apply_transform(s, tr or "levels")
    df = pd.DataFrame(frames).sort_index()
    return dcc.send_data_frame(df.to_csv, "eurostat_data.csv")


# ═══════════════════════════════════════════════════════════════════════════════
# CALLBACKS — TAB ARIMA
# ═══════════════════════════════════════════════════════════════════════════════

@app.callback(
    Output("arima-series", "options"),
    Input("store-series",  "data"),
    prevent_initial_call=False,
)
def arima_populate_series(store):
    series_dict = _load_all_series(store)
    return [{"label": n, "value": n} for n in series_dict]


@app.callback(
    Output("arima-seasonal-box", "style"),
    Input("arima-seasonal", "value"),
)
def toggle_seasonal(val):
    return {"display": "block", "margin-top": "4px"} if val else {"display": "none"}


@app.callback(
    Output("arima-step1-out", "children"),
    Output("store-arima-series", "data"),
    Output("arima-status", "children"),
    Input("btn-arima-step1", "n_clicks"),
    State("arima-series",   "value"),
    State("arima-transform","value"),
    State("arima-detrend",  "value"),
    State("store-series",   "data"),
    prevent_initial_call=True,
)
def arima_step1(n, name, tr, detrend, store):
    _err = lambda m: (None, None, f"❌ {m}")
    if not name:
        return _err("Seleziona una serie.")
    sd = _load_all_series(store)
    if name not in sd:
        return _err("Serie non disponibile.")
    orig = sd[name].dropna()
    if len(orig) < 24:
        return _err("Meno di 24 osservazioni — espandi il periodo.")

    # Trasformazione
    tr = tr or "log"
    try:
        trans = apply_transform(orig, tr)
        if trans.empty:
            return _err("Trasformazione produce serie vuota (valori ≤ 0?).")
    except Exception as e:
        return _err(f"Trasformazione: {e}")
    trans_lbl = tr_label(name, tr)

    # Detrend
    if detrend == "ma12" and len(trans) >= 13:
        trend  = trans.rolling(12, center=True).mean()
        stat   = trans - trend
        stat_lbl = f"{trans_lbl} − MA(12)"
    elif detrend == "hp":
        _, trend = sm.tsa.filters.hpfilter(trans, lamb=1600)
        stat   = trans - pd.Series(trend, index=trans.index)
        stat_lbl = f"{trans_lbl} − HP"
    elif detrend == "sdiff":
        stat   = trans.diff(12).dropna()
        stat_lbl = f"Δ12 {trans_lbl}"
    else:
        stat   = trans.copy()
        stat_lbl = trans_lbl
        trend  = None

    stat = stat.dropna()

    # Grafici
    fig = make_subplots(rows=2, cols=2,
                        subplot_titles=["Originale", "Trasformata",
                                        "Trend (se applicato)", "Serie stazionaria"])
    fig.add_trace(go.Scatter(x=orig.index, y=orig.values, name=name,
                              line=dict(color="#1f77b4", width=1.2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=trans.index, y=trans.values, name=trans_lbl,
                              line=dict(color="#ff7f0e", width=1.2)), row=1, col=2)
    if trend is not None:
        fig.add_trace(go.Scatter(x=trend.index, y=trend.values, name="Trend",
                                  line=dict(color="#2ca02c", width=1.5)), row=2, col=1)
    fig.add_trace(go.Scatter(x=stat.index, y=stat.values, name=stat_lbl,
                              line=dict(color="#d62728", width=1.2)), row=2, col=2)
    fig.update_layout(height=480, margin=dict(t=50, b=30, l=45, r=15),
                      paper_bgcolor="white", plot_bgcolor="#f8f9fa",
                      showlegend=False,
                      title=dict(text="① Trasformazione & Detrend", font=dict(size=11), x=0.01))

    # Salva serie stazionaria per i passi successivi
    store_data = {"series_json": stat.to_json(date_format="iso", orient="split"),
                  "name": name, "stat_label": stat_lbl}

    return (html.Div([
        dcc.Graph(figure=fig, config={"responsive": True}),
        html.P(f"Serie stazionaria: '{stat_lbl}'  |  N={len(stat)}  |  "
               f"{stat.index.min().strftime('%Y-%m')} → {stat.index.max().strftime('%Y-%m')}",
               style={"font-size": "10px", "color": "#666", "margin": "4px 0 0 4px"}),
    ]), store_data, f"✅ Serie analizzata: {stat_lbl} ({len(stat)} obs)")


@app.callback(
    Output("arima-step2-out", "children"),
    Output("ar-p", "value"),
    Output("ar-d", "value"),
    Output("ar-q", "value"),
    Input("btn-arima-step2",  "n_clicks"),
    State("store-arima-series", "data"),
    prevent_initial_call=True,
)
def arima_step2(n, store_data):
    if not store_data:
        return html.Div("⚠ Esegui prima il Passo ①.", style={"color":"#c62828","font-size":"11px"}), no_update, no_update, no_update

    series = pd.read_json(io.StringIO(store_data["series_json"]), typ="series")
    series.index = pd.to_datetime(series.index)
    series = series.dropna()
    stat_lbl = store_data.get("stat_label", "serie")

    # ADF test
    try:
        adf_stat, adf_p, _, _, adf_cv, _ = adfuller(series, autolag="AIC")
        adf_ok = adf_p < 0.05
    except Exception:
        adf_stat = adf_p = float("nan"); adf_ok = False; adf_cv = {}

    # ACF / PACF
    nlags = min(36, len(series) // 2 - 1)
    try:
        acf_vals,  acf_ci  = _acf_fn(series,  nlags=nlags, alpha=0.05)
        pacf_vals, pacf_ci = _pacf_fn(series, nlags=nlags, alpha=0.05, method="ols")
    except Exception:
        acf_vals = pacf_vals = np.zeros(nlags + 1)
        acf_ci = pacf_ci = np.zeros((nlags + 1, 2))

    lags = list(range(len(acf_vals)))
    ci_upper = acf_ci[:, 1] - acf_vals
    ci_lower = acf_vals - acf_ci[:, 0]
    conf_line = 1.96 / np.sqrt(len(series))

    fig = make_subplots(rows=1, cols=2, subplot_titles=[
        f"ACF — {stat_lbl}", f"PACF — {stat_lbl}"])
    for gi, (vals, ci_u, ci_l, title) in enumerate([
        (acf_vals[1:], ci_upper[1:], ci_lower[1:], "ACF"),
        (pacf_vals[1:], pacf_ci[1:, 1] - pacf_vals[1:],
         pacf_vals[1:] - pacf_ci[1:, 0], "PACF"),
    ]):
        li = lags[1:]
        fig.add_trace(go.Bar(x=li, y=vals, name=title,
                              marker_color=["#d62728" if abs(v) > conf_line else "#1f77b4"
                                            for v in vals],
                              error_y=dict(type="data", symmetric=False,
                                           array=ci_u, arrayminus=ci_l, visible=True)),
                      row=1, col=gi + 1)
        fig.add_hline(y=conf_line,   line_dash="dash", line_color="#888", line_width=0.8,
                      row=1, col=gi + 1)
        fig.add_hline(y=-conf_line,  line_dash="dash", line_color="#888", line_width=0.8,
                      row=1, col=gi + 1)
        fig.add_hline(y=0,           line_color="#333", line_width=0.5,
                      row=1, col=gi + 1)

    fig.update_layout(height=340, showlegend=False,
                      margin=dict(t=50, b=30, l=45, r=15),
                      paper_bgcolor="white", plot_bgcolor="#f8f9fa",
                      title=dict(text="② ACF & PACF", font=dict(size=11), x=0.01))

    # Suggerisci ordini
    sig_acf  = [i for i, v in enumerate(acf_vals[1:], 1)  if abs(v) > conf_line]
    sig_pacf = [i for i, v in enumerate(pacf_vals[1:], 1) if abs(v) > conf_line]
    sug_q = max(sig_acf[:3]) if sig_acf else 0
    sug_p = max(sig_pacf[:3]) if sig_pacf else 1
    sug_d = 0 if adf_ok else 1

    # ADF table
    adf_rows = [
        ["Test ADF", "Statistica", "p-value", "Stazionaria?"],
        ["", f"{adf_stat:.4f}", f"{adf_p:.4e}", "✅ Sì" if adf_ok else "❌ No (differenzia)"],
        ["CV 1%",  f"{adf_cv.get('1%', '—'):.3f}" if isinstance(adf_cv.get('1%'), float) else "—", "", ""],
        ["CV 5%",  f"{adf_cv.get('5%', '—'):.3f}" if isinstance(adf_cv.get('5%'), float) else "—", "", ""],
        ["CV 10%", f"{adf_cv.get('10%','—'):.3f}" if isinstance(adf_cv.get('10%'), float) else "—", "", ""],
    ]

    out = html.Div([
        dcc.Graph(figure=fig, config={"responsive": True}),
        html.Div([
            _make_table(adf_rows, "#c62828"),
            html.P(f"Suggerimento ordini  →  p={sug_p}, d={sug_d}, q={sug_q}  "
                   f"(modificabili nella sidebar)",
                   style={"font-size": "11px", "color": "#1565c0",
                          "margin-top": "8px", "font-weight": "bold"}),
        ], style={"margin": "0 4px"}),
    ])
    return out, sug_p, sug_d, sug_q


@app.callback(
    Output("arima-fit-out",  "children"),
    Output("arima-status",   "children", allow_duplicate=True),
    Input("btn-arima-fit",   "n_clicks"),
    State("store-arima-series", "data"),
    State("ar-p", "value"), State("ar-d", "value"), State("ar-q", "value"),
    State("arima-seasonal",  "value"),
    State("ar-P", "value"), State("ar-D", "value"), State("ar-Q", "value"),
    State("ar-s", "value"),
    State("arima-fc-steps",  "value"),
    prevent_initial_call=True,
)
def arima_fit(n, store_data, p, d, q, seasonal, P_, D_, Q_, s_, fc_steps):
    _err = lambda m: (html.Div(m, style={"color": "#c62828"}), f"❌ {m}")
    if not store_data:
        return _err("Esegui prima i Passi ① e ②.")

    series = pd.read_json(io.StringIO(store_data["series_json"]), typ="series")
    series.index = pd.to_datetime(series.index)
    series = series.dropna().sort_index()
    stat_lbl = store_data.get("stat_label", "serie")
    if len(series) < 20:
        return _err("Meno di 20 osservazioni.")

    p, d, q = int(p or 1), int(d or 0), int(q or 0)
    P_, D_, Q_, s_ = int(P_ or 1), int(D_ or 1), int(Q_ or 1), int(s_ or 12)
    use_seasonal = bool(seasonal)
    steps = int(fc_steps or 24)

    order         = (p, d, q)
    seasonal_order = (P_, D_, Q_, s_) if use_seasonal else (0, 0, 0, 0)

    try:
        mod = SARIMAX(series, order=order, seasonal_order=seasonal_order,
                      enforce_stationarity=False, enforce_invertibility=False)
        res = mod.fit(disp=False, maxiter=200)
    except Exception as e:
        return _err(f"Stima fallita: {e}")

    # Forecast
    try:
        fc_obj = res.get_forecast(steps=steps)
        fc_mean = fc_obj.predicted_mean
        fc_ci   = fc_obj.conf_int(alpha=0.05)
    except Exception:
        fc_mean = fc_ci = None

    # Grafici fit + forecast
    fig_fit = go.Figure()
    fig_fit.add_trace(go.Scatter(x=series.index, y=series.values, name="Osservato",
                                  line=dict(color="#1f77b4", width=1.5)))
    fig_fit.add_trace(go.Scatter(x=series.index, y=res.fittedvalues, name="Fitted",
                                  line=dict(color="#d62728", width=1.5, dash="dot")))
    if fc_mean is not None:
        fig_fit.add_trace(go.Scatter(x=fc_mean.index, y=fc_mean.values, name="Previsione",
                                      line=dict(color="#ff7f0e", width=2)))
        fig_fit.add_trace(go.Scatter(
            x=list(fc_ci.index) + list(fc_ci.index[::-1]),
            y=list(fc_ci.iloc[:, 1]) + list(fc_ci.iloc[::-1, 0]),
            fill="toself", fillcolor="rgba(255,127,14,0.15)",
            line=dict(color="rgba(255,127,14,0)"), name="IC 95%"))
    fig_fit.update_layout(
        title=dict(text=f"SARIMA{order}×{seasonal_order if use_seasonal else ''}  |  AIC={res.aic:.1f}",
                   font=dict(size=11), x=0.01),
        hovermode="x unified", margin=dict(t=48, b=30, l=55, r=20),
        paper_bgcolor="white", plot_bgcolor="#f8f9fa",
        legend=dict(orientation="h", y=1.02, font=dict(size=9)),
        height=400,
    )

    # Residui
    resid = res.resid.dropna()
    fig_resid = make_subplots(rows=1, cols=2,
                               subplot_titles=["Residui nel tempo", "Distribuzione residui"])
    fig_resid.add_trace(go.Scatter(x=resid.index, y=resid.values, mode="lines",
                                    line=dict(color="#7f7f7f", width=1)), row=1, col=1)
    fig_resid.add_hline(y=0, line_color="#aaa", line_width=0.8, row=1, col=1)
    fig_resid.add_trace(go.Histogram(x=resid.values, nbinsx=25,
                                      marker_color="#9467bd", opacity=0.7,
                                      name="Distribuzione"), row=1, col=2)
    fig_resid.update_layout(height=280, showlegend=False,
                             margin=dict(t=40, b=30, l=45, r=15),
                             paper_bgcolor="white", plot_bgcolor="#f8f9fa")

    # Statistiche
    try:
        jb_stat, jb_p, jb_sk, jb_ku = sm.stats.stattools.jarque_bera(resid)
    except Exception:
        jb_stat = jb_p = jb_sk = jb_ku = float("nan")
    try:
        dw = float(sm.stats.stattools.durbin_watson(resid))
    except Exception:
        dw = float("nan")

    model_name = (f"SARIMA({p},{d},{q})({P_},{D_},{Q_})[{s_}]"
                  if use_seasonal else f"ARIMA({p},{d},{q})")

    stats_rows = [
        ["Statistica", "Valore", "Interpretazione"],
        ["Modello",    model_name, ""],
        ["N obs",      str(len(series)), ""],
        ["AIC",        f"{res.aic:.3f}",  "↓ migliore"],
        ["BIC",        f"{res.bic:.3f}",  "↓ migliore"],
        ["Log-lik.",   f"{res.llf:.3f}",  "↑ migliore"],
        ["Durbin-Watson", f"{dw:.4f}",
         "✓" if 1.5 <= dw <= 2.5 else "⚠ autocorrelazione"],
        ["Jarque-Bera",   f"{jb_stat:.3f}", f"p={jb_p:.3e} {_pstar(jb_p)}"],
        ["  Asimmetria",  f"{jb_sk:.4f}",   "~0 = simmetrico"],
        ["  Curtosi",     f"{jb_ku:.4f}",   "normale=3"],
    ]

    # Tabella coefficienti
    coef_rows = [["Parametro", "Coeff.", "Std Err", "z-stat", "p-val", "Sig."]]
    try:
        for pname, coef, se, zv, pv in zip(
            res.param_names, res.params, res.bse, res.tvalues, res.pvalues
        ):
            coef_rows.append([pname, f"{coef:+.6f}", f"{se:.6f}",
                               f"{zv:.4f}", f"{pv:.4e}", _pstar(pv)])
    except Exception:
        pass

    out = html.Div([
        dcc.Graph(figure=fig_fit, config={"responsive": True, "scrollZoom": True}),
        dcc.Graph(figure=fig_resid, config={"responsive": True}),
        html.Div([
            html.Div(_make_table(stats_rows), style={"margin-right": "16px", "flex": "1"}),
            html.Div(_make_table(coef_rows, "#6a1b9a"), style={"flex": "2"}),
        ], style={"display": "flex", "gap": "12px", "margin-top": "8px"}),
        html.P("*** p<0.001  ** p<0.01  * p<0.05  · p<0.10",
               style={"font-size": "9px", "color": "#777", "margin-top": "4px"}),
    ])
    return out, f"✅ {model_name}  |  AIC={res.aic:.1f}  BIC={res.bic:.1f}"


# ═══════════════════════════════════════════════════════════════════════════════
# CALLBACKS — TAB ADL
# ═══════════════════════════════════════════════════════════════════════════════

@app.callback(
    Output("adl-y",   "options"),
    Input("store-series", "data"),
    prevent_initial_call=False,
)
def adl_populate_y(store):
    sd = _load_all_series(store)
    return [{"label": n, "value": n} for n in sd]


@app.callback(
    Output("adl-x-panel", "children"),
    Input("store-series", "data"),
    State("adl-y", "value"),
    prevent_initial_call=False,
)
def adl_build_x_panel(store, y_sel):
    sd = _load_all_series(store)
    if not sd:
        return html.Div("Nessuna serie disponibile.",
                        style={"font-size": "10px", "color": "#aaa"})
    rows = []
    for name in sd:
        if name == y_sel:
            continue
        short = name[:32] + "…" if len(name) > 32 else name
        rows.append(html.Div([
            dcc.Checklist(id={"type": "adl-x-active", "index": name},
                          options=[{"label": f" {short}", "value": name}],
                          value=[], style={"font-size": "9px"},
                          inputStyle={"margin-right": "3px"}),
            dcc.Dropdown(id={"type": "adl-x-tr", "index": name},
                         options=TR_OPTIONS, value="yoy", clearable=False,
                         style={"font-size": "9px", "margin": "2px 0"}),
            dcc.Dropdown(id={"type": "adl-x-lags", "index": name},
                         options=LAG_OPTIONS, value=[0], multi=True,
                         placeholder="lag…",
                         style={"font-size": "9px", "margin-bottom": "4px"}),
        ], style={"padding": "4px 0", "border-bottom": "1px solid #eee"}))
    return rows or [html.Div("Nessuna X disponibile.",
                              style={"font-size": "10px", "color": "#aaa"})]


@app.callback(
    Output("adl-equation",    "children"),
    Output("adl-stats-out",   "children"),
    Output("adl-coef-out",    "children"),
    Output("chart-adl-fit",   "figure"),
    Output("chart-adl-irf",   "figure"),
    Output("chart-adl-resid", "figure"),
    Output("adl-status",      "children"),
    Input("btn-adl-run",      "n_clicks"),
    State("store-series",     "data"),
    State("adl-y",            "value"),
    State("adl-y-tr",         "value"),
    State("adl-ar",           "value"),
    State({"type": "adl-x-active","index": ALL}, "value"),
    State({"type": "adl-x-active","index": ALL}, "id"),
    State({"type": "adl-x-tr",    "index": ALL}, "value"),
    State({"type": "adl-x-lags",  "index": ALL}, "value"),
    State("adl-cov",          "value"),
    State("adl-const",        "value"),
    prevent_initial_call=True,
)
def run_adl(n, store, y_col, y_tr, ar_lags, x_active, x_ids, x_trs, x_lags_list, cov_type, add_const):
    def _err(msg):
        ef = empty_fig(msg)
        return msg, "", "", ef, ef, ef, f"❌ {msg}"

    if not y_col:
        return _err("Seleziona la variabile dipendente Y.")
    sd = _load_all_series(store)
    if y_col not in sd:
        return _err("Serie Y non disponibile.")

    df = pd.DataFrame(sd)
    y_series = apply_transform(df[y_col], y_tr or "yoy").dropna()
    y_label  = tr_label(y_col, y_tr or "yoy")

    active_x = {}
    for chk, xid, xtr, xlags in zip(x_active, x_ids, x_trs, x_lags_list):
        col = xid["index"]
        if chk and col in df.columns:
            active_x[col] = (xtr or "yoy", xlags or [0])

    if not active_x and not ar_lags:
        return _err("Seleziona almeno una variabile X o un lag AR.")

    combined = pd.DataFrame({"__y__": y_series})
    for col, (xtr, xlags) in active_x.items():
        x_base = apply_transform(df[col], xtr).dropna()
        for lag in sorted(set(xlags)):
            cname = col if lag == 0 else f"{col}_L{lag}"
            combined[cname] = x_base.shift(lag)
    ar_lags = sorted(set(ar_lags or []))
    for k in ar_lags:
        combined[f"Y(t-{k})"] = combined["__y__"].shift(k)
    combined = combined.dropna()
    if len(combined) < 10:
        return _err(f"Osservazioni insufficienti: {len(combined)}")

    y_vec  = combined["__y__"]
    X_cols = [c for c in combined.columns if c != "__y__"]
    X_mat  = combined[X_cols].copy()
    if add_const and "const" in (add_const or []):
        X_mat = sm.add_constant(X_mat, has_constant="add")

    try:
        res = sm.OLS(y_vec, X_mat).fit()
        cov = cov_type or "HC3"
        if cov == "HAC":
            rob = res.get_robustcov_results(cov_type="HAC", maxlags=int(len(combined)**0.25))
        elif cov == "HC3":
            rob = res.get_robustcov_results(cov_type="HC3")
        else:
            rob = res
        pnames  = X_mat.columns.tolist()
        _p  = rob.params;  _pv = rob.pvalues
        _tv = rob.tvalues; _bs = rob.bse
        params  = pd.Series(_p  if hasattr(_p,  "index") else _p,  index=pnames)
        pvalues = pd.Series(_pv if hasattr(_pv, "index") else _pv, index=pnames)
        tvalues = pd.Series(_tv if hasattr(_tv, "index") else _tv, index=pnames)
        bse     = pd.Series(_bs if hasattr(_bs, "index") else _bs, index=pnames)
    except Exception as e:
        return _err(f"OLS: {e}")

    # Equazione
    terms = [f"  α = {params['const']:+.6f}" if "const" in params.index else ""]
    for v, cv in params.items():
        if v != "const":
            terms.append(f"  {'+'if cv>=0 else '−'} {abs(cv):.6f} · {v}")
    equation = f"{y_label} =\n" + "\n".join(t for t in terms if t) + "\n  + ε"

    # Diagnostiche
    try:
        dw = float(sm.stats.stattools.durbin_watson(res.resid))
    except Exception:
        dw = float("nan")
    try:
        jb_stat, jb_p, jb_sk, jb_ku = sm.stats.stattools.jarque_bera(res.resid)
    except Exception:
        jb_stat = jb_p = jb_sk = jb_ku = float("nan")
    try:
        bp_lm, bp_p, *_ = sm.stats.diagnostic.het_breuschpagan(res.resid, res.model.exog)
    except Exception:
        bp_lm = bp_p = float("nan")

    cov_lbl = {"nonrobust": "OLS standard", "HC3": "HC3", "HAC": "HAC Newey-West"}.get(cov, cov)
    stats_rows = [
        ["Statistica", "Valore", "Interpretazione"],
        ["N obs",        str(len(combined)), ""],
        ["Std. Error",   cov_lbl, ""],
        ["R²",           f"{res.rsquared:.6f}", ""],
        ["R² adj.",      f"{res.rsquared_adj:.6f}", ""],
        ["F-stat",       f"{res.fvalue:.4f}", f"p={res.f_pvalue:.3e} {_pstar(res.f_pvalue)}"],
        ["AIC",          f"{res.aic:.3f}", "↓ migliore"],
        ["BIC",          f"{res.bic:.3f}", "↓ migliore"],
        ["Durbin-Watson",f"{dw:.4f}", "✓" if 1.5 <= dw <= 2.5 else "⚠ autocorrelazione"],
        ["Jarque-Bera",  f"{jb_stat:.4f}", f"p={jb_p:.3e} {_pstar(jb_p)}"],
        ["  Asimmetria", f"{jb_sk:.4f}", "~0 = simmetrico"],
        ["  Curtosi",    f"{jb_ku:.4f}", "normale=3"],
        ["Breusch-Pagan",f"{bp_lm:.4f}", f"p={bp_p:.3e} {_pstar(bp_p)}"],
    ]
    stats_out = _make_table(stats_rows)

    # Coefficienti + VIF
    x_cols_vif = [c for c in X_mat.columns if c != "const"]
    vif_dict = {}
    if len(x_cols_vif) > 1:
        for xc in x_cols_vif:
            other = [c for c in x_cols_vif if c != xc]
            try:
                r2v = sm.OLS(X_mat[xc], sm.add_constant(X_mat[other])).fit().rsquared
                vif_dict[xc] = 1 / (1 - r2v) if r2v < 1 else np.inf
            except Exception:
                vif_dict[xc] = float("nan")
    try:
        conf = res.conf_int(alpha=0.05)
        if not hasattr(conf, "loc"):
            conf = pd.DataFrame(conf, index=params.index, columns=[0, 1])
    except Exception:
        conf = pd.DataFrame({0: params * np.nan, 1: params * np.nan})

    coef_rows = [["Variabile", "Coeff.", "Std Err", "t-stat", "p-val", "Sig.", "IC95 inf", "IC95 sup", "VIF"]]
    for v in params.index:
        p_v = pvalues[v]
        vif = vif_dict.get(v, float("nan"))
        vif_s = f"{vif:.2f}" if isinstance(vif, float) and not np.isnan(vif) else "—"
        try:
            ic_lo = f"{conf.loc[v, 0]:.5f}"
            ic_hi = f"{conf.loc[v, 1]:.5f}"
        except Exception:
            ic_lo = ic_hi = "—"
        coef_rows.append([v, f"{params[v]:+.6f}", f"{bse[v]:.6f}",
                           f"{tvalues[v]:.4f}", f"{p_v:.4e}", _pstar(p_v), ic_lo, ic_hi, vif_s])
    coef_out = html.Div([
        _make_table(coef_rows, "#2e6da4"),
        html.P("*** p<0.001  ** p<0.01  * p<0.05  · p<0.10",
               style={"font-size": "9px", "color": "#777", "margin-top": "4px"}),
    ])

    # Fit chart
    fig_fit = go.Figure()
    fig_fit.add_trace(go.Scatter(x=y_vec.index, y=y_vec.values, name=y_label,
                                  line=dict(color="#1f77b4", width=1.5)))
    fig_fit.add_trace(go.Scatter(x=y_vec.index, y=res.fittedvalues.values, name="Fitted",
                                  line=dict(color="#d62728", width=1.5, dash="dot")))
    fig_fit.add_hline(y=0, line_color="#bbb", line_width=0.7)
    fig_fit.update_layout(
        title=dict(text=f"Fit  |  R²={res.rsquared:.4f}  R²adj={res.rsquared_adj:.4f}",
                   font=dict(size=11), x=0.01),
        hovermode="x unified", margin=dict(t=45, b=30, l=55, r=20),
        paper_bgcolor="white", plot_bgcolor="#f8f9fa",
        legend=dict(orientation="h", y=1.02, font=dict(size=9)),
    )

    # IRF
    _PCT_TR = {"yoy", "dlog"}
    irf_scale  = 100.0 if (y_tr or "yoy") in _PCT_TR else 1.0
    irf_suffix = "%" if irf_scale == 100.0 else ""
    ar_coefs = {}
    for v in params.index:
        if v.startswith("Y(t-"):
            try:
                ar_coefs[int(v[4:-1])] = float(params[v])
            except ValueError:
                pass
    x_groups = {}
    for v in params.index:
        if v in ("const",) or v.startswith("Y(t-"):
            continue
        base, lag = (v.rsplit("_L", 1)[0], int(v.rsplit("_L", 1)[1])) if "_L" in v else (v, 0)
        x_groups.setdefault(base, []).append((lag, float(params[v])))
    x_sigmas = {c: float(combined[c].std()) for c in combined.columns if c != "__y__"}

    n_g = len(x_groups)
    if n_g == 0:
        fig_irf = empty_fig("Nessuna variabile X")
    else:
        H = 12
        cpr = min(3, n_g)
        nri = (n_g + cpr - 1) // cpr
        sp_titles = [b[:30] for b in x_groups]
        fig_irf = make_subplots(rows=nri, cols=cpr, subplot_titles=sp_titles,
                                 vertical_spacing=0.14, horizontal_spacing=0.08)
        fmt = (lambda v: f"{v:+.2f}%") if irf_suffix else (lambda v: f"{v:+.4f}")
        for gi, (base, lc) in enumerate(x_groups.items()):
            ri = gi // cpr + 1; ci = gi % cpr + 1
            color = COLORS[gi % len(COLORS)]
            sigma = next((x_sigmas.get(k, 1.0) for k in combined.columns
                          if k != "__y__" and (k == base or k.startswith(base + "_L"))), 1.0)
            direct = {lag: coef * sigma for lag, coef in lc}
            irf_vals = []
            for h in range(H):
                d_h = direct.get(h, 0.0)
                ar_part = sum(ar_coefs.get(j, 0.0) * irf_vals[h - j]
                              for j in ar_coefs if 0 < j <= h)
                irf_vals.append(d_h + ar_part)
            irf_sc = [v * irf_scale for v in irf_vals]
            cum_sc = list(np.cumsum(irf_sc))
            fig_irf.add_trace(go.Bar(
                x=list(range(H)), y=irf_sc, name=base,
                marker_color=["#2ca02c" if v >= 0 else "#d62728" for v in irf_sc],
                marker_line_width=0, showlegend=False,
                text=[fmt(v) for v in irf_sc], textposition="outside",
                textfont=dict(size=7)), row=ri, col=ci)
            fig_irf.add_trace(go.Scatter(
                x=list(range(H)), y=cum_sc, mode="lines+markers",
                line=dict(color=color, width=2, dash="dot"),
                marker=dict(size=4), showlegend=False, name=f"Cum.{base}"),
                row=ri, col=ci)
            fig_irf.add_hline(y=0, line_color="#bbb", line_width=0.8, row=ri, col=ci)
        fig_irf.update_layout(
            title=dict(text=f"IRF — +1σ per variabile  |  Y: {y_label}",
                       font=dict(size=11), x=0.01),
            height=max(280, 230 * nri),
            margin=dict(t=50, b=30, l=45, r=15),
            paper_bgcolor="white", plot_bgcolor="#f8f9fa",
        )
        fig_irf.update_xaxes(title_text="Mesi", showgrid=True, gridcolor="#e8e8e8")
        fig_irf.update_yaxes(title_text=f"Δ Y ({irf_suffix or 'unità'})",
                              showgrid=True, gridcolor="#e8e8e8")

    # Residui
    resid = res.resid
    fig_resid = go.Figure()
    fig_resid.add_trace(go.Scatter(x=resid.index, y=resid.values,
                                    mode="lines", line=dict(color="#7f7f7f", width=1),
                                    name="Residui"))
    fig_resid.add_hline(y=0, line_color="#aaa", line_width=0.8)
    fig_resid.update_layout(
        title=dict(text="Residui", font=dict(size=11), x=0.01),
        margin=dict(t=40, b=30, l=55, r=20),
        paper_bgcolor="white", plot_bgcolor="#f8f9fa",
    )

    status = (f"✅ N={len(combined)}  R²={res.rsquared:.4f}  "
              f"R²adj={res.rsquared_adj:.4f}  DW={dw:.3f}  |  {cov_lbl}")
    return equation, stats_out, coef_out, fig_fit, fig_irf, fig_resid, status


# ═══════════════════════════════════════════════════════════════════════════════
# AVVIO
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app.run(debug=True, port=8052)
