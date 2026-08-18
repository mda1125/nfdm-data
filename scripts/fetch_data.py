import os
import json
import base64
import time
import re
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


def fetch_with_retry(url, headers=None, params=None, timeout=30, retries=3):
    """Fetch a URL with retry logic for transient failures."""
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            if attempt < retries - 1:
                wait = (attempt + 1) * 10
                print(f"  Retry {attempt + 1}/{retries} after {wait}s: {e}")
                time.sleep(wait)
            else:
                raise


def fetch_mpr(path, params=None):
    """Fetch from the LMPR/DPMRP public API (mpr.datamart)."""
    url = f"{MPR_BASE}/{quote(path, safe='/')}"
    return fetch_with_retry(url, params=params)


def fetch_mars(slug, params=None):
    """Fetch from the MMN API (marsapi) — requires DATAMART_API_KEY."""
    url = f"{MARS_BASE}/{quote(str(slug), safe='/')}"
    return fetch_with_retry(url, headers=MARS_HEADERS, params=params)


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


# ---------------------------------------------------------------------------
# Whey ingredient intelligence (WPC80, WPI, WPC34) — USDA Dairy Market News.
#
# These products are NOT on the public MPR API; they come from the keyed MMN/MARS
# API (same as CME spot / report 1603). WPC34 (report 1053) is published as a
# formal low/high/mostly range; WPC80 and WPI appear only as prose ranges in the
# DMN weekly whey narrative, so they are regex-parsed from text.
#
# The exact MARS field names / narrative report id cannot be verified without the
# API key, so this fetcher is defensive and logs the report shape (like
# fetch_cme_spot). Refine WHEY_NARRATIVE_REPORTS / field matching from the first
# CI run's [DEBUG] output.
# ---------------------------------------------------------------------------
MT_PER_LB = 2204.62

# Report 1053 = "Whey Protein Concentrate - Central and West U.S." — structured
# weekly rows (price_min/max, mostly_low/high_price, grade) plus a report narrative
# that also comments on WPC 80% and WPI. Confirmed from CI logs. One report powers
# all three products: WPC34/WPC80 structured where a grade row exists, WPC80/WPI
# from the latest week's narrative otherwise.
WHEY_WPC34_REPORT = "1053"

WHEY_PRODUCTS = {
    "WPC80": {
        "name": "Whey Protein Concentrate 80%",
        "aliases": ["wpc 80", "wpc80", "whey protein concentrate 80", "80% wpc", "wpc 80%"],
        "grade_key": "80",
    },
    "WPI": {
        "name": "Whey Protein Isolate",
        "aliases": ["whey protein isolate", "wpi", "protein isolate"],
        "grade_key": None,
    },
    "WPC34": {
        "name": "Whey Protein Concentrate 34%",
        "aliases": ["wpc 34", "wpc34", "whey protein concentrate 34", "34% wpc", "wpc 34%"],
        "grade_key": "34",
    },
}

STATUS_KEYWORDS = ["tight", "firm", "balanced", "soft", "steady"]

_RANGE_RE = re.compile(r"\$?\s*(\d+(?:\.\d+)?)\s*(?:-|–|—|to)\s*\$?\s*(\d+(?:\.\d+)?)")
_SINGLE_RE = re.compile(r"\$\s*(\d+(?:\.\d+)?)")
# In-the-dollar idioms DMN uses: "upper $14s", "mid-$13s", "low $14s". The offset
# places the value within that dollar (low ~.2, mid ~.5, upper/high ~.8).
_QUAL_RE = re.compile(r"(low(?:er)?|mid(?:dle)?|upper|high|top)[\s\-]*\$?\s*(\d+)\s*s\b", re.IGNORECASE)
_QUAL_OFFSET = {"low": 0.2, "lower": 0.2, "mid": 0.5, "middle": 0.5,
                "upper": 0.8, "high": 0.8, "top": 0.9}


def _qual_value(word, n):
    return round(float(n) + _QUAL_OFFSET.get(word.lower(), 0.5), 2)


def to_mt(usd_lb):
    """Convert USD/lb to USD/metric-ton, or None."""
    return None if usd_lb is None else round(usd_lb * MT_PER_LB, 1)


