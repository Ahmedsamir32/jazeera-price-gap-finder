# Handoff: Jazeera Price Gap Finder

_Last updated: 2026-08-09, after commit `c1a8530`._

## 1. Project Goal

A competitive-pricing tool for Jazeera Airways: given a route (or several) and date range, it queries Google Flights (via SerpAPI) to find every airline's cheapest fare, then flags days/routes where a competitor undercuts Jazeera. It's deployed as a public Streamlit web app the user shares with colleagues, plus a standalone CLI script for the original batch-scan use case.

## 2. Tech Stack & Architecture

- **Language**: Python 3.9
- **Web framework**: Streamlit (`streamlit>=1.35`), deployed on **Streamlit Community Cloud**
- **Data source**: [SerpAPI](https://serpapi.com) `google_flights` engine (paid, metered — user is on a **$25/mo "Starter" plan, 1,000 searches/month**, previously hit the 250/mo free-tier limit)
- **Airport data**: `airportsdata` package (offline, ~7,859 IATA airports, no network call)
- **Key libs**: `pandas`, `requests`, `openpyxl` (Excel export), `python-dateutil`, `tqdm` (CLI only)
- **Repo**: https://github.com/Ahmedsamir32/jazeera-price-gap-finder (public — no secrets in code, key lives in Streamlit Secrets)
- **Live URL**: https://jazeera-price-gap-finder-wxgtzefg7dyqgzghgtbsim.streamlit.app/

**File structure:**
```
jazeera-price-scan/
├── app.py                          # Main Streamlit web app (511 lines)
├── bootstrap.py                    # API key resolution + shared constants
├── price_gap_finder_serpapi.py     # Core SerpAPI logic (shared by app.py AND standalone CLI)
├── requirements.txt
├── .streamlit/config.toml          # Theme (Jazeera blue #1B4B9C)
├── .gitignore                      # excludes .venv/, data/, .streamlit/secrets.toml, etc.
├── assets/jazeera_logo.png         # Real logo, embedded as base64 in header
├── run_app.command                 # Double-click launcher (local, macOS)
├── run_scan.command                # Double-click launcher for the CLI script
└── data/history_log.csv            # Runtime-generated, gitignored, NOT in repo
```

`app.py` imports functions from `price_gap_finder_serpapi.py` (`build_params`, `serpapi_search`, `extract_cheapest_by_airline`) rather than duplicating logic — that module is also independently runnable as a CLI (`run_scan()` / `CONFIG` dict at module level) for batch scans outside the web UI.

## 3. Key Decisions Made (don't re-litigate)

- **No password/access gate** — user explicitly chose fully open access over a password wall, accepting that anyone with the link can spend their SerpAPI quota.
- **SerpAPI's own `stops` filter is broken** — passing `stops=1` (non-stop only) to the API silently nulls out prices for some genuinely non-stop itineraries (reproduced/confirmed for Jazeera specifically). Fix: **always** request `stops=0` (any) from the API and filter by leg-count client-side (`filter_itineraries_by_stops` / `MAX_STOPS_ALLOWED`).
- **History storage is local CSV on app disk, not a real database** — deliberate tradeoff to avoid needing the user to set up an external service/account. Explicitly documented in-UI as "not guaranteed to survive a redeploy or long idle period." User accepted this tradeoff.
- **Airport search**: Streamlit's native `st.selectbox` filter is fuzzy/useless at 7,800 options (typing "ktm" surfaced "Frankfurt"). Solution: a single `st.text_input` drives a hand-rolled ranking function (`search_airports`) that auto-resolves to the top match, shown as a small caption — **not** a second dropdown (user explicitly asked to collapse two widgets into one).
- **Route input UI**: One From/To sector row + "+ Add another route" button (session_state-driven dynamic rows, live rerun via `st.rerun()`), matching a hand-drawn sketch the user provided. Widgets live **outside** `st.form` because form-contained widgets don't rerun until submit — needed for live add/remove and live search-as-you-type.
- **Cost guardrail**: `MAX_COMBINATIONS = 31` (routes × dates ≤ 31 API calls per search), enforced before any calls are made.
- **git identity**: Repo commits use `GIT_AUTHOR_NAME="Ahmedsamir32"` / a GitHub noreply email (`312276575+Ahmedsamir32@users.noreply.github.com`) passed via env vars per-commit — **never** run `git config` (global or local) to set identity; the harness prohibits modifying git config.
- **GitHub CLI (`gh`)** is installed locally at `.local-tools/gh_2.97.0_macOS_arm64/bin/gh` (downloaded standalone, not via Homebrew — none installed) and already authenticated (`gh auth login` device flow completed). Use it for repo operations; `git push` needs `gh auth setup-git` run once per fresh shell if it fails with "could not read Username."
- **Testing philosophy given SerpAPI cost**: prefer local reproduction with synthetic/mocked data or direct-to-API test scripts over spending the user's quota; only do live browser-driven searches when a fix genuinely can't be verified otherwise, and say so explicitly.

## 4. Current State — What's Built and Working

- ✅ Multi-route, multi-date search against live SerpAPI data
- ✅ Jazeera-vs-competitor comparison table with red/green alert highlighting
- ✅ Excel export of results
- ✅ Global airport search (single field, auto-resolves to ranked top match)
- ✅ Dynamic route rows (Add/Remove)
- ✅ Per-route historical logging with change-tracking ("Nile Air price 40→35; Alert Yes→No"), scoped so searching one route only shows that route's own history, amber-highlighted when something changed since the last scan of that exact route+competitor+date
- ✅ Self-healing CSV schema migration (if history file has stale columns, old file is archived to `history_log_legacy.csv` and a fresh one starts)
- ✅ Jazeera branding (real logo, blue theme, matches company colors)
- ✅ Deployed and live on Streamlit Community Cloud, secret configured correctly
- ✅ Standalone CLI script (`price_gap_finder_serpapi.py` / `run_scan.command`) still works independently for the original batch-scan use case

**Last commit**: `c1a8530` "Collapse From/To sector search to a single field per side" — pushed and should be live.

## 5. Known Bugs/Issues

- **None currently open** that I'm aware of — the last several turns were bug-fix cycles (see git log below) and all were verified fixed. **However**: the single-field airport search UI change (`c1a8530`) was verified locally/live by me but the user has not yet confirmed it themselves in this session — worth a quick sanity check next session.
- **Streamlit Cloud + browser automation tools**: the deployed app renders inside a **cross-origin iframe**, which automated browser tools cannot reliably click into (confirmed limitation, not an app bug). Local testing (`streamlit run app.py --server.port XXXX`) works fine with the same tooling. For live-deployment verification, either test locally first (preferred) or ask the user to click through manually.
- **`data/history_log_legacy.csv`** may exist on the deployed instance from the schema-migration fix (see decision above) — harmless, just old pre-migration data sitting there, never surfaced in the UI.

## 6. Next Steps (discussed but not started)

From the user's own prioritized list:
1. **Scheduled daily runs + email/Slack alerts** — biggest requested upgrade, turns this from "tool you check" into "tool that checks itself." Not started. Would need: a scheduling mechanism (Streamlit Cloud has no built-in cron) and an email/notification service (new account/credentials the user would need to set up).
2. Verify the single-field sector-search UI live one more time (was mid-verification when the previous session's context ran out).
3. Possibly revisit **real persistent storage** for history (e.g., writing to a private file in the GitHub repo via API, reusing the user's existing GitHub account rather than a new service) if the local-disk approach ever proves unreliable in practice.
4. User may want more airports/routes added to test with, or to expand beyond the KWI hub-and-spoke routes tested so far (KWI-CAI, KWI-IST, KWI-ADJ, KWI-TZX, KWI-HBE, KWI-KTM were all tested at some point).

## 7. Critical Code/Config

### `bootstrap.py` (full file — 40 lines)
```python
import os
import re

import streamlit as st


def ensure_api_key() -> bool:
    if os.getenv("SERPAPI_API_KEY"):
        return True

    # Cloud deployment (Streamlit Community Cloud): key is set as a secret
    # in the app's dashboard, not as a shell environment variable.
    try:
        key = st.secrets.get("SERPAPI_API_KEY")
    except Exception:
        key = None
    if key:
        os.environ["SERPAPI_API_KEY"] = key
        return True

    # Local convenience: pick up the key from ~/.zshrc if it's not already
    # exported (this file won't exist on a cloud server, so it's a no-op there).
    path = os.path.expanduser("~/.zshrc")
    if not os.path.exists(path):
        return False
    with open(path) as f:
        content = f.read()
    match = re.search(r'SERPAPI_API_KEY="([^"]*)"', content)
    if not match:
        return False
    os.environ["SERPAPI_API_KEY"] = match.group(1)
    return True


STOPS_LABELS = {
    "Non-stop only": "NON_STOP",
    "Any number of stops": "ANY",
    "1 stop or fewer": "ONE_STOP_OR_FEWER",
    "2 stops or fewer": "TWO_STOPS_OR_FEWER",
}
```
**Critical ordering constraint**: `app.py` calls `ensure_api_key()` **before** `import price_gap_finder_serpapi as core`, because `core` reads `SERPAPI_KEY = os.getenv("SERPAPI_API_KEY")` at **module import time**. Getting this order wrong silently breaks the deployed app (key resolves via Streamlit Secrets too late).

### `price_gap_finder_serpapi.py` — core search/extraction functions (key excerpts)

```python
# SerpAPI's server-side "stops" filter is unreliable: when set to non-stop-only,
# it silently nulls out the price for some genuinely non-stop itineraries
# (observed with Jazeera flights) instead of just omitting stopped ones.
# So we always request "ANY" from the API and filter by stop count ourselves
# client-side, using this max-stops-allowed mapping instead.
MAX_STOPS_ALLOWED = {
    "ANY": None,
    "NON_STOP": 0,
    "ONE_STOP_OR_FEWER": 1,
    "TWO_STOPS_OR_FEWER": 2,
}

def build_params(origin, destination, date_out, date_back, currency, trip_type):
    params = {
        "departure_id": origin,
        "arrival_id": destination,
        "type": 1 if trip_type == "RT" else 2,
        "stops": 0,  # always "ANY" — see MAX_STOPS_ALLOWED comment above
        "currency": currency,
        "hl": "en",
        "gl": CONFIG["market"],
        "outbound_date": date_out.strftime("%Y-%m-%d"),
    }
    if trip_type == "RT" and date_back:
        params["return_date"] = date_back.strftime("%Y-%m-%d")
    return params

def filter_itineraries_by_stops(itineraries, stops_label):
    max_stops = MAX_STOPS_ALLOWED[stops_label]
    if max_stops is None:
        return itineraries
    return [it for it in itineraries if len(it.get("flights") or []) - 1 <= max_stops]

def extract_cheapest_by_airline(data, stops_label="ANY"):
    """Returns {carrier_code: {"price": float, "name": str}}, keyed by the
    operating carrier of the itinerary's first leg (flight_number, e.g.
    "J9 101" -> "J9"). The API nests airline/price info per itinerary under
    best_flights/other_flights -> flights[0], not at the itinerary top level."""
    cheapest = {}
    for bucket in ("best_flights", "other_flights"):
        itineraries = filter_itineraries_by_stops(data.get(bucket) or [], stops_label)
        for itinerary in itineraries:
            price = itinerary.get("price")
            legs = itinerary.get("flights") or []
            if price is None or not legs:
                continue
            airline_name = legs[0].get("airline")
            flight_number = legs[0].get("flight_number", "")
            code = flight_number.split(" ")[0] if flight_number else airline_name
            if not code:
                continue
            existing = cheapest.get(code)
            if existing is None or price < existing["price"]:
                cheapest[code] = {"price": price, "name": airline_name or code}
    return cheapest
```

**IPv4 forcing** (top of file, before anything else runs):
```python
import urllib3.util.connection as urllib3_cn
# This machine's IPv6 route to some hosts (incl. SerpAPI's edge) intermittently
# hangs in SYN_SENT instead of failing fast, stalling requests for the full
# timeout. Forcing IPv4 avoids that; IPv4 connectivity has been reliable.
urllib3_cn.allowed_gai_family = lambda: socket.AF_INET
```

### `app.py` — the airport search core (the part most likely to need touching next)
```python
def search_airports(query, limit=15):
    query = query.strip().upper()
    if not query:
        return []
    exact_code, code_prefix, city_prefix, contains = [], [], [], []
    for code, city in AIRPORTS.items():
        city_upper = city.upper()
        if code == query:
            exact_code.append(code)
        elif code.startswith(query):
            code_prefix.append(code)
        elif city_upper.startswith(query):
            city_prefix.append(code)
        elif query in city_upper:
            contains.append(code)
    code_prefix.sort()
    city_prefix.sort(key=lambda c: AIRPORTS[c])
    contains.sort(key=lambda c: AIRPORTS[c])
    ordered = exact_code + code_prefix + city_prefix + contains
    if not ordered and len(query) == 3 and query.isalpha():
        return [query]  # not in dataset -- use as typed rather than block
    return ordered[:limit]

def sector_picker(label, key_prefix, default_query=""):
    query = st.text_input(label, value=default_query, key=f"{key_prefix}_query", placeholder="e.g. Kathmandu or KTM")
    matches = search_airports(query, limit=1)
    if not matches:
        if query.strip():
            st.caption("⚠️ No match — try a different search, or type the 3-letter airport code.")
        return ""
    code = matches[0]
    st.caption(f"✓ {label_for(code)}")
    return code
```

### History schema-migration self-heal (`app.py`)
```python
HISTORY_PATH = os.path.join(os.path.dirname(__file__), "data", "history_log.csv")
HISTORY_COLUMNS = [
    "Scanned At", "Route", "Date", "Competitor",
    "Competitor Price (KWD)", "Jazeera Price (KWD)", "Price Gap (KWD)", "Alert",
    "Previous Price (KWD)", "Previous Alert", "Change Note",
]

def append_history(new_rows):
    """Returns (ok, message)."""
    if not new_rows:
        return True, "Nothing to log."
    try:
        os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
        file_exists = os.path.exists(HISTORY_PATH)
        if file_exists and _existing_header(HISTORY_PATH) != HISTORY_COLUMNS:
            # Old schema -- archive rather than corrupt with a ragged CSV.
            legacy_path = HISTORY_PATH.replace(".csv", "_legacy.csv")
            try:
                os.replace(HISTORY_PATH, legacy_path)
            except Exception:
                pass
            file_exists = False
        df_new = pd.DataFrame(new_rows, columns=HISTORY_COLUMNS)
        df_new.to_csv(HISTORY_PATH, mode="a", header=not file_exists, index=False, encoding="utf-8")
    except Exception as e:
        return False, f"Could not write history file: {e}"
    # Verify by reading straight back -- don't trust absence-of-exception alone.
    try:
        check_df = pd.read_csv(HISTORY_PATH, encoding="utf-8")
    except Exception as e:
        return False, f"Wrote history but could not verify it by reading it back: {e}"
    scanned_at_written = {r["Scanned At"] for r in new_rows}
    found = check_df[check_df["Scanned At"].isin(scanned_at_written)]
    if len(found) < len(new_rows):
        return False, f"Wrote {len(new_rows)} history row(s) but only {len(found)} were readable back immediately after (path: {HISTORY_PATH})."
    return True, f"Logged {len(new_rows)} row(s) to history."
```

### `requirements.txt` (full)
```
requests>=2.31
pandas>=2.0
tqdm>=4.66
python-dateutil>=2.8
openpyxl>=3.1
streamlit>=1.35
airportsdata>=20250909
```

### `.streamlit/config.toml` (full)
```toml
[theme]
primaryColor = "#1B4B9C"
backgroundColor = "#FFFFFF"
secondaryBackgroundColor = "#EEF2F9"
textColor = "#12203C"
font = "sans serif"
```

### Streamlit Secrets (set on Cloud dashboard, NOT in repo)
```
SERPAPI_API_KEY = "<the real key — user has it in ~/.zshrc locally too>"
```

### SerpAPI real response shape (non-obvious, cost real debugging time)
```json
{
  "best_flights": [
    {
      "flights": [
        {
          "departure_airport": {"name": "...", "id": "KWI", "time": "..."},
          "arrival_airport": {"name": "...", "id": "CAI", "time": "..."},
          "airline": "Jazeera",
          "flight_number": "J9 733"
        }
      ],
      "price": 36
    }
  ],
  "other_flights": [ ]
}
```
Price is a **plain number** directly on the itinerary (not nested `{"raw": ...}` as one might assume), and `airline`/`flight_number` live on `flights[0]`, **not** at the itinerary top level.

## 8. Gotchas / Things to Remember

- **SerpAPI param names are non-obvious**: `type` (not `type_of_trip`) — `1`=round-trip, `2`=one-way. `stops` is an int 0-3, not a string. Both differ from what seems intuitive; verified against SerpAPI's actual docs.
- **`stops` server-side filter is unreliable for non-stop** — see key decision above. Always send `stops=0` and filter client-side.
- **`.get(key, default)` doesn't catch `None` values** — a real bug hit twice: if the API returns `{"flights": null}` (key present, value `None`), `.get("flights", [])` still returns `None`, not `[]`, because the default only applies when the key is **absent**. Fixed everywhere with `.get(key) or []` pattern. Watch for this anywhere new API-response parsing is added.
- **CSV schema changes need migration** — if `HISTORY_COLUMNS` changes again, the self-heal logic in `append_history` handles it automatically (archives old file), but worth remembering *why* it's there.
- **`st.form`-contained widgets don't rerun live** — anything needing immediate visual feedback (Add route button, live search-as-you-type) must live **outside** `st.form`, only the final "Search" submit button needs form batching.
- **`@st.cache_data` used for**: `load_airports()` (loads ~7,859 entries once, not every rerun). Simple and correct for this use case.
- **Browser automation + Streamlit Cloud iframe**: can't click through the deployed app with an in-session browser automation tool (cross-origin iframe blocks synthetic events). Always test locally first with `streamlit run app.py --server.port <unused port> --server.headless true` — that works fine.
- **git author identity**: this environment has no global git config set (by design). Every commit needs `GIT_AUTHOR_NAME`/`GIT_AUTHOR_EMAIL`/`GIT_COMMITTER_NAME`/`GIT_COMMITTER_EMAIL` env vars inline, or it fails with "Author identity unknown."
- **`gh` CLI location**: `.local-tools/gh_2.97.0_macOS_arm64/bin/gh` — not on PATH, use full path or `gh auth setup-git` once per session then plain `git push` works.
- **Repo must stay public** — Streamlit Cloud's default OAuth scope couldn't see the repo when it was private (needed extra GitHub App permission flow); switched to public since there are no secrets in the code anyway. Don't flip it back to private without also fixing the Streamlit Cloud connection.
- **User's other launcher scripts still work standalone**: `run_app.command` (web app) and `run_scan.command` (CLI batch scan using `CONFIG` dict in `price_gap_finder_serpapi.py`) — both extract the key from `~/.zshrc` the same way, independent of Streamlit Secrets.
- **Testing airports used so far**: KWI (Kuwait City), CAI (Cairo), HBE (Alexandria/Borg El Arab), IST (Istanbul), ADJ (Amman Civil), TZX (Trabzon), KTM (Kathmandu) — all real, all verified against live SerpAPI data at some point.
