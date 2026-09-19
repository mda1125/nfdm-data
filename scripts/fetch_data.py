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

def _month_end(month_str):
    """'YYYY-MM' -> 'YYYY-MM-DD' for the last day of that month."""
    if not month_str:
        return None
    year, mon = int(month_str[:4]), int(month_str[5:7])
    nxt = datetime(year + 1, 1, 1) if mon == 12 else datetime(year, mon + 1, 1)
    return (nxt - timedelta(days=1)).strftime("%Y-%m-%d")


# (key, label, filename, extractor(parsed_json) -> "YYYY-MM-DD" or None)
# Extractor reads the latest *data point's own date*, not updated_at (script
# run time) — a normal weekend/reporting-lag gap on a daily series shouldn't
# read as stale. Monthly sources use month-END (not month-start): Census/NASS
# publish ~5-6 weeks after a month closes, so measuring from month-start would
# make an on-schedule data point look ~30 days staler than it really is.
STATUS_SOURCES = [
    ("cme", "CME spot", "cme.json", lambda d: (d.get("data") or [{}])[-1].get("date")),
    ("butter", "CME butter", "butter.json", lambda d: (d.get("data") or [{}])[-1].get("date")),
    ("nass", "NASS NDPSR", "nass.json", lambda d: (d.get("data") or [{}])[-1].get("date")),
    ("class_iv", "FMMO Class IV", "class_iv.json", lambda d: (d.get("data") or [{}])[-1].get("date")),
    ("futures", "NFDM futures curve", "futures.json", lambda d: d.get("trade_date")),
    ("fundamentals", "Supply fundamentals", "fundamentals.json",
     lambda d: _month_end((d.get("data") or [{}])[-1].get("month"))),
    ("exports", "Export markets", "exports.json",
     lambda d: _month_end((d.get("data") or [{}])[-1].get("month"))),
    ("sugar", "Sugar #11", "sugar.json", lambda d: (d.get("data") or [{}])[-1].get("date")),
    ("cocoa", "Cocoa", "cocoa.json", lambda d: (d.get("data") or [{}])[-1].get("date")),
    ("whey", "Whey indications", "whey.json",
     lambda d: max([r.get("published_date") for r in (d.get("data") or []) if r.get("published_date")], default=None)),
]

# (fresh_through_days, stale_after_days) — anything beyond the second value
# is "stale"; between the two is "aging". fundamentals/exports are generous
# (measured from month-end) because a healthy ~5-6wk-lag release can leave
# the latest point looking ~70-80 days old right before the next one lands.
CADENCE_DAYS = {
    "cme": (4, 7), "butter": (4, 7), "nass": (8, 11), "class_iv": (40, 50),
    "futures": (4, 7), "fundamentals": (55, 85), "exports": (55, 85),
    "sugar": (4, 7), "cocoa": (4, 7), "whey": (8, 11),
}


def compute_status(failures):
    now = datetime.utcnow()
    sources = []
    for key, label, filename, extractor in STATUS_SOURCES:
        latest = None
        try:
            parsed = json.loads((DATA_DIR / filename).read_text())
            latest = extractor(parsed)
        except Exception:
            latest = None

        age_days = None
        if latest:
            try:
                age_days = (now - datetime.strptime(latest[:10], "%Y-%m-%d")).total_seconds() / 86400
            except Exception:
                age_days = None

        fresh_days, stale_days = CADENCE_DAYS[key]
        if key in failures:
            state = "error"
        elif age_days is None:
            state = "unknown"
        elif age_days <= fresh_days:
            state = "fresh"
        elif age_days <= stale_days:
            state = "aging"
        else:
            state = "stale"

        sources.append({
            "key": key,
            "label": label,
            "latest_date": latest,
            "age_days": round(age_days, 1) if age_days is not None else None,
            "expected_fresh_days": fresh_days,
            "expected_stale_days": stale_days,
            "fetch_ok_this_run": key not in failures,
            "state": state,
        })
    return sources


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


_PRICE_SKIP_KEYS = (
    "date", "report_date", "published_date", "commodity",
    "report_title", "slug_name", "slug_id", "narrative",
    "office_name", "office_code", "office_city", "office_state",
    "market_location_name", "market_location_city",
    "market_location_state", "market_type", "market_type_category",
    "created_date",
)


def _fetch_1603_rows():
    """Report 1603 (CME Group Daily Cash Trading WTD) via MMN API — requires
    DATAMART_API_KEY. Fetched once and shared by the NFDM and butter filters
    below so we only pay for the call once."""
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

        butter_rows = [r for r in results if "butter" in str(r).lower()]
        if butter_rows:
            print(f"[DEBUG] First butter-matching row: {json.dumps(butter_rows[0], indent=2)}")
        else:
            print(f"[DEBUG] No rows contain 'butter'.")

    return results


