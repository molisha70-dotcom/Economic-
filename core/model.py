
from typing import Dict, Any, List, Tuple
from .utils import clamp

def _lever_to_tfp_keys(lever: List[str]) -> List[str]:
    mapping = {
        "logistics": "logistics",
        "infrastructure": "infrastructure",
        "education": "education",
        "regulation": "regulation",
        "governance": "governance",
        "energy": "energy",
        "trade": "trade",
        "industry": "industry",
        "finance": "finance",
        "security": "security",
        "automation": "automation"
    }
    return [mapping.get(x, x) for x in lever or []]

# core/model.py

def clamp(x, lo, hi):
    return max(lo, min(hi, x))

# core/model.py

from typing import Dict, Any, List
import math

def _policy_gain(profile: Dict[str, Any], policies: List[Dict[str, Any]]) -> float:
    # --- ここはあなたの最新版があるならそれでOK。無ければ簡易版 ---
    if not policies:
        return 0.0
    tier = (profile.get("income_tier") or "middle_income").lower()
    tfp_k, capex_k = (0.10, 0.08) if "high" in tier else ((0.15, 0.10) if "middle" in tier else (0.20, 0.12))
    bonus = 0.0
    for p in policies:
        lev = " / ".join((p.get("lever") or [])).lower()
        scale = (p.get("scale") or {}).get("value"); base = 0.02 if scale is None else min(0.005*float(scale), 0.5)
        if any(k in lev for k in ["infrastructure","infra","port","rail","grid","logistics","インフラ","港","鉄道","送電","物流","ロジ"]):
            gain = capex_k * base
        elif any(k in lev for k in ["education","human capital","reskilling","教育","人材","職業訓練","リスキリング"]):
            gain = tfp_k * base * 0.8
        elif any(k in lev for k in ["regulation","deregulation","governance","business","規制","規制改革","ガバナンス","ビジネス"]):
            gain = tfp_k * base
        elif any(k in lev for k in ["tax","subsidy","industry","semiconductor","manufacturing","税","減税","補助","補助金","産業","半導体","製造"]):
            gain = 0.5*(tfp_k+capex_k)*base
        elif any(k in lev for k in ["trade","fta","貿易","通商"]):
            gain = tfp_k * base * 0.7
        else:
            gain = 0.5*(tfp_k+capex_k)*(base*0.5)
        lag = p.get("lag_years") or 0
        gain *= (1.0 - min(max(lag,0),4)*0.1)
        bonus += gain
    return max(-1.0, min(bonus, 1.5))

def make_growth_paths(profile: Dict[str, Any], policies: List[Dict[str, Any]], horizon: int):
    horizon = max(1, min(10, int(horizon or 5)))
    tp = (profile.get("tier_params") or {})
    base_g = float(tp.get("potential_g", 3.0))
    invest = profile.get("investment_rate"); open_ = profile.get("openness_ratio")
    infl = profile.get("inflation_recent"); target = float(tp.get("inflation_target", 3.0))

    adj = 0.0
    if isinstance(invest,(int,float)): adj += 0.5 * ((float(invest)-0.25)/0.10)
    if isinstance(open_, (int,float)): adj += 0.3 * ((float(open_)-0.8)/0.20)
    if isinstance(infl,  (int,float)): adj -= 0.15 * min(abs(float(infl)-target), 5.0)

    pol = _policy_gain(profile, policies)
    g0 = base_g + adj + pol

    inc = (profile.get("income_tier") or "").lower()
    band = 0.8 if "high" in inc else (1.0 if "middle" in inc else 1.2)

    base_path = [max(-3.0, min(g0, 10.0))]
    low_path  = [base_path[0] - band]
    high_path = [base_path[0] + band]

    for t in range(1, horizon):
        decay = 0.5
        pol_rt = min(1.0, 0.7 + 0.15*t)
        g_t = (base_g + adj)*(1-pol_rt) + (base_g + adj + pol)*pol_rt
        next_b = base_path[-1] + decay*(g_t - base_path[-1])
        base_path.append(max(-3.0, min(next_b, 10.0)))
        low_path.append(base_path[-1] - band)
        high_path.append(base_path[-1] + band)

    explain = (
        f"[Model] base_g={base_g:.2f}, adj={adj:.2f}, pol={pol:.2f}, "
        f"tier={profile.get('income_tier')}, invest={invest}, open={open_}, infl={infl}, target={target}"
    )
    return {"base": base_path, "low": low_path, "high": high_path}, None, explain

