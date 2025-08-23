
import httpx, asyncio

async def resolve_iso3(country_name: str | None) -> str | None:
    if not country_name:
        return None
    cn = country_name.strip()
    if len(cn) == 3 and cn.isalpha():
        return cn.upper()
    # NOTE: 簡易のため省略（本番はWB country APIで検索）
    return None

async def fetch_wb_indicator(iso3: str, code: str):
    url = f"https://api.worldbank.org/v2/country/{iso3}/indicator/{code}?format=json&per_page=1"
    async with httpx.AsyncClient(timeout=25) as client:
        r = await client.get(url)
        r.raise_for_status()
        js = r.json()
        try:
            return js[1][0]["value"]
        except Exception:
            return None

async def fetch_wb_profile(country_name: str | None):
    iso3 = await resolve_iso3(country_name) if country_name else None
    if not iso3:
        return {}
    series = {
        "NY.GDP.MKTP.CD": "gdp_nom_usd",
        "NY.GDP.PCAP.CD": "gdp_pc_usd",
        "NE.GDI.FTOT.ZS": "investment_rate",
        "NE.TRD.GNFS.ZS": "openness",
        "FP.CPI.TOTL.ZG": "inflation_cpi",
        "SP.POP.GROW":    "pop_growth"
    }
    tasks = [fetch_wb_indicator(iso3, code) for code in series.keys()]
    vals = await asyncio.gather(*tasks, return_exceptions=True)
    out = {}
    for (code, key), v in zip(series.items(), vals):
        if not isinstance(v, Exception) and v is not None:
            try:
                out[key] = float(v)
            except Exception:
                pass
    return out
