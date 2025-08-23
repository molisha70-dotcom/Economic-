
import os, httpx, json
from core.schemas import JSON_SCHEMA

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

PROMPT_TMPL = '''あなたは政策テキストを構造化するエンジンです。
次のJSONスキーマに完全準拠して出力。未知は null/unknown。
スキーマ: {schema}
政策テキスト:
{text}
出力は JSON のみ。
'''

async def extract_policies_claude(policy_text: str) -> str:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    prompt = PROMPT_TMPL.format(schema=json.dumps(JSON_SCHEMA, ensure_ascii=False), text=policy_text)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01"
            },
            json={
                "model": "claude-3-7-sonnet-20250219",
                "max_tokens": 2048,
                "messages": [{"role":"user","content":prompt}],
            }
        )
        r.raise_for_status()
        data = r.json()
        text = data["content"][0]["text"]
        try:
            obj = json.loads(text)
            obj["_model_name"] = "claude"
            return json.dumps(obj, ensure_ascii=False)
        except Exception:
            return text
