"""
Rendimenti Storici — App standalone
Analisi dei rendimenti per periodo: YTD, annuali, T-N, Information Ratio, Sharpe Ratio.
Legge i dati direttamente dal portafoglio condiviso (market_data.pkl / buffer live).
"""

import json
import pickle
import sys
import os
from pathlib import Path

import numpy as np
import pandas as pd

from dash import Dash, html, dcc, Input, Output, State, callback_context, no_update, ALL
from dash.exceptions import PreventUpdate

_sys_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _sys_path not in sys.path:
    sys.path.insert(0, _sys_path)
from settings.browser_css import BROWSER_RESET_CSS
from navbar import make_navbar

# ─── App ─────────────────────────────────────────────────────────────────────
_EXT = [
    'https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;700&family=Inter:wght@400;600;700&display=swap',
    'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css',
]
app = Dash(__name__, suppress_callback_exceptions=True, external_stylesheets=_EXT,
           requests_pathname_prefix='/rendimenti/',
           routes_pathname_prefix='/rendimenti/')

# ─── Percorso dati condivisi ──────────────────────────────────────────────────
_PORT_PKL = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '..', 'portafoglio', 'sessions', 'market_data.pkl',
))

# ─── Helpers ─────────────────────────────────────────────────────────────────
_NU = no_update

def _to_json(df):
    if df is None:
        return None
    return df.to_json(date_format='iso', orient='split')

def _get_df(js):
    if not js:
        return None
    return pd.read_json(js, orient='split')

def _get_username():
    try:
        from flask import session as _fs
        return _fs.get('username') or 'anon'
    except Exception:
        return 'anon'

def _read_user_json():
    try:
        u = _get_username()
        root = Path(os.path.dirname(os.path.abspath(__file__))).parent
        return json.load(open(root / 'sessions' / u / 'current.json'))
    except Exception:
        return {}

def _reconstruct_from_json(ns):
    try:
        first = next(iter(ns.values()))
        dates = pd.to_datetime(first['dates'])
        pr, ret = {}, {}
        for desc, v in ns.items():
            p = v.get('prices') or []
            r = v.get('returns') or []
            if p:
                pr[desc]  = [float(x) if x is not None else float('nan') for x in p]
            if r:
                ret[desc] = [float(x) if x is not None else float('nan') for x in r]
        op = pd.DataFrame(pr,  index=dates) if pr  else None
        cr = pd.DataFrame(ret, index=dates) if ret else None
        return op, cr
    except Exception:
        return None, None

def _read_shared_data():
    """Legge prezzi/rendimenti dal portafoglio: JSON utente → buffer live → pkl."""
    try:
        ns = _read_user_json()
        if ns:
            op, cr = _reconstruct_from_json(ns)
            if op is not None and cr is not None:
                return op, cr, ''
    except Exception:
        pass
    try:
        port = sys.modules.get('_app_portafoglio')
        if port is not None:
            with port._DL_LOCK:
                buf = dict(port._DL_BUFFER)
            prices  = buf.get('original_prices')
            returns = buf.get('close_returns')
            if prices is not None and returns is not None:
                return prices, returns, buf.get('saved_at', '')
    except Exception:
        pass
    try:
        if os.path.exists(_PORT_PKL):
            with open(_PORT_PKL, 'rb') as f:
                data = pickle.load(f)
            prices  = data.get('original_prices')
            returns = data.get('close_returns')
            if prices is not None and returns is not None:
                return prices, returns, data.get('saved_at', '')
    except Exception:
        pass
    return None, None, None

# ─── Calcolo rendimenti ───────────────────────────────────────────────────────
def calculate_return_for_period(prices_series, days_back):
    prices_clean = prices_series.dropna()
    if len(prices_clean) < days_back + 1:
        return None
    price_now  = prices_clean.iloc[-1]
    price_then = prices_clean.iloc[-(days_back + 1)]
    if price_then == 0 or pd.isna(price_then):
        return None
    return (price_now / price_then) - 1

def calculate_year_return(prices_series, year):
    prices_clean = prices_series.dropna()
    if prices_clean.empty:
        return None
    jan_first = pd.Timestamp(f'{year}-01-01')
    dec_31    = pd.Timestamp(f'{year}-12-31')
    before = prices_clean[prices_clean.index < jan_first]
    if before.empty:
        return None
    price_start = before.iloc[-1]
    within = prices_clean[(prices_clean.index >= jan_first) & (prices_clean.index <= dec_31)]
    if within.empty:
        return None
    price_end = within.iloc[-1]
    if price_start == 0 or pd.isna(price_start) or pd.isna(price_end):
        return None
    return (price_end / price_start) - 1

