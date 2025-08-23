
# core/orchestrator.py
import os, asyncio, json, yaml
from typing import Dict, Any
from .schemas import JSON_SCHEMA, coerce_extract_output
from .ensemble import merge_outputs
from .model import forecast
from .cache import get_cache, cache_key

from providers.llm_openai import extract_policies_openai   # async
from providers.llm_gemini import extract_policies_gemini   # async
# from providers.llm_claude import extract_policies_claude # 使うなら async
from providers.llm_local import extract_policies_local     # ← これは同期関数！
from providers.data_worldbank import fetch_country_profile as fetch_wb_profile
from providers.data_imf import fetch_imf_profile
from providers.fx_exchangerate import fetch_fx
from providers.data_comtrade import fetch_comtrade

import asyncio

async def _run_sync(func, *args, **kwargs):
    """同期関数をスレッドで非ブロッキング実行（awaitable化）"""
    return await asyncio.to_thread(func, *args, **kwargs)

with open("tiers.yml", "r", encoding="utf-8") as f:
    TIERS = yaml.safe_load(f)
    
# ==== ティア定義（importsの直後に置く）====
TIERS = {
    "high_income": {
        "potential_g": 2.0,
        "capital_share": 0.35,
        "inflation_target": 2.0,
        "fiscal_multiplier": {"capex": 0.8, "current": 0.4},
        "trade_elasticity": 0.2,
        "tfp_coeff": {
            "logistics": 0.15, "automation": 0.25, "education": 0.15, "regulation": 0.25,
            "governance": 0.2, "energy": 0.1, "infrastructure": 0.1, "trade": 0.15,
            "industry": 0.15, "finance": 0.1, "security": 0.1
        },
        "default_lags": {"infra": 2, "ports": 2, "education": 3, "regulation": 1}
    },
    "middle_income": {
        "potential_g": 4.0,
        "capital_share": 0.35,
        "inflation_target": 4.0,
        "fiscal_multiplier": {"capex": 1.0, "current": 0.5},
        "trade_elasticity": 0.3,
        "tfp_coeff": {
            "logistics": 0.2, "automation": 0.3, "education": 0.2, "regulation": 0.3,
            "governance": 0.2, "energy": 0.15, "infrastructure": 0.15, "trade": 0.2,
            "industry": 0.2, "finance": 0.1, "security": 0.1
        },
        "default_lags": {"infra": 2, "ports": 2, "education": 3, "regulation": 1}
    },
    "low_income": {
        "potential_g": 5.5,
        "capital_share": 0.35,
        "inflation_target": 5.0,
        "fiscal_multiplier": {"capex": 1.2, "current": 0.6},
        "trade_elasticity": 0.35,
        "tfp_coeff": {
            "logistics": 0.25, "automation": 0.2, "education": 0.25, "regulation": 0.25,
            "governance": 0.2, "energy": 0.2, "infrastructure": 0.2, "trade": 0.25,
            "industry": 0.25, "finance": 0.1, "security": 0.1
        },
        "default_lags": {"infra": 2, "ports": 2, "education": 3, "regulation": 1}
    },
}

def _normalize_tier(name: str | None) -> str:
    """HIC/MIC/LIC や表記ゆれを規格化してキー（*_income）に揃える"""
    if not name:
        return "middle_income"
    s = str(name).strip().lower()
    if s in ("hic", "high", "high income", "high_income"):
        return "high_income"
    if s in ("lic", "low", "low income", "low_income"):
        return "low_income"
    # umc/lmc/mic などはまとめて middle
    if any(x in s for x in ("umc", "lmc", "mic", "middle")):
        return "middle_income"
    return "middle_income"

def get_tier_params(income_tier: str | None) -> dict:
    key = _normalize_tier(income_tier)
    # .get で安全に、なければ middle_income を返す
    return TIERS.get(key, TIERS["middle_income"])


_channel_overrides: Dict[int, Dict[str, Any]] = {}
_channel_explain: Dict[int, str] = {}

def set_overrides_for_channel(ch: int, overrides: Dict[str, Any]):
    _channel_overrides[ch] = overrides

def get_overrides_for_channel(ch: int) -> Dict[str, Any]:
    return _channel_overrides.get(ch, {})

def set_last_explain_for_channel(ch: int, explain: str):
    _channel_explain[ch] = explain

def get_last_explain_for_channel(ch: int) -> str | None:
    return _channel_explain.get(ch)


async def extract_policies(text: str):
    tasks = []
    # ここには「await できるもの（async関数呼び出し）」だけを入れる
    if os.getenv("OPENAI_API_KEY"):
        tasks.append(extract_policies_openai(text))
    if os.getenv("GEMINI_API_KEY"):
        tasks.append(extract_policies_gemini(text))
    # if os.getenv("ANTHROPIC_API_KEY"):
    #     tasks.append(extract_policies_claude(text))

    results = []
    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)

    # ローカル抽出は同期関数なので、gather に混ぜず “あとから” 追加
    try:
        results.append(extract_policies_local(text))
    except Exception as e:
        results.append(e)

    # 以降は変えずにOK（JSON → 正規化 → 合議）
    valids = []
    for r in results:
        if isinstance(r, Exception):
            continue
        try:
            data_obj = json.loads(r) if isinstance(r, str) else r
            data_norm = coerce_extract_output(data_obj)
            mname = (data_obj.get("_model_name") if isinstance(data_obj, dict) else None) or "unknown"
            data_norm["_model_name"] = mname
            valids.append(data_norm)
        except Exception:
            continue

    if not valids:
        raise RuntimeError("政策抽出に失敗（全プロバイダ）")

    return merge_outputs(valids)
    return merged
            
