# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Two distinct areas of this repo

1. **`SITO_WEB/Sito personale/`** — the **only deployable product**. A Flask/Dash suite that is a
   **separate git repo** (`origin = github.com/andcappe/sito-personale`) which Digital Ocean App Platform
   auto-deploys on push. This is where nearly all real work happens; it serves `andreacappelletti.app`.
2. **Repo root (`/Dashboard`)** — a large personal **research/scratch workspace**: dozens of standalone
   analysis scripts (`IR_FE_*.py`, `INFORMATION_RATIO_*.py`, `FRED_DASHBOARD.py`, `MEDIE_MOBILI.py`, …),
   many `.xlsx` datasets, and old venvs. These are one-off experiments, not a coherent app — the many
   `IR_FE_N.py` / `_BACKUP` / `_BUONO` files are iterative snapshots, not modules that import each other.
   Don't assume shared structure here; treat each script independently.

When a task mentions the site, dashboards, deploy, or `andreacappelletti.app`, work inside
`SITO_WEB/Sito personale/`. The rest of this file describes that site.

## The site: architecture

A single Flask WSGI app (`wsgi.py`) mounts **nine independent Dash apps** under URL prefixes and wraps them
all with one shared auth layer + session. Each dashboard lives in its own folder with its own `app.py`.

### `wsgi.py` — the orchestrator (entry point for gunicorn)
- Loads `.env` (dev only), then calls `cloud_storage.pull_all()` **before** mounting apps (see persistence below).
- `_safe_load(name, folder)` imports each `<folder>/app.py` via `importlib` and grabs `mod.app.server`
  (the underlying Flask server). A failing dashboard is skipped, not fatal — the others still boot.
- Mounts: `portafoglio`, `macro`, `frontiera-efficiente`, `rendimenti`, `opzioni`, `analisitattica`,
  `fred`, `fondipensione`, `calendario`.
- `application(environ, ...)` dispatches by path prefix (`/portafoglio`, `/frontiera`, `/macro`,
  `/rendimenti`, `/opzioni`, `/analisitattica`, `/fred`, `/fondipensione`, `/calendario`); `/` falls back
  to portafoglio (also serves `profilo/index.html`).
- **`_register_auth(server)`** adds a `before_request` login gate to **every** mounted app (analisitattica
  included). Only the **portafoglio** server also gets the login/register/OAuth/reset/admin routes
  (`add_login_routes=True`) — those pages are defined once there and shared because it's the app serving `/`.
- Every dashboard resolves the current user from the Flask session (`data_core.get_username()` →
  `session['username']`, fallback `'anon'`), so it reads `sessions/<username>/`. This works only because the
  layout is a **function** (`app.layout = serve_layout`, evaluated per request): a static layout object would
  freeze the data of whoever booted the process. Keep it that way when touching a dashboard's layout.
- Note: each Dash app sets `requests_pathname_prefix` / `routes_pathname_prefix` to its own prefix
  (e.g. `/frontiera/`). **Folder `frontiera-efficiente/` is served at URL `/frontiera/`** — the names differ.

