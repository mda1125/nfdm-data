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


def normalize_date(date_str):
    """Convert MM/DD/YYYY (with optional time) to YYYY-MM-DD for correct sorting."""
    if not date_str:
        return ""
    date_part = date_str.split(" ")[0]
    try:
        dt = datetime.strptime(date_part, "%m/%d/%Y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return date_str


def fetch_ndpsr_nfdm():
    """NDPSR report 2993, NFDM section — preliminary + final, deduplicated by sales week."""
    best = {}

    for section, is_final in [
        ("Nonfat Dry Milk Prices and Sales", False),
        ("Final Nonfat Dry Milk Prices and Sales", True),
    ]:
        raw = fetch_mpr(f"2993/{section}")
        for row in raw.get("results", []):
            try:
                sales_week = normalize_date(row.get("Week Ending Date") or row.get("week_ending_date"))
                price = parse_num(row.get("nonfat_milk_Price"))
                volume = parse_num(row.get("nonfat_milk_Sales"))
                if not sales_week or not price:
                    continue
                published = row.get("published_date", "")
                prev = best.get(sales_week)
                if prev is None or is_final or published > prev["_pub"]:
                    best[sales_week] = {
                        "date": sales_week,
                        "price": price,
                        "volume": volume,
                        "final": is_final,
                        "_pub": published,
                    }
            except (TypeError, ValueError):
                continue

    out = [{"date": v["date"], "price": v["price"], "volume": v["volume"], "final": v["final"]}
           for v in best.values()]
    out.sort(key=lambda x: x["date"])
    print(f"  {sum(1 for r in out if r['final'])} final + {sum(1 for r in out if not r['final'])} preliminary")
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
                "date": normalize_date(row.get("week_ending_date")),
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
    out.sort(key=lambda x: x["date"])
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
            date = normalize_date(row.get("report_date") or row.get("published_date") or row.get("date"))
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
    out.sort(key=lambda x: x["date"])
    return out


QUICKSTATS_KEY = os.environ.get('QUICKSTATS_API_KEY', '')
QUICKSTATS_BASE = "https://quickstats.nass.usda.gov/api/api_GET/"


def fetch_quickstats(short_desc, freq="MONTHLY", year_ge=2018):
    """Fetch national-level data from USDA NASS QuickStats API."""
    params = {
        "key": QUICKSTATS_KEY,
        "short_desc": short_desc,
        "agg_level_desc": "NATIONAL",
        "freq_desc": freq,
        "year__GE": str(year_ge),
        "format": "JSON",
    }
    r = requests.get(QUICKSTATS_BASE, params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("data", [])


def fetch_fundamentals():
    """Fetch NFDM & butter production and stocks from NASS QuickStats."""
    if not QUICKSTATS_KEY:
        print("  QUICKSTATS_API_KEY not set, skipping fundamentals")
        return []

    series = [
        ("nfdm_production", "MILK, DRY, NONFAT, HUMAN - PRODUCTION, MEASURED IN LB", "MONTHLY"),
        ("nfdm_stocks", "MILK, DRY, NONFAT, HUMAN - STOCKS, MEASURED IN LB", "POINT IN TIME"),
        ("butter_production", "BUTTER - PRODUCTION, MEASURED IN LB", "MONTHLY"),
        ("butter_stocks", "BUTTER, COLD STORAGE - STOCKS, MEASURED IN LB", "POINT IN TIME"),
        ("milk_production", "MILK - PRODUCTION, MEASURED IN LB", "MONTHLY"),
    ]

    all_data = {}
    for key, desc, freq in series:
        try:
            rows = fetch_quickstats(desc, freq=freq)
            parsed = []
            for row in rows:
                year = row.get("year", "")
                begin = row.get("begin_code", "")
                ref = row.get("reference_period_desc", "")
                val_str = row.get("Value", "")
                if not year or not begin or len(begin) > 2:
                    continue
                if "THRU" in ref or ref == "YEAR":
                    continue
                try:
                    val = parse_num(val_str)
                except (ValueError, TypeError):
                    continue
                month = f"{year}-{int(begin):02d}"
                parsed.append({"month": month, "value": val})
            parsed.sort(key=lambda x: x["month"])
            seen = set()
            deduped = []
            for p in parsed:
                if p["month"] not in seen:
                    seen.add(p["month"])
                    deduped.append(p)
            all_data[key] = deduped
            print(f"  {key}: {len(deduped)} months")
        except Exception as e:
            print(f"  {key} failed: {e}")
            all_data[key] = []

    months = set()
    for series_data in all_data.values():
        for d in series_data:
            months.add(d["month"])

    lookup = {}
    for key, series_data in all_data.items():
        for d in series_data:
            if d["month"] not in lookup:
                lookup[d["month"]] = {"month": d["month"]}
            lookup[d["month"]][key] = d["value"]

    out = sorted(lookup.values(), key=lambda x: x["month"])
    return out


MONTH_CODES = "FGHJKMNQUVXZ"
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
YAHOO_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"


def fetch_futures():
    """Fetch NFDM futures curve from Yahoo Finance (GNF contracts on CME)."""
    now = datetime.utcnow()
    symbols = []
    for offset in range(24):
        m = (now.month - 1 + offset) % 12
        y = now.year + (now.month - 1 + offset) // 12
        code = MONTH_CODES[m]
        sym = f"GNF{code}{y % 100:02d}.CME"
        symbols.append((sym, f"{y}-{m + 1:02d}", MONTH_NAMES[m], y))

    out = []
    spot_price = None
    for sym, iso_month, month_name, year in symbols:
        try:
            r = requests.get(
                f"{YAHOO_BASE}/{sym}",
                params={"interval": "1d", "range": "1d"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            if r.status_code != 200:
                continue
            data = r.json()
            result = data.get("chart", {}).get("result", [])
            if not result:
                continue
            meta = result[0].get("meta", {})
            price = meta.get("regularMarketPrice")
            if not price or price <= 0:
                continue
            volume = meta.get("regularMarketVolume", 0)
            settle = round(price / 100, 4)
            out.append({
                "month": iso_month,
                "label": f"{month_name} {year % 100:02d}",
                "settle": settle,
                "volume": volume or 0,
            })
            if spot_price is None:
                spot_price = settle
        except Exception as e:
            print(f"  Skipping {sym}: {e}")
            continue

    out.sort(key=lambda x: x["month"])
    return out, spot_price


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

    print("Fetching fundamentals (NASS QuickStats)...")
    try:
        write_json("fundamentals", fetch_fundamentals())
    except Exception as e:
        print(f"Fundamentals fetch failed: {e}")

    print("Fetching NFDM futures curve (Yahoo Finance)...")
    try:
        curve, spot = fetch_futures()
        path = DATA_DIR / "futures.json"
        payload = {
            "updated_at": datetime.utcnow().isoformat() + "Z",
            "trade_date": datetime.utcnow().strftime("%Y-%m-%d"),
            "spot": spot,
            "count": len(curve),
            "data": curve,
        }
        path.write_text(json.dumps(payload, indent=2))
        print(f"Wrote {len(curve)} contracts to {path}")
    except Exception as e:
        print(f"Futures fetch failed: {e}")

    print("Done.")
