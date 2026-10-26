from __future__ import annotations

import asyncio
import io
import logging
import os
from datetime import date, datetime, time, timedelta
from functools import wraps

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from madeira.domain.config import Settings
from madeira.domain.models import OUTCOME_LABELS
from madeira.presenters import telegram as fmt
from madeira.services import forecast, story_image, webexport
from madeira.storage.db import Store

log = logging.getLogger("madeira.bot")
TZ = forecast.TZ

HELP = (
    "<b>Madeira Sunrise Bot</b>\n"
    "/sunrise [today|tomorrow|YYYY-MM-DD] – skóre východu na Arieiru\n"
    "/story – obrázek 9:16 pro Instagram/WhatsApp Story\n"
    "/week – výhled na 7 dní\n"
    "/compare [den] [od] [do] – srovnání lokalit, např. <code>/compare tomorrow 13:00 18:00</code>\n"
    "/rate [den] – zapsat, jak východ opravdu dopadl\n"
    "/stop – vypnout večerní digest, /start – znovu zapnout"
)


def feedback_keyboard(day: date) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(label, callback_data=f"fb:{day.isoformat()}:{key}")]
        for key, label in OUTCOME_LABELS.items()
    ]
    rows.append(
        [InlineKeyboardButton("🤷 Nebyl jsem tam", callback_data=f"fb:{day.isoformat()}:skip")]
    )
    return InlineKeyboardMarkup(rows)


def guarded(fn):
    """Pokud je nastaveno MADEIRA_ALLOWED_CHATS, reaguje jen na tyto chaty."""

    @wraps(fn)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        allowed: set[int] = context.application.bot_data["allowed_chats"]
        chat = update.effective_chat
        if allowed and (chat is None or chat.id not in allowed):
            log.warning("Ignoruji nepovolený chat %s", chat.id if chat else None)
            return
        await fn(update, context)

    return wrapper


def _store(context) -> Store:
    return context.application.bot_data["store"]


def _settings(context) -> Settings:
    return context.application.bot_data["settings"]


async def _reply(update: Update, text: str, **kwargs) -> None:
    await update.effective_message.reply_html(text, **kwargs)


def _arg_day(context, default: str = "tomorrow") -> date:
    return forecast.parse_day(context.args[0] if context.args else default)


@guarded
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _store(context).subscribe(update.effective_chat.id)
    s = _settings(context).bot
    await _reply(
        update,
        f"Ahoj! Večerní digest chodí v {s.digest_time}, dotaz na výsledek v {s.feedback_time} "
        f"(čas Madeiry).\n\n{HELP}",
    )


@guarded
async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _store(context).unsubscribe(update.effective_chat.id)
    await _reply(update, "Digest vypnut. Příkazy fungují dál, /start ho zapne znovu.")


@guarded
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, HELP)


