# core/ensemble.py（全置換）
from typing import List, Dict, Any
import difflib
from .utils import normalize_title, jaccard

BASE_WEIGHTS = {"openai":0.4, "claude":0.35, "gemini":0.25, "local":0.15}
CONF_W = {"S":1.0,"A":0.9,"B":0.7,"C":0.5,"D":0.3}

def _title_sim(a: str, b: str) -> float:
    a_tokens = " ".join(sorted(a.split()))
    b_tokens = " ".join(sorted(b.split()))
    return difflib.SequenceMatcher(None, a_tokens, b_tokens).ratio()

def cluster_policies(outputs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items = []
    for o in outputs:
        model_name = o.get("_model_name", "unknown")
        for pol in o.get("policies", []):
            items.append({"model": model_name, "policy": pol})

    clusters = []
    used = [False]*len(items)
    for i, it in enumerate(items):
        if used[i]: continue
        base_title = normalize_title(it["policy"].get("title",""))
        base_lever = set(it["policy"].get("lever") or [])
        group = [i]
        used[i] = True
        for j in range(i+1, len(items)):
            if used[j]: continue
            jt = normalize_title(items[j]["policy"].get("title",""))
            jl = set(items[j]["policy"].get("lever") or [])
            title_sim = _title_sim(base_title, jt)
            lever_sim = jaccard(base_lever, jl)
            if 0.5*title_sim + 0.5*lever_sim >= 0.75:
                used[j] = True
                group.append(j)
        clusters.append({"members":[items[k] for k in group]})
    return clusters

def level_from_score(score: float) -> str:
    if score >= 0.85: return "S"
    if score >= 0.7:  return "A"
    if score >= 0.55: return "B"
    if score >= 0.4:  return "C"
    return "D"

def merge_outputs(outputs: List[Dict[str, Any]]) -> Dict[str, Any]:
    clusters = cluster_policies(outputs)
    merged_policies = []
    for cl in clusters:
        votes = []
        for m in cl["members"]:
            model = m["model"]
            pol = m["policy"]
            w_model = BASE_WEIGHTS.get(model, 0.2)
            w_conf = CONF_W.get((pol.get("confidence") or "B"), 0.5)
            votes.append(w_model * w_conf)
        score = sum(votes)
        if score < 0.5:
            continue

        titles = [m["policy"].get("title") for m in cl["members"] if m["policy"].get("title")]
        title = max(titles, key=len) if titles else "(untitled)"

        from collections import Counter
        levs = []
        for m in cl["members"]:
            levs += (m["policy"].get("lever") or [])
        lever_counts = Counter(levs)
        lever = [x for x,_ in lever_counts.most_common(3)]

        dirs = []
        for m in cl["members"]:
            dirs += (m["policy"].get("direction") or [])
        dir_counts = Counter(dirs)
        direction = [x for x,_ in dir_counts.most_common(3)]

        lags = [m["policy"].get("lag_years") for m in cl["members"] if m["policy"].get("lag_years") is not None]
        lag = int(sorted(lags)[len(lags)//2]) if lags else None

        priority = {"%GDP":3,"USD":2,"LCU":1,"qty":0,"unknown":-1}
        scales = [m["policy"].get("scale") for m in cl["members"] if m["policy"].get("scale") is not None]
        scales = [s for s in scales if isinstance(s, dict) and s.get("unit") is not None]
        scale = None
        if scales:
            scales.sort(key=lambda s: priority.get(s.get("unit"), -1), reverse=True)
            scale = {"value": scales[0].get("value"), "unit": scales[0].get("unit")}

        merged_policies.append({
            "title": title,
            "lever": lever,
            "direction": direction[:3],
            "lag_years": lag,
            "scale": scale,
            "confidence": level_from_score(score)
        })

    horizon = max([int(o.get("horizon_years", 5)) for o in outputs] + [5])
    return {"horizon_years": horizon, "policies": merged_policies}
