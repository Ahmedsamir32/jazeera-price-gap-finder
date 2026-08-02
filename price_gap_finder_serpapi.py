import os
import math
import socket
import time
from datetime import datetime, timedelta
from dateutil.rrule import rrule, DAILY
from typing import Optional, List, Dict
import requests
import urllib3.util.connection as urllib3_cn
import pandas as pd
from tqdm import tqdm

# This machine's IPv6 route to some hosts (incl. SerpAPI's edge) intermittently
# hangs in SYN_SENT instead of failing fast, stalling requests for the full
# timeout. Forcing IPv4 avoids that; IPv4 connectivity has been reliable.
urllib3_cn.allowed_gai_family = lambda: socket.AF_INET

# ---------------- CONFIGURATION ----------------

SERPAPI_KEY = os.getenv("SERPAPI_API_KEY")

CONFIG = {
    "origin": "KWI",
    "destination": "CAI",
    "trip_type": "OW",  # "OW" or "RT"

    "start_date": "2026-09-01",
    "end_date": "2026-09-30",

    # For RT only:
    "fixed_stay_nights": 3,
    "min_stay_nights": None,
    "max_stay_nights": None,

    "currency": "KWD",
    "stops": "NON_STOP",  # ANY | NON_STOP | ONE_STOP_OR_FEWER | TWO_STOPS_OR_FEWER
    "market": "KW",

    "only_alerts": True,
    "competitor_allowlist": None,
    "competitor_blocklist": [],

    "sleep_seconds_between_calls": 1.0,
    "max_retries": 3,
    "timeout_seconds": 60,
}

# SerpAPI's Google Flights engine expects integer codes for these, not the
# descriptive strings used in CONFIG.
STOPS_CODES = {
    "ANY": 0,
    "NON_STOP": 1,
    "ONE_STOP_OR_FEWER": 2,
    "TWO_STOPS_OR_FEWER": 3,
}

# ---------------- FUNCTIONS ----------------

def validate_config():
    if not SERPAPI_KEY:
        raise RuntimeError("Please set SERPAPI_API_KEY environment variable.")
    if CONFIG["start_date"] > CONFIG["end_date"]:
        raise ValueError("start_date must not be after end_date.")

def serpapi_search(session: requests.Session, params: Dict) -> Dict:
    base = "https://serpapi.com/search.json"
    p = params.copy()
    p["engine"] = "google_flights"
    p["api_key"] = SERPAPI_KEY
    last_error = "unknown error"
    for attempt in range(1, CONFIG["max_retries"] + 1):
        try:
            resp = session.get(base, params=p, timeout=CONFIG["timeout_seconds"])
            if resp.status_code == 200:
                return resp.json()
            last_error = f"HTTP {resp.status_code}"
        except requests.RequestException as e:
            last_error = str(e)
        time.sleep(2 * attempt)
    raise RuntimeError(f"SerpAPI request failed after {CONFIG['max_retries']} attempts: {last_error}")

def build_params(origin, destination, date_out, date_back, currency, trip_type):
    params = {
        "departure_id": origin,
        "arrival_id": destination,
        "type": 1 if trip_type == "RT" else 2,
        "stops": STOPS_CODES[CONFIG["stops"]],
        "currency": currency,
        "hl": "en",
        "gl": CONFIG["market"],
        "outbound_date": date_out.strftime("%Y-%m-%d"),
    }
    if trip_type == "RT" and date_back:
        params["return_date"] = date_back.strftime("%Y-%m-%d")
    return params

def extract_cheapest_by_airline(data: Dict) -> Dict[str, Dict]:
    """Returns {carrier_code: {"price": float, "name": str}}, keyed by the
    operating carrier of the itinerary's first leg (flight_number, e.g.
    "J9 101" -> "J9"). The API nests airline/price info per itinerary under
    best_flights/other_flights -> flights[0], not at the itinerary top level.
    """
    cheapest = {}
    for bucket in ("best_flights", "other_flights"):
        for itinerary in data.get(bucket, []):
            price = itinerary.get("price")
            legs = itinerary.get("flights", [])
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

def daterange(start, end):
    s = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(end, "%Y-%m-%d").date()
    return [dt.date() for dt in rrule(DAILY, dtstart=s, until=e)]

def stay_nights_options() -> List[Optional[int]]:
    if CONFIG["trip_type"] != "RT":
        return [None]
    if CONFIG["fixed_stay_nights"] is not None:
        return [CONFIG["fixed_stay_nights"]]
    if CONFIG["min_stay_nights"] is not None and CONFIG["max_stay_nights"] is not None:
        return list(range(CONFIG["min_stay_nights"], CONFIG["max_stay_nights"] + 1))
    raise ValueError("RT trip_type requires fixed_stay_nights or both min_stay_nights and max_stay_nights.")

def competitor_allowed(code: str) -> bool:
    allowlist = CONFIG["competitor_allowlist"]
    if allowlist is not None and code not in allowlist:
        return False
    if code in CONFIG["competitor_blocklist"]:
        return False
    return True

def run_scan():
    validate_config()
    dates = daterange(CONFIG["start_date"], CONFIG["end_date"])
    nights_options = stay_nights_options()

    rows = []
    session = requests.Session()
    pbar = tqdm(total=len(dates) * len(nights_options), desc="Scanning dates")

    for d in dates:
        for nights in nights_options:
            date_back = d + timedelta(days=nights) if nights is not None else None
            params = build_params(CONFIG["origin"], CONFIG["destination"], d, date_back, CONFIG["currency"], CONFIG["trip_type"])

            try:
                data = serpapi_search(session, params)
            except RuntimeError as e:
                tqdm.write(f"Warning: skipping {d} ({nights if nights is not None else 'N/A'} nights): {e}")
                pbar.update(1)
                time.sleep(CONFIG["sleep_seconds_between_calls"])
                continue

            cheapest = extract_cheapest_by_airline(data)
            jazeera_entry = cheapest.get("J9")
            jazeera = jazeera_entry["price"] if jazeera_entry else math.nan
            jazeera_available = jazeera_entry is not None

            for code, entry in cheapest.items():
                if code == "J9" or not competitor_allowed(code):
                    continue
                price = entry["price"]

                if jazeera_available:
                    alert = price < jazeera
                    if CONFIG["only_alerts"] and not alert:
                        continue
                    alert_label = "Yes" if alert else "No"
                    gap = price - jazeera
                else:
                    # Jazeera has no fare for this date/stay length, so there's
                    # no gap to compute, but the competitor fare is still worth surfacing.
                    alert_label = "Jazeera N/A"
                    gap = math.nan

                row = {"Date": d.strftime("%Y-%m-%d")}
                if CONFIG["trip_type"] == "RT":
                    row["Return Date"] = date_back.strftime("%Y-%m-%d")
                    row["Stay Nights"] = nights
                row.update({
                    "Competitor": entry["name"],
                    "Competitor Price(KWD)": price,
                    "Jazeera Price(KWD)": jazeera,
                    "Price Gap(KWD)": gap,
                    "Alert": alert_label,
                })
                rows.append(row)

            pbar.update(1)
            time.sleep(CONFIG["sleep_seconds_between_calls"])

    pbar.close()

    if not rows:
        print("No matching rows found - nothing to save.")
        return

    df = pd.DataFrame(rows)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = f"price_scan_results_{timestamp}.xlsx"
    df.to_excel(output, index=False)
    print(f"Saved: {output}")

if __name__ == "__main__":
    run_scan()