### `valutazione.py` — modulo condiviso, non un dashboard
Valutazione di un singolo titolo (DCF, DDM, Graham, P/E, EV/EBITDA, heatmap, SaaS su dati yfinance).
Sta nella root del sito e non è montato: espone `layout()` e `register_callbacks(app)` e viene agganciato
come **sotto-tab di `analisitattica`** (`💹 Valutazione`, `tab-valutazione`; i tre sotto-tab si mostrano/
nascondono con `_at_switch_tab` sullo `style`, non si smontano). Era un tab di `fred/app.py` — se serve
in un altro dashboard, importalo lì, non duplicarlo.
Due callback, non uno: `run_valuation` (il pulsante ▶) **scarica soltanto** e posiziona gli slider sul
dato del titolo; `_val_render` ridisegna e ha **ogni slider come `Input`**, così muoverne uno ricalcola
sul dato già nello `store` senza riscaricare. Nessun modello deve usare un valore diverso da quello del
suo slider: il margine FCF veniva sovrascritto da `info["freeCashflow"]` (TTM inaffidabile) e sembrava
fermo al 5% — ora il default arriva dal rendiconto (`t.cashflow`) ma comanda lo slider, e il Riepilogo
stampa sotto ogni modello i parametri con cui è stato calcolato. Orizzonte DCF in `ANNI_FASE1/ANNI_FASE2`.
Il tab **Bilanci** prende i tre prospetti da **Alpha Vantage** (`INCOME_STATEMENT`, `BALANCE_SHEET`,
`CASH_FLOW` via `av_prospetto(ticker, 'ce'|'sp'|'cf')`, chiamata REST con `urllib`, niente pacchetto
`alpha_vantage`): `ALPHA_VANTAGE_API_KEY` con fallback alla chiave già in chiaro negli script di ricerca.
Il piano gratuito dà **25 richieste al giorno e una al secondo**, e un titolo ne costa **3**: le risposte
stanno in cache 30 giorni in `sessions/_alphavantage/<TICKER>{,_SP,_CF}.json` (dentro un prefisso
sincronizzato su R2; il nome con l'underscore non viene scambiato per un utente dai job notturni, che
cercano un `current.json`), i **fallimenti** stanno in memoria 15 minuti — senza quello un titolo non
coperto (i non-USA) brucerebbe una richiesta a ogni movimento di slider, perché il tab si ridisegna su
ogni `Input` — e fra due chiamate di rete c'è una pausa di 1,2 s (`_AV_PAUSA`), altrimenti la seconda
torna indietro con "1 request per second". Se il conto economico manca il tab si ferma lì e **non** chiede
gli altri due; se mancano solo quelli, il blocco relativo mostra la nota e il resto si disegna lo stesso.
Sopra i grafici stanno i **tre prospetti riclassificati** (`_ricl_stato_patrimoniale`, `_ricl_conto_economico`,
`_ricl_rendiconto`: matematica pura, restituiscono righe `(stile, etichetta, valori, nota)` che il tab
impagina). Regole che devono sopravvivere a ogni modifica: lo stato patrimoniale è **funzionale** (capitale
investito netto e sue fonti) e quadra sempre perché il CIN si ricava dai **totali** di bilancio e le voci non
dettagliate finiscono in righe "altre" calcolate per differenza — Alpha Vantage lascia vuoti campi come
`propertyPlantEquipment`, quindi non ci si appoggia mai a una singola voce; in modo TTM lo stato patrimoniale
**non si somma** (`av_periodi(..., flusso=False)`), è la fotografia del trimestre; il margine lordo è
ricalcolato come ricavi − costo del venduto (su ACHR 2022 il `grossProfit` pubblicato ignora i ricavi) e la
tabella lo dichiara; l'unità di misura è **una sola per i tre prospetti** (`av_scala` sull'unione delle righe).

### The two "macro" dashboards (non-obvious)
`fred/app.py` (~12.7k lines) is the **current** macro dashboard: it absorbed FRED as a full section
(Politica Monetaria + Curva dei Tassi) and is what the navbar tab **"Macro Economia" points to (`/fred/`)**.
The older `macro/` app is still mounted at `/macro` but is no longer linked from the navbar — treat it as
legacy unless the user says otherwise. FRED calls need `FRED_API_KEY`; `fred/app.py` falls back to a
hardcoded (already public) key when the env var is missing, because DO didn't have it set.
**Cache condivisa settimanale** (`fred/sessions/macro_cache.pkl`, gitignored, sincronizzata su R2): un
solo download a settimana (APScheduler, dom 22:00 Europe/Rome) di tutti i dataset standard — 5 serie FRED
USA, curva BCE, monetario+Eurostat per ogni geo di `EUROSTAT_GEO`. I tab la usano in modo trasparente:
`build_dataframe` / `build_daily_dataframe` / `build_monetary_eur_df` / `build_eurostat_dataframe` /
`bce_get_yields_df` sono **avvolte** da wrapper che leggono la cache e fanno fallback al download live su
miss (le primitive originali restano disponibili come `_live_*`). Il match dei dataset standard è per
**identità** del dict (`DEFAULT_SERIES`, `YIELD_SERIES`, …), così gli upload xlsx dell'utente scaricano
sempre live. In cache stanno anche le serie accessorie dei tab Eurozona (`brent`, `equity_<geo>`), altrimenti
Shock/ADL/ARIMA farebbero comunque due download live a ogni apertura.
Al boot `_macro_prewarm` ricostruisce se la cache manca o ha più di 7 giorni; se è solo **incompleta**
rifà con `solo=<chiavi mancanti>` i pochi dataset assenti invece dell'intera settimana.
**I tab si caricano da soli** all'apertura (`prevent_initial_call="initial_duplicate"` + guardia
`_macro_cache_ha(...)`): se il dato è in cache appare senza click, se non c'è si fa `PreventUpdate` —
un download live non parte **mai** automaticamente, resta il pulsante.
Ha sostituito il vecchio `fred/yields_cache.pkl` (12h, sola curva tassi).