def _sentence_around(text, idx):
    """Return the sentence containing index idx. A period counts as a boundary
    only when followed by whitespace/end (so decimals like $12.00 don't split)."""
    start = 0
    for m in re.finditer(r"(?:[.!?](?=\s)|\n)", text[:idx]):
        start = m.end()
    m = re.search(r"[.!?](?=\s|$)|\n", text[idx:])
    end = idx + m.end() if m else len(text)
    return text[start:end].strip()


def _alias_sentences(text, aliases):
    """Yield each sentence that mentions any alias (all occurrences), so parsing
    stays scoped to the product and never bleeds into an adjacent product."""
    low = text.lower()
    for alias in aliases:
        start = 0
        while True:
            pos = low.find(alias, start)
            if pos == -1:
                break
            start = pos + len(alias)
            yield _sentence_around(text, pos)


def detect_status_near(text, aliases):
    """Status derived only from sentences that name this product — a DMN tone
    word ('firm', 'tight', ...) in one of the product's own sentences."""
    if not text:
        return "unknown"
    for sentence in _alias_sentences(text, aliases):
        st = detect_status(sentence)
        if st != "unknown":
            return st
    return "unknown"


def parse_whey_range(text, aliases):
    """Extract a (low, high, excerpt) $/lb range near any alias mention in text.

    Returns (low, high, excerpt) with low/high possibly None. Handles explicit
    numeric ranges, the 'upper-$14s' idiom (high rounds up to next dollar), and a
    lone dollar figure (low == high). Preserves the matched sentence verbatim.
    """
    if not text:
        return None, None, None

    # Prefer an explicit numeric range, then in-the-dollar qualifier idioms,
    # scoped to the product's own sentence so an adjacent product's range can't
    # be captured. A lone figure is a weak fallback (kept only if nothing better).
    weak = None
    for sentence in _alias_sentences(text, aliases):
        m = _RANGE_RE.search(sentence)
        if m:
            lo, hi = float(m.group(1)), float(m.group(2))
            return (min(lo, hi), max(lo, hi), sentence)

        quals = _QUAL_RE.findall(sentence)  # e.g. [('upper','13'), ('mid','14')]
        sm = _SINGLE_RE.search(sentence)
        if len(quals) >= 2:
            vals = [_qual_value(w, n) for w, n in quals]
            return (min(vals), max(vals), sentence)
        if len(quals) == 1:
            qv = _qual_value(*quals[0])
            if sm:  # e.g. "$14 to upper-$14s" -> (14, 14.8)
                base = float(sm.group(1))
                return (min(base, qv), max(base, qv), sentence)
            if weak is None:
                weak = (qv, qv, sentence)
            continue
        if sm and weak is None:
            v = float(sm.group(1))
            weak = (v, v, sentence)

    return weak if weak else (None, None, None)


def detect_status(text):
    """Rule-based market status from DMN tone words; 'unknown' if none present."""
    if not text:
        return "unknown"
    low = text.lower()
    for kw in STATUS_KEYWORDS:
        if kw in low:
            return "firm" if kw == "steady" else kw
    return "unknown"


def _collect_narrative(rows):
    """Concatenate narrative-like text fields from the given report rows."""
    chunks, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        for k, v in row.items():
            if isinstance(v, str) and v.strip() and ("narrative" in k.lower() or "comment" in k.lower()):
                if v not in seen:
                    seen.add(v)
                    chunks.append(v)
    return "\n".join(chunks)


def _row_date(row):
    """Best available date for a report row, normalized to YYYY-MM-DD."""
    return normalize_date(row.get("report_end_date") or row.get("report_date")
                          or row.get("published_date") or "")


def _latest_rows(results):
    """Rows belonging to the most recent report week; ('' if none). Avoids parsing
    across the report's multi-year history."""
    dated = [(r, _row_date(r)) for r in results if isinstance(r, dict) and _row_date(r)]
    if not dated:
        return [r for r in results if isinstance(r, dict)], ""
    latest = max(d for _, d in dated)
    return [r for r, d in dated if d == latest], latest


def _fmt_period(row):
    """Human 'Aug 10-14, 2026' style period from a row's begin/end dates."""
    b = normalize_date(row.get("report_begin_date") or "")
    e = normalize_date(row.get("report_end_date") or "")
    try:
        bd = datetime.strptime(b, "%Y-%m-%d")
        ed = datetime.strptime(e, "%Y-%m-%d")
    except ValueError:
        return e or b or ""
    if bd.month == ed.month:
        return f"{bd.strftime('%b')} {bd.day}-{ed.day}, {ed.year}"
    return f"{bd.strftime('%b')} {bd.day} - {ed.strftime('%b')} {ed.day}, {ed.year}"