# ★ これが呼ばれる前提で（古い固定版を完全に上書き）
def forecast(profile: Dict[str, Any], policies: List[Dict[str, Any]], horizon: int):
    return make_growth_paths(profile, policies, horizon)
def make_growth_paths(profile: Dict[str, Any], policies: List[Dict[str, Any]], horizon: int):
    """
    profile["tier_params"]["potential_g"] をベースに、政策ボーナス/マクロ状態で調整して
    BASE/LOW/HIGH の年次パスを返す。
    戻り値: {"base":[...], "low":[...], "high":[...]}
    """
    horizon = max(1, min(10, int(horizon or 5)))
    tp = (profile.get("tier_params") or {})
    base_g = float(tp.get("potential_g", 3.0))  # ← ここが「国ごとに違う」肝

    # マクロ状況で微調整：投資率・開放度・インフレ乖離
    invest = profile.get("investment_rate")
    open_  = profile.get("openness_ratio")
    infl   = profile.get("inflation_recent")
    target = float(tp.get("inflation_target", 3.0))

    adj = 0.0
    if isinstance(invest, (int,float)):
        # 投資率25%を基準、±10%ptで ±0.5pp 程度
        adj += 0.5 * ((float(invest) - 0.25) / 0.10)
    if isinstance(open_, (int,float)):
        # 開放度80%を基準、±20%ptで ±0.3pp 程度
        adj += 0.3 * ((float(open_) - 0.8) / 0.20)
    if isinstance(infl, (int,float)):
        # インフレ目標からの乖離で成長減衰（過熱/デフレともにマイナス）
        gap = abs(float(infl) - target)
        adj -= 0.15 * min(gap, 5.0)  # 最大 -0.75pp

    # 政策ボーナス
    pol = _policy_gain(profile, policies)

    # ベース成長率
    g0 = base_g + adj + pol
    # LOW/HIGH バンド幅（ティアに応じて）
    if tp is None:
        band = 0.8
    else:
        inc = (profile.get("income_tier") or "").lower()
        band = 0.8 if "high" in inc else (1.0 if "middle" in inc else 1.2)

    base_path = [max(-3.0, min(g0, 10.0))]
    low_path  = [base_path[0] - band]
    high_path = [base_path[0] + band]

    # 2年目以降は徐々に潜在へ回帰（±50%収斂/年）、政策は3年で7割発現
    for t in range(1, horizon):
        decay = 0.5
        pol_rt = min(1.0, 0.7 + 0.15*t)
        g_t = (base_g + adj) * (1 - pol_rt) + (base_g + adj + pol) * pol_rt
        prev_b = base_path[-1]
        next_b = prev_b + decay*(g_t - prev_b)
        base_path.append(max(-3.0, min(next_b, 10.0)))
        low_path.append(base_path[-1] - band)
        high_path.append(base_path[-1] + band)

    # 小数1位＆%に換算するのは呼び出し側（bot.py）がやっているので数値のまま返す
    return {"base": base_path, "low": low_path, "high": high_path}




def _scale_to_intensity(scale: Dict[str, Any] | None, baseline_gdp: float) -> float:
    if not scale or scale.get("unit") is None:
        return 1.0
    unit = scale.get("unit")
    val = scale.get("value", 0) or 0
    try:
        val = float(val)
    except:
        val = 0.0
    if unit == "%GDP":
        return clamp(val, 0.0, 100.0)
    if unit == "USD" and baseline_gdp > 0:
        return clamp(100.0 * val / baseline_gdp, 0.0, 100.0)
    return 1.0

def _confidence_weight(conf: str) -> float:
    table = {"S":1.0,"A":0.9,"B":0.7,"C":0.5,"D":0.3}
    return table.get(conf, 0.6)