def calculate_ytd_return(prices_series):
    prices_clean = prices_series.dropna()
    if prices_clean.empty:
        return None
    current_year = prices_clean.index[-1].year
    jan_first = pd.Timestamp(f'{current_year}-01-01')
    before_year = prices_clean[prices_clean.index < jan_first]
    price_start = before_year.iloc[-1] if not before_year.empty else prices_clean.iloc[0]
    price_now = prices_clean.iloc[-1]
    if price_start == 0 or pd.isna(price_start):
        return None
    return (price_now / price_start) - 1

def format_return(value):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "N/D"
    return f"{'+' if value >= 0 else ''}{value * 100:.2f}%"

def get_cell_style(value, is_portfolio=False):
    base = {
        'padding': '8px 10px', 'textAlign': 'right', 'fontSize': '12px',
        'whiteSpace': 'nowrap', 'border': '1px solid #ddd',
    }
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return {**base, 'backgroundColor': '#f9f9f9', 'color': '#aaa'}
    if value >= 0:
        return {**base, 'backgroundColor': '#e8f5e9', 'color': '#1b5e20',
                'fontWeight': 'bold' if is_portfolio else 'normal'}
    return {**base, 'backgroundColor': '#ffebee', 'color': '#b71c1c',
            'fontWeight': 'bold' if is_portfolio else 'normal'}

def calculate_ir_for_period(asset_returns, benchmark_returns, days_back):
    if benchmark_returns is None or len(benchmark_returns) == 0:
        return None
    if isinstance(asset_returns, np.ndarray) and isinstance(benchmark_returns, np.ndarray):
        if len(asset_returns) < days_back + 1:
            return None
        active = asset_returns[-days_back:] - benchmark_returns[-days_back:]
    else:
        combined = pd.concat([asset_returns, benchmark_returns], axis=1).dropna()
        if len(combined) < days_back + 1:
            return None
        active = combined.iloc[-days_back:, 0].values - combined.iloc[-days_back:, 1].values
    std = active.std()
    if std == 0 or np.isnan(std):
        return None
    return (active.mean() / std) * np.sqrt(252)

def calculate_sharpe_for_period(asset_returns, days_back, annual_rf_pct):
    if asset_returns is None:
        return None
    arr = asset_returns if isinstance(asset_returns, np.ndarray) else np.asarray(asset_returns)
    arr = arr[~np.isnan(arr)]
    if len(arr) < days_back + 1:
        return None
    w = arr[-days_back:]
    ann_ret = w.mean() * 252
    ann_std = w.std() * np.sqrt(252)
    if ann_std == 0 or np.isnan(ann_std):
        return None
    rf = (annual_rf_pct or 0.0) / 100.0
    return (ann_ret - rf) / ann_std

# ─── Index string ─────────────────────────────────────────────────────────────
app.index_string = '''<!DOCTYPE html><html>
<head>{%metas%}<title>Rendimenti Storici — Andrea Cappelletti</title>{%favicon%}{%css%}
<style>
''' + BROWSER_RESET_CSS + '''
</style>
</head>
<body>{%app_entry%}<footer>{%config%}{%scripts%}{%renderer%}</footer>
</body></html>
'''

