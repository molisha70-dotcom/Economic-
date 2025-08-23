# core/schemas.py（全置換）
from typing import Any, Dict, List

# LLMへ渡す JSON スキーマ（そのまま文字列化して使う）
JSON_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "horizon_years": {"type": "integer"},
        "policies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "lever": {"type": "array", "items": {"type": "string"}},
                    "scale": {
                        "anyOf": [
                            {"type": "null"},
                            {
                                "type": "object",
                                "properties": {
                                    "value": {"type": ["number", "null"]},
                                    "unit":  {"type": ["string", "null"]}
                                },
                                "required": ["value", "unit"]
                            }
                        ]
                    },
                    "direction": {"type": "array", "items": {"type": "string"}},
                    "lag_years": {"type": ["integer", "null"]},
                    "confidence": {"type": "string"}
                },
                "required": ["title"]
            }
        }
    },
    "required": ["policies"]
}

def coerce_extract_output(obj: Dict[str, Any]) -> Dict[str, Any]:
    """最低限の形に整える（欠損はデフォルト埋め）"""
    out: Dict[str, Any] = {}
    out["horizon_years"] = int(obj.get("horizon_years", 5))
    pols = obj.get("policies") or []
    norm: List[Dict[str, Any]] = []
    for p in pols:
        if not isinstance(p, dict): 
            continue
        norm.append({
            "title":       str(p.get("title", ""))[:256] or "(untitled)",
            "lever":       [str(x) for x in (p.get("lever") or [])][:5],
            "scale":       p.get("scale"),
            "direction":   [str(x) for x in (p.get("direction") or [])][:5],
            "lag_years":   None if p.get("lag_years") is None else int(p.get("lag_years")),
            "confidence":  str(p.get("confidence", "B"))[:1]
        })
    out["policies"] = norm
    # _model_name は orchestrator 側でセット
    return out