def _inflation_penalty(inflation_recent: float, target: float, t: int) -> float:
    gap = max(0.0, (inflation_recent or target) - target)
    decay = max(0.2, 1.0 - 0.2 * t)
    return 0.15 * gap * decay / 10.0

def forecast(profile: Dict[str, Any], extract: Dict[str, Any], horizon: int) -> Tuple[Dict[str,List[float]], List[float], str]:
    tier = profile["tier_params"]
    potential_g = float(tier["potential_g"])
    target = float(tier.get("inflation_target", 4.0))
    baseline_gdp = float(profile.get("baseline_gdp_usd", 1e9))
    invest_rate = float(profile.get("investment_rate", 0.25))
    openness = float(profile.get("openness_ratio", 0.8))
    inflation_recent = float(profile.get("inflation_recent", target))

    tfp_coeff = tier.get("tfp_coeff", {})
    fiscal_mult = tier.get("fiscal_multiplier", {"capex":1.0, "current":0.5})
    trade_elast = float(tier.get("trade_elasticity", 0.3))

    years = list(range(horizon))
    g_pot = [potential_g]*horizon
    demand = [0.0]*horizon

    explain_lines = []
    explain_lines.append(f"[Tier] potential_g={potential_g} target_infl={target} mult={fiscal_mult} trade_elast={trade_elast}")
    explain_lines.append(f"[Profile] invest_rate={invest_rate} openness={openness} inflation_recent={inflation_recent} baseline_gdp={baseline_gdp:.3e}")

    for p in extract.get("policies", []):
        lever = p.get("lever", [])
        lag = p.get("lag_years", None)
        if lag is None:
            default_lags = tier.get("default_lags", {})
            if "infrastructure" in lever or "logistics" in lever:
                lag = default_lags.get("infra", 2)
            elif "education" in lever:
                lag = default_lags.get("education", 3)
            elif "regulation" in lever or "governance" in lever:
                lag = default_lags.get("regulation", 1)
            else:
                lag = 1
        lag = int(clamp(lag, 0, 7))
        conf_w = _confidence_weight(p.get("confidence","B"))
        intensity = _scale_to_intensity(p.get("scale"), baseline_gdp)

        tfp_pp = 0.0
        for k in _lever_to_tfp_keys(lever):
            tfp_pp += float(tfp_coeff.get(k, 0.0)) * (intensity/5.0) * conf_w
        for t in range(lag, horizon):
            g_pot[t] += tfp_pp

        demand_imp = 0.0
        if any(x in lever for x in ["infrastructure","industry","energy","logistics"]):
            demand_imp = float(fiscal_mult.get("capex",1.0)) * (intensity/5.0)
        elif any(x in lever for x in ["finance","governance","regulation"]):
            demand_imp = float(fiscal_mult.get("current",0.5)) * (intensity/5.0) * 0.5
        elif "trade" in lever:
            demand_imp = trade_elast * (min(1.0, openness) * intensity/10.0)

        if demand_imp != 0.0:
            if lag < horizon:
                demand[lag] += demand_imp*0.6
            if lag+1 < horizon:
                demand[lag+1] += demand_imp*0.4

        explain_lines.append(f"[Policy] {p.get('title')} lever={lever} lag={lag} intensity(%GDP)={intensity:.2f} tfp_pp/yr~{tfp_pp:.2f} demand_imp~{demand_imp:.2f}")

    base = []
    low = []
    high = []
    cpi = []
    for t in range(horizon):
        penalty = _inflation_penalty(inflation_recent, target, t)
        g_real = g_pot[t] + demand[t] - penalty
        g_real = clamp(g_real, -5.0, 15.0)
        base.append(g_real)
        low.append(g_real - 0.8)
        high.append(g_real + 0.8)
        gap = (inflation_recent - target) * (0.6 ** (t+1))
        cpi.append(target + gap)

    scenarios = {"base": base, "low": low, "high": high}
    explain = "\n".join(explain_lines)
    return scenarios, cpi, explain
