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
