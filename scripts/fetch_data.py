import os
import json
import base64
import requests
from datetime import datetime, timedelta
from pathlib import Path

API_KEY = os.environ['DATAMART_API_KEY']
AUTH = base64.b64encode(f"{API_KEY}:".encode()).decode()
HEADERS = {"Authorization": f"Basic {AUTH}"}
BASE = "https://marsapi.ams.usda.gov/services/v1.2/reports"

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

def fetch_report(slug, start_date=None):
    params = {}
    if start_date:
        params["q"] = f"report_begin_date={start_date}"
    r = requests.get(f"{BASE}/{slug}", headers=HEADERS, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def fetch_nass():
    raw = fetch_report("2993")
    out = []
    for row in raw.get("results", []):
        out.append({
            "date": row.get("week_ending_date"),
            "price": float(row.get("weighted_price", 0)),
            "volume": float(row.get("sales_volume", 0)),
        })
    out.sort(key=lambda x: x["date"])
    return out

def fetch_class_iv():
    raw = fetch_report("2991")
    out = []
    for row in raw.get("results", []):
        out.append({
            "date": row.get("report_date"),
            "announced": float(row.get("class_iv_price", 0)),
            "skim": float(row.get("skim_price", 0)),
            "butterfat": float(row.get("butterfat_price", 0)),
            "nfdm_avg": float(row.get("nfdm_monthly_avg", 0)),
            "butter_avg": float(row.get("butter_monthly_avg", 0)),
        })
    out.sort(key=lambda x: x["date"])
    return out

def fetch_cme_cheese_reporter():
    from bs4 import BeautifulSoup
    url = "https://www.cheesereporter.com/cme-nfdm-prices/"
    r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for row in soup.select("table tr"):
        cells = [c.text.strip() for c in row.select("td")]
        if len(cells) >= 2:
            try:
                date_str = cells[0]
                price = float(cells[1].replace("$", "").replace(",", ""))
                out.append({"date": date_str, "price": price})
            except (ValueError, IndexError):
                continue
    return out

def write_json(name, data):
    path = DATA_DIR / f"{name}.json"
    payload = {
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "count": len(data),
        "data": data,
    }
    path.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {len(data)} rows to {path}")

if __name__ == "__main__":
    print("Fetching NASS NDPSR...")
    write_json("nass", fetch_nass())

    print("Fetching Class IV...")
    write_json("class_iv", fetch_class_iv())

    print("Fetching CME spot from Cheese Reporter...")
    write_json("cme", fetch_cme_cheese_reporter())

    print("Done.")