# ─── Layout ──────────────────────────────────────────────────────────────────
app.layout = html.Div([
    make_navbar("Rendimenti"),

    # ── Stores ───────────────────────────────────────────────────────────────
    dcc.Store(id='rend-prices-data'),
    dcc.Store(id='rend-stock-data'),
    dcc.Store(id='rend-weights-p1', data={}),
    dcc.Store(id='rend-weights-p2', data={}),
    dcc.Store(id='rend-weights-p3', data={}),
    dcc.Store(id='rend-selected', data=[]),
    dcc.Store(id='rend-perf-data'),
    dcc.Store(id='rend-sort-state', data={}),
    dcc.Store(id='rend-ir-bench-store'),
    dcc.Store(id='rend-rf-store', data=0.0),

    # Trigger caricamento dati al primo render
    dcc.Interval(id='rend-init', interval=400, max_intervals=1),

    html.Div([

        # ── Barra controlli ────────────────────────────────────────────────
        html.Div([
            html.H2('Rendimenti Storici per Periodo', style={
                'fontFamily': 'Inter, sans-serif', 'fontSize': '1.1rem',
                'fontWeight': '700', 'color': '#1a3a6b',
                'margin': '0', 'marginRight': '24px', 'whiteSpace': 'nowrap',
            }),
            html.Div([
                html.Label('Benchmark IR:', style={
                    'fontSize': '11px', 'whiteSpace': 'nowrap',
                    'marginRight': '6px', 'color': '#4a1a7c', 'fontWeight': 'bold',
                }),
                dcc.Dropdown(id='rend-ir-bench', options=[], value=None,
                             placeholder='Seleziona benchmark…', clearable=True,
                             style={'width': '200px', 'fontSize': '11px'}),
            ], style={'display': 'flex', 'alignItems': 'center', 'marginLeft': '16px'}),
            html.Div([
                html.Label('Risk-Free SR (% ann.):', style={
                    'fontSize': '11px', 'whiteSpace': 'nowrap',
                    'marginRight': '6px', 'color': '#7a5c00', 'fontWeight': 'bold',
                }),
                dcc.Input(id='rend-rf-input', type='number', value=0.0,
                          min=0, max=20, step=0.1, placeholder='es. 3.5',
                          style={'width': '72px', 'fontSize': '11px',
                                 'border': '1px solid #aaa', 'borderRadius': '3px',
                                 'padding': '4px 6px'}),
            ], style={'display': 'flex', 'alignItems': 'center', 'marginLeft': '16px'}),
            html.Button('Aggiorna Tabella', id='rend-update-btn', n_clicks=0, style={
                'background': '#0066cc', 'color': 'white', 'border': 'none',
                'padding': '8px 20px', 'borderRadius': '4px', 'cursor': 'pointer',
                'fontWeight': 'bold', 'fontSize': '12px', 'marginLeft': 'auto',
                'whiteSpace': 'nowrap',
            }),
        ], style={
            'display': 'flex', 'alignItems': 'center', 'marginBottom': '10px',
            'flexWrap': 'wrap', 'gap': '8px',
        }),

        html.Div(id='rend-data-info', style={
            'fontSize': '11px', 'color': '#555', 'marginBottom': '10px',
        }),
        html.Hr(style={'margin': '0 0 12px 0', 'borderColor': '#e0e6ef'}),

        # ── Corpo: griglia sinistra + tabella destra ─────────────────────
        html.Div([

            # Colonna sinistra (30%)
            html.Div([
                html.Div(id='rend-asset-grid'),
                html.Hr(style={'margin': '8px 0'}),
                html.Div([
                    html.Div('Totale Pesi:', style={
                        'width': '35%', 'fontWeight': 'bold',
                        'fontSize': '10px', 'paddingLeft': '5px',
                    }),
                    html.Div('', style={'width': '10%'}),
                    html.Div(id='rend-sum-p1', children='0%', style={
                        'width': '15%', 'textAlign': 'center',
                        'color': '#d62728', 'fontSize': '10px', 'fontWeight': 'bold',
                    }),
                    html.Div(id='rend-sum-p2', children='0%', style={
                        'width': '15%', 'textAlign': 'center',
                        'color': '#d62728', 'fontSize': '10px', 'fontWeight': 'bold',
                    }),
                    html.Div(id='rend-sum-p3', children='0%', style={
                        'width': '15%', 'textAlign': 'center',
                        'color': '#d62728', 'fontSize': '10px', 'fontWeight': 'bold',
                    }),
                ], style={'display': 'flex', 'alignItems': 'center'}),
            ], style={
                'width': '30%', 'paddingRight': '15px', 'verticalAlign': 'top',
                'overflowY': 'auto', 'maxHeight': '78vh',
            }),

            # Colonna destra (70%)
            html.Div([
                dcc.Loading(type='circle', children=[
                    html.Div(id='rend-perf-table', style={
                        'overflowX': 'auto', 'overflowY': 'auto',
                        'maxHeight': '78vh',
                        'border': '1px solid #ddd', 'borderRadius': '4px',
                    }),
                ]),
            ], style={'width': '70%', 'verticalAlign': 'top'}),

        ], style={'display': 'flex'}),

    ], style={
        'paddingTop': '112px',
        'padding': '112px 5% 32px',
        'fontFamily': 'Inter, sans-serif',
    }),
])

# ─── Callback 1: Carica dati al primo render ──────────────────────────────────
@app.callback(
    Output('rend-prices-data', 'data'),
    Output('rend-stock-data', 'data'),
    Output('rend-data-info', 'children'),
    Input('rend-init', 'n_intervals'),
    prevent_initial_call=False,
)
def load_default_data(_):
    prices, returns, saved_at = _read_shared_data()
    if prices is None or returns is None:
        return None, None, html.Span(
            'Nessun dato disponibile. Vai su Analisi di Portafoglio per caricare i dati.',
            style={'color': '#c0392b'},
        )
    n_assets = len(prices.columns)
    last_date = prices.index[-1].strftime('%d/%m/%Y') if not prices.empty else 'N/D'
    info_parts = [
        html.I(className='fa-solid fa-circle-info', style={'marginRight': '6px', 'color': '#1a3a6b'}),
        f'{n_assets} asset · dati al {last_date}',
    ]
    if saved_at:
        info_parts.append(f' · aggiornati il {saved_at}')
    info_parts.append(html.Span(
        ' — Seleziona gli asset, configura i pesi P1/P2/P3 e clicca Aggiorna Tabella',
        style={'color': '#888'},
    ))
    return _to_json(prices), _to_json(returns), html.Span(info_parts)


