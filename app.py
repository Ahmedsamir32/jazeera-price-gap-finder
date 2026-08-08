import base64
import io
import math
import os
import re
from datetime import date, datetime, timedelta

import pandas as pd
import requests
import streamlit as st

from bootstrap import STOPS_LABELS, ensure_api_key

st.set_page_config(page_title="Jazeera Price Gap Finder", page_icon="✈️", layout="wide")

_has_api_key = ensure_api_key()
import price_gap_finder_serpapi as core

LOGO_PATH = os.path.join(os.path.dirname(__file__), "assets", "jazeera_logo.png")
_logo_b64 = ""
if os.path.exists(LOGO_PATH):
    with open(LOGO_PATH, "rb") as f:
        _logo_b64 = base64.b64encode(f.read()).decode()

_logo_img_html = f'<img src="data:image/png;base64,{_logo_b64}" alt="Jazeera">' if _logo_b64 else ""

_header_html = """
    <style>
    .jazeera-header {
        background: #FFFFFF;
        border-bottom: 4px solid #1B4B9C;
        padding: 1.2rem 0.2rem 1rem 0.2rem;
        margin-bottom: 1.6rem;
        display: flex;
        align-items: center;
        gap: 1rem;
    }
    .jazeera-header img {
        height: 72px;
    }
    .jazeera-header .tool-name {
        color: #1B4B9C;
        font-size: 2.6rem;
        font-weight: 300;
        border-left: 2px solid #C9D6EC;
        padding-left: 1.2rem;
        margin-left: 0.3rem;
    }
    .jazeera-header .subtitle {
        color: #5B6B85;
        font-size: 0.95rem;
        margin-top: 0.3rem;
    }
    div[data-testid="stForm"] {
        border: 1px solid #E3E8F0;
        border-radius: 10px;
        padding: 1.2rem 1.4rem 0.6rem 1.4rem;
        background: #FAFBFD;
    }
    </style>
    <div class="jazeera-header">
        __LOGO_IMG__
        <div>
            <span class="tool-name">J9 Price Gap Finder</span>
            <div class="subtitle">Compare Jazeera Airways fares against every other airline flying the same route.</div>
        </div>
    </div>
    """.replace("__LOGO_IMG__", _logo_img_html)

st.markdown(_header_html, unsafe_allow_html=True)

if not _has_api_key:
    st.error("No SerpAPI key found. Set SERPAPI_API_KEY in this app's Secrets (or ~/.zshrc locally) and restart.")
    st.stop()

MAX_COMBINATIONS = 31  # routes x dates -- each combination is 1 API call
JAZEERA_CODE = "J9"

HISTORY_PATH = os.path.join(os.path.dirname(__file__), "data", "history_log.csv")
HISTORY_COLUMNS = [
    "Scanned At", "Route", "Date", "Competitor",
    "Competitor Price (KWD)", "Jazeera Price (KWD)", "Price Gap (KWD)", "Alert",
]

# A few routes already validated in this app; add more here as you confirm
# them. You can also type any custom route(s) below regardless of what's
# selected here.
ROUTE_PRESETS = {
    "Kuwait → Cairo (KWI → CAI)": ("KWI", "CAI"),
    "Kuwait → Istanbul (KWI → IST)": ("KWI", "IST"),
    "Kuwait → Amman (KWI → ADJ)": ("KWI", "ADJ"),
}

selected_presets = st.multiselect(
    "Routes", list(ROUTE_PRESETS.keys()), default=[next(iter(ROUTE_PRESETS))]
)
custom_routes_text = st.text_area(
    "Custom routes (optional)",
    value="",
    placeholder="One per line, e.g.\nKWI-DXB\nKWI-JED",
    help="Any airport-code pairs not already covered by the routes above.",
)


def parse_custom_routes(text):
    routes = []
    for line in text.splitlines():
        codes = re.findall(r"\b[A-Za-z]{3}\b", line)
        if len(codes) >= 2:
            routes.append((codes[0].upper(), codes[1].upper()))
    return routes


def load_history():
    if not os.path.exists(HISTORY_PATH):
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    try:
        return pd.read_csv(HISTORY_PATH)
    except Exception:
        return pd.DataFrame(columns=HISTORY_COLUMNS)


def append_history(new_rows):
    if not new_rows:
        return
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    df_new = pd.DataFrame(new_rows, columns=HISTORY_COLUMNS)
    file_exists = os.path.exists(HISTORY_PATH)
    df_new.to_csv(HISTORY_PATH, mode="a", header=not file_exists, index=False)


with st.form("search_form"):
    trip_type_label = st.radio("Trip type", ["One-way", "Round-trip"], horizontal=True)
    trip_type = "OW" if trip_type_label == "One-way" else "RT"

    col3, col4 = st.columns(2)
    with col3:
        start_date = st.date_input("From date", value=date.today() + timedelta(days=30))
    with col4:
        end_date = st.date_input("To date", value=date.today() + timedelta(days=30))

    stay_nights = None
    if trip_type == "RT":
        stay_nights = st.number_input("Nights at destination", min_value=1, max_value=60, value=3)

    stops_label = st.selectbox("Stops", list(STOPS_LABELS.keys()))
    only_alerts = st.checkbox("Only show days a competitor is cheaper than Jazeera", value=False)

    submitted = st.form_submit_button("Search", width="stretch")

