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
    "Previous Price (KWD)", "Previous Alert", "Change Note",
]

# Sectors known to this app so far. Add more here as you need them — anything
# not listed can still be typed manually via "Other".
AIRPORTS = {
    "KWI": "Kuwait City",
    "CAI": "Cairo",
    "HBE": "Alexandria (Borg El Arab)",
    "SSH": "Sharm El Sheikh",
    "HRG": "Hurghada",
    "LXR": "Luxor",
    "ASW": "Aswan",
    "IST": "Istanbul",
    "SAW": "Istanbul (Sabiha Gökçen)",
    "ADB": "Izmir",
    "AYT": "Antalya",
    "TZX": "Trabzon",
    "ESB": "Ankara",
    "ADJ": "Amman (Civil)",
    "AMM": "Amman (Queen Alia)",
    "BEY": "Beirut",
    "DXB": "Dubai",
    "AUH": "Abu Dhabi",
    "DOH": "Doha",
    "RUH": "Riyadh",
    "JED": "Jeddah",
    "DMM": "Dammam",
    "MED": "Medina",
    "BAH": "Bahrain",
    "MCT": "Muscat",
    "BGW": "Baghdad",
    "BSR": "Basra",
    "NJF": "Najaf",
    "EBL": "Erbil",
    "BOM": "Mumbai",
    "DEL": "Delhi",
    "COK": "Kochi",
    "TRV": "Thiruvananthapuram",
    "DAC": "Dhaka",
    "KHI": "Karachi",
    "LHE": "Lahore",
    "ISB": "Islamabad",
}
OTHER_LABEL = "Other (type code manually)"


SELECT_LABEL = "— Select sector —"


def airport_options():
    opts = [SELECT_LABEL]
    opts += [f"{name} ({code})" for code, name in sorted(AIRPORTS.items(), key=lambda kv: kv[1])]
    opts.append(OTHER_LABEL)
    return opts


def resolve_code(selection, manual_text):
    if selection in (SELECT_LABEL, ""):
        return ""
    if selection == OTHER_LABEL:
        return manual_text.strip().upper()
    m = re.search(r"\(([A-Za-z]{3})\)$", selection)
    return m.group(1).upper() if m else selection.strip().upper()


def option_for_code(code, options):
    label = f"{AIRPORTS[code]} ({code})" if code in AIRPORTS else None
    return options.index(label) if label in options else 0