# ─── Callback 2: Costruisce la griglia asset ──────────────────────────────────
@app.callback(
    Output('rend-asset-grid', 'children'),
    Output('rend-ir-bench', 'options'),
    Input('rend-stock-data', 'data'),
    State('rend-selected', 'data'),
    State('rend-weights-p1', 'data'),
    State('rend-weights-p2', 'data'),
    State('rend-weights-p3', 'data'),
)
def build_asset_grid(stock_json, selected, p1, p2, p3):
    if not stock_json:
        return html.Div('Caricamento dati in corso…',
                        style={'padding': '12px', 'color': '#888', 'fontSize': '12px'}), []
    try:
        returns = _get_df(stock_json)
    except Exception:
        return html.Div('Errore nel caricamento dei dati.',
                        style={'padding': '12px', 'color': '#c0392b', 'fontSize': '12px'}), []

    asset_names = list(returns.columns)
    selected = selected or []
    p1 = p1 or {}
    p2 = p2 or {}
    p3 = p3 or {}

    def has_weights(w):
        return bool(w and any(v and v > 0 for v in w.values()))

    defined = [f'P{i}' for i, w in enumerate([p1, p2, p3], 1) if has_weights(w)]
    if defined:
        badge = html.Div(
            f'Portafogli con pesi: {", ".join(defined)}',
            style={'fontSize': '10px', 'color': '#1b5e20', 'background': '#e8f5e9',
                   'border': '1px solid #81c784', 'borderRadius': '3px',
                   'padding': '4px 8px', 'marginBottom': '6px'},
        )
    else:
        badge = html.Div(
            'Inserisci pesi P1/P2/P3 e clicca Aggiorna',
            style={'fontSize': '10px', 'color': '#e65100', 'background': '#fff3e0',
                   'border': '1px solid #ffb74d', 'borderRadius': '3px',
                   'padding': '4px 8px', 'marginBottom': '6px'},
        )

    header = html.Div([
        html.Div('Asset', style={'width': '35%', 'fontWeight': 'bold',
                                  'paddingLeft': '5px', 'fontSize': '10px'}),
        html.Div(
            html.Button('Deseleziona', id='rend-deselect-btn', n_clicks=0,
                        style={'fontSize': '8px', 'padding': '2px 4px', 'width': '95%'}),
            style={'width': '10%', 'textAlign': 'center'},
        ),
        html.Div('P1', style={'width': '15%', 'textAlign': 'center',
                               'fontWeight': 'bold', 'fontSize': '10px'}),
        html.Div('P2', style={'width': '15%', 'textAlign': 'center',
                               'fontWeight': 'bold', 'fontSize': '10px'}),
        html.Div('P3', style={'width': '15%', 'textAlign': 'center',
                               'fontWeight': 'bold', 'fontSize': '10px'}),
    ], style={'display': 'flex', 'marginBottom': '4px',
              'borderBottom': '2px solid #ccc', 'paddingBottom': '4px'})

    sub_header = html.Div([
        html.Div('', style={'width': '35%'}),
        html.Div('Sel', style={'width': '10%', 'textAlign': 'center',
                                'fontWeight': 'bold', 'fontSize': '9px'}),
        html.Div('%', style={'width': '15%', 'textAlign': 'center', 'fontSize': '9px'}),
        html.Div('%', style={'width': '15%', 'textAlign': 'center', 'fontSize': '9px'}),
        html.Div('%', style={'width': '15%', 'textAlign': 'center', 'fontSize': '9px'}),
    ], style={'display': 'flex', 'marginBottom': '3px', 'borderBottom': '1px solid #eee'})

    rows = [badge, header, sub_header]

    for asset in asset_names:
        asset_val = [asset] if asset in selected else []

        def make_weight(p_idx, a=asset):
            val = {1: p1, 2: p2, 3: p3}[p_idx].get(a, 0)
            return dcc.Input(
                id={'type': 'rend-weight', 'index': f'P{p_idx}-{a}'},
                type='number', value=val, min=0, max=100, step=0.1, placeholder='0',
                style={'width': '90%', 'textAlign': 'right', 'fontSize': '9px',
                       'marginBottom': '2px'},
            )

        row = html.Div([
            html.Div(html.B(asset), style={
                'width': '35%', 'height': '28px', 'display': 'flex',
                'alignItems': 'center', 'paddingLeft': '5px',
                'fontSize': '10px', 'overflow': 'hidden', 'whiteSpace': 'nowrap',
            }),
            html.Div(
                dcc.Checklist(
                    id={'type': 'rend-check', 'index': asset},
                    options=[{'label': '', 'value': asset}],
                    value=asset_val,
                    style={'fontSize': '10px', 'justifyContent': 'center'},
                ),
                style={'width': '10%', 'height': '28px', 'display': 'flex',
                       'alignItems': 'center', 'justifyContent': 'center'},
            ),
            html.Div(make_weight(1), style={'width': '15%'}),
            html.Div(make_weight(2), style={'width': '15%'}),
            html.Div(make_weight(3), style={'width': '15%'}),
        ], style={'display': 'flex', 'borderBottom': '1px dotted #eee'})
        rows.append(row)

    # Righe portafogli (read-only, mostra totale pesi)
    for p_num in [1, 2, 3]:
        p_name = f'Port{p_num}'
        port_val = [p_name] if p_name in selected else []
        w_dict = {1: p1, 2: p2, 3: p3}[p_num]
        total_w = sum(v for v in w_dict.values() if v and v > 0) if w_dict else 0
        t_color = '#2ca02c' if 99 <= total_w <= 101 else ('#d62728' if total_w > 0 else '#aaa')
        t_label = f'{total_w:.0f}%'

        port_row = html.Div([
            html.Div(html.B(p_name, style={'color': '#0066cc'}), style={
                'width': '35%', 'height': '28px', 'display': 'flex',
                'alignItems': 'center', 'paddingLeft': '5px', 'fontSize': '10px',
            }),
            html.Div(
                dcc.Checklist(
                    id={'type': 'rend-check', 'index': p_name},
                    options=[{'label': '', 'value': p_name}],
                    value=port_val,
                    style={'fontSize': '10px', 'justifyContent': 'center'},
                ),
                style={'width': '10%', 'height': '28px', 'display': 'flex',
                       'alignItems': 'center', 'justifyContent': 'center'},
            ),
            html.Div(html.Span(t_label, style={'color': t_color, 'fontWeight': 'bold',
                                               'fontSize': '10px'}),
                     style={'width': '15%', 'display': 'flex', 'alignItems': 'center',
                            'justifyContent': 'center'}),
            html.Div('', style={'width': '15%'}),
            html.Div('', style={'width': '15%'}),
        ], style={'display': 'flex', 'borderBottom': '1px dotted #eee',
                  'backgroundColor': '#eef4ff'})
        rows.append(port_row)

    # Opzioni benchmark: asset + portafogli configurati
    bench_options = [{'label': a, 'value': a} for a in asset_names]
    for p_num, w_dict in [(1, p1), (2, p2), (3, p3)]:
        if w_dict and any(v and v > 0 for v in w_dict.values()):
            bench_options.append({'label': f'Port{p_num}', 'value': f'Port{p_num}'})

    return rows, bench_options