def _structured_range(row):
    """(low, high) from a report row: prefer the 'mostly' range, else min/max."""
    for lo_k, hi_k in (("mostly_low_price", "mostly_high_price"), ("price_min", "price_max")):
        lo, hi = row.get(lo_k), row.get(hi_k)
        if lo not in (None, "") and hi not in (None, ""):
            try:
                return parse_num(lo), parse_num(hi), f"{lo_k}={lo}, {hi_k}={hi}"
            except (TypeError, ValueError):
                continue
    return None, None, None


def _grade_matches(row, code):
    """True if a report row is the structured line for this product. Report 1053's
    commodity is already 'Whey Protein Concentrate', so match on the grade number
    (34/80) in the grade fields; falls back to word aliases in the title/desc."""
    grade_key = WHEY_PRODUCTS[code].get("grade_key")
    grade_blob = (str(row.get("grade", "")) + " " + str(row.get("other_Grades", ""))).lower()
    if grade_key and grade_key in grade_blob:
        return True
    text_blob = " ".join(str(row.get(k, "")) for k in
                         ("report_title", "lot_Desc")).lower()
    return any(a in text_blob for a in WHEY_PRODUCTS[code]["aliases"])


def _build_product(code, low, high, source_type, source_report, report_id,
                   published_date, reporting_period, excerpt, status, note=None):
    mid = None if (low is None or high is None) else round((low + high) / 2, 4)
    if low is None or high is None:
        confidence = "low"
    elif source_type == "formal":
        confidence = "high"
    elif low == high:
        confidence = "low"
    else:
        confidence = "medium"

    if mid is not None:
        interp = (f"Market indication {code} ${low:.2f}-${high:.2f}/lb "
                  f"(midpoint ${mid:.2f}). This is a "
                  + ("formally published USDA range" if source_type == "formal"
                     else "DMN narrative reading")
                  + ", a market indication before freight and conversion costs — "
                  "not an executable quote or a transaction-weighted average.")
    else:
        interp = (f"No current {code} range parsed from the USDA source. "
                  "Shown for reference only.")

    return {
        "code": code,
        "name": WHEY_PRODUCTS[code]["name"],
        "low": low, "mid": mid, "high": high,
        "low_mt": to_mt(low), "mid_mt": to_mt(mid), "high_mt": to_mt(high),
        "reporting_period": reporting_period,
        "source_report": source_report,
        "source_report_id": report_id,
        "published_date": published_date,
        "last_verified": datetime.utcnow().isoformat() + "Z",
        "status": status,
        "confidence": confidence,
        "source_type": source_type,
        "excerpt": excerpt,
        "interpretation": interp,
        "note": note,
    }