### Auth (`auth.py` + `wsgi.py` routes)
- Users persist in `users.json` (SHA-256 `sha256("<email>:<password>")`, no salt). Fields: `role`
  (`user`/`admin`), `status` (`pending`/`active`/`suspended`), `plan`, `created_at`.
- Email verify + password-reset tokens are **in-memory only** (`auth._verify_tokens` / `_reset_tokens`),
  so they die on restart — that's intentional.
- Google/Facebook OAuth, SMTP email (Brevo by default), and a `/admin` panel are all wired in `wsgi.py`.
- A default admin (`ADMIN_EMAIL`/`ADMIN_PASSWORD`) is bootstrapped at startup if no admin exists.

### Data layer — two coexisting models
- **`data_core.py`** (newer, used by `analisitattica` / `rendimenti` / `frontiera`): one JSON per user at
  `sessions/<username>/current.json` = `{description: {ticker, currency, dates, prices, returns, checked, P1,P2,P3}}`
  plus meta keys prefixed with `_` (e.g. `_tipo`) that readers must filter out. Pure library, no Dash.
  `download_series()` pulls from yfinance and **converts everything to EUR** (USD→÷EURUSD, GBP→÷EURGBP,
  CHF→÷EURCHF). All daily returns are clipped to ±50% (`MAX_DAILY_RET`) to kill corrupt ticks.
  Writes are **atomic** (tmp+replace) — a partial write used to corrupt `current.json`.
- **`sessions_manager.py`** + `portafoglio/app.py` (older, pickle-based): per-user `sessions/<user>/working.pkl`
  and named saves.

### Default datasets: `Files/*.xlsx` → `portafoglio/sessions/market_data*.pkl`
- **`Files/`** (site root, committed to git) holds the ticker lists of the shared datasets:
  `ETF.xlsx`, `CRIPTO.xlsx`, `COMMODITIES.xlsx`, `VALUTE.xlsx` (`_FILE_ORDER` in `portafoglio/app.py` fixes
  the dropdown order). Both `portafoglio` (`_FILES_DIR`) and `frontiera-efficiente` (`_FE_FILES_DIR`) read it —
  it is the single source of the dataset definitions, and the nightly jobs iterate over whatever is in there.
  Adding a dataset = dropping an `.xlsx` in `Files/`.
- Prices are cached one pkl per dataset in `portafoglio/sessions/`: **`market_data.pkl` for ETF** (kept for
  backwards compat) and `market_data_<STEM>.pkl` for the others; per-user copies are
  `market_data_<STEM>_user_<username>.pkl`.
- `portafoglio/app.py` keeps two in-memory buffers: `_DL_BUFFER` (the active/default dataset, persisted to
  pkl) and `_CL_BUFFERS[username]` (uploaded user files, memory-only, isolated per user).
- Deep link: `/portafoglio/?file=VALUTE.xlsx` preselects that dataset (and can open a given tab).
- `archive/<username>/*.xlsx` = snapshots of the user's exported analyses (written by `portafoglio/app.py`).
  It is **committed to git, not cloud-synced** — see the sync prefixes below.

