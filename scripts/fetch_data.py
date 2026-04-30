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


def parse_num(val):
    """Parse a numeric string that may contain commas or be None."""
    if val is None:
        return 0.0
    return float(str(val).replace(",", ""))


def fetch_ndpsr_nfdm():
    """NDPSR report 2993, NFDM section — weekly prices and sales volumes."""
    raw = fetch_mpr("2993/Final Nonfat Dry Milk Prices and Sales")
    out = []
    for row in raw.get("results", []):
        try:
            out.append({
                "date": row.get("week_ending_date"),
                "price": parse_num(row.get("nonfat_milk_Price")),
                "volume": parse_num(row.get("nonfat_milk_Sales")),
            })
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: x["date"] or "")
    return out


def compute_implied_class_iv(nfdm, butter):
    """FMMO Class IV formula: Skim = ((NFDM - 0.1678) * 0.99) * 9, BFat = (Butter - 0.1715) * 1.211"""
    skim = ((nfdm - 0.1678) * 0.99) * 9
    bfat = (butter - 0.1715) * 1.211
    return round((skim * 0.965 + bfat * 3.5), 2)


def fetch_class_iv():
    """Report 2991, detail section — announced class and component prices."""
    raw = fetch_mpr("2991/detail")
    out = []
    for row in raw.get("results", []):
        try:
            nfdm = parse_num(row.get("nfdm_monthly_avg_Price"))
            butter = parse_num(row.get("butter_monthly_avg_Price"))
            butterfat = parse_num(row.get("butterfat_Price"))
            announced = parse_num(row.get("class_4_Price"))
            out.append({
                "date": row.get("week_ending_date"),
                "month": row.get("report_month"),
                "year": row.get("report_year"),
                "announced": announced,
                "implied": compute_implied_class_iv(nfdm, butter) if nfdm and butter else 0.0,
                "skim": parse_num(row.get("class_4_skim_milk_Price")),
                "butterfat": butterfat,
                "nfdm_avg": nfdm,
                "butter_avg": butter,
            })
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: x["date"] or "")
    return out


def fetch_cme_spot():
    """Report 1603 (CME Group Daily Cash Trading WTD) via MMN API — requires DATAMART_API_KEY."""
    raw = fetch_mars("1603")
    results = raw.get("results", [])

    if results:
        print(f"[DEBUG] Report 1603 first row keys: {list(results[0].keys())}")
        print(f"[DEBUG] Report 1603 first row: {json.dumps(results[0], indent=2)}")
        nfdm_rows = [r for r in results if "nonfat" in str(r).lower() or "nfdm" in str(r).lower()]
        if nfdm_rows:
            print(f"[DEBUG] First NFDM-matching row: {json.dumps(nfdm_rows[0], indent=2)}")
        else:
            print(f"[DEBUG] No rows contain 'nonfat' or 'nfdm'. Sample values from first 3 rows:")
            for r in results[:3]:
                print(f"[DEBUG]   {json.dumps(r, indent=2)}")

    out = []
    for row in results:
        try:
            commodity = str(row).lower()
            if "nonfat" not in commodity and "nfdm" not in commodity:
                continue
            date = row.get("report_date") or row.get("published_date") or row.get("date")
            price = None
            for key in row:
                if key.lower() in ("date", "report_date", "published_date", "commodity",
                                    "report_title", "slug_name", "slug_id", "narrative",
                                    "office_name", "office_code", "office_city", "office_state",
                                    "market_location_name", "market_location_city",
                                    "market_location_state", "market_type", "market_type_category",
                                    "created_date"):
                    continue
                val = row[key]
                if val is not None:
                    try:
                        p = parse_num(val)
                        if p > 0:
                            price = p
                            print(f"[DEBUG] Using field '{key}' = {p} for price")
                            break
                    except (ValueError, TypeError):
                        continue
            if date and price:
                out.append({"date": date, "price": price})
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: x["date"] or "")
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

    print("Fetching CME spot (report 1603)...")
    try:
        write_json("cme", fetch_cme_spot())
    except Exception as e:
        print(f"CME fetch failed: {e}")
        write_json("cme", [])

    print("Done.")