# ─── Callback 3: Raccoglie asset selezionati ──────────────────────────────────
@app.callback(
    Output('rend-selected', 'data'),
    Input({'type': 'rend-check', 'index': ALL}, 'value'),
    prevent_initial_call=True,
)
def collect_selected(all_values):
    return [v[0] for v in all_values if v]


# ─── Callback 4: Aggiorna pesi ────────────────────────────────────────────────
@app.callback(
    Output('rend-weights-p1', 'data'),
    Output('rend-weights-p2', 'data'),
    Output('rend-weights-p3', 'data'),
    Input({'type': 'rend-weight', 'index': ALL}, 'value'),
    State({'type': 'rend-weight', 'index': ALL}, 'id'),
    State('rend-weights-p1', 'data'),
    State('rend-weights-p2', 'data'),
    State('rend-weights-p3', 'data'),
    prevent_initial_call=True,
)
def update_weights(all_values, all_ids, p1, p2, p3):
    p1 = p1 or {}
    p2 = p2 or {}
    p3 = p3 or {}
    for inp_id, val in zip(all_ids, all_values):
        if isinstance(inp_id, dict) and inp_id.get('type') == 'rend-weight':
            parts = inp_id['index'].split('-', 1)
            if len(parts) == 2:
                port_id, asset = parts
                w = val if val is not None else 0
                if port_id == 'P1':
                    p1[asset] = w
                elif port_id == 'P2':
                    p2[asset] = w
                elif port_id == 'P3':
                    p3[asset] = w
    return p1, p2, p3


# ─── Callback 5: Totali pesi ─────────────────────────────────────────────────
@app.callback(
    Output('rend-sum-p1', 'children'),
    Output('rend-sum-p1', 'style'),
    Output('rend-sum-p2', 'children'),
    Output('rend-sum-p2', 'style'),
    Output('rend-sum-p3', 'children'),
    Output('rend-sum-p3', 'style'),
    Input('rend-weights-p1', 'data'),
    Input('rend-weights-p2', 'data'),
    Input('rend-weights-p3', 'data'),
)
def update_weight_sums(p1, p2, p3):
    base = {'width': '15%', 'textAlign': 'center', 'fontSize': '10px', 'fontWeight': 'bold'}
    def fmt(w_dict):
        total = sum(w_dict.values()) if w_dict else 0
        color = '#2ca02c' if 99 <= total <= 101 else '#d62728'
        return f'{total:.1f}%', {**base, 'color': color}
    t1, s1 = fmt(p1)
    t2, s2 = fmt(p2)
    t3, s3 = fmt(p3)
    return t1, s1, t2, s2, t3, s3