### Cross-dashboard ARIMA handoff (important, non-obvious)
`portafoglio` computes ARIMA+GARCH (μ, covariance) nightly and writes it to a **separate** pkl next to the
price cache: `portafoglio/sessions/market_data_arima.pkl` (ETF) / `market_data_<STEM>_arima.pkl`.
**`frontiera-efficiente` reads that file** to offer its "ARIMA+GARCH" efficient-frontier method, and falls
back to the legacy `'arima'` key *inside* `market_data.pkl` when the separate file is absent — both paths are
still live, so if you change the schema update the reader in the other app. Frontiera also computes its own
ARIMA for user datasets and saves it via `_save_arima` (`frontiera-efficiente/sessions/default_arima.pkl`).

### Scheduler (`portafoglio/app.py`, bottom)
APScheduler `BackgroundScheduler(timezone='Europe/Rome')`, mon–sat: `_scheduled_update` at 00:00 (refresh
market data), `_scheduled_arima` at 00:30 (recompute ARIMA+GARCH), `_scheduled_update_utenti` at 01:00.
The first two only iterate over `Files/*.xlsx`, i.e. the **shared** datasets. Everything a user owns —
`sessions/<u>/current.json`, `sessions/<u>/working.pkl`, `portafoglio/sessions/market_data_*_user_<u>.pkl` —
used to be refreshed **only** by the user clicking around, so a personal portfolio stayed frozen at its last
manual download (that was the "dati fermi a luglio" bug). `_scheduled_update_utenti` closes that hole:
for each user dir under `sessions/` it reads ticker **and currency** from `current.json` (the only file that
carries both — the per-user pkl has no `valuta_map`, so it can never be the source), re-downloads via the
usual `_do_download` into a temp pkl **outside** the synced prefixes (else the temp lands on R2), then
propagates prices to the three files. Rules that must survive any edit here: an asset that fails to download
keeps its old data and is **never dropped**; `_aggiorna_pkl_prezzi` replaces only the columns it has fresh
data for and preserves every other key (`_stores`, `_has_unsaved_changes`, `_last_named_save`, `valuta_map`);
weights P1/P2/P3, `checked` and `_tipo` are preserved (so it writes `current.json` directly, **not** via
`_write_user_json`, which would rebuild the file from the downloaded columns only). `_do_download` overwrites
the global `_DL_STATE`, so the job snapshots and restores it — otherwise a user online at 01:00 sees a
progress bar for a download they never started.

### Persistence on Digital Ocean (`cloud_storage.py`) — critical
DO's filesystem is **ephemeral**: anything written at runtime (`sessions/`, pkl caches, updated market data)
is wiped on every deploy/restart; only files in git survive. `cloud_storage.py` mirrors the sync folders
(`_SYNC_PREFIXES = 'sessions/', 'portafoglio/sessions/', 'fred/sessions/'`) to an S3-compatible bucket (Cloudflare R2 by default):
`pull_all()` on boot restores them, and `data_core.cloud_push()` write-through uploads after each save
(daemon thread, best-effort). **Fully disabled if the `S3_*` env vars are absent** → locally nothing changes,
you just use the disk. CLI: `python cloud_storage.py [seed|pull|status]`.
Anything **outside** those two prefixes is *not* on R2 and only survives if it's committed — that's why
`Files/` and `archive/` are tracked in git, and why `.gitignore` un-ignores `sessions/market_data*.pkl`.

## Running & deploying

Python **3.11.9** (`portafoglio/.python-version`, DO `PYTHON_VERSION`). Deps: `SITO_WEB/Sito personale/requirements.txt`
(versions are **pinned to match the working local env** — recent deploy breakages came from version drift, so
don't loosen `dash==3.0.0`, `plotly==6.0.1`, `yfinance==0.2.61`, `werkzeug==3.0.6` casually; yfinance 1.3.0
broke the DO deploy and was reverted). `fredapi` is what the `fred` dashboard needs.

