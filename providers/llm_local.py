
# providers/llm_local.py
import re
import json

_NUMBER = r'([0-9]+(?:\.[0-9]+)?)'

def _guess_scale(text: str):
    """
    テキストからざっくり規模を推定（例：'年1.5兆円' -> value=15, unit=trillion_yen_per_year 相当）
    数字が見つからなければ None を返す（モデル側でデフォルト微小効果にする）
    """
    t = text.lower()
    # 兆/億/年 を簡易判定
    m = re.search(_NUMBER + r'\s*兆', t)
    if m:
        v = float(m.group(1))
        return {"value": v, "unit": "trillion_yen_per_year" if "年" in t else "trillion_yen"}
    m = re.search(_NUMBER + r'\s*億', t)
    if m:
        v = float(m.group(1)) / 10.0  # ざっくり換算：10億 = 1兆の1/10
        return {"value": v, "unit": "trillion_yen_per_year" if "年" in t else "trillion_yen"}
    m = re.search(_NUMBER + r'\s*%|％', t)
    if m:
        return {"value": float(m.group(1)), "unit": "percent"}
    return None

def _mk(title, lever, text):
    return {
        "title": title,
        "lever": lever,             # 後段で英語カテゴリに正規化されます
        "lag_years": 1 if any(k in text for k in ["整備","建設","infra","infrastructure","港","鉄道","送電","電力"]) else 0,
        "scale": _guess_scale(text)
    }

def extract_policies_local(text: str):
    """日本語キーワードの簡易抽出（キーが無くても常に何か返す）"""
    t = (text or "").strip()
    items = []

    # インフラ系
    if any(k in t for k in ["インフラ","港","港湾","空港","道路","鉄道","送電","電力網","グリッド","物流","ロジ"]):
        items.append(_mk("インフラ投資", ["インフラ"], t))

    # 教育・人材
    if any(k in t for k in ["教育","学校","人材","職業訓練","リスキリング"]):
        items.append(_mk("教育投資", ["教育"], t))

    # 規制・統治
    if any(k in t for k in ["規制緩和","規制改革","ガバナンス","手続","ビジネス環境","起業"]):
        items.append(_mk("規制改革", ["規制"], t))

    # 産業・税・補助
    if any(k in t for k in ["半導体","産業政策","製造業","減税","税額控除","補助金"]):
        items.append(_mk("産業・税制", ["産業","税"], t))

    # 貿易・開放
    if any(k in t for k in ["貿易","通商","FTA","輸出","輸入"]):
        items.append(_mk("通商・貿易", ["貿易"], t))

    # 何もヒットしない場合は、弱い汎用政策を1件返す（効果は小さめ）
    if not items:
        items.append(_mk("一般的な成長施策", ["規制"], t))

    out = {"policies": items, "_model_name": "local_rules_v1"}
    # ライブラリ側が文字列JSONを期待していても大丈夫なように文字列で返す
    return json.dumps(out, ensure_ascii=False)
