
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

# ---- country name cleanup & tier hints ----
def _clean_country_name(name: str | None) -> str:
    # 例: '"Japan" ' → japan
    if not name:
        return ""
    s = str(name).strip().strip('"').strip("'")
    return s.lower()

COUNTRY_TIER_HINT = {
    # high income
    "japan": "high_income",
    "korea": "high_income",
    "south korea": "high_income",
    "republic of korea": "high_income",
    "united states": "high_income",
    "united kingdom": "high_income",
    "germany": "high_income",
    "france": "high_income",
    "italy": "high_income",
    "spain": "high_income",
    # middle income
    "vietnam": "middle_income",
    "india": "middle_income",
    "china": "middle_income",
}

def _hint_tier_from_country(name: str | None) -> str | None:
    key = _clean_country_name(name)
    return COUNTRY_TIER_HINT.get(key)
    
# --- lever を英語カテゴリへ正規化（日本語にも対応）---
def _normalize_lever_token(s: str) -> str:
    if not s:
        return ""
    t = str(s).strip().lower()
    # インフラ
    if any(k in t for k in ["インフラ", "インフラ投資", "道路", "港", "港湾", "鉄道", "送電", "電力網", "空港", "物流", "ロジ", "ロジスティクス", "infrastructure", "infra", "port", "rail", "grid", "logistics"]):
        return "infrastructure"
    # 教育・人材
    if any(k in t for k in ["教育", "人材", "訓練", "職業訓練", "リスキリング", "education", "human capital", "reskilling"]):
        return "education"
    # 規制・統治
    if any(k in t for k in ["規制", "規制改革", "ガバナンス", "行政", "手続", "ビジネス環境", "regulation", "deregulation", "governance", "business"]):
        return "regulation"
    # 産業・税・補助
    if any(k in t for k in ["半導体", "製造", "産業", "税", "減税", "補助", "補助金", "industry", "semiconductor", "manufacturing", "tax", "subsidy"]):
        return "industry"
    # 貿易・開放
    if any(k in t for k in ["貿易", "輸出", "輸入", "fta", "通商", "関税", "trade", "fta"]):
        return "trade"
    return t

def _normalize_policies_schema(data: dict) -> dict:
    """policies の lever 配列を英語カテゴリに正規化"""
    if not isinstance(data, dict):
        return data
    items = data.get("policies") or []
    for p in items:
        lev = p.get("lever") or []
        if isinstance(lev, (list, tuple)):
            p["lever"] = [_normalize_lever_token(x) for x in lev if x]
        else:
            p["lever"] = [_normalize_lever_token(str(lev))]
    return data



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
    active = []

    if os.getenv("OPENAI_API_KEY"):
        tasks.append(extract_policies_openai(text)); active.append("openai")
    if os.getenv("GEMINI_API_KEY"):
        tasks.append(extract_policies_gemini(text)); active.append("gemini")
    print(f"[extract] providers active={active}")

    results = []
    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)

    # ローカル（同期）は最後に必ず追加
    try:
        results.append(extract_policies_local(text))
    except Exception as e:
        results.append(e)

    valids = []
    for r in results:
        if isinstance(r, Exception):
            print("[extract] provider error:", repr(r))
            continue

        data_obj = r
        if isinstance(r, str):
            try:
                data_obj = json.loads(r)
            except Exception as e:
                print("[extract] json parse fail:", e)
                continue

        try:
            data_norm = coerce_extract_output(data_obj)  # ← 既存のバリデータ
            data_norm = _normalize_policies_schema(data_norm)  # ← ここを必ず通す
            if data_norm and isinstance(data_norm.get("policies"), list):
                valids.append(data_norm)
        except Exception as e:
            print("[extract] coerce/normalize fail:", e)

    # 1件も取れなかった場合は、最後の保険：ローカルをもう一度
    if not valids:
        try:
            fallback = extract_policies_local(text)
            if isinstance(fallback, str):
                fallback = json.loads(fallback)
            fallback = _normalize_policies_schema(coerce_extract_output(fallback))
            if fallback and isinstance(fallback.get("policies"), list):
                valids.append(fallback)
        except Exception as e:
            print("[extract] fallback fail:", e)

    # マージ（あなたの merge/選別ロジックでOK）
    if not valids:
        return {"policies": []}
    merged = merge_outputs(valids) if 'merge_outputs' in globals() else valids[0]
    print("[extract result]", json.dumps(merged, ensure_ascii=False))
    return merged

    # --- 4) ティア確定の直前にヒント補正を入れる（WB失敗時の安全網） ---
    hint = _hint_tier_from_country(prof.get("display_name") or country_name)
    if hint and (not prof.get("income_tier") or str(prof.get("income_tier")).lower() == "middle_income"):
        # すでに high/low が確実なら触らない。middle または None のときだけ上書き
        prof["income_tier"] = hint
            

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

    # --- 2) デフォルト穴埋め ---
    prof.setdefault("display_name", country_name)
    prof["baseline_gdp_usd"] = prof.get("baseline_gdp_usd", baseline_gdp_usd)
    prof.setdefault("income_tier", "middle_income")
    prof.setdefault("inflation_recent", 4.0)   # %
    prof.setdefault("openness_ratio", 0.8)     # ratio
    prof.setdefault("investment_rate", 0.25)   # ratio
    prof.setdefault("labor_growth", 1.0)       # %
    prof.setdefault("debt_to_gdp", 0.5)

    # --- 3) overrides 反映（旧キー baseline_gdp 互換も吸収） ---
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
     # --- ★ ヒント強制（ここで一度入れる：WB失敗や引用符付き国名対策） ---
    # 例: display_name が '"Japan"' とか country_name が '"Korea"' でも拾える
    name_for_hint = (wb or {}).get("display_name") or country_name or ""
    hint = _hint_tier_from_country(name_for_hint)
    if hint: 
        print(f"[tier-hint] applying hint '{hint}' for country='{name_for_hint}'")
        prof["income_tier"] = hint

    # --- 4) ティア確定（ここ“だけ”で tier_name を決める。未定義にならない） ---
    tier_name: str = "middle_income"  # 先に初期化（UnboundLocalError対策）

    ov_tier = (overrides or {}).get("income_tier") if overrides else None
    if ov_tier:
        tier_name = ov_tier
    elif prof.get("income_tier"):
        tier_name = prof["income_tier"]
    else:
        tier_name = pick_tier_by_gdp_pc(prof.get("gdp_per_capita"))  # None OK

    tier_name: str = prof.get("income_tier") or "middle_income"
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