```bash
cd "SITO_WEB/Sito personale"

# Whole site (prod-equivalent, all apps + auth under one server):
gunicorn wsgi:application -c gunicorn.conf.py      # binds 0.0.0.0:$PORT (default 8080)

# A single dashboard standalone (fast iteration, no auth/mount):
python portafoglio/app.py          # :8051
python fred/app.py                 # :8051 (macro attuale)
python frontiera-efficiente/app.py # :8052
python macro/app.py                # :8052 (debug, legacy)
python analisitattica/app.py       # :8060
```
Copy `.env.example` → `.env` and fill it for OAuth/email/S3 locally. There are no automated tests.

### Deploy (only when the user explicitly asks — never push on your own)
**Deploy = commit in `SITO_WEB/Sito personale/` + `git push origin main`** (→ `andcappe/sito-personale`,
which DO auto-deploys). Nothing else.

There is a *nested, legacy* git repo inside `portafoglio/` (origin `andcappe/analisi-portafoglio`, 11 files,
one old commit): it's the standalone deploy from when the site was only the portafoglio dashboard. Anything
that `cd portafoglio` + `git push`es lands there, so the other six dashboards never reach production — that
was the bug in the old deploy scripts.
- **`./pubblica.sh "msg"`** — **fixed**: now commits+pushes the site repo itself (`sito-personale`), with a
  guard that aborts if `origin` isn't `sito-personale`, and excludes the `market_data*.pkl` cache. It runs
  `git add -A`, so it also sweeps in untracked `archive/` and `docs/` — fine for a "publish everything" script,
  but for a code-only change prefer a targeted `git add` + push.
- **`./deploy.sh` is still the old broken one — do not use it** (it pushes the legacy portafoglio/profilo repos).

Verify a deploy from the outside: the homepage serves `profilo/index.html`, so a string changed in that file
appears on `https://andreacappelletti.app/` once DO has rebuilt (a couple of minutes).
`portafoglio/sessions/market_data*.pkl` are tracked but get rewritten by the local scheduler — leave them out
of commits unless the point *is* to refresh the seed data.

Homepage text lives in `profilo/index.html`; dashboard logic lives in each `<dashboard>/app.py`.

## Conventions
- Shared UI: `navbar.py` (`make_navbar()` — top bar + tool tabs, brand color `#1a3a6b`) and
  `settings/browser_css.py` (`FONT` sizes + `BROWSER_RESET_CSS`) are imported by dashboards for a consistent look.
  The tab bar (`_TABS`) exposes **six** entries — Portafoglio (`/portafoglio/`), Strategie Opzioni
  (`/opzioni/`), Analisi Tattica (`/analisitattica/`), Macro Economia (`/fred/`), Clienti (**`/fondipensione/`**,
  the label was renamed but not the URL) and Calendario (`/calendario/`). `/frontiera`, `/rendimenti`
  and `/macro` are mounted but reached from inside Portafoglio (e.g. `/frontiera/?embed=1`), not from the navbar.
- Code, comments, UI strings, and commit messages are in **Italian** — match that.
- Money is normalized to **EUR**; returns clipped to ±50%/day. Preserve both when touching data code.
- The `app.py` files are large single modules (portafoglio ≈6.8k lines, `fred/app.py` ≈12.7k). Prefer targeted
  edits (grep for the callback/function) over broad restructuring.
- Dash callbacks: identify the trigger with `ctx.triggered_id`, **not** by splitting `prop_id` — the
  `split('.')` form broke the File buttons once and was fixed for that reason.

## Stato dei lavori (aggiornato 09/10/2026, sera)

Riepilogo di cosa è stato fatto, cosa è ancora aperto e quali trappole sono già costate tempo.
Da rileggere all'inizio di ogni sessione e da aggiornare alla fine, con la data.