def _extract_price(row):
    """Pull the first positive numeric field from a report row, skipping the
    known non-price keys. Shared by the CME spot and butter filters."""
    for key in row:
        if key.lower() in _PRICE_SKIP_KEYS:
            continue
        val = row[key]
        if val is not None:
            try:
                p = parse_num(val)
                if p > 0:
                    print(f"[DEBUG] Using field '{key}' = {p} for price")
                    return p
            except (ValueError, TypeError):
                continue
    return None


def fetch_cme_spot():
    """Report 1603 (CME Group Daily Cash Trading WTD), NFDM rows.

    Confirmed live 2026-09-16 that report 1603 rows carry a clean, exact
    'commodity' field (e.g. "Nonfat Dry Milk", "Butter", "Dry Whey") — match
    on that field directly rather than the whole stringified row, which also
    contains report_title/narrative-ish text that could false-positive."""
    results = _fetch_1603_rows()

    out = []
    for row in results:
        try:
            commodity = str(row.get("commodity") or "").lower()
            if "nonfat" not in commodity:
                continue
            date = normalize_date(row.get("report_date") or row.get("published_date") or row.get("date"))
            price = _extract_price(row)
            if date and price:
                out.append({"date": date, "price": price})
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: x["date"])
    return out


def fetch_cme_butter():
    """Report 1603 (CME Group Daily Cash Trading WTD), Grade AA butter rows.

    Same 2026-09-16 confirmation as fetch_cme_spot(): matches the exact
    'commodity' field ("Butter") rather than the whole stringified row, so
    'buttermilk' (which isn't a commodity in this report but would still be
    excluded if it ever appeared) can't false-positive. grade came back as
    the exact string "Grade AA" for butter rows that day."""
    results = _fetch_1603_rows()

    out = []
    for row in results:
        try:
            commodity = str(row.get("commodity") or "").lower()
            if "butter" not in commodity or "buttermilk" in commodity:
                continue
            grade = str(row.get("grade") or "").lower()
            if grade and "aa" not in grade:
                continue
            date = normalize_date(row.get("report_date") or row.get("published_date") or row.get("date"))
            price = _extract_price(row)
            if date and price:
                out.append({"date": date, "price": price})
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: x["date"])
    return out


# ---------------------------------------------------------------------------
# Whey Market intelligence — USDA Dairy Market News (keyed MMN/MARS API, same as
# CME spot / report 1603). These products are NOT on the public MPR API.
#
# Report IDs confirmed from the live MARS catalog (all "Point of Sale - Dairy",
# same schema: price_min/max, mostly_low/high_price, grade, report_begin/end_date,
# report_narrative):
#   1053 Whey Protein Concentrate - Central & West   (WPC34 structured; WPC80/WPI narrative)
#   1045/1046/1047 Dry Whey - Central / West / East   (structured)
#   1052 Lactose - Central & West                     (structured)
#
# Structured products read the latest week's mostly/min-max range (formal, high
# confidence). Narrative products (WPC80/WPI) are regex-parsed from 1053's latest
# narrative (medium). No USDA report exists for MPC/MPI or whey permeate.
# ---------------------------------------------------------------------------
MT_PER_LB = 2204.62

WHEY_WPC34_REPORT = "1053"  # narrative source for WPC80/WPI

WHEY_PRODUCTS = {
    "WPC80": {"name": "Whey Protein Concentrate 80%",
              "aliases": ["wpc 80", "wpc80", "whey protein concentrate 80", "80% wpc", "wpc 80%"],
              "grade_key": "80"},
    "WPI": {"name": "Whey Protein Isolate",
            "aliases": ["whey protein isolate", "wpi", "protein isolate"],
            "grade_key": None},
    "WPC34": {"name": "Whey Protein Concentrate 34%",
              "aliases": ["wpc 34", "wpc34", "whey protein concentrate 34", "34% wpc", "wpc 34%"],
              "grade_key": "34"},
    "DRYWHEY": {"name": "Dry Whey (sweet whey powder)",
                "aliases": ["dry whey", "sweet whey"], "grade_key": None},
    "LACTOSE": {"name": "Lactose",
                "aliases": ["lactose"], "grade_key": None},
}

# Ordered product config: proteins first, then commodities. 'reports' lists the
# structured MARS report id(s) (multiple = regional, combined into a U.S. range).
WHEY_MARKET = [
    {"code": "WPC80", "mode": "narrative", "narrative_report": "1053"},
    {"code": "WPI", "mode": "narrative", "narrative_report": "1053"},
    {"code": "WPC34", "mode": "structured", "reports": ["1053"], "exclude_grade": "80"},
    {"code": "DRYWHEY", "mode": "structured", "reports": ["1045", "1046", "1047"],
     "include_grades": ["extra", "grade a", "edible"]},  # food-grade only (skip animal-feed rows)
    {"code": "LACTOSE", "mode": "structured", "reports": ["1052"]},
]

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