@guarded
async def cmd_sunrise(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        day = _arg_day(context)
    except ValueError as exc:
        await _reply(update, str(exc))
        return
    analysis = await asyncio.to_thread(forecast.predict, day, _settings(context), _store(context))
    markup = None
    if analysis.sunrise_utc <= datetime.now(analysis.sunrise_utc.tzinfo):
        markup = feedback_keyboard(day)  # východ už proběhl → rovnou nabídni hodnocení
    await _reply(update, fmt.format_sunrise(analysis), reply_markup=markup)


@guarded
async def cmd_story(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    def make() -> bytes:
        payload = webexport.build_payload(_settings(context), 1, store=_store(context))
        return story_image.story_bytes(payload)

    data = await asyncio.to_thread(make)
    await update.effective_message.reply_photo(io.BytesIO(data), filename="story.png")


@guarded
async def cmd_week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    analyses = await asyncio.to_thread(forecast.predict_week, _settings(context), _store(context))
    await _reply(update, fmt.format_week(analyses))


@guarded
async def cmd_compare(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = _settings(context).bot
    args = context.args or []
    try:
        day = forecast.parse_day(args[0] if args else "tomorrow")
        from_ = args[1] if len(args) > 1 else cfg.compare_from
        to = args[2] if len(args) > 2 else cfg.compare_to
        time.fromisoformat(from_), time.fromisoformat(to)
    except ValueError:
        await _reply(update, "Použití: /compare [den] [HH:MM] [HH:MM]")
        return
    comparison = await asyncio.to_thread(forecast.compare, day, from_, to)
    await _reply(update, fmt.format_comparison(comparison))


@guarded
async def cmd_rate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        day = _arg_day(context, default="today")
    except ValueError as exc:
        await _reply(update, str(exc))
        return
    store = _store(context)
    pred = store.latest_prediction(day)
    hint = f"\nPredikce byla {pred['score']:.0f} % ({pred['verdict']})." if pred else ""
    await _reply(
        update,
        f"Jak dopadl východ slunce <b>{day:%d.%m.%Y}</b> na Arieiru?{hint}",
        reply_markup=feedback_keyboard(day),
    )


@guarded
async def cb_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        _, day_s, key = query.data.split(":")
        day = date.fromisoformat(day_s)
    except ValueError:
        return
    if key == "skip":
        await query.edit_message_text("Ok, přeskočeno.")
        return
    if key not in OUTCOME_LABELS:
        return
    try:
        result = await asyncio.to_thread(
            forecast.record_outcome,
            day,
            key,
            _settings(context),
            _store(context),
            update.effective_chat.id,
        )
    except ValueError as exc:
        await query.edit_message_text(str(exc))
        return
    lines = [
        f"✅ Zapsáno pro {day:%d.%m.%Y}: {OUTCOME_LABELS[key]}",
        f"(Změna: /rate {day.isoformat()})",
    ]
    if result.backfilled:
        score = result.analysis.score_pct
        lines.append(f"Predikce chyběla, dopočítáno z historie: {score:.0f} %.")
    if result.warning:
        lines.append(f"⚠️ {result.warning}")
    text = "\n".join(lines)
    await query.edit_message_text(text)


async def job_digest(context: ContextTypes.DEFAULT_TYPE) -> None:
    store, settings = _store(context), _settings(context)
    chats = store.subscribers()
    if not chats:
        return
    try:
        week = await asyncio.to_thread(forecast.predict_week, settings, store)
        tomorrow_date = forecast.today_local() + timedelta(days=1)
        tomorrow = next((a for a in week if a.target_date == tomorrow_date), None)
        if tomorrow is None:
            raise RuntimeError("Předpověď neobsahuje zítřejší den")
        text = fmt.format_digest(tomorrow, week)
    except Exception:
        log.exception("Digest selhal")
        text = "⚠️ Nepodařilo se získat předpověď pro večerní digest."
    for chat_id in chats:
        try:
            await context.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
        except Exception:
            log.exception("Odeslání digestu do chatu %s selhalo", chat_id)


async def job_feedback(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ráno se zeptá, jak dnešní východ dopadl (jen pokud existuje predikce a ještě není hodnocení)."""
    store = _store(context)
    today = forecast.today_local()
    pred = store.latest_prediction(today)
    if pred is None or store.get_outcome(today) is not None:
        return
    text = (
        f"🌅 Jak dopadl dnešní východ slunce na Arieiru?\n"
        f"Predikce byla {pred['score']:.0f} % ({pred['verdict']})."
    )
    for chat_id in store.subscribers():
        try:
            await context.bot.send_message(chat_id, text, reply_markup=feedback_keyboard(today))
        except Exception:
            log.exception("Odeslání dotazu do chatu %s selhalo", chat_id)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Chyba v handleru", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            "Něco se pokazilo (zdroj dat nedostupný nebo špatný vstup). Zkus to znovu později."
        )


def _parse_chats(raw: str | None) -> set[int]:
    return {int(x) for x in (raw or "").replace(";", ",").split(",") if x.strip()}


def build_application(token: str, store: Store, settings: Settings) -> Application:
    app = Application.builder().token(token).build()
    app.bot_data.update(
        store=store,
        settings=settings,
        allowed_chats=_parse_chats(os.environ.get("MADEIRA_ALLOWED_CHATS")),
    )
    for name, fn in [
        ("start", cmd_start),
        ("stop", cmd_stop),
        ("help", cmd_help),
        ("sunrise", cmd_sunrise),
        ("story", cmd_story),
        ("week", cmd_week),
        ("compare", cmd_compare),
        ("rate", cmd_rate),
    ]:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(cb_feedback, pattern=r"^fb:"))
    app.add_error_handler(on_error)

    jq = app.job_queue
    digest_t = time.fromisoformat(settings.bot.digest_time).replace(tzinfo=TZ)
    feedback_t = time.fromisoformat(settings.bot.feedback_time).replace(tzinfo=TZ)
    jq.run_daily(job_digest, digest_t, name="digest")
    jq.run_daily(job_feedback, feedback_t, name="feedback")
    return app