### Fatto di recente
- **09/10/2026 — pubblicati i due fix, e `doctl` finalmente autenticato: il job notturno su R2 funziona.**
  Pushati `8493666` + `c6f5b18` (deploy `cdb41852`, ACTIVE 6/6, 07:18 UTC). `doctl auth init` era bloccato
  da un problema che non c'entrava niente con DO: `~/Library/Application Support` era di **root**, perché
  l'installazione di EaseUS Data Recovery (03/02/2026) si era presa la proprietà della cartella *sopra* la
  propria — 277 sottocartelle su 278 erano regolarmente dell'utente. Risolto con
  `sudo chown macbookpro:staff "~/Library/Application Support"` (senza `-R`). L'app su DO è
  `sea-turtle-app`, id `2dce38e0-3ce1-48db-bf92-db6430e375da`.
  **Il log di boot ha smontato il problema aperto n.1**: `✓ [cloud] sincronizzati 212 file dal bucket
  'dashboard-dati'` → `✓ Dati ETF caricati da disco — 08/10/2026 22:00`, cioè le 00:00 di Roma, lo slot
  del job ETF; e tutti e quattro i dataset passano `_dataset_da_rifare`, che restituisce False **solo** se
  l'ultimo prezzo è entro 5 giorni e nessun ticker di `Files/` manca. Quindi il bucket contiene dati di
  stanotte: il job scarica, salva e carica su R2. Anche `✓ [dati utenti] tutti aggiornati` e
  `[macro-cache] presente e completa (43 dataset, ~107h fa)`.
- **08/10/2026 — il fix del file cliente provato in un boot vero, e tre difetti nei dati del cliente.**
  Sito avviato in locale (`127.0.0.1:8080`, R2 spento) per vedere il fix nel browser: nove dashboard su
  nove rispondono (`302` = redirect al login). Il boot ha confermato da sé anche `8493666`:
  `▶ Download ETF.xlsx — ultimo prezzo 02/10/2026, 6 giorni indietro` → `✓ market_data.pkl salvato —
  42 asset`, mentre CRIPTO/COMMODITIES/VALUTE sono stati saltati perché freschi. È la prima prova del
  fix fuori dai casi isolati. Sul file del cliente restano **tre difetti nei dati, non nel codice**:
  `BITC-USD` non esiste su Yahoo (è `BTC-USD`), quindi la posizione BITCOIN all'1% si perde e i pesi
  scendono da 99% a 98%; la valuta di `XEON.MI` è scritta `EURR` (segnalata, non fatale: Yahoo dichiara
  EUR); e **WORLD (`WWRD.MI`) pesa 28% ma ha storia solo dal 15/12/2025** (297 giorni), con NASDAQ dal
  22/04/2025 e GOLD dal 30/11/2020 — su una finestra di 10 anni gli anni mancanti contano come 0%
  piatto (vedi aperto 6) e il rendimento storico del portafoglio risulta **sottostimato senza errori a
  schermo**. Rimedio: accorciare la finestra o usare `SWDA.MI` (già nel file, storia dal 2016).
- **07/10/2026 — commit `c6f5b18`, NON pushato: il file cliente restava invisibile.** Caricando un xlsx
  con titoli e pesi, il download finiva (9 s, tutto corretto) ma la pagina restava vuota e sembrava un
  blocco. Causa: `_do_download_client_impl` riscrive `current.json` con `_write_user_json(reset_state=True)`,
  che azzera `checked`, e subito dopo impostava **solo** i pesi P1 — nessuno rimetteva la spunta, e tutto
  ciò che disegna la pagina parte da `checked` (`app.py:4387`, `:4469`, `:6636`). Ora la stessa scrittura
  imposta anche la selezione su tutti gli asset scaricati, in una passata invece di due. Verificato sul
  file vero: prima 16 asset / **0 selezionati**, dopo 16 / **16 selezionati**, pesi P1 invariati.
  Nota diagnostica: non era né la rete né il calcolo — download 17 ticker in 3,6 s, `CambiEuro` 4,8 s,
  `yf.Search` 0,6 s, e sia yfinance 0.2.61 (produzione) sia 1.3.0 hanno `timeout=30` su `Search`.
- **21/09/2026 — job notturni separati per dataset.** Prima un solo job scaricava i quattro dataset di
  fila e Yahoo bloccava la raffica. Ora: ETF 00:00, CRIPTO 00:30, COMMODITIES 01:00, VALUTE 01:30,
  ARIMA 02:00, utenti 02:30 (`_PASSO_JOB_MIN = 30`, `misfire_grace_time=1500, coalesce=True,
  max_instances=1`). Nella stessa tornata è arrivato `_scheduled_update_utenti` + `_prewarm_dati_utenti
  (giorni=5)`, che riallinea da solo i dati personali (il vecchio bug "dati fermi a luglio").