def pick_tier(gdp_pc: float | None) -> str:
    if gdp_pc is None:
        return "middle_income"
    if gdp_pc < 1500: return "low_income"
    if gdp_pc < 13000: return "middle_income"
    return "high_income"

def fuse_profile(wb, imf, fx, trade, overrides, country_name: str) -> dict:
    prof: dict = {}

    # 1) 最優先：World Bank
    if isinstance(wb, dict):
        for k in ("display_name","iso3","baseline_gdp_usd","income_tier",
                  "inflation_recent","openness_ratio","investment_rate",
                  "labor_growth","debt_to_gdp"):
            if wb.get(k) is not None:
                prof[k] = wb[k]
    tier_name = pick_tier(gdp_pc)
    tier = TIERS["tiers"][tier_name]

    prof = {
        "display_name": country or "Unknown",
        "baseline_gdp_usd": overrides.get("baseline_gdp_usd", baseline_gdp or 1e10),
        "income_tier": tier_name,
        "inflation_recent": overrides.get("inflation_recent", infl if infl is not None else tier.get("inflation_target", 4.0)),
        "openness_ratio": overrides.get("openness_ratio", wb.get("openness", 0.8)/100.0 if wb.get("openness") else 0.8),
        "investment_rate": overrides.get("investment_rate", wb.get("investment_rate", 25.0)/100.0 if wb.get("investment_rate") else 0.25),
        "labor_growth": overrides.get("labor_growth", wb.get("pop_growth", 1.0)*100 if wb.get("pop_growth") else 1.0),
        "debt_to_gdp": overrides.get("debt_to_gdp", imf.get("debt_to_gdp") if imf else 0.5),
        "tier_params": tier
    }
        prof.setdefault("display_name", country_name)
    prof.setdefault("baseline_gdp_usd", 1.0e10)
    prof.setdefault("income_tier", "middle_income")
    prof.setdefault("inflation_recent", 4.0)
    prof.setdefault("openness_ratio", 0.8)
    prof.setdefault("investment_rate", 0.25)
    prof.setdefault("labor_growth", 1.0)
    prof.setdefault("debt_to_gdp", 0.5)

    # 4) overrides の反映
    if overrides:
        for k, v in overrides.items():
            if v is not None:
                prof[k] = v

    # 5) ティアパラメータを必ず付与
    prof["income_tier"] = _normalize_tier(prof.get("income_tier"))
    prof["tier_params"] = get_tier_params(prof["income_tier"])

    return prof

async def build_country_profile(country_name: str, overrides: dict):
    # 取得（wb=同期→to_thread、他はasync）
    wb, imf, fx, trade = await asyncio.gather(
        _run_sync(fetch_wb_profile, country_name),
        fetch_imf_profile(country_name),
        fetch_fx(country_name),
        fetch_comtrade(country_name),
        return_exceptions=True
    )

    # 例外を None に
    def _ok(x):
        return None if isinstance(x, Exception) else x
    wb, imf, fx, trade = (_ok(wb), _ok(imf), _ok(fx), _ok(trade))

    # まずは既存のマージロジックを試す
    prof = None
    try:
        prof = fuse_profile(wb, imf, fx, trade, overrides, country_name)
    except Exception:
        prof = None

    # ★ フォールバック：必ず辞書を返す
    if not isinstance(prof, dict) or not prof:
        prof = {
            "display_name": (wb or {}).get("display_name", country_name),
            "iso3": (wb or {}).get("iso3"),
            "baseline_gdp_usd": (wb or {}).get("baseline_gdp_usd", 1.0e10),
            "income_tier": (wb or {}).get("income_tier", "middle_income"),
            "inflation_recent": (wb or {}).get("inflation_recent", 4.0),   # %
            "openness_ratio": (wb or {}).get("openness_ratio", 0.8),       # ratio
            "investment_rate": (wb or {}).get("investment_rate", 0.25),    # ratio
            "labor_growth": (wb or {}).get("labor_growth", 1.0),            # %
            "debt_to_gdp": (wb or {}).get("debt_to_gdp", 0.5),
        }
        # overrides で上書き
        if overrides:
            for k, v in overrides.items():
                if v is not None:
                    prof[k] = v

    prof = prof or {}
    prof["income_tier"] = _normalize_tier(prof.get("income_tier"))
    prof["tier_params"] = get_tier_params(prof["income_tier"])
    prof = fuse_profile(wb, imf, fx, trade, overrides, country_name)
    
    return prof
 




async def run_pipeline(country: str|None, horizon: int, text: str, overrides: Dict[str,Any]):
    horizon = max(1, min(10, horizon))
    policies = await extract_policies(text)
    profile  = await build_country_profile(country, overrides)
    scenarios, cpi_path, explain = forecast(profile, policies, horizon)
    explain += f"\n[ProfileResolved] {json.dumps(profile, ensure_ascii=False)}"
    return {
        "scenarios": scenarios,
        "cpi": cpi_path,
        "explain": explain,
        "profile_used": profile,
        "policies_struct": policies
    }