def _build_product(code, low, high, source_type, source_report, report_id,
                   published_date, reporting_period, excerpt, status, note=None,
                   wow_pct=None, prev_mid=None):
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
        "wow_pct": wow_pct,
        "prev_mid": prev_mid,
    }


_REPORT_CACHE = {}


def _get_report(rid):
    """Fetch a MARS report once per run (1053 powers WPC34/WPC80/WPI)."""
    if rid not in _REPORT_CACHE:
        _REPORT_CACHE[rid] = fetch_mars(rid)
    return _REPORT_CACHE[rid]


def fetch_structured_product(code, report_ids, exclude_grade=None, include_grades=None):
    """Latest-week structured $/lb range for a product, combining regional reports
    into one U.S. range. Returns a dict (low/high/excerpt/published/period/
    narrative) or None if nothing structured parsed.

    exclude_grade skips rows whose grade contains it (e.g. WPC's '80'); when
    include_grades is set a row must match one of them (e.g. food grades only,
    skipping animal-feed rows). A region with no qualifying row is dropped."""
    regions = []  # one entry per report that yielded a structured row
    narr_parts = []
    for rid in report_ids:
        try:
            raw = _get_report(rid)
        except Exception as e:
            print(f"  [whey] {code} report {rid} failed: {e}")
            continue
        results = raw.get("results", []) if isinstance(raw, dict) else []
        latest, wk = _latest_rows(results)
        narr_parts.append(_collect_narrative(latest))
        row = None
        for r in latest:
            gb = (str(r.get("grade", "")) + " " + str(r.get("other_Grades", "")) + " "
                  + str(r.get("application", ""))).lower()
            if exclude_grade and exclude_grade in gb:
                continue
            if include_grades and not any(g in gb for g in include_grades):
                continue
            if _structured_range(r)[0] is not None:
                row = r
                break
        if row is None:
            continue
        lo, hi, sx = _structured_range(row)
        regions.append({"week": wk, "low": lo, "high": hi,
                        "region": row.get("region") or rid,
                        "published": normalize_date(row.get("published_date")),
                        "period": _fmt_period(row), "grade": row.get("grade"), "sx": sx})
        print(f"[DEBUG] {code} report {rid}: week={wk} region={row.get('region')!r} "
              f"grade={row.get('grade')!r} mostly={lo}-{hi}")
    if not regions:
        return None

    freshest = max(r["week"] for r in regions if r["week"]) if any(r["week"] for r in regions) else ""
    fresh = [r for r in regions if r["week"] == freshest] or regions
    low = min(r["low"] for r in fresh)
    high = max(r["high"] for r in fresh)
    names = ", ".join(str(r["region"]) for r in fresh)
    pubs = [r["published"] for r in fresh if r["published"]]
    published = max(pubs) if pubs else ""
    if len(fresh) > 1:
        excerpt = f"{WHEY_PRODUCTS[code]['name']} — U.S. mostly range across {names}: ${low:.2f}–${high:.2f}/lb"
    else:
        excerpt = f"{WHEY_PRODUCTS[code]['name']} ({names}), {fresh[0]['grade']}: mostly ${low:.2f}–${high:.2f}/lb ({fresh[0]['sx']})"
    return {"low": low, "high": high, "excerpt": excerpt, "published": published,
            "period": fresh[0]["period"], "narrative": "\n".join(narr_parts)}


def fetch_whey():
    """Fetch the Whey Market products (config-driven; see WHEY_MARKET).

    Structured products (WPC34, Dry Whey, Lactose) read the latest week's
    mostly/min-max range from their MMN point-of-sale report(s), combining
    regional reports into a U.S. range (formal, high confidence). Narrative
    products (WPC80, WPI) are parsed from report 1053's latest narrative (medium).
    Each product degrades to null + note on failure rather than crashing the run.
    """
    products = {}
    for cfg in WHEY_MARKET:
        code = cfg["code"]
        aliases = WHEY_PRODUCTS[code]["aliases"]
        try:
            if cfg["mode"] == "structured":
                rids = cfg["reports"]
                src = f"USDA AMS Dairy Market News (report{'s' if len(rids) > 1 else ''} {'/'.join(rids)})"
                res = fetch_structured_product(code, rids, cfg.get("exclude_grade"),
                                               cfg.get("include_grades"))
                if res:
                    products[code] = _build_product(
                        code, res["low"], res["high"], "formal", src, "/".join(rids),
                        res["published"], res["period"], res["excerpt"],
                        detect_status_near(res["narrative"], aliases))
                else:
                    products[code] = _build_product(
                        code, None, None, "formal", src, "/".join(rids), "", "", None,
                        "unknown", note=f"No structured range in report(s) {'/'.join(rids)}.")
            else:  # narrative
                rid = cfg["narrative_report"]
                raw = _get_report(rid)
                results = raw.get("results", []) if isinstance(raw, dict) else []
                latest, wk = _latest_rows(results)
                narrative = _collect_narrative(latest)
                published = normalize_date(latest[0].get("published_date")) if latest else ""
                period = _fmt_period(latest[0]) if latest else ""
                lo, hi, ex = parse_whey_range(narrative, aliases)
                products[code] = _build_product(
                    code, lo, hi, "narrative",
                    f"USDA AMS Dairy Market News, report {rid} (weekly whey narrative)",
                    rid, published, period, ex, detect_status_near(narrative, aliases),
                    note=None if lo is not None else
                    f"No {code} range in report {rid} narrative for {wk or 'latest week'}.")
        except Exception as e:
            print(f"  [whey] {code} failed: {e}")
            products[code] = _build_product(
                code, None, None, "formal" if cfg["mode"] == "structured" else "narrative",
                "USDA AMS Dairy Market News",
                "/".join(cfg.get("reports", [])) or cfg.get("narrative_report", ""),
                "", "", None, "unknown", note=f"Fetch failed: {e}")

    out = [products[c["code"]] for c in WHEY_MARKET if c["code"] in products]
    parsed = sum(1 for p in out if p["mid"] is not None)
    print(f"  whey: {parsed}/{len(out)} products parsed")
    return out


