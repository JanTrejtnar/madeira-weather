from __future__ import annotations

import asyncio
import io
import logging
import os
from datetime import date, datetime, time, timedelta
from functools import wraps
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from madeira.domain.config import Settings, spot_event
from madeira.domain.models import OUTCOME_LABELS
from madeira.presenters import telegram as fmt
from madeira.services import forecast, ground_truth, story_renderer, webexport
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


def _labels_path(context) -> Path:
    return context.application.bot_data.get("labels_path", ground_truth.LABELS_PATH)


def _spot_label(settings: Settings, spot: str) -> str:
    return settings.spots[spot].name.removeprefix("Miradouro da ").removeprefix("Miradouro do ")


def survey_keyboard(days: dict[str, date], settings: Settings, labels: dict[str, str | None]) -> InlineKeyboardMarkup:
    """Jedna zpráva, řádek na spot: [název] [🟢] [🟡] [🔴]; zvolený verdikt je označen ✅."""
    rows = []
    for spot, day in days.items():
        row = [InlineKeyboardButton(_spot_label(settings, spot), callback_data="gt:noop")]
        for code, verdict in ground_truth.VERDICT_CODES.items():
            mark = "✅" if labels.get(spot) == verdict else ground_truth.VERDICT_ICONS[verdict]
            row.append(InlineKeyboardButton(mark, callback_data=f"gt:{day.isoformat()}:{spot}:{code}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def survey_text(days: dict[str, date], settings: Settings) -> str:
    sunrise = next((d for k, d in days.items() if spot_event(settings.spots[k]) == "sunrise"), None)
    sunset = next((d for k, d in days.items() if spot_event(settings.spots[k]) == "sunset"), None)
    parts = ([f"východy {sunrise:%d.%m.}"] if sunrise else []) + ([f"západy {sunset:%d.%m.}"] if sunset else [])
    return (
        f"🌅 Jak to dopadlo? ({', '.join(parts)})\n"
        "Jedním klikem 🟢 GO / 🟡 MAYBE / 🔴 NO-GO u míst, kde jsi byl nebo máš přehled. Zbytek přeskoč."
    )


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


STORY_CAPTION = "📸 Tady je připravená předpověď na zítřek pro Instagram Stories."


def _make_story(context) -> bytes:
    payload = webexport.build_payload(_settings(context), 1, store=_store(context))
    return story_renderer.story_bytes(payload)


async def _send_story(bot, chat_id: int, data: bytes) -> None:
    """Primárně fotka (velký náhled a rychlé sdílení do Instagramu), pod ní originál jako soubor bez komprese."""
    await bot.send_photo(chat_id, io.BytesIO(data), caption=STORY_CAPTION)
    try:
        await bot.send_document(chat_id, io.BytesIO(data), filename="story.png")
    except Exception:
        log.exception("Odeslání story jako souboru do chatu %s selhalo", chat_id)


@guarded
async def cmd_story(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = await asyncio.to_thread(_make_story, context)
    await _send_story(context.bot, update.effective_chat.id, data)


async def job_story(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Večer vygeneruje story na zítřek a pošle ji odběratelům (fotka + soubor)."""
    chats = _store(context).subscribers()
    if not chats:
        return
    try:
        data = await asyncio.to_thread(_make_story, context)
    except Exception:
        log.exception("Generování story selhalo")
        return
    for chat_id in chats:
        try:
            await _send_story(context.bot, chat_id, data)
        except Exception:
            log.exception("Odeslání story do chatu %s selhalo", chat_id)


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


@guarded
async def cb_survey(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    settings = _settings(context)
    try:
        _, day_s, spot, code = query.data.split(":")
        day = date.fromisoformat(day_s)
        verdict = ground_truth.VERDICT_CODES[code]
    except (ValueError, KeyError):
        return  # "gt:noop" (klik na název místa) nebo cizí data
    if spot not in ground_truth.SURVEY_SPOTS or spot not in settings.spots:
        return
    path = _labels_path(context)
    chat_id = update.effective_chat.id
    store = _store(context)
    pred = await asyncio.to_thread(store.latest_prediction, day, spot)
    await asyncio.to_thread(ground_truth.record, day, spot, verdict, chat_id, path, pred)
    note = f"predicted {pred['score']:.0f} % {pred['verdict']}" if pred else None
    await asyncio.to_thread(store.set_outcome, day, verdict, chat_id, spot, note)
    days = ground_truth.survey_days(forecast.today_local(), settings)
    labels = {s: ground_truth.get(d, s, path) for s, d in days.items()}
    await query.edit_message_reply_markup(survey_keyboard(days, settings, labels))


async def job_feedback(context: ContextTypes.DEFAULT_TYPE) -> None:
    """V 10:00 pošle JEDNU souhrnnou zprávu s tlačítky pro všechna sledovaná místa (bez notifikačního spamu)."""
    store, settings = _store(context), _settings(context)
    path = _labels_path(context)
    days = ground_truth.survey_days(forecast.today_local(), settings)
    labels = {s: ground_truth.get(d, s, path) for s, d in days.items()}
    if all(labels.values()):
        return
    text = survey_text(days, settings)
    markup = survey_keyboard(days, settings, labels)
    for chat_id in store.subscribers():
        try:
            await context.bot.send_message(chat_id, text, reply_markup=markup)
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
    app.add_handler(CallbackQueryHandler(cb_survey, pattern=r"^gt:"))
    app.add_error_handler(on_error)

    jq = app.job_queue
    digest_t = time.fromisoformat(settings.bot.digest_time).replace(tzinfo=TZ)
    feedback_t = time.fromisoformat(settings.bot.feedback_time).replace(tzinfo=TZ)
    jq.run_daily(job_digest, digest_t, name="digest")
    jq.run_daily(job_feedback, feedback_t, name="feedback")
    story_t = time.fromisoformat(settings.bot.story_time).replace(tzinfo=TZ)
    jq.run_daily(job_story, story_t, name="story")
    return app
