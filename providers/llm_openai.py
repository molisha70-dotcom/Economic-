
import os, httpx, json
from core.schemas import JSON_SCHEMA

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

PROMPT_TMPL = '''あなたは政策テキストを構造化するエンジンです。
次のJSONスキーマに完全準拠し、未知は null/unknown を使用してください。
スキーマ: {schema}
政策テキスト:
{text}
出力は JSON のみ。
'''

async def extract_policies_openai(policy_text: str) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set")
    prompt = PROMPT_TMPL.format(schema=json.dumps(JSON_SCHEMA, ensure_ascii=False), text=policy_text)
    payload = {
        "model": "gpt-4.1-mini",
        "input": prompt,
        "response_format": {"type": "json_object"}
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json=payload
        )
        r.raise_for_status()
        data = r.json()
        text = data.get("output_text")
        if not text:
            try:
                text = data["output"][0]["content"][0]["text"]
            except Exception:
                text = json.dumps({"horizon_years":5,"policies":[]}, ensure_ascii=False)
        try:
            obj = json.loads(text)
            obj["_model_name"] = "openai"
            return json.dumps(obj, ensure_ascii=False)
        except Exception:
            return text