def archive_whey_snapshot(products):
    """Upsert each product's midpoint into data/whey_history.json, keyed by the
    product's reporting week (published_date). Repeated daily runs in the same
    USDA week overwrite that week's entry, so history holds one point per week."""
    path = DATA_DIR / "whey_history.json"
    hist = json.loads(path.read_text()) if path.exists() else {"products": {}}
    ph = hist.setdefault("products", {})
    for p in products:
        week = p.get("published_date") or ""
        if p.get("mid") is None or not week:
            continue
        series = ph.setdefault(p["code"], [])
        series[:] = [s for s in series if s.get("week") != week]
        series.append({"week": week, "mid": p["mid"]})
        series.sort(key=lambda s: s["week"])
    hist["updated_at"] = datetime.utcnow().isoformat() + "Z"
    path.write_text(json.dumps(hist, indent=2))
    weeks = max((len(v) for v in ph.values()), default=0)
    print(f"Archived whey snapshot ({len(ph)} products, up to {weeks} weeks each)")
    return hist


def apply_whey_wow(products, history):
    """Attach week-over-week % change to each product from its history (two most
    recent distinct weeks). Null until at least two report weeks are archived."""
    ph = history.get("products", {})
    for p in products:
        series = ph.get(p["code"], [])
        by_week = sorted({s["week"]: s["mid"] for s in series}.items())
        if p.get("mid") is not None and len(by_week) >= 2:
            prev_mid = by_week[-2][1]
            if prev_mid:
                p["prev_mid"] = prev_mid
                p["wow_pct"] = round((p["mid"] - prev_mid) / prev_mid * 100, 2)


# ---------------------------------------------------------------------------
# Whey market call (WPC80): a single-number analyst-style guess for a rolling
# forward quarter, distinct from the Whey Booking Lean shown on the dashboard.
# Whey has no futures market and (so far) only a few weeks of published history,
# so there's no defensible curve-implied or seasonal price the way NFDM's
# Booking Signal works. This is explicitly a market call, not a forecast or a
# recommendation: one blended number from whatever signals exist that week,
# with each contributing method's own implied price shown next to it, logged
# weekly so the calls can be scored against realized prices once the target
# quarter's history actually publishes. The weighting scheme below is a
# tunable hypothesis, not a settled model -- that's the point of the log.
# ---------------------------------------------------------------------------
WHEY_CALL_PRODUCT = "WPC80"
WHEY_CALL_DAMPEN_WEEKS = 4  # weeks of the observed trend applied before holding flat


def _target_quarter(as_of):
    """Roll 2 full calendar quarters ahead of as_of (skip the current quarter and
    the next one), e.g. Sep 2026 (Q3) -> 2027 Q1. Recomputed every run so the
    target advances automatically as time passes."""
    q = (as_of.month - 1) // 3 + 1 + 2
    y = as_of.year + (q - 1) // 4
    q = (q - 1) % 4 + 1
    return y, q


def _quarter_mid_month(q):
    return (q - 1) * 3 + 2


def _weekly_rate(history):
    """Geometric average weekly % change across the trailing published weeks,
    and the latest mid. (None, None) if fewer than 2 weeks exist."""
    pts = sorted((s for s in (history or []) if s.get("mid") is not None), key=lambda s: s["week"])
    if len(pts) < 2 or not pts[0]["mid"]:
        return None, None
    first, last, weeks = pts[0]["mid"], pts[-1]["mid"], len(pts) - 1
    if weeks == 0:
        return None, None
    weekly = (last / first) ** (1.0 / weeks) - 1
    return weekly, last