def fetch_whey():
    """Fetch WPC34, WPC80 and WPI whey indications from report 1053.

    Report 1053 carries structured weekly rows (price_min/max, mostly_low/high,
    grade) plus a report narrative that also comments on WPC 80% and WPI. We scope
    everything to the LATEST report week: structured ranges where a grade row
    exists (formal, high confidence), narrative parsing otherwise (medium).
    """
    formal_src = "USDA AMS Dairy Market News, report 1053 (Whey Protein Concentrate, Central/West U.S.)"
    narr_src = "USDA AMS Dairy Market News, report 1053 (weekly whey narrative)"

    def missing(code, note):
        return _build_product(code, None, None, "narrative", narr_src,
                              WHEY_WPC34_REPORT, "", "", None, "unknown", note=note)

    try:
        raw = fetch_mars(WHEY_WPC34_REPORT)
    except Exception as e:
        print(f"  [whey] report {WHEY_WPC34_REPORT} fetch failed: {e}")
        note = f"Fetch failed: {e}"
        return [missing(c, note) for c in ("WPC80", "WPI", "WPC34")]

    results = raw.get("results", []) if isinstance(raw, dict) else []
    latest, latest_date = _latest_rows(results)
    if results:
        print(f"[DEBUG] report {WHEY_WPC34_REPORT}: {len(results)} rows, latest={latest_date}, "
              f"{len(latest)} latest-week rows")
        for r in latest[:8]:
            print(f"[DEBUG]   grade={r.get('grade')!r} other={r.get('other_Grades')!r} "
                  f"min={r.get('price_min')} max={r.get('price_max')} "
                  f"mostly={r.get('mostly_low_price')}-{r.get('mostly_high_price')}")

    period = _fmt_period(latest[0]) if latest else ""
    published = normalize_date(latest[0].get("published_date")) if latest else ""
    narrative = _collect_narrative(latest)

    products = {}

    # WPC34 / WPC80: structured row if a matching grade exists this week, else
    # fall back to the latest narrative.
    for code, stype_default in (("WPC34", "formal"), ("WPC80", "narrative")):
        aliases = WHEY_PRODUCTS[code]["aliases"]
        row = next((r for r in latest if _grade_matches(r, code)), None)
        lo = hi = None
        excerpt = None
        stype = stype_default
        if row is not None:
            lo, hi, excerpt = _structured_range(row)
            if lo is not None:
                stype = "formal"
        if lo is None:  # no structured row/price -> narrative
            lo, hi, excerpt = parse_whey_range(narrative, aliases)
            stype = "narrative"
        note = None if lo is not None else \
            f"No {code} range in report 1053 (structured or narrative) for {latest_date or 'latest week'}."
        products[code] = _build_product(
            code, lo, hi, stype,
            formal_src if stype == "formal" else narr_src,
            WHEY_WPC34_REPORT, published, period, excerpt,
            detect_status_near(narrative, aliases), note=note)

    # WPI: narrative only (report 1053 has no WPI grade rows).
    aliases = WHEY_PRODUCTS["WPI"]["aliases"]
    lo, hi, ex = parse_whey_range(narrative, aliases)
    products["WPI"] = _build_product(
        "WPI", lo, hi, "narrative", narr_src, WHEY_WPC34_REPORT,
        published, period, ex, detect_status_near(narrative, aliases),
        note=None if lo is not None else
        f"No WPI range in the report 1053 narrative for {latest_date or 'latest week'}.")

    out = [products[c] for c in ("WPC80", "WPI", "WPC34") if c in products]
    parsed = sum(1 for p in out if p["mid"] is not None)
    print(f"  whey: {parsed}/{len(out)} products parsed (week {latest_date})")
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

SUGAR_FUTURES_MONTHS = [2, 4, 6, 9]  # Mar(H), May(K), Jul(N), Oct(V)


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


