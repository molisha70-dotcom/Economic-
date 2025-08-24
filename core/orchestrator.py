
# core/orchestrator.py
from core.model import make_growth_paths
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
    
# ==== TIER定義 & ヘルパー ====
TIERS = {
    "high_income": {
        "potential_g": 2.0, "capital_share": 0.35, "inflation_target": 2.0,
        "fiscal_multiplier": {"capex": 0.8, "current": 0.4}, "trade_elasticity": 0.2,
        "tfp_coeff": {"logistics":0.15,"automation":0.25,"education":0.15,"regulation":0.25,
                      "governance":0.2,"energy":0.1,"infrastructure":0.1,"trade":0.15,
                      "industry":0.15,"finance":0.1,"security":0.1},
        "default_lags": {"infra":2,"ports":2,"education":3,"regulation":1}
    },
    "middle_income": {
        "potential_g": 4.0, "capital_share": 0.35, "inflation_target": 4.0,
        "fiscal_multiplier": {"capex": 1.0, "current": 0.5}, "trade_elasticity": 0.3,
        "tfp_coeff": {"logistics":0.2,"automation":0.3,"education":0.2,"regulation":0.3,
                      "governance":0.2,"energy":0.15,"infrastructure":0.15,"trade":0.2,
                      "industry":0.2,"finance":0.1,"security":0.1},
        "default_lags": {"infra":2,"ports":2,"education":3,"regulation":1}
    },
    "low_income": {
        "potential_g": 5.5, "capital_share": 0.35, "inflation_target": 5.0,
        "fiscal_multiplier": {"capex": 1.2, "current": 0.6}, "trade_elasticity": 0.35,
        "tfp_coeff": {"logistics":0.25,"automation":0.2,"education":0.25,"regulation":0.25,
                      "governance":0.2,"energy":0.2,"infrastructure":0.2,"trade":0.25,
                      "industry":0.25,"finance":0.1,"security":0.1},
        "default_lags": {"infra":2,"ports":2,"education":3,"regulation":1}
    },
}

def pick_tier_by_gdp_pc(gdp_pc: float | None) -> str:
    if gdp_pc is None: return "middle_income"
    if gdp_pc < 1500:  return "low_income"
    if gdp_pc < 13000: return "middle_income"
    return "high_income"

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
            

def fuse_profile(wb, imf, fx, trade, overrides, country_name: str) -> dict:
    """
    各ソースをマージ → デフォルトで穴埋め → overrides 反映 →
    income_tier と tier_params を必ずセットして返す安全版。
    """
    prof: dict = {}

    # --- 0) baseline_gdp_usd を安全に決定（未定義変数は使わない） ---
    baseline_gdp_usd = None
    if isinstance(wb, dict):
        baseline_gdp_usd = wb.get("baseline_gdp_usd") or wb.get("gdp") or wb.get("ny_gdp_mktp_cd")
    if baseline_gdp_usd is None and isinstance(imf, dict):
        baseline_gdp_usd = imf.get("baseline_gdp_usd")
    if baseline_gdp_usd is None:
        baseline_gdp_usd = 1.0e10

    # --- 1) World Bank を最優先でコピー ---
    if isinstance(wb, dict):
        for k in ("display_name","iso3","baseline_gdp_usd","income_tier",
                  "inflation_recent","openness_ratio","investment_rate",
                  "labor_growth","debt_to_gdp","gdp_per_capita"):
            v = wb.get(k)
            if v is not None:
                prof[k] = v

    # --- 2) デフォルトで穴埋め ---
    prof.setdefault("display_name", country_name)
    prof["baseline_gdp_usd"] = prof.get("baseline_gdp_usd", baseline_gdp_usd)
    prof.setdefault("income_tier", "middle_income")
    prof.setdefault("inflation_recent", 4.0)   # %
    prof.setdefault("openness_ratio", 0.8)     # ratio
    prof.setdefault("investment_rate", 0.25)   # ratio
    prof.setdefault("labor_growth", 1.0)       # %
    prof.setdefault("debt_to_gdp", 0.5)

    # --- 3) overrides 反映（baseline_gdp の旧キー互換も吸収） ---
    if overrides:
        if overrides.get("baseline_gdp_usd") is not None:
            prof["baseline_gdp_usd"] = overrides["baseline_gdp_usd"]
        elif overrides.get("baseline_gdp") is not None:
            prof["baseline_gdp_usd"] = overrides["baseline_gdp"]
        for k, v in overrides.items():
            if k in ("baseline_gdp","baseline_gdp_usd"):
                continue
            if v is not None:
                prof[k] = v

    # --- 4) ティア確定（必ず代入される安全版。ここ以外で tier_name を使わない） ---
    # 先に初期化（これで UnboundLocalError は起きません）
    tier_name: str = "middle_income"

    # 候補：overrides > 既存プロフ > gdp_per_capita から推定
    ov_tier = (overrides or {}).get("income_tier") if overrides else None
    if ov_tier:
        tier_name = ov_tier
    elif prof.get("income_tier"):
        tier_name = prof["income_tier"]
    else:
        tier_name = pick_tier_by_gdp_pc(prof.get("gdp_per_capita"))  # None OK

    # 正規化して確定
    tier_name = _normalize_tier(tier_name)
    prof["income_tier"] = tier_name
    prof["tier_params"] = get_tier_params(tier_name)

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
    
    return prof
 

async def run_pipeline(country: str | None, horizon: int, text: str, overrides: Dict[str, Any]):
    horizon = max(1, min(10, horizon))

    # 政策抽出 & プロファイル作成
    policies_struct = await extract_policies(text)
    profile = await build_country_profile(country, overrides)  
    # ★ 固定値だった forecast(...) を廃止し、プロファイル & 政策で成長パスを計算
    scenarios = make_growth_paths(
        profile,
        (policies_struct or {}).get("policies", []),
        horizon
    )

    # CPIパスや説明文は最小限（必要に応じて後で拡張）
    cpi_path = []  # 使っていなければ空でOK
    explain  = "Policy-driven forecast based on profile & levers."
    explain += f"\n[ProfileResolved] {json.dumps(profile, ensure_ascii=False)}"

    return {
        "scenarios": scenarios,          # {"BASE":[..], "LOW":[..], "HIGH":[..]}
        "cpi": cpi_path,
        "explain": explain,
        "profile_used": profile,
        "policies_struct": policies_struct
    }

