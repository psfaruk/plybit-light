"""Telegram alert sender for HIGH+ grade signals."""

import asyncio
import logging
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GRADE_HIGH

log = logging.getLogger(__name__)

try:
    from aiogram import Bot
    from aiogram.enums import ParseMode
    _HAS_AIOGRAM = True
except ImportError:
    _HAS_AIOGRAM = False


def _grade_emoji(grade: str) -> str:
    return {"ELITE": "👑", "HIGH": "✅", "MODERATE": "⚡"}.get(grade, "")


def _dir_emoji(direction: str) -> str:
    return "🟢" if direction == "GREEN" else "🔴"


def _format_message(signal: dict[str, object], pair: str) -> str:
    d          = signal.get("signal", "?")
    conf       = float(signal.get("confidence", 0)) * 100
    grade_str  = str(signal.get("grade", "?"))
    mtf        = signal.get("mtf_context", {})
    smc        = signal.get("smc_context", {})
    react      = signal.get("candle_reaction", {})
    models     = signal.get("ai_models", {})
    patterns   = signal.get("patterns", [])

    pair_clean = pair.replace("frx", "").replace("USDT", "/USDT")

    tf_line = ""
    if isinstance(mtf, dict):
        tfs = mtf.get("tfs", {})
        if isinstance(tfs, dict):
            parts = []
            for tf, label in [("1h","1H"), ("15m","15M"), ("5m","5M"), ("2m","2M")]:
                v = tfs.get(tf, {})
                b = v.get("bias", "?") if isinstance(v, dict) else "?"
                arrow = "↑" if b == "bull" else ("↓" if b == "bear" else "→")
                parts.append(f"{label}{arrow}")
            tf_line = " ".join(parts)
        kz = mtf.get("killzone", "none")
        if kz and kz != "none":
            tf_line += f"\n⚡ KZ: {kz.title()} Open Active"

    smc_line = ""
    if isinstance(smc, dict):
        checks = []
        if smc.get("price_at_bullish_ob") or smc.get("price_at_bearish_ob"):
            checks.append("OB✓")
        if smc.get("price_in_bullish_fvg") or smc.get("price_in_bearish_fvg"):
            checks.append("FVG✓")
        if smc.get("bullish_bos") or smc.get("bearish_bos"):
            checks.append("BOS✓")
        if smc.get("in_ote_zone"):
            checks.append("OTE✓")
        smc_line = " ".join(checks)

    react_line = ""
    if isinstance(react, dict):
        bull_r = int(float(react.get("bull_score", 0)))
        bear_r = int(float(react.get("bear_score", 0)))
        pin    = "PIN✓" if react.get("pin_bar") else ""
        react_line = f"Bull {bull_r} | Bear {bear_r} {pin}"

    top_models = ""
    if isinstance(models, dict):
        sorted_m = sorted(models.items(), key=lambda x: x[1], reverse=True)[:3]
        top_models = " | ".join(f"{k.upper()} {int(v*100)}%" for k, v in sorted_m)

    pattern_line = ", ".join(str(p) for p in patterns[:3]) if patterns else ""

    msg = (
        f"{_dir_emoji(d)} *{pair_clean}* — {d} (CALL/BUY)" if d == "GREEN"
        else f"{_dir_emoji(d)} *{pair_clean}* — {d} (PUT/SELL)"
    )
    msg += f"\nGrade: {_grade_emoji(grade_str)} {grade_str} | Confidence: {conf:.1f}%"
    msg += f"\nTrade: Time Candle 1M | Entry: IMMEDIATE"
    if tf_line:
        msg += f"\n\n📊 MTF: {tf_line}"
    if smc_line:
        msg += f"\n🏛️ SMC: {smc_line}"
    if react_line:
        msg += f"\n⚡ Reaction: {react_line}"
    if top_models:
        msg += f"\n🤖 Top Models: {top_models}"
    if pattern_line:
        msg += f"\n📐 Patterns: {pattern_line}"
    msg += f"\n\n⏰ Max delay: 5 seconds!"
    msg += f"\n#PlaybitAI #{pair_clean.replace('/', '')}"

    return msg


async def send_signal_alert(signal: dict[str, object], pair: str) -> None:
    """Send Telegram alert for HIGH+ signals."""
    grade_str = str(signal.get("grade", "SKIP"))
    if grade_str not in ("ELITE", "HIGH"):
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    if not _HAS_AIOGRAM:
        log.warning("aiogram not installed — Telegram alerts disabled")
        return

    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        msg = _format_message(signal, pair)
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=msg,
            parse_mode=ParseMode.MARKDOWN,
        )
        await bot.session.close()
    except Exception as e:
        log.error("Telegram send failed: %s", e)