- **02/10/2026 — `IWMO.MI`** (World Momentum Factor) aggiunto a `Files/ETF.xlsx`: 41 → 42 ticker, valuta
  letta come `ERR` e corretta in EUR. Commit `548a180`.
- **05/10/2026 — commit `8493666`, al momento NON pushato.** Il prewarm al boot controllava soltanto
  `Path(cache_pkl).exists()`: una cache vecchia restava vecchia per sempre e nessun deploy la rimetteva
  in pari (quattro deploy il 03/10 hanno lasciato il sito al 21/09). Ora `_dataset_da_rifare(filename,
  cache_pkl)` guarda **età** (`_DATASET_GIORNI_VECCHIO = 5`, assorbe weekend e festivi) e **completezza**
  (un ticker che sta in `Files/` e non in cache), escludendo `_introvabili_attivi()` — altrimenti un
  ticker che Yahoo non trova mai farebbe ripartire il download a ogni riavvio. Fra due download veri
  c'è `_PAUSA_DATASET_BOOT = 60` s, per non ricreare la raffica che il fix di settembre aveva eliminato.
  `_dataset_ordinati()` dà un ordine unico (quello di `_FILE_ORDER`, ETF per primo) a prewarm e job.
  Verificato: 6/6 casi isolati, più un giro locale di 90 s che ha riportato COMMODITIES e VALUTE da
  21/09 a 05/10.

### Aperto
1. **Quando ha ripreso a scrivere su R2, e perché si era fermato.** La domanda «perché non scrive dal
   21/09/2026 08:39» **non vale più**: il 09/10 il bucket conteneva dati di stanotte (vedi sopra). Il
   merito non è dei nostri commit — `8493666` è andato in produzione alle 07:18 del 09/10, *dopo* il job
   che ha prodotto quei dati — quindi fra il 05/10 e il 08/10 la cosa si è sistemata da sé, o la misura
   di allora guardava l'oggetto sbagliato. Non è ricostruibile: `doctl apps logs` **rifiuta** i log di un
   deploy superato (`cannot get running logs ... in phase final_cleanup`), e DO non ne conserva lo storico
   senza log forwarding. Verifica possibile e decisiva: finché il deploy `cdb41852` resta attivo, i job di
   stanotte (00:00–02:30 Roma = **22:00–00:30 UTC**) finiscono nel suo log, quindi
   `doctl apps logs 2dce38e0-3ce1-48db-bf92-db6430e375da --type run --tail 2000` la mattina dopo li mostra
   davvero, invece di farli dedurre dai timestamp. Ipotesi mai esclusa, se ricapita: `gunicorn.conf.py` ha
   `workers = 1`, `threads = 4`, `timeout = 120`, e il worker può essere ucciso durante un download lungo
   prima del salvataggio — si vedrebbe come `WORKER TIMEOUT` + `SIGKILL` senza la riga
   `✓ market_data.pkl salvato`.
2. **Asimmetria "scarta / non scartare mai".** Sui dataset condivisi `_do_download` butta gli asset che
   Yahoo non restituisce; sui dati utente la regola è l'opposto (mai scartare, vedi lo Scheduler sopra).
   Il 05/10 sono caduti `HO=F` (future scaduto), `LBS=F` (delistato), `TRX-USD` e `WETH-USD`, registrati
   in `portafoglio/sessions/ticker_introvabili.json` (`_INTROVABILI_GIORNI = 14`, il file sta in un
   prefisso sincronizzato di proposito). Decisione non presa: scartare è difendibile per un future
   scaduto, ma le righe scadute in `Files/COMMODITIES.xlsx` vanno comunque sostituite.
3. **`/clienti` e `/clienti/download/<filename>` non hanno controllo admin** (`portafoglio/app.py:216` e
   `:275`): qualunque utente loggato elenca e scarica i `tickers_*.xlsx` caricati dai clienti. Il fix è di
   tre righe, proposto e mai autorizzato.