def _existing_header(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.readline().rstrip("\n").rstrip("\r").split(",")
    except Exception:
        return None


def load_history():
    if not os.path.exists(HISTORY_PATH):
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    try:
        df = pd.read_csv(HISTORY_PATH, encoding="utf-8")
    except Exception:
        # Most likely an older/ragged schema (columns added since this file
        # was created) rather than genuine corruption -- try a lenient parse
        # that skips only the rows that don't fit before giving up entirely.
        try:
            df = pd.read_csv(HISTORY_PATH, encoding="utf-8", engine="python", on_bad_lines="skip")
        except Exception as e:
            st.warning(
                f"Could not read the existing history file ({e}). Showing empty history for now — "
                "it will rebuild from here. (It'll be cleaned up automatically on your next search.)"
            )
            return pd.DataFrame(columns=HISTORY_COLUMNS)
    for col in HISTORY_COLUMNS:
        if col not in df.columns:
            df[col] = None
    return df


def append_history(new_rows):
    """Returns (ok, message) -- ok is False if the write didn't actually
    take (surfaced to the user instead of failing silently)."""
    if not new_rows:
        return True, "Nothing to log."
    try:
        os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
        file_exists = os.path.exists(HISTORY_PATH)

        if file_exists and _existing_header(HISTORY_PATH) != HISTORY_COLUMNS:
            # The file was created by an older version of the app with a
            # different set of columns -- appending now would produce a
            # ragged CSV pandas can't parse. Archive the old one (data isn't
            # lost, just split out) and start a fresh file with today's
            # schema instead of corrupting it further.
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

    # Verify the write actually landed -- some hosting filesystems accept
    # writes without error but don't persist them the way a normal local
    # disk would, so confirm by reading straight back rather than trusting
    # the absence of an exception.
    try:
        check_df = pd.read_csv(HISTORY_PATH, encoding="utf-8")
    except Exception as e:
        return False, f"Wrote history but could not verify it by reading it back: {e}"

    scanned_at_written = {r["Scanned At"] for r in new_rows}
    found = check_df[check_df["Scanned At"].isin(scanned_at_written)]
    if len(found) < len(new_rows):
        return False, (
            f"Wrote {len(new_rows)} history row(s) but only {len(found)} were readable back "
            f"immediately after (path: {HISTORY_PATH}). This app's hosting may not support "
            "persistent local-disk storage the way this feature needs."
        )
    return True, f"Logged {len(new_rows)} row(s) to history."


def fmt_num(x):
    if pd.isna(x):
        return ""
    if isinstance(x, (int, float)):
        return f"{x:g}"
    return x


def highlight_alert(row):
    if row.get("Alert") == "Yes":
        return ["background-color: #FCE4E7"] * len(row)
    if row.get("Alert") == "No":
        return ["background-color: #E5F3E8"] * len(row)
    return [""] * len(row)


def highlight_history(row):
    styles = highlight_alert(row)
    note = row.get("Change Note")
    if note not in (None, "", "No change", "First time seen") and not (isinstance(note, float) and pd.isna(note)):
        return ["background-color: #FDECC8"] * len(row)  # amber overrides -- flags a real change
    return styles


# ---------------- Route rows (outside the form so "Add" can rerun live) ----------------

if "num_routes" not in st.session_state:
    st.session_state.num_routes = 1

st.subheader("Routes")
AIRPORT_OPTIONS = airport_options()

route_pairs = []
for i in range(st.session_state.num_routes):
    col1, col2, col3 = st.columns([5, 5, 1])
    with col1:
        default_idx = option_for_code("KWI", AIRPORT_OPTIONS) if i == 0 else 0
        from_sel = st.selectbox("From (sector)", AIRPORT_OPTIONS, index=default_idx, key=f"from_sel_{i}")
        from_manual = ""
        if from_sel == OTHER_LABEL:
            from_manual = st.text_input("From — airport code", key=f"from_manual_{i}")
    with col2:
        default_idx = option_for_code("CAI", AIRPORT_OPTIONS) if i == 0 else 0
        to_sel = st.selectbox("To (sector)", AIRPORT_OPTIONS, index=default_idx, key=f"to_sel_{i}")
        to_manual = ""
        if to_sel == OTHER_LABEL:
            to_manual = st.text_input("To — airport code", key=f"to_manual_{i}")
    with col3:
        st.markdown("<div style='height: 1.9rem'></div>", unsafe_allow_html=True)
        if st.session_state.num_routes > 1:
            if st.button("✕", key=f"remove_route_{i}", help="Remove this route"):
                st.session_state.num_routes -= 1
                st.rerun()
    route_pairs.append((resolve_code(from_sel, from_manual), resolve_code(to_sel, to_manual)))

if st.button("+ Add another route"):
    st.session_state.num_routes += 1
    st.rerun()

# ---------------- Search form ----------------

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
    complete_pairs = [r for r in route_pairs if r[0] and r[1]]
    incomplete_count = len(route_pairs) - len(complete_pairs)
    routes = list(dict.fromkeys(complete_pairs))  # dedupe, keep order

    if not routes:
        st.error("Pick a valid From/To sector for at least one route.")
        st.stop()
    if incomplete_count:
        st.warning(f"Skipped {incomplete_count} route row(s) left at \"{SELECT_LABEL}\".")
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

    history_before = load_history()  # snapshot to diff new results against

    total_steps = len(routes) * len(dates)
    step = 0
    progress = st.progress(0.0, text="Starting search...")

    for origin, destination in routes:
        route_label = f"{origin}→{destination}"
        for d in dates:
            date_str = d.strftime("%Y-%m-%d")
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

                prior = history_before[
                    (history_before["Route"] == route_label)
                    & (history_before["Competitor"] == entry["name"])
                    & (history_before["Date"] == date_str)
                ]
                if not prior.empty:
                    prior_row = prior.sort_values("Scanned At").iloc[-1]
                    prev_price = prior_row["Competitor Price (KWD)"]
                    prev_alert = prior_row["Alert"]
                    prev_jazeera = prior_row.get("Jazeera Price (KWD)")
                    change_parts = []
                    if pd.notna(prev_price) and prev_price != price:
                        change_parts.append(f"{entry['name']} price {prev_price:g}→{price:g}")
                    if pd.notna(prev_jazeera) and pd.notna(jazeera_price) and prev_jazeera != jazeera_price:
                        change_parts.append(f"Jazeera price {prev_jazeera:g}→{jazeera_price:g}")
                    if pd.notna(prev_alert) and prev_alert != alert_label:
                        change_parts.append(f"Alert {prev_alert}→{alert_label}")
                    change_note = "; ".join(change_parts) if change_parts else "No change"
                else:
                    prev_price = None
                    prev_alert = None
                    change_note = "First time seen"

                history_rows.append({
                    "Scanned At": scanned_at,
                    "Route": route_label,
                    "Date": date_str,
                    "Competitor": entry["name"],
                    "Competitor Price (KWD)": price,
                    "Jazeera Price (KWD)": jazeera_price,
                    "Price Gap (KWD)": gap,
                    "Alert": alert_label,
                    "Previous Price (KWD)": prev_price,
                    "Previous Alert": prev_alert,
                    "Change Note": change_note,
                })

                if only_alerts and not alert:
                    continue

                row = {"Route": route_label, "Date": date_str}
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
    history_ok, history_message = append_history(history_rows)
    if not history_ok:
        st.error(f"⚠️ History logging failed this run: {history_message}")

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

    # Per-route history, scoped so searching TZX only shows TZX's own past
    # scans, not every route ever searched. Changes since the last scan of
    # the exact same route/competitor/date are highlighted amber.
    full_history = load_history()
    st.subheader("📈 History for the route(s) just searched")
    st.caption(
        "🟧 Something changed since the last time this exact route/competitor/date was scanned "
        "(see 'Change Note' for what and which competitor). Stored on the app's own disk — not "
        "guaranteed to survive a redeploy or long idle period."
    )
    for origin, destination in routes:
        route_label = f"{origin}→{destination}"
        route_history = full_history[full_history["Route"] == route_label].sort_values(
            "Scanned At", ascending=False
        )
        with st.expander(f"{route_label} ({len(route_history)} logged row(s))"):
            if route_history.empty:
                st.caption("No history yet for this route.")
            else:
                st.dataframe(
                    route_history.style.apply(highlight_history, axis=1).format(fmt_num),
                    width="stretch", hide_index=True,
                )
                hist_buffer = io.BytesIO()
                route_history.to_excel(hist_buffer, index=False)
                st.download_button(
                    f"Download {route_label} history as Excel",
                    data=hist_buffer.getvalue(),
                    file_name=f"history_{origin}_{destination}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    width="stretch",
                    key=f"hist_dl_{origin}_{destination}",
                )