# ─── Callback 6: Deseleziona / seleziona tutti ───────────────────────────────
@app.callback(
    Output({'type': 'rend-check', 'index': ALL}, 'value'),
    Output('rend-deselect-btn', 'children'),
    Input('rend-deselect-btn', 'n_clicks'),
    State({'type': 'rend-check', 'index': ALL}, 'value'),
    State({'type': 'rend-check', 'index': ALL}, 'options'),
    prevent_initial_call=True,
)
def toggle_all_checks(n, current_values, all_options):
    if not all_options:
        return [], 'Deseleziona'
    if any(v for v in current_values):
        return [[] for _ in all_options], 'Seleziona'
    return [[opts[0]['value']] if opts else [] for opts in all_options], 'Deseleziona'


# ─── Callback 7: Relay stores IR e RF ────────────────────────────────────────
@app.callback(
    Output('rend-ir-bench-store', 'data'),
    Input('rend-ir-bench', 'value'),
    prevent_initial_call=True,
)
def relay_ir_bench(val):
    return val

@app.callback(
    Output('rend-rf-store', 'data'),
    Input('rend-rf-input', 'value'),
    prevent_initial_call=True,
)
def relay_rf(val):
    return val if val is not None else 0.0


# ─── Callback 8: Calcola dati performance ────────────────────────────────────
@app.callback(
    Output('rend-perf-data', 'data'),
    Input('rend-update-btn', 'n_clicks'),
    State('rend-selected', 'data'),
    State('rend-stock-data', 'data'),
    State('rend-prices-data', 'data'),
    State('rend-weights-p1', 'data'),
    State('rend-weights-p2', 'data'),
    State('rend-weights-p3', 'data'),
    State('rend-ir-bench-store', 'data'),
    State('rend-rf-store', 'data'),
    prevent_initial_call=True,
)
def compute_performance(n_clicks, selected_items, stock_json, prices_json,
                        w_p1, w_p2, w_p3, ir_benchmark_name, annual_rf_pct):
    if not stock_json or not prices_json or not selected_items:
        raise PreventUpdate

    try:
        close_returns   = _get_df(stock_json)
        original_prices = _get_df(prices_json)
    except Exception as e:
        print(f'[rendimenti] compute_performance error: {e}')
        raise PreventUpdate

    # Costruisce prezzi portafogli
    portfolio_prices  = {}
    portfolio_returns = {}

    for p_num, w_dict in [(1, w_p1), (2, w_p2), (3, w_p3)]:
        p_name = f'Port{p_num}'
        if not w_dict or not any(v and v > 0 for v in w_dict.values()):
            continue
        normalized = {
            asset: w / 100.0
            for asset, w in w_dict.items()
            if w and w > 0 and asset in close_returns.columns
        }
        if not normalized:
            continue
        port_ret = pd.Series(0.0, index=close_returns.index)
        for asset, w in normalized.items():
            port_ret += close_returns[asset].fillna(0) * w
        valid_idx = close_returns[list(normalized.keys())].replace(0, np.nan).dropna(how='any').index
        if valid_idx.empty:
            continue
        port_ret    = port_ret.loc[valid_idx.min():]
        port_prices = (1 + port_ret).cumprod() * 100
        portfolio_prices[p_name]  = port_prices
        portfolio_returns[p_name] = port_prices.pct_change().dropna()

    # Benchmark IR
    bmark_ret_raw = None
    if ir_benchmark_name:
        if ir_benchmark_name in portfolio_returns:
            bmark_ret_raw = portfolio_returns[ir_benchmark_name]
        elif ir_benchmark_name in close_returns.columns:
            bmark_ret_raw = close_returns[ir_benchmark_name]

    annual_rf_pct = annual_rf_pct or 0.0

    bmark_arr = np.asarray(bmark_ret_raw.dropna(), dtype=float) if bmark_ret_raw is not None else None

    ret_periods = [
        ('YTD', None), ('2025', 2025), ('2024', 2024), ('2023', 2023),
        ('T-30', 30), ('T-60', 60), ('T-90', 90), ('T-180', 180),
        ('T-250', 250), ('T-500', 500), ('T-750', 750),
    ]
    ir_periods = [('IR-30', 30), ('IR-60', 60), ('IR-100', 100), ('IR-250', 250)]
    sr_periods = [('SR-30', 30), ('SR-60', 60), ('SR-100', 100), ('SR-250', 250)]
    all_periods = ret_periods + ir_periods + sr_periods

    rows_data = []
    for item in (selected_items or []):
        is_portfolio = item.startswith('Port')

        if is_portfolio:
            prices_series = portfolio_prices.get(item)
            asset_ret_s   = portfolio_returns.get(item)
            if prices_series is None:
                row = {'name': item, 'is_portfolio': True}
                for p, _ in all_periods:
                    row[p] = None
                rows_data.append(row)
                continue
        else:
            if item not in original_prices.columns:
                continue
            prices_series = original_prices[item].dropna()
            if prices_series.empty:
                continue
            asset_ret_s = close_returns[item] if item in close_returns.columns else None

        asset_arr = np.asarray(asset_ret_s.dropna(), dtype=float) if asset_ret_s is not None else None

        if asset_arr is not None and bmark_arr is not None:
            min_len = min(len(asset_arr), len(bmark_arr))
            asset_aligned = asset_arr[-min_len:]
            bmark_aligned = bmark_arr[-min_len:]
        else:
            asset_aligned = asset_arr
            bmark_aligned = bmark_arr

        row = {'name': item, 'is_portfolio': is_portfolio}

        for period_name, val in ret_periods:
            if period_name == 'YTD':
                row[period_name] = calculate_ytd_return(prices_series)
            elif isinstance(val, int) and val > 1000:
                row[period_name] = calculate_year_return(prices_series, val)
            else:
                row[period_name] = calculate_return_for_period(prices_series, val)

        for period_name, days in ir_periods:
            row[period_name] = calculate_ir_for_period(asset_aligned, bmark_aligned, days) \
                               if asset_aligned is not None else None

        for period_name, days in sr_periods:
            row[period_name] = calculate_sharpe_for_period(asset_arr, days, annual_rf_pct) \
                               if asset_arr is not None else None

        rows_data.append(row)

    last_date = (original_prices.index[-1].strftime('%d/%m/%Y')
                 if not original_prices.empty else 'N/D')

    return {'rows': rows_data, 'last_date': last_date}


