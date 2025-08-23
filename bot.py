
import os, asyncio, json
import discord
from discord import app_commands
from dotenv import load_dotenv

from core.orchestrator import run_pipeline, set_overrides_for_channel, get_overrides_for_channel, get_last_explain_for_channel

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

INTENTS = discord.Intents.default()
client = discord.Client(intents=INTENTS)
tree = app_commands.CommandTree(client)

@client.event
async def on_ready():
    await tree.sync()
    print(f"Logged in as {client.user}")

@tree.command(name="ping", description="動作確認")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong! 🏓 Bot is alive.")

@tree.command(name="assume", description="国プロファイルの前提を上書き (例: investment_rate:0.30 inflation_recent:6)")
@app_commands.describe(kv_pairs="スペース区切りで key:value を複数指定可")
async def assume(interaction: discord.Interaction, kv_pairs: str):
    ch = interaction.channel_id
    overrides = get_overrides_for_channel(ch)
    for kv in kv_pairs.split():
        if ":" in kv:
            k, v = kv.split(":", 1)
            try:
                v2 = float(v)
            except:
                v2 = v
            overrides[k] = v2
    set_overrides_for_channel(ch, overrides)
    await interaction.response.send_message(f"✅ 上書き設定を保存しました: `{json.dumps(overrides, ensure_ascii=False)}`")

from discord import app_commands
import asyncio, traceback
# ★ 可能ならファイル先頭でimportしておく（関数内importで失敗しても返信は済ませられる）
from core.orchestrator import set_last_explain_for_channel

@tree.command(name="forecast", description="政策テキストからGDP成長率を推定")
@app_commands.describe(text="政策テキスト（箇条書きOK）", horizon="予測年数(1-10)", country="国名（任意）")
async def forecast_cmd(  # ★ 関数名は forecast_cmd に（他の "forecast" と衝突しがち）
    interaction: discord.Interaction, 
    text: str, 
    horizon: int = 5, 
    country: str | None = None
):
    # ★ 1) 最初にACK（thinking表示）— これが最初のawaitになるように！
    await interaction.response.defer(thinking=True)

    try:
        ch = interaction.channel_id
        overrides = get_overrides_for_channel(ch)

        # ★ 2) 重い処理はタイムアウト付き（長時間でトークン失効を避ける）
        result = await asyncio.wait_for(
            run_pipeline(country=country, horizon=horizon, text=text, overrides=overrides),
            timeout=60
        )

        # ---- ここから整形 ----
        scenarios = result["scenarios"]
        profile = result["profile_used"]
        policies = result["policies_struct"]["policies"]
        explain  = result["explain"]

        lines = []
        lines.append(f"**【予測結果】{profile.get('display_name','Unknown')} / {horizon}年**")
        lines.append(f"潜在成長の基準: {profile['tier_params']['potential_g']:.1f}%（ティア: {profile['income_tier']}）")
        lines.append(f"直近インフレ: {profile.get('inflation_recent','?')}% ／ 投資率: {profile.get('investment_rate','?')} ／ 開放度: {profile.get('openness_ratio','?')}")
        lines.append("")
        for k, path in scenarios.items():
            yrs = ", ".join([f"{x:.1f}%" for x in path])
            lines.append(f"・{k.upper()}：{yrs}")
        lines.append("")
        lines.append("— 抽出された政策（要約） —")
        for p in policies[:8]:
            lever = "/".join(p.get("lever", []))
            scale = p.get("scale")
            scale_txt = ""
            if scale:
                unit = scale.get("unit","")
                val = scale.get("value")
                if val is not None:
                    scale_txt = f"（規模: {val} {unit}）"
            lines.append(f"・{p.get('title','(no title)')}｜{lever}｜lag={p.get('lag_years')} {scale_txt}")
        if len(policies) > 8:
            lines.append(f"...and {len(policies)-8} more")
        content = "\n".join(lines)

        # ★ 3) defer後は “edit_original_response” で1回だけ返す（followupは使わない）
        #    （2000文字超対策：長い時は分割してfollowupで追加送信してもOK）
        if len(content) <= 1900:
            await interaction.edit_original_response(content=content)
        else:
            await interaction.edit_original_response(content=content[:1900] + "\n…(続きは追送)")
            await interaction.followup.send(content[1900:])

        # ★ 4) 返答後に副作用系を実行（ここで失敗してもユーザーには返答済み）
        set_overrides_for_channel(ch, overrides)
        set_last_explain_for_channel(ch, explain)

    except asyncio.TimeoutError:
        # ★ defer後は常に edit_original_response
        await interaction.edit_original_response(content="⏳ 少し時間がかかっています。もう一度お試しください。")
    except Exception as e:
        traceback.print_exc()
        await interaction.edit_original_response(content=f"❌ エラー: {type(e).__name__}: {e}")
@tree.command(name="explain", description="直近の推計の根拠・係数を表示")
async def explain(interaction: discord.Interaction):
    ch = interaction.channel_id
    exp = get_last_explain_for_channel(ch)
    if not exp:
        await interaction.response.send_message("（まだ説明がありません。/forecast を実行してください）")
        return
    await interaction.response.send_message(exp[:1900])  # Discord文字数制限回避（簡易）

if __name__ == "__main__":
    from keep_alive import keep_alive
    import os, time, socket

    # 1) 先にFlaskでPORTをlistenさせる
    keep_alive()
    port = int(os.getenv("PORT", "8080"))
    print(f"[keep_alive] trying to open port {port}")

    # 2) ポートが開くのを最大10秒待つ
    for _ in range(20):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                print(f"[keep_alive] port {port} is open ✅")
                break
        time.sleep(0.5)

    # 3) Discord Bot を起動
    client.run(TOKEN)