def compute_whey_market_call(whey_history_products, futures_curve, spot, as_of=None):
    """One blended analyst-style price call for WHEY_CALL_PRODUCT at a rolling
    forward quarter, plus each contributing method's own implied price and
    reasoning. Returns None if there isn't enough WPC80 history yet."""
    as_of = as_of or datetime.utcnow().date()
    weekly_rate, current = _weekly_rate(whey_history_products.get(WHEY_CALL_PRODUCT))
    if weekly_rate is None or not current:
        return None

    ty, tq = _target_quarter(as_of)
    target_label = f"Q{tq} {ty}"
    weeks_out = max(0, round((datetime(ty, _quarter_mid_month(tq), 15) - datetime(as_of.year, as_of.month, as_of.day)).days / 7))

    # Method A: straight-line extrapolation of the observed weekly rate, no damping.
    naive_price = round(current * (1 + weekly_rate) ** weeks_out, 4)
    naive_note = (f"Straight-line extrapolation of the observed weekly move "
                  f"({weekly_rate * 100:+.2f}%/wk) held constant for all {weeks_out} weeks "
                  f"to {target_label}. Treats the whole trailing move as durable -- tends to "
                  f"overreact when the trailing window is short.")

    # Method B: same weekly rate, applied for a limited window, then held flat --
    # a short move is assumed to decelerate rather than persist in a straight line.
    damp_weeks = min(weeks_out, WHEY_CALL_DAMPEN_WEEKS)
    dampened_price = round(current * (1 + weekly_rate) ** damp_weeks, 4)
    dampened_note = (f"Same weekly move applied for {damp_weeks} more weeks, then held flat "
                      f"through {target_label} ({weeks_out - damp_weeks} weeks flat) -- assumes "
                      f"a short trend decelerates rather than continuing unchanged for months.")

    # Method C: cross-check against the NFDM forward curve for the same target
    # quarter -- a loose proxy (WPC80 comes off the cheese-whey stream, not skim
    # solids) but it captures the shared dairy-complex cycle.
    nfdm_price, nfdm_note = None, "NFDM futures curve unavailable this run -- cross-check skipped."
    if futures_curve and spot:
        q_settles = [c["settle"] for c in futures_curve
                     if len(c.get("month", "").split("-")) == 2
                     and c["month"].split("-")[0].isdigit()
                     and int(c["month"].split("-")[0]) == ty
                     and (int(c["month"].split("-")[1]) - 1) // 3 + 1 == tq]
        if q_settles:
            q_avg = sum(q_settles) / len(q_settles)
            discount = (q_avg - spot) / spot
            nfdm_price = round(current * (1 + discount), 4)
            nfdm_note = (f"NFDM curve prices {target_label} at {discount * 100:+.1f}% vs current spot; "
                         f"applying that same move to WPC80's current price as a dairy-complex "
                         f"cross-check (loose proxy -- different raw stream than NFDM).")

    # Qualitative signal: the documented WPC80 vs WPC34/dry-whey reallocation
    # pattern (mirrors WHEY_REALLOC_TRIO in app.js). When active, WPC80's move
    # has a specific, product-level explanation rather than being generic noise,
    # so the blend leans harder on WPC80's own trend and lighter on the NFDM
    # cross-check; otherwise it leans the other way as a hedge against noise.
    wpc34_rate, _ = _weekly_rate(whey_history_products.get("WPC34"))
    dryw_rate, _ = _weekly_rate(whey_history_products.get("DRYWHEY"))
    realloc_active = bool(
        wpc34_rate is not None and dryw_rate is not None
        and abs(weekly_rate) > 0.001 and abs(wpc34_rate) > 0.001 and abs(dryw_rate) > 0.001
        and (wpc34_rate > 0) == (dryw_rate > 0) and (wpc34_rate > 0) != (weekly_rate > 0)
    )
    if realloc_active:
        weights = {"naive": 0.20, "dampened": 0.60, "nfdm": 0.20}
        qual_note = ("WPC80 is moving opposite WPC34 and dry whey together -- the reallocation "
                     "pattern USDA's Jun 2026 narrative described (manufacturers shift output "
                     "between these off the same finite whey stream as WPC80 demand shifts). "
                     "Documented, product-specific explanation, so this call leans more on "
                     "WPC80's own trend and less on the generic NFDM cross-check.")
    else:
        weights = {"naive": 0.10, "dampened": 0.55, "nfdm": 0.35}
        qual_note = ("No reallocation pattern active this week -- no product-specific "
                     "explanation for WPC80's move, so this call weighs the dampened trend "
                     "and the NFDM cross-check more evenly as a hedge against noise.")

    prices = {"naive": naive_price, "dampened": dampened_price}
    if nfdm_price is not None:
        prices["nfdm"] = nfdm_price
    else:
        w_sum = weights["naive"] + weights["dampened"]
        weights = {"naive": weights["naive"] / w_sum, "dampened": weights["dampened"] / w_sum, "nfdm": 0.0}
    blended = round(sum(prices[k] * weights[k] for k in prices), 4)

    return {
        "as_of": as_of.isoformat(),
        "iso_week": f"{as_of.isocalendar()[0]}-W{as_of.isocalendar()[1]:02d}",
        "product": WHEY_CALL_PRODUCT,
        "target_quarter": f"{ty}-Q{tq}",
        "target_label": target_label,
        "weeks_out": weeks_out,
        "current_price": current,
        "methods": {
            "naive": {"price": naive_price, "weight": round(weights["naive"], 3), "note": naive_note},
            "dampened": {"price": dampened_price, "weight": round(weights["dampened"], 3), "note": dampened_note},
            "nfdm_crosscheck": {"price": nfdm_price, "weight": round(weights.get("nfdm", 0), 3), "note": nfdm_note},
        },
        "qualitative": {"reallocation_active": realloc_active, "note": qual_note},
        "blended": blended,
        "range": {"low": round(min(prices.values()), 4), "high": round(max(prices.values()), 4)},
    }


def archive_whey_market_call(entry):
    """Append this week's call to data/whey_market_call.json, deduped by ISO
    week (a re-run mid-week overwrites that week's entry with the latest info,
    rather than duplicating it). The log itself is the point: it's what
    eventually lets us score naive vs dampened vs qualitative-adjusted calls
    against realized prices, instead of guessing from one data point."""
    if entry is None:
        return None
    path = DATA_DIR / "whey_market_call.json"
    doc = json.loads(path.read_text()) if path.exists() else {"log": []}
    log = doc.setdefault("log", [])
    log[:] = [e for e in log if e.get("iso_week") != entry["iso_week"]]
    log.append(entry)
    log.sort(key=lambda e: e["as_of"])
    doc["updated_at"] = datetime.utcnow().isoformat() + "Z"
    doc["latest"] = log[-1]
    path.write_text(json.dumps(doc, indent=2))
    print(f"Whey market call: {entry['target_label']} blended ${entry['blended']:.2f}/lb "
          f"(range ${entry['range']['low']:.2f}-${entry['range']['high']:.2f}), "
          f"{len(log)} weekly entries logged")
    return doc


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


CENSUS_KEY = os.environ.get('CENSUS_API_KEY', '')
CENSUS_EXPORTS_BASE = "https://api.census.gov/data/timeseries/intltrade/exports/hs"
# Census only publishes QTY_1_MO/UNIT_QY1 at the 10-digit HS10 level, not HS6 —
# at HS6 (COMM_LVL=HS6, E_COMMODITY=040210) quantity comes back as 0/"-" for
# every row. 0402100000 is the sole HS10 code under HS6 040210 (verified live —
# no sibling codes), so nothing is lost by querying at HS10 instead.
NFDM_HS_CODE = "0402100000"
NFDM_HS_DESC = "Milk and cream in powder, fat content <= 1.5% (NFDM/SMP)"
KG_TO_LB = 2.20462
EXPORTS_START_YEAR = 2018
EXPORTS_TOP_N = 5


def fetch_census_exports(year):
    """Fetch one calendar year of NFDM/SMP exports by destination from Census."""
    params = {
        "get": "CTY_CODE,CTY_NAME,ALL_VAL_MO,QTY_1_MO,UNIT_QY1,YEAR,MONTH",
        "E_COMMODITY": NFDM_HS_CODE,
        "COMM_LVL": "HS10",
        "SUMMARY_LVL": "DET",
        "time": str(year),
        "key": CENSUS_KEY,
    }
    rows = fetch_with_retry(CENSUS_EXPORTS_BASE, params=params)
    if not rows or len(rows) < 2:
        return []
    header = rows[0]
    return [dict(zip(header, row)) for row in rows[1:]]


def fetch_exports():
    """Fetch NFDM/SMP (HS 040210) exports by destination from the Census trade API.

    Returns (rows, top_countries): one dict per month, plus the ranked top-N
    destination metadata the dashboard uses for labels and colors.
    """
    if not CENSUS_KEY:
        print("  CENSUS_API_KEY not set, skipping exports")
        return [], []

    by_month = {}   # "2026-05" -> {"MEXICO": {"volume_lb": x, "value_usd": y}, ...}
    world = {}       # "2026-05" -> {"volume_lb": x, "value_usd": y}  (CTY_CODE == "-")
    cty_codes = {}   # "MEXICO" -> "2010"
    this_year = datetime.utcnow().year

    for year in range(EXPORTS_START_YEAR, this_year + 1):
        try:
            raw = fetch_census_exports(year)
        except Exception as e:
            print(f"  {year} failed: {e}")
            continue
        kept = 0
        for row in raw:
            code = (row.get("CTY_CODE") or "").strip()
            name = (row.get("CTY_NAME") or "").strip()
            yr, mo = row.get("YEAR"), row.get("MONTH")
            if not code or not yr or not mo:
                continue
            try:
                month = f"{yr}-{int(mo):02d}"
            except (ValueError, TypeError):
                continue
            val = parse_num(row.get("ALL_VAL_MO"))
            qty = parse_num(row.get("QTY_1_MO"))
            unit = (row.get("UNIT_QY1") or "").strip().upper()
            vol = qty * KG_TO_LB if unit in ("KG", "") else 0.0
            if code == "-":
                t = world.setdefault(month, {"volume_lb": 0.0, "value_usd": 0.0})
                t["volume_lb"] += vol
                t["value_usd"] += val
            else:
                c = by_month.setdefault(month, {}).setdefault(
                    name, {"volume_lb": 0.0, "value_usd": 0.0})
                c["volume_lb"] += vol
                c["value_usd"] += val
                cty_codes.setdefault(name, code)
            kept += 1
        print(f"  {year}: {kept} country-month rows")

    months = sorted(by_month.keys())
    if not months:
        return [], []

    # Rank top N by trailing-12-month volume (falls back to value if volume is sparse).
    ttm_months = months[-12:]
    totals = {}
    for m in ttm_months:
        for name, c in by_month[m].items():
            t = totals.setdefault(name, {"volume_lb": 0.0, "value_usd": 0.0})
            t["volume_lb"] += c["volume_lb"]
            t["value_usd"] += c["value_usd"]

    rank_key = "volume_lb" if any(v["volume_lb"] for v in totals.values()) else "value_usd"
    ranked = sorted(totals.items(), key=lambda kv: kv[1][rank_key], reverse=True)[:EXPORTS_TOP_N]
    ttm_world_lb = sum(world.get(m, {}).get("volume_lb", 0.0) for m in ttm_months)
    ttm_world_usd = sum(world.get(m, {}).get("value_usd", 0.0) for m in ttm_months)

    top_countries = []
    for i, (name, t) in enumerate(ranked):
        share_base = ttm_world_lb if rank_key == "volume_lb" else ttm_world_usd
        share = round(t[rank_key] / share_base * 100, 1) if share_base else 0.0
        top_countries.append({
            "rank": i + 1,
            "code": cty_codes.get(name, ""),
            "name": name,
            "ttm_lb": round(t["volume_lb"]),
            "ttm_usd": round(t["value_usd"]),
            "ttm_share_pct": share,
        })

    top_names = [c["name"] for c in top_countries]

    out = []
    for m in months:
        countries = by_month[m]
        row_total_lb = world.get(m, {}).get("volume_lb") or sum(
            c["volume_lb"] for c in countries.values())
        row_total_usd = world.get(m, {}).get("value_usd") or sum(
            c["value_usd"] for c in countries.values())
        if not row_total_lb and not row_total_usd:
            continue

        top_lb = sum(countries.get(n, {}).get("volume_lb", 0.0) for n in top_names)
        top_usd = sum(countries.get(n, {}).get("value_usd", 0.0) for n in top_names)
        if row_total_lb and top_lb > row_total_lb * 1.02:
            print(f"  warning: {m} top-{EXPORTS_TOP_N} exceeds world total "
                  f"({top_lb / 1e6:.1f}M vs {row_total_lb / 1e6:.1f}M) — check grain/groupings")

        row = {
            "month": m,
            "total_lb": round(row_total_lb),
            "total_usd": round(row_total_usd),
            "countries": {
                n: {"volume_lb": round(countries[n]["volume_lb"]),
                    "value_usd": round(countries[n]["value_usd"])}
                for n in top_names if n in countries
            },
            "rest_of_world": {
                "volume_lb": round(max(0.0, row_total_lb - top_lb)),
                "value_usd": round(max(0.0, row_total_usd - top_usd)),
            },
        }
        out.append(row)

    return out, top_countries


MONTH_CODES = "FGHJKMNQUVXZ"
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
YAHOO_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"

SUGAR_FUTURES_MONTHS = [2, 4, 6, 9]  # Mar(H), May(K), Jul(N), Oct(V)
COCOA_FUTURES_MONTHS = [2, 4, 6, 8, 11]  # Mar(H), May(K), Jul(N), Sep(U), Dec(Z)
MT_TO_LB = 2204.62


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


def fetch_cocoa_spot():
    """Fetch NY cocoa (CC=F) daily prices from Yahoo Finance — 5 year history."""
    r = requests.get(
        f"{YAHOO_BASE}/CC=F",
        params={"interval": "1d", "range": "5y"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )
    if r.status_code != 200:
        raise Exception(f"Yahoo returned HTTP {r.status_code}")
    data = r.json()
    result = data.get("chart", {}).get("result", [])
    if not result:
        raise Exception("No chart data returned for CC=F")

    timestamps = result[0].get("timestamp", [])
    closes = result[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
    meta = result[0].get("meta", {})

    out = []
    for ts, close in zip(timestamps, closes):
        if close is not None and close > 0:
            dt = datetime.utcfromtimestamp(ts)
            out.append({
                "date": dt.strftime("%Y-%m-%d"),
                "price_usd_mt": round(close, 2),
                "price_usd_lb": round(close / MT_TO_LB, 4),
            })
    out.sort(key=lambda x: x["date"])
    return out, meta.get("regularMarketPrice")


def fetch_cocoa_futures():
    """Fetch NY cocoa futures curve from Yahoo Finance (CC contracts on ICE US/NYB)."""
    now = datetime.utcnow()
    symbols = []
    for offset in range(36):
        m = (now.month - 1 + offset) % 12
        y = now.year + (now.month - 1 + offset) // 12
        if m not in COCOA_FUTURES_MONTHS:
            continue
        code = MONTH_CODES[m]
        sym = f"CC{code}{y % 100:02d}.NYB"
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
                "settle_usd_mt": round(price, 2),
                "settle_usd_lb": round(price / MT_TO_LB, 4),
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

    print("Fetching CME butter (report 1603)...")
    try:
        write_json("butter", fetch_cme_butter())
    except Exception as e:
        print(f"Butter fetch failed: {e}")
        failures.append("butter")

    print("Fetching Whey Market (WPC/WPI/Dry Whey/Lactose via MMN)...")
    try:
        whey_products = fetch_whey()
        whey_history = archive_whey_snapshot(whey_products)
        apply_whey_wow(whey_products, whey_history)
        write_json("whey", whey_products)
    except Exception as e:
        print(f"Whey fetch failed: {e}")
        failures.append("whey")

    print("Fetching fundamentals (NASS QuickStats)...")
    try:
        write_json("fundamentals", fetch_fundamentals())
    except Exception as e:
        print(f"Fundamentals fetch failed: {e}")
        failures.append("fundamentals")

    print("Fetching NFDM/SMP exports by destination (Census trade API)...")
    try:
        exp_rows, exp_top = fetch_exports()
        if exp_rows:
            payload = {
                "updated_at": datetime.utcnow().isoformat() + "Z",
                "hs_code": NFDM_HS_CODE,
                "hs_desc": NFDM_HS_DESC,
                "unit": "lb",
                "top_countries": exp_top,
                "count": len(exp_rows),
                "data": exp_rows,
            }
            (DATA_DIR / "exports.json").write_text(json.dumps(payload, indent=2))
            print(f"  wrote {len(exp_rows)} months to data/exports.json")
        else:
            print("  no export rows — leaving data/exports.json unchanged")
    except Exception as e:
        print(f"Exports fetch failed: {e}")
        failures.append("exports")

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

    print(f"Computing whey market call ({WHEY_CALL_PRODUCT})...")
    try:
        whey_call = compute_whey_market_call(whey_history.get("products", {}), curve, spot)
        archive_whey_market_call(whey_call)
    except Exception as e:
        print(f"Whey market call failed: {e}")
        failures.append("whey_call")

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

    print("Fetching NY Cocoa spot (Yahoo Finance)...")
    try:
        cocoa_data, cocoa_current = fetch_cocoa_spot()
        path = DATA_DIR / "cocoa.json"
        payload = {
            "updated_at": datetime.utcnow().isoformat() + "Z",
            "current_usd_mt": cocoa_current,
            "current_usd_lb": round(cocoa_current / MT_TO_LB, 4) if cocoa_current else None,
            "count": len(cocoa_data),
            "data": cocoa_data,
        }
        path.write_text(json.dumps(payload, indent=2))
        print(f"Wrote {len(cocoa_data)} days to {path}")
    except Exception as e:
        print(f"Cocoa spot fetch failed: {e}")
        failures.append("cocoa")

    print("Fetching NY Cocoa futures curve (Yahoo Finance)...")
    try:
        cocoa_curve = fetch_cocoa_futures()
        path = DATA_DIR / "cocoa_futures.json"
        payload = {
            "updated_at": datetime.utcnow().isoformat() + "Z",
            "trade_date": datetime.utcnow().strftime("%Y-%m-%d"),
            "count": len(cocoa_curve),
            "data": cocoa_curve,
        }
        path.write_text(json.dumps(payload, indent=2))
        print(f"Wrote {len(cocoa_curve)} contracts to {path}")
    except Exception as e:
        print(f"Cocoa futures fetch failed: {e}")
        failures.append("cocoa_futures")

    if failures:
        print(f"\nCompleted with {len(failures)} failure(s): {', '.join(failures)}")
        print("Partial data was still written for sources that succeeded.")
    else:
        print("\nDone — all sources fetched successfully.")

    status = compute_status(failures)
    (DATA_DIR / "status.json").write_text(json.dumps({
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "sources": status,
    }, indent=2))
    stale_or_error = [s["key"] for s in status if s["state"] in ("stale", "error")]
    if stale_or_error:
        print(f"Data health: {len(stale_or_error)} source(s) stale/error: {', '.join(stale_or_error)}")
    else:
        print("Data health: all sources fresh or aging.")