# ─── Callback 9: Aggiorna stato ordinamento ──────────────────────────────────
@app.callback(
    Output('rend-sort-state', 'data'),
    Input({'type': 'rend-col-header', 'index': ALL}, 'n_clicks'),
    State('rend-sort-state', 'data'),
    prevent_initial_call=True,
)
def update_sort_state(n_clicks_list, current_sort):
    ctx = callback_context
    if not ctx.triggered or not ctx.triggered[0]['value']:
        raise PreventUpdate
    try:
        id_dict = json.loads(ctx.triggered[0]['prop_id'].split('.')[0])
        clicked_col = id_dict['index']
    except Exception:
        raise PreventUpdate
    if current_sort and current_sort.get('col') == clicked_col:
        new_dir = 'asc' if current_sort.get('direction') == 'desc' else 'desc'
    else:
        new_dir = 'desc'
    return {'col': clicked_col, 'direction': new_dir}


# ─── Callback 10: Renderizza la tabella ──────────────────────────────────────
@app.callback(
    Output('rend-perf-table', 'children'),
    Input('rend-perf-data', 'data'),
    Input('rend-sort-state', 'data'),
    prevent_initial_call=True,
)
def render_table(perf_data, sort_state):
    if not perf_data or not perf_data.get('rows'):
        return html.Div(
            'Seleziona gli asset, configura i pesi e clicca "Aggiorna Tabella".',
            style={'padding': '20px', 'color': '#666', 'fontSize': '13px'},
        )

    rows_data = perf_data['rows']
    last_date = perf_data.get('last_date', 'N/D')

    ret_cols = ['YTD', '2025', '2024', '2023', 'T-30', 'T-60', 'T-90', 'T-180', 'T-250', 'T-500', 'T-750']
    ir_cols  = ['IR-30', 'IR-60', 'IR-100', 'IR-250']
    sr_cols  = ['SR-30', 'SR-60', 'SR-100', 'SR-250']
    periods  = ret_cols + ir_cols + sr_cols

    sort_col = sort_state.get('col') if sort_state else None
    sort_dir = sort_state.get('direction', 'desc') if sort_state else 'desc'

    if sort_col and sort_col in periods:
        def sort_key(row):
            val = row.get(sort_col)
            if val is None or (isinstance(val, float) and np.isnan(val)):
                return -float('inf') if sort_dir == 'desc' else float('inf')
            return val
        rows_data = sorted(rows_data, key=sort_key, reverse=(sort_dir == 'desc'))

    def make_th(label, col_id=None):
        is_active = sort_col == col_id if col_id else False
        arrow = (' ▼' if sort_dir == 'desc' else ' ▲') if is_active else (' ↕' if col_id else '')
        th_style = {
            'padding': '0px',
            'backgroundColor': '#0d2a4a' if is_active else '#1a3a5c',
            'color': 'white', 'position': 'sticky', 'top': '0',
            'zIndex': '2', 'minWidth': '90px',
            'border': '1px solid #0d2540', 'whiteSpace': 'nowrap',
        }
        if col_id is None:
            th_style['textAlign'] = 'left'
            th_style['minWidth']  = '160px'

        if col_id is not None:
            btn_style = {
                'background': 'none', 'border': 'none',
                'color': '#ffd700' if is_active else 'white',
                'cursor': 'pointer', 'fontWeight': 'bold' if is_active else 'normal',
                'fontSize': '12px', 'padding': '10px 14px', 'width': '100%',
                'textAlign': 'right', 'userSelect': 'none', 'whiteSpace': 'nowrap',
            }
            return html.Th(
                html.Button(f'{label}{arrow}',
                            id={'type': 'rend-col-header', 'index': col_id},
                            n_clicks=0, style=btn_style),
                style=th_style,
            )
        return html.Th(label, style={**th_style, 'padding': '10px 14px'})

    header_cells = [make_th('Asset / Portafoglio')]
    for p in ret_cols:
        header_cells.append(make_th(p, col_id=p))
    for p in ir_cols:
        th = make_th(p, col_id=p)
        if sort_col != p:
            th.style['backgroundColor'] = '#4a1a7c'
        header_cells.append(th)
    for p in sr_cols:
        th = make_th(p, col_id=p)
        if sort_col != p:
            th.style['backgroundColor'] = '#7a5c00'
        header_cells.append(th)

    table_rows = [html.Tr(header_cells)]

    for row_idx, row in enumerate(rows_data):
        item = row['name']
        is_portfolio = row['is_portfolio']
        row_bg = '#dce8ff' if is_portfolio else ('#ffffff' if row_idx % 2 == 0 else '#f8f8f8')

        name_style = {
            'padding': '8px 10px', 'fontSize': '11px',
            'fontWeight': 'bold' if is_portfolio else 'normal',
            'color': '#0066cc' if is_portfolio else '#222',
            'backgroundColor': row_bg,
            'position': 'sticky', 'left': '0', 'zIndex': '1',
            'border': '1px solid #ddd', 'whiteSpace': 'nowrap', 'minWidth': '160px',
        }
        cells = [html.Td(item, style=name_style)]

        for period in ret_cols:
            val = row.get(period)
            cs = get_cell_style(val, is_portfolio=is_portfolio)
            if sort_col == period and val is not None and not (isinstance(val, float) and np.isnan(val)):
                cs['backgroundColor'] = '#d0edce' if val >= 0 else '#ffdde0'
            cells.append(html.Td(format_return(val), style=cs))

        for period in ir_cols:
            val = row.get(period)
            if val is None or (isinstance(val, float) and np.isnan(val)):
                txt = 'N/D'
                cs  = {'padding': '8px 10px', 'textAlign': 'right', 'fontSize': '12px',
                       'whiteSpace': 'nowrap', 'border': '1px solid #ddd',
                       'backgroundColor': '#f3eeff', 'color': '#aaa'}
            else:
                txt = f'{val:+.2f}'
                cs  = {'padding': '8px 10px', 'textAlign': 'right', 'fontSize': '12px',
                       'whiteSpace': 'nowrap', 'border': '1px solid #ddd',
                       'fontWeight': 'bold' if is_portfolio else 'normal',
                       'backgroundColor': '#ede7f6' if val >= 0 else '#fce4ec',
                       'color': '#4a148c' if val >= 0 else '#880e4f'}
            if sort_col == period and val is not None and not (isinstance(val, float) and np.isnan(val)):
                cs['backgroundColor'] = '#d1c4e9' if val >= 0 else '#f8bbd0'
            cells.append(html.Td(txt, style=cs))

        for period in sr_cols:
            val = row.get(period)
            if val is None or (isinstance(val, float) and np.isnan(val)):
                txt = 'N/D'
                cs  = {'padding': '8px 10px', 'textAlign': 'right', 'fontSize': '12px',
                       'whiteSpace': 'nowrap', 'border': '1px solid #ddd',
                       'backgroundColor': '#fdf8e6', 'color': '#aaa'}
            else:
                txt = f'{val:+.2f}'
                cs  = {'padding': '8px 10px', 'textAlign': 'right', 'fontSize': '12px',
                       'whiteSpace': 'nowrap', 'border': '1px solid #ddd',
                       'fontWeight': 'bold' if is_portfolio else 'normal',
                       'backgroundColor': '#fff9e6' if val >= 0 else '#fff0e6',
                       'color': '#7a5c00' if val >= 0 else '#a03000'}
            if sort_col == period and val is not None and not (isinstance(val, float) and np.isnan(val)):
                cs['backgroundColor'] = '#ffe082' if val >= 0 else '#ffccbc'
            cells.append(html.Td(txt, style=cs))

        table_rows.append(html.Tr(cells, style={'backgroundColor': row_bg}))

    table = html.Table(table_rows, style={
        'borderCollapse': 'collapse', 'width': '100%',
        'fontFamily': 'Arial, sans-serif',
    })

    info_bar = html.Div([
        html.Span(f'Dati al: {last_date}',
                  style={'fontSize': '11px', 'color': '#555', 'marginRight': '20px'}),
        html.Span('Verde = positivo · Rosso = negativo · Clicca intestazione per ordinare',
                  style={'fontSize': '11px', 'color': '#555'}),
    ], style={'padding': '8px 12px', 'backgroundColor': '#f0f4fa',
              'borderBottom': '1px solid #ddd'})

    return html.Div([info_bar, table])
