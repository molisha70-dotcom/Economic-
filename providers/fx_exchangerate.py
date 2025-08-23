
import httpx

async def fetch_fx(base: str = "USD"):
    try:
        url = f"https://api.exchangerate.host/latest?base={base}"
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url)
            r.raise_for_status()
            js = r.json()
            return {"base": base, "date": js.get("date"), "rates": js.get("rates", {})}
    except Exception:
        return {"base": base, "rates": {}}
