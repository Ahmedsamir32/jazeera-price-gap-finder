import base64
import io
import math
import os
from datetime import date, timedelta

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

MAX_DAYS = 31
JAZEERA_CODE = "J9"

with st.form("search_form"):
    col1, col2 = st.columns(2)
    with col1:
        origin = st.text_input("From", value="KWI").strip().upper()
    with col2:
        destination = st.text_input("To", value="CAI").strip().upper()

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
    if not origin or not destination:
        st.error("Please enter both airport codes.")
        st.stop()
    if start_date > end_date:
        st.error("The start date must be before the end date.")
        st.stop()

    num_days = (end_date - start_date).days + 1
    if num_days > MAX_DAYS:
        st.error(f"Please search {MAX_DAYS} days or fewer at a time — each day uses one API call.")
        st.stop()

    core.CONFIG["stops"] = STOPS_LABELS[stops_label]
    core.CONFIG["market"] = "KW"
    core.CONFIG["currency"] = "KWD"

    dates = [start_date + timedelta(days=i) for i in range(num_days)]
    session = requests.Session()
    rows = []
    any_itinerary_found = False
    competitor_seen = False

    progress = st.progress(0.0, text="Starting search...")

    for i, d in enumerate(dates):
        date_back = d + timedelta(days=stay_nights) if trip_type == "RT" else None
        params = core.build_params(origin, destination, d, date_back, core.CONFIG["currency"], trip_type)

        try:
            data = core.serpapi_search(session, params)
        except RuntimeError as e:
            st.warning(f"Skipped {d}: {e}")
            progress.progress((i + 1) / num_days)
            continue

        cheapest = core.extract_cheapest_by_airline(data)
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
                if only_alerts and not alert:
                    continue
                alert_label = "Yes" if alert else "No"
                gap = price - jazeera_price
            else:
                alert_label = "Jazeera N/A"
                gap = math.nan

            row = {"Date": d.strftime("%Y-%m-%d")}
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

        progress.progress((i + 1) / num_days, text=f"Checked {d}")

    progress.empty()

    if not rows:
        if not any_itinerary_found:
            st.info(
                f"No {stops_label.lower()} flights found for {origin}→{destination} in this date range. "
                "Try a different stop option or dates."
            )
        elif not competitor_seen:
            st.info(
                f"Jazeera flies {origin}→{destination} with these filters, but no other airline does — "
                "there's no competitor fare to compare it against."
            )
        else:
            st.info(
                "No days found where a competitor beats Jazeera. Uncheck "
                "'Only show days a competitor is cheaper than Jazeera' to see every fare found."
            )
    else:
        df = pd.DataFrame(rows)
        st.success(f"Found {len(df)} result(s).")

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
            file_name=f"price_scan_{origin}_{destination}_{start_date}_{end_date}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
        )