if submitted:
    routes = [ROUTE_PRESETS[label] for label in selected_presets] + parse_custom_routes(custom_routes_text)
    routes = list(dict.fromkeys(routes))  # dedupe, keep order

    if not routes:
        st.error("Select at least one route, or enter a custom route.")
        st.stop()
    if start_date > end_date:
        st.error("The start date must be before the end date.")
        st.stop()

    num_days = (end_date - start_date).days + 1
    total_combinations = num_days * len(routes)
    if total_combinations > MAX_COMBINATIONS:
        st.error(
            f"{len(routes)} route(s) × {num_days} date(s) = {total_combinations} API calls, over the "
            f"{MAX_COMBINATIONS} limit per search. Narrow the date range or select fewer routes."
        )
        st.stop()

    core.CONFIG["stops"] = STOPS_LABELS[stops_label]
    core.CONFIG["market"] = "KW"
    core.CONFIG["currency"] = "KWD"

    dates = [start_date + timedelta(days=i) for i in range(num_days)]
    session = requests.Session()
    rows = []
    history_rows = []
    any_itinerary_found = False
    competitor_seen = False
    scanned_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    total_steps = len(routes) * len(dates)
    step = 0
    progress = st.progress(0.0, text="Starting search...")

    for origin, destination in routes:
        route_label = f"{origin}→{destination}"
        for d in dates:
            date_back = d + timedelta(days=stay_nights) if trip_type == "RT" else None
            params = core.build_params(origin, destination, d, date_back, core.CONFIG["currency"], trip_type)

            try:
                data = core.serpapi_search(session, params)
            except RuntimeError as e:
                st.warning(f"Skipped {route_label} on {d}: {e}")
                step += 1
                progress.progress(step / total_steps)
                continue

            cheapest = core.extract_cheapest_by_airline(data, STOPS_LABELS[stops_label])
            if cheapest:
                any_itinerary_found = True

            jazeera_entry = cheapest.get(JAZEERA_CODE)
            jazeera_price = jazeera_entry["price"] if jazeera_entry else math.nan
            jazeera_available = jazeera_entry is not None
            if any(code != JAZEERA_CODE for code in cheapest):
                competitor_seen = True

            for code, entry in cheapest.items():
                if code == JAZEERA_CODE:
                    continue
                price = entry["price"]

                if jazeera_available:
                    alert = price < jazeera_price
                    alert_label = "Yes" if alert else "No"
                    gap = price - jazeera_price
                else:
                    alert = False
                    alert_label = "Jazeera N/A"
                    gap = math.nan

                history_rows.append({
                    "Scanned At": scanned_at,
                    "Route": route_label,
                    "Date": d.strftime("%Y-%m-%d"),
                    "Competitor": entry["name"],
                    "Competitor Price (KWD)": price,
                    "Jazeera Price (KWD)": jazeera_price,
                    "Price Gap (KWD)": gap,
                    "Alert": alert_label,
                })

                if only_alerts and not alert:
                    continue

                row = {"Route": route_label, "Date": d.strftime("%Y-%m-%d")}
                if trip_type == "RT":
                    row["Return Date"] = date_back.strftime("%Y-%m-%d")
                    row["Stay Nights"] = stay_nights
                row.update({
                    "Competitor": entry["name"],
                    "Competitor Price (KWD)": price,
                    "Jazeera Price (KWD)": jazeera_price,
                    "Price Gap (KWD)": gap,
                    "Alert": alert_label,
                })
                rows.append(row)

            step += 1
            progress.progress(step / total_steps, text=f"Checked {route_label} on {d}")

    progress.empty()
    append_history(history_rows)

    if not rows:
        if not any_itinerary_found:
            st.info(
                f"No {stops_label.lower()} flights found for the selected route(s)/dates. "
                "Try a different stop option or dates."
            )
        elif not competitor_seen:
            st.info(
                "Jazeera flies these route(s) with these filters, but no other airline does — "
                "there's no competitor fare to compare against."
            )
        else:
            st.info(
                "No days found where a competitor beats Jazeera. Uncheck "
                "'Only show days a competitor is cheaper than Jazeera' to see every fare found."
            )
    else:
        df = pd.DataFrame(rows)
        st.success(f"Found {len(df)} result(s) across {len(routes)} route(s).")

        def highlight_alert(row):
            if row["Alert"] == "Yes":
                return ["background-color: #FCE4E7"] * len(row)
            if row["Alert"] == "No":
                return ["background-color: #E5F3E8"] * len(row)
            return [""] * len(row)

        def fmt_num(x):
            if pd.isna(x):
                return ""
            if isinstance(x, (int, float)):
                return f"{x:g}"
            return x

        st.caption("🔴 Competitor is cheaper than Jazeera　🟢 Jazeera is cheaper or equal")
        st.dataframe(
            df.style.apply(highlight_alert, axis=1).format(fmt_num),
            width="stretch", hide_index=True,
        )

        buffer = io.BytesIO()
        df.to_excel(buffer, index=False)
        st.download_button(
            "Download as Excel",
            data=buffer.getvalue(),
            file_name=f"price_scan_{start_date}_{end_date}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
        )

with st.expander("📈 Historical alert log"):
    st.caption(
        "Every search you run gets appended here, so you can see patterns over time — e.g. how often a "
        "competitor has undercut Jazeera on a route. Note: this is stored on the app's own disk, not a "
        "real database, so it may reset if the app redeploys or sleeps for a long time — not guaranteed "
        "permanent storage."
    )
    history_df = load_history()
    if history_df.empty:
        st.caption("No history yet — run a search above to start logging.")
    else:
        st.dataframe(history_df.sort_values("Scanned At", ascending=False), width="stretch", hide_index=True)
        history_buffer = io.BytesIO()
        history_df.to_excel(history_buffer, index=False)
        st.download_button(
            "Download full history as Excel",
            data=history_buffer.getvalue(),
            file_name="price_gap_history.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
        )
