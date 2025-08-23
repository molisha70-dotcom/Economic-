
import os, httpx, json
from core.schemas import JSON_SCHEMA

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

PROMPT_TMPL = '''あなたは政策テキストを構造化するエンジンです。
次のJSONスキーマに完全準拠し、出力は JSON のみ。
未知の値は null か "unknown" を使ってください。
スキーマ: {schema}
政策テキスト:
{text}
'''

async def extract_policies_gemini(policy_text: str) -> str:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not set")
    prompt = PROMPT_TMPL.format(schema=json.dumps(JSON_SCHEMA, ensure_ascii=False), text=policy_text)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent?key={GEMINI_API_KEY}"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, json={
            "contents":[{"parts":[{"text": prompt}]}],
            "generationConfig":{"responseMimeType":"application/json"}
        })
        r.raise_for_status()
        data = r.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        try:
            obj = json.loads(text)
            obj["_model_name"] = "gemini"
            return json.dumps(obj, ensure_ascii=False)
        except Exception:
            return text
