import os
import json
import base64
import requests
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

API_KEY = os.environ.get('DATAMART_API_KEY', '')
AUTH = base64.b64encode(f"{API_KEY}:".encode()).decode()
MARS_HEADERS = {"Authorization": f"Basic {AUTH}"}

# Two separate USDA APIs:
# LMPR/DPMRP (public) — dairy mandatory reporting, FMMOS
MPR_BASE = "https://mpr.datamart.ams.usda.gov/services/v1.1/reports"
# MMN (requires API key) — regional market news
MARS_BASE = "https://marsapi.ams.usda.gov/services/v1.2/reports"

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)


def fetch_mpr(path, params=None):
    """Fetch from the LMPR/DPMRP public API (mpr.datamart)."""
    url = f"{MPR_BASE}/{quote(path, safe='/')}"
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_mars(slug, params=None):
    """Fetch from the MMN API (marsapi) — requires DATAMART_API_KEY."""
    url = f"{MARS_BASE}/{quote(str(slug), safe='/')}"
    r = requests.get(url, headers=MARS_HEADERS, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_ndpsr_nfdm():
    """NDPSR report 2993, NFDM section — weekly prices and sales volumes."""
    raw = fetch_mpr("2993/Final Nonfat Dry Milk Prices and Sales")
    out = []
    for row in raw.get("results", []):
        try:
            out.append({
                "date": row.get("week_ending_date") or row.get("published_date"),
                "price": float(row.get("weighted_price", 0) or 0),
                "volume": float(row.get("sales", 0) or 0),
            })
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: x["date"] or "")
    return out


def fetch_class_iv():
    """Report 2991, detail section — announced class and component prices."""
    raw = fetch_mpr("2991/detail")
    out = []
    for row in raw.get("results", []):
        try:
            out.append({
                "date": row.get("report_date") or row.get("published_date"),
                "announced": float(row.get("class_iv_price", 0) or 0),
                "skim": float(row.get("skim_price", 0) or 0),
                "butterfat": float(row.get("butterfat_price", 0) or 0),
                "nfdm_avg": float(row.get("nfdm_monthly_avg", 0) or 0),
                "butter_avg": float(row.get("butter_monthly_avg", 0) or 0),
            })
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: x["date"] or "")
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
    print("Fetching NDPSR NFDM (report 2993)...")
    write_json("nass", fetch_ndpsr_nfdm())

    print("Fetching Class IV (report 2991)...")
    write_json("class_iv", fetch_class_iv())

    print("Fetching CME spot from Cheese Reporter...")
    write_json("cme", fetch_cme_cheese_reporter())

    print("Done.")