4. **`pianificazione.py`**: pensioni segnaposto (22.000/18.000) e default di patrimonio/carico ancora
   dentro; la card in home si chiama ancora «Fondi Pensione» (`profilo/index.html:829` e
   `portafoglio/profilo.html:829`) e l'URL resta `/fondipensione/` anche se il tab in navbar ora dice
   «Clienti». L'utente ha detto «POI LA MODIFICHEREMO»: non toccarlo senza che lo chieda.

5. **I due commit `8493666` e `c6f5b18` non sono ancora pushati** (`main` è 2 avanti su `origin`): né il
   riscarico al boot né la selezione degli asset del file cliente sono in produzione. Si pubblica solo
   quando l'utente lo chiede.
6. **`.fillna(0)` sui rendimenti maschera le storie corte.** `_buyhold_cum` (`portafoglio/app.py:340`) e
   `_rebalanced_cum` (`:363`) riempiono a zero i giorni in cui un asset non esisteva ancora: nessun
   avviso a schermo, e la curva del portafoglio sottostima quanto più pesa un asset giovane. Decisione
   non presa: avvisare quando un asset selezionato copre meno della finestra richiesta, oppure far
   partire la finestra dall'asset più giovane.

### Trappole già pagate
- **Dall'interfaccia non si può riscaricare un dataset condiviso.** `⟳ Aggiorna` (`refresh-data-btn`,
  `portafoglio/app.py:3328`) sta dentro un Div `style={'display': 'none'}` ("Controlli legacy nascosti");
  e anche se fosse visibile, `start_refresh` ricadrebbe sempre sul ricaricamento **da disco**, perché
  `_PENDING` non viene popolato in nessun punto del file. Il `🔄` visibile nel pannello `📁 File`
  (`fp-refresh-btn`) aggiorna solo l'elenco dei file. Quindi se i prezzi sul sito sono fermi, la risposta
  non è mai "clicca il pulsante": è il job notturno o il prewarm al boot.
- **Il `.env` locale punta al bucket di PRODUZIONE** (`dashboard-dati`). Prima di ogni esecuzione locale
  azzerare `S3_BUCKET S3_ENDPOINT_URL S3_ACCESS_KEY_ID S3_SECRET_ACCESS_KEY`, o la prova scrive sui dati
  veri. Comando buono: `S3_BUCKET= S3_ENDPOINT_URL= S3_ACCESS_KEY_ID= S3_SECRET_ACCESS_KEY= PORT=8080
  python3.13 -m gunicorn wsgi:application -c gunicorn.conf.py -b 127.0.0.1:8080` — con `127.0.0.1` al
  posto dello `0.0.0.0` del config, così il sito autenticato non resta esposto sulla rete locale; il log
  deve dire `[cloud] storage persistente disattivato`. Correggere la produzione si fa con una modifica
  al codice + deploy, non a mano sul bucket.
- **`pull_all()` gira solo al boot**: una correzione fatta a mano sul bucket non arriva al sito finché il
  processo non riparte.
- **`saved_at` scritto su DO è in UTC** (`datetime.now()` senza timezone): un job delle 00:00 di Roma si
  etichetta 22:00. Non è un job che sbaglia orario.
- **L'ARIMA in locale su questo Mac va in segfault** dentro il BLAS di Accelerate (stack `libBLAS` →
  `libLAPACK dgetrs` → `_dsolve_discrete_lyapunov`). Non è il codice: su Linux il job delle 02:00
  completa. Non si rigenera in locale per poi caricarlo.
- **Il primo ingresso in `/calendario/` costa ~7 s**, poi 2 ms (misurato 09/10/2026 su due boot: il
  ritardo c'è anche senza alcun download in corso, quindi è l'avvio a freddo di quella dashboard, non
  contesa sul worker singolo). Un `curl --max-time 5` sul calendario appena dopo il boot sembra un
  errore e non lo è.
- `yf.download` ha già `timeout=10` di default: non serve aggiungerlo.