def fetch_sugar_spot():
    """Fetch Sugar #11 (SB=F) daily prices from Yahoo Finance — 5 year history."""
    r = requests.get(
        f"{YAHOO_BASE}/SB=F",
        params={"interval": "1d", "range": "5y"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )
    if r.status_code != 200:
        raise Exception(f"Yahoo returned HTTP {r.status_code}")
    data = r.json()
    result = data.get("chart", {}).get("result", [])
    if not result:
        raise Exception("No chart data returned for SB=F")

    timestamps = result[0].get("timestamp", [])
    closes = result[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
    meta = result[0].get("meta", {})

    out = []
    for ts, close in zip(timestamps, closes):
        if close is not None and close > 0:
            dt = datetime.utcfromtimestamp(ts)
            out.append({
                "date": dt.strftime("%Y-%m-%d"),
                "price_cents_lb": round(close, 2),
                "price_usd_kg": round(close * 2.20462 / 100, 4),
            })
    out.sort(key=lambda x: x["date"])
    return out, meta.get("regularMarketPrice")


def fetch_sugar_futures():
    """Fetch Sugar #11 futures curve from Yahoo Finance (SB contracts on ICE/NYB)."""
    now = datetime.utcnow()
    symbols = []
    for offset in range(36):
        m = (now.month - 1 + offset) % 12
        y = now.year + (now.month - 1 + offset) // 12
        if m not in SUGAR_FUTURES_MONTHS:
            continue
        code = MONTH_CODES[m]
        sym = f"SB{code}{y % 100:02d}.NYB"
        symbols.append((sym, f"{y}-{m + 1:02d}", MONTH_NAMES[m], y))

    out = []
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
            out.append({
                "month": iso_month,
                "label": f"{month_name} {year % 100:02d}",
                "settle_cents_lb": round(price, 2),
                "settle_usd_kg": round(price * 2.20462 / 100, 4),
                "volume": volume or 0,
            })
        except Exception as e:
            print(f"  Skipping {sym}: {e}")
            continue

    out.sort(key=lambda x: x["month"])
    return out


def archive_futures_snapshot(trade_date, spot, curve):
    """Append today's futures curve to the rolling history file."""
    hist_path = DATA_DIR / "futures_history.json"
    if hist_path.exists():
        history = json.loads(hist_path.read_text())
    else:
        history = {"snapshots": []}

    history["snapshots"] = [
        s for s in history["snapshots"] if s["trade_date"] != trade_date
    ]

    history["snapshots"].append({
        "trade_date": trade_date,
        "spot": spot,
        "contracts": [{"month": c["month"], "settle": c["settle"]} for c in curve],
    })

    history["snapshots"].sort(key=lambda s: s["trade_date"])
    hist_path.write_text(json.dumps(history, indent=2))
    print(f"Archived futures snapshot for {trade_date} ({len(curve)} contracts, {len(history['snapshots'])} total snapshots)")


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
    failures = []

    print("Fetching NDPSR NFDM (report 2993)...")
    try:
        write_json("nass", fetch_ndpsr_nfdm())
    except Exception as e:
        print(f"NASS fetch failed: {e}")
        failures.append("nass")

    print("Fetching Class IV (report 2991)...")
    try:
        write_json("class_iv", fetch_class_iv())
    except Exception as e:
        print(f"Class IV fetch failed: {e}")
        failures.append("class_iv")

    print("Fetching CME spot (report 1603)...")
    try:
        write_json("cme", fetch_cme_spot())
    except Exception as e:
        print(f"CME fetch failed: {e}")
        failures.append("cme")

    print("Fetching whey ingredients (WPC80/WPI/WPC34 via MMN)...")
    try:
        write_json("whey", fetch_whey())
    except Exception as e:
        print(f"Whey fetch failed: {e}")
        failures.append("whey")

    print("Fetching fundamentals (NASS QuickStats)...")
    try:
        write_json("fundamentals", fetch_fundamentals())
    except Exception as e:
        print(f"Fundamentals fetch failed: {e}")
        failures.append("fundamentals")

    print("Fetching NFDM futures curve (Yahoo Finance)...")
    try:
        curve, spot = fetch_futures()
        trade_date = datetime.utcnow().strftime("%Y-%m-%d")
        path = DATA_DIR / "futures.json"
        payload = {
            "updated_at": datetime.utcnow().isoformat() + "Z",
            "trade_date": trade_date,
            "spot": spot,
            "count": len(curve),
            "data": curve,
        }
        path.write_text(json.dumps(payload, indent=2))
        print(f"Wrote {len(curve)} contracts to {path}")

        archive_futures_snapshot(trade_date, spot, curve)
    except Exception as e:
        print(f"Futures fetch failed: {e}")
        failures.append("futures")

    print("Fetching Sugar #11 spot (Yahoo Finance)...")
    try:
        sugar_data, sugar_current = fetch_sugar_spot()
        path = DATA_DIR / "sugar.json"
        payload = {
            "updated_at": datetime.utcnow().isoformat() + "Z",
            "current_cents_lb": sugar_current,
            "current_usd_kg": round(sugar_current * 2.20462 / 100, 4) if sugar_current else None,
            "count": len(sugar_data),
            "data": sugar_data,
        }
        path.write_text(json.dumps(payload, indent=2))
        print(f"Wrote {len(sugar_data)} days to {path}")
    except Exception as e:
        print(f"Sugar spot fetch failed: {e}")
        failures.append("sugar")

    print("Fetching Sugar #11 futures curve (Yahoo Finance)...")
    try:
        sugar_curve = fetch_sugar_futures()
        path = DATA_DIR / "sugar_futures.json"
        payload = {
            "updated_at": datetime.utcnow().isoformat() + "Z",
            "trade_date": datetime.utcnow().strftime("%Y-%m-%d"),
            "count": len(sugar_curve),
            "data": sugar_curve,
        }
        path.write_text(json.dumps(payload, indent=2))
        print(f"Wrote {len(sugar_curve)} contracts to {path}")
    except Exception as e:
        print(f"Sugar futures fetch failed: {e}")
        failures.append("sugar_futures")

    if failures:
        print(f"\nCompleted with {len(failures)} failure(s): {', '.join(failures)}")
        print("Partial data was still written for sources that succeeded.")
    else:
        print("\nDone — all sources fetched successfully.")
