
import json, re

# 超簡易フォールバック抽出（LLMが使えない環境用）
def _guess_levers(line: str):
    m = []
    l = line.lower()
    if any(k in l for k in ["港","港湾","物流","通関","レール","鉄道","道路"]): m += ["logistics","infrastructure"]
    if any(k in l for k in ["教育","stemi","stem","大学","学校","人的"]): m += ["education"]
    if any(k in l for k in ["規制","自由化","汚職","透明性","手続","行政"]): m += ["regulation","governance"]
    if any(k in l for k in ["エネルギ","発電","電力","ガス","油","再エネ"]): m += ["energy"]
    if any(k in l for k in ["輸出","輸入","関税","fta","通商"]): m += ["trade"]
    if any(k in l for k in ["工場","製造","産業","投資"]): m += ["industry","infrastructure"]
    if any(k in l for k in ["金融","貸出","金利","引締","量的"]): m += ["finance"]
    return list(dict.fromkeys(m)) or ["industry"]

def extract_policies_local(text: str) -> str:
    lines = [x.strip(" ・-—\t") for x in text.splitlines() if x.strip()]
    policies = []
    for ln in lines:
        levers = _guess_levers(ln)
        lag = 2 if ("教育" in ln or "university" in ln.lower()) else 1
        scale = None
        m = re.search(r"(\d+[\d,\.]*)(?=\s*(USD|ドル|%|％))", ln)
        if m:
            val = float(m.group(1).replace(",", ""))
            unit = m.group(2)
            if unit in ["%","％"]:
                scale = {"value": val, "unit": "%GDP"}
            else:
                scale = {"value": val*1e9 if val<1e6 else val, "unit": "USD"}
        policies.append({
            "title": ln[:64],
            "lever": levers,
            "scale": scale or {"value": None, "unit": "unknown"},
            "direction": ["demand+","supply+"],
            "lag_years": lag,
            "confidence": "C"
        })
    obj = {"_model_name":"local","horizon_years":5,"policies":policies}
    return json.dumps(obj, ensure_ascii=False)
