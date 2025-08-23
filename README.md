
# Discord GDP Forecast Bot (Ensemble LLM + Data APIs)

このボットは、Discord上で政策テキストから将来のGDP成長率を推定します。
- 国を固定せず、**動的に国プロファイルを推定**（外部API→不足はプリセット補完）
- **複数LLMの合議制**（OpenAI / Anthropic / Google Gemini + ローカル簡易抽出のフォールバック）
- **World Bank /（任意）IMF / ExchangeRate.host /（任意）UN Comtrade** からデータ取得
- 計算は**自前決定論モデル**（再現性）

## 使い方（ローカル）
```bash
pip install -r requirements.txt
cp .env.example .env
# .env を編集して各キーを設定（少なくとも DISCORD_TOKEN）
python bot.py
```

## コマンド
- `/ping` 動作確認
- `/forecast text:<政策> horizon:5 country:<任意>`
- `/assume key:value ...` 例: `investment_rate:0.30 inflation_recent:6`
- `/explain` 直近実行の根拠・係数を表示
