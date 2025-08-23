
# providers/data_worldbank.py
import httpx
from typing import Optional, Dict, Any, List

WB_BASE = "https://api.worldbank.org/v2"

# 最低限のISO3フォールバック（失敗時でも動かすため）
ISO3_FALLBACK = {
    "japan": "JPN",
    "united states": "USA",
    "united kingdom": "GBR",
    "germany": "DEU",
    "france": "FRA",
    "italy": "ITA",
    "spain": "ESP",
    "vietnam": "VNM",
    "india": "IND",
    "china": "CHN",
    "korea": "KOR",
}

def resolve_iso3(country_name: str) -> Optional[str]:
    if not country_name:
        return None
    key = country_name.strip().lower()
    if key in ISO3_FALLBACK:
        return ISO3_FALLBACK[key]
    # Web API: 国一覧から検索
    try:
        url = f"{WB_BASE}/country?format=json&per_page=400"
        r = httpx.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        rows: List[Dict[str, Any]] = data[1]
        # 名前/別名/ISO2/ISO3のいずれかにヒットさせる
        for row in rows:
            names = [
                (row.get("name") or "").lower(),
                (row.get("region", {}).get("value") or "").lower(),
                (row.get("iso2Code") or "").lower(),
                (row.get("id") or "").lower(),
            ]
            if key in names:
                return row.get("id")
            # 部分一致（Japan / 日本 など曖昧対応）
            if key in names[0]:
                return row.get("id")
    except Exception:
        pass
    return None

def _latest_non_null(series: List[Dict[str, Any]]) -> Optional[float]:
    # world bank series は [0]に古い年が来る場合あり。後ろから探す
    for row in reversed(series):
        v = row.get("value")
        if v is not None:
            return float(v)
    return None

def fetch_country_profile(country_name: str) -> Optional[Dict[str, Any]]:
    # 既存の fetch_country_profile(...) のすぐ下に追加
def fetch_wb_profile(country_name: str):
    return fetch_country_profile(country_name)
    iso3 = resolve_iso3(country_name)
    if not iso3:
        return None
    # 主要指標
    IND = {
        "gdp": "NY.GDP.MKTP.CD",
        "gdp_pc": "NY.GDP.PCAP.CD",
        "invest_rate": "NE.GDI.FTOT.ZS",
        "openness": "NE.TRD.GNFS.ZS",
        "inflation": "FP.CPI.TOTL.ZG",
        "pop_grow": "SP.POP.GROW",
    }
    try:
        ind_list = ",".join(IND.values())
        url = f"{WB_BASE}/country/{iso3}/indicator/{ind_list}?format=json&per_page=20000"
        r = httpx.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()[1]  # [0]にメタ、[1]にデータ
        # seriesごとに分ける
        by_code: Dict[str, List[Dict[str, Any]]] = {}
        for row in data:
            code = row.get("indicator", {}).get("id")
            by_code.setdefault(code, []).append({"date": row.get("date"), "value": row.get("value")})

        gdp = _latest_non_null(by_code.get(IND["gdp"], []))
        invest = _latest_non_null(by_code.get(IND["invest_rate"], []))
        open_ = _latest_non_null(by_code.get(IND["openness"], []))
        infl = _latest_non_null(by_code.get(IND["inflation"], []))
        pop = _latest_non_null(by_code.get(IND["pop_grow"], []))

        # 所得ティアを取得（高・中・低）。日本は "HIC" → high_income
        url2 = f"{WB_BASE}/country/{iso3}?format=json"
        r2 = httpx.get(url2, timeout=10)
        r2.raise_for_status()
        meta = r2.json()[1][0]
        income_id = (meta.get("incomeLevel", {}) or {}).get("id")  # HIC, MIC, LIC 等

        def tier_from_income(x: str) -> str:
            if x == "HIC":
                return "high_income"
            if x in ("UMC", "LMC", "MIC"):
                return "middle_income"
            if x in ("LIC",):
                return "low_income"
            return "middle_income"

        tier = tier_from_income(income_id or "")

        profile = {
            "display_name": meta.get("name") or country_name,
            "iso3": iso3,
            "baseline_gdp_usd": gdp,
            "income_tier": tier,
            "inflation_recent": infl,               # % 表示
            "openness_ratio": (open_ / 100.0) if open_ is not None else None,
            "investment_rate": (invest / 100.0) if invest is not None else None,
            "labor_growth": pop,                    # % （必要に応じて/100してください）
            "debt_to_gdp": None,
        }
        return profile
    except Exception:
        return None

def fetch_wb_profile(country_name: str):
    # 互換用。昔のコードが呼んでも fetch_country_profile を返す
    return fetch_country_profile(country_name)
