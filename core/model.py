
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

def _policy_effect(year: int, policies: list, tier_params: dict) -> float:
    """
    年yearに効く政策の合計寄与（%pt）。ラグ後に効く簡易版。
    lever重みは tier_params["tfp_coeff"] を見る。
    """
    tfp = (tier_params or {}).get("tfp_coeff", {}) or {}
    total = 0.0
    for p in policies or []:
        lag = int(p.get("lag_years") or 1)
        if (year + 1) < lag:
            continue
        levers = p.get("lever", []) or []
        weight = sum(float(tfp.get(lv, 0.0)) for lv in levers)

        # 規模スケーリング（ざっくり）
        scale = p.get("scale") or {}
        mag = scale.get("value")
        unit = (scale.get("unit") or "").lower()
        s_mult = 1.0
        if isinstance(mag, (int, float)):
            if unit in ("%gdp", "%"):
                s_mult += 0.5 * (mag / 1.0)   # GDP比1%で+0.5倍程度の寄与
            # USD/LCUはここでは無視（本格実装は別換算）
        total += 0.3 * weight * s_mult      # 係数は暫定
    return total

def make_growth_paths(profile: dict, policies: list, horizon: int) -> dict:
    """
    プロファイル（投資率・開放度・インフレ目標/乖離）と政策から
    BASE/LOW/HIGH の年次成長率パスを返す。
    """
    profile = profile or {}
    tp = profile.get("tier_params", {}) or {}
    pot = float(tp.get("potential_g", 4.0))

    infl   = profile.get("inflation_recent")
    target = tp.get("inflation_target", 2.0 if (profile.get("income_tier")=="high_income") else 4.0)
    inv    = profile.get("investment_rate")
    opn    = profile.get("openness_ratio")

    # マクロ調整：投資率(基準0.25)、開放度(基準0.60)、インフレ超過のマイナス
    adj = 0.0
    if isinstance(inv, (int, float)):
        adj += 2.0 * (inv - 0.25)     # 投資率+0.05で+0.10pp
    if isinstance(opn, (int, float)):
        adj += 0.5 * (opn - 0.60)     # 開放度+0.10で+0.05pp
    if isinstance(infl, (int, float)) and isinstance(target, (int, float)):
        gap = max(0.0, infl - target)
        adj -= 0.2 * gap              # ターゲット超過1ptで-0.2pp

    base, low, high = [], [], []
    for y in range(int(horizon)):
        pol = _policy_effect(y, policies or [], tp)  # 政策寄与（年毎）
        g_base = clamp(pot + adj + pol, pot - 2.0, pot + 3.0)
        base.append(g_base)
        low.append(clamp(g_base - 0.8, 0.0, 15.0))
        high.append(clamp(g_base + 0.8, 0.0, 15.0))
    return {"BASE": base, "LOW": low, "HIGH": high}


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
