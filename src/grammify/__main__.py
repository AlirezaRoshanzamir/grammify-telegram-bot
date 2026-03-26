import logging
import re

from telegram import ForceReply, Update, constants, Message
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from openai import OpenAI
import httpx
from grammify.grammar_agent import GrammarAgent, GrammarAgentResponse
from grammify.general_agent import GeneralAgent
from pydantic_settings import BaseSettings, SettingsConfigDict
import tempfile
import pathlib
import sys
from decimal import Decimal

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.DEBUG
)
logging.getLogger("httpx").setLevel(logging.INFO)

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler(sys.stdout))


class AppSettings(BaseSettings):
    openai_base_url: str | None = None
    openai_token: str
    telegram_bot_token: str
    telegram_base_url: str | None = None
    telegram_timeout: float | None = 90.0
    openai_proxy: str | None = None
    max_text_length: int = constants.MessageLimit.MAX_TEXT_LENGTH
    telegram_proxy: str | None = None
    selected_users: list[int]
    admin_user: int
    show_cost: bool = False

    model_config = SettingsConfigDict(
        env_prefix="GRAMMIFY_", env_file=".env", extra="ignore"
    )


app_settings = AppSettings()

_EMOJI_CHARACTER_RANGES = (
    "\U0001f1e6-\U0001f1ff"
    "\U0001f300-\U0001f5ff"
    "\U0001f600-\U0001f64f"
    "\U0001f680-\U0001f6ff"
    "\U0001f700-\U0001f77f"
    "\U0001f780-\U0001f7ff"
    "\U0001f800-\U0001f8ff"
    "\U0001f900-\U0001f9ff"
    "\U0001fa00-\U0001fa6f"
    "\U0001fa70-\U0001faff"
    "\u2600-\u26ff"
    "\u2700-\u27bf"
)
EMOJI_ONLY_PATTERN = re.compile(f"^[{_EMOJI_CHARACTER_RANGES}]+$")
PERSIAN_LETTER_PATTERN = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")
IGNORABLE_EMOJI_CHARS_PATTERN = re.compile(r"[\s\u200d\ufe0e\ufe0f]")

openai_client = OpenAI(
    base_url=app_settings.openai_base_url,
    api_key=app_settings.openai_token,
    http_client=(
        httpx.Client(proxy=app_settings.openai_proxy)
        if app_settings.openai_proxy
        else None
    ),
)

grammar_agent = GrammarAgent(
    client=openai_client, calculate_cost=app_settings.show_cost
)
general_agent = GeneralAgent(
    client=openai_client, calculate_cost=app_settings.show_cost
)

consequent_failures: int = 0
liveness_status_sent: bool = False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.effective_user is not None, "For typing."
    assert update.message is not None, "For typing."

    await update.message.reply_html(
        rf"Hi {update.effective_user.mention_html()}!",
        reply_markup=ForceReply(selective=True),
    )


async def get_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.effective_user is not None, "For typing."
    assert update.message is not None, "For typing."

    await reply_text(
        message=update.message,
        text=str(update.effective_user.id),
        reply_to_message_id=update.message.message_id,
    )


async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message is not None, "For typing."

    if not app_settings.selected_users:
        text = "All"
    else:
        text = "\n".join(str(user_id) for user_id in app_settings.selected_users)

    await reply_text(
        message=update.message, text=text, reply_to_message_id=update.message.message_id
    )


def format_grammar_agent_response(
    response: GrammarAgentResponse, cost: Decimal, show_cost: bool
) -> str:
    if response.needs_correction:
        result = response.final_corrected_text

        if response.correction_notes:
            result += "\n\n"
            result += "<b>Notes:</b>"
            result += "\n"
            result += "\n".join(f"- {note}" for note in response.correction_notes)
    else:
        result = "There's no need of correction."

    if response.answered_question:
        result += "\n\n"
        result += "<b>Answer:</b>"
        result += "\n"
        result += response.answered_question

    if show_cost:
        result += "\n\n"
        result += f"<b>Cost: ${cost}</b>"

    if len(result) > constants.MessageLimit.CAPTION_LENGTH:
        result = result[: constants.MessageLimit.CAPTION_LENGTH - 3] + "..."

    return result


def format_general_agent_response(response: str) -> str:
    return response.replace("**", "*")


def should_ignore_message_text(text: str) -> bool:
    normalized_text = text.strip()

    if not normalized_text:
        return False

    if PERSIAN_LETTER_PATTERN.search(normalized_text):
        return True

    compact_text = IGNORABLE_EMOJI_CHARS_PATTERN.sub("", normalized_text)
    return bool(compact_text) and bool(EMOJI_ONLY_PATTERN.fullmatch(compact_text))


def escape_text(text: str) -> str:
    special_chars = "_*[]()~`>#+-=|{}.!"
    escaped_text = ""
    for char in text:
        if char in special_chars:
            escaped_text += "\\" + char
        else:
            escaped_text += char
    return escaped_text


async def set_reaction_if_supported(
    message: Message, reaction: constants.ReactionEmoji
) -> None:
    if (
        app_settings.telegram_base_url is not None
        and "telegram" in app_settings.telegram_base_url
    ):
        await message.set_reaction(reaction)


async def reply_text(
    message: Message,
    text: str,
    reply_to_message_id: int | None = None,
    parse_mode: constants.ParseMode = constants.ParseMode.HTML,
) -> None:
    parts = [
        text[i : i + app_settings.max_text_length]
        for i in range(0, len(text), app_settings.max_text_length)
    ]
    for part in parts:
        await message.reply_text(
            text=part,
            reply_to_message_id=reply_to_message_id,
            parse_mode=parse_mode,
        )


async def handle_grammar_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    global consequent_failures

    from grammify.text_to_image import tagged_text_to_image

    message = update.message or update.edited_message

    assert message is not None, "For typing."

    message_text = message.text or ""

    if should_ignore_message_text(message_text):
        return

    await set_reaction_if_supported(
        message=message, reaction=constants.ReactionEmoji.OK_HAND_SIGN
    )

    try:
        response, cost = grammar_agent.handle(message_text)
    except Exception as e:
        await set_reaction_if_supported(
            message=message, reaction=constants.ReactionEmoji.PERSON_WITH_FOLDED_HANDS
        )
        await reply_text(
            message=message, text=f"I'm sorry, there's an error with backend: {e}"
        )
        return

    if not response.needs_correction:
        await set_reaction_if_supported(
            message=message, reaction=constants.ReactionEmoji.FIRE
        )
    else:
        await set_reaction_if_supported(
            message=message, reaction=constants.ReactionEmoji.WRITING_HAND
        )

    if response.needs_correction or response.answered_question:
        response_text = format_grammar_agent_response(
            response, cost, app_settings.show_cost
        )
        try:
            if response.needs_correction:
                temp_file_pth = tempfile.mktemp(suffix=".png")
                try:
                    tagged_text_to_image(
                        tagged_text=response.diff_text,
                        max_letters_in_a_row=70,
                        each_tag_style={
                            "b": lambda text: text.color("green"),
                            "s": lambda text: text.color("red").strikethrough(),
                        },
                        output_path=temp_file_pth,
                    )
                    await message.reply_photo(
                        photo=temp_file_pth,
                        caption=response_text,
                        reply_to_message_id=message.message_id,
                        parse_mode=constants.ParseMode.HTML,
                    )
                finally:
                    pathlib.Path(temp_file_pth).unlink(missing_ok=True)
            else:
                await reply_text(
                    message=message,
                    text=response_text,
                    reply_to_message_id=message.message_id,
                    parse_mode=constants.ParseMode.HTML,
                )
            consequent_failures = 0
        except Exception as e:
            consequent_failures += 1
            await set_reaction_if_supported(
                message=message,
                reaction=constants.ReactionEmoji.PERSON_WITH_FOLDED_HANDS,
            )
            await reply_text(
                message=message, text=f"I'm sorry, there's an error with backend: {e}"
            )
            return


async def handle_general_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    global consequent_failures

    message = update.message or update.edited_message

    assert message is not None, "For typing."

    message_text = message.text or message.caption or ""

    logger.info(f"A general request with the following text:\n{message_text}")

    try:
        response, cost = general_agent.handle(message_text)

        await reply_text(
            message=message,
            text=format_general_agent_response(response),
            reply_to_message_id=message.message_id,
            parse_mode=constants.ParseMode.HTML,
        )
        consequent_failures = 0
    except Exception as e:
        consequent_failures += 1
        await reply_text(
            message=message, text=f"I'm sorry, there's an error with backend: {e}"
        )
        return


async def send_liveness_status(context: ContextTypes.DEFAULT_TYPE) -> None:
    global liveness_status_sent

    if consequent_failures > 0:
        liveness_status_sent = False
        return
    
    if liveness_status_sent:
        return

    notifying_users = [app_settings.admin_user, *app_settings.selected_users]
    for user in notifying_users:
        await context.bot.send_message(chat_id=user, text="I'm back :)")
    liveness_status_sent = True


def start_the_bot() -> None:
    application_builder = (
        Application.builder()
        .token(app_settings.telegram_bot_token)
        .read_timeout(app_settings.telegram_timeout)
        .write_timeout(app_settings.telegram_timeout)
        .connect_timeout(app_settings.telegram_timeout)
        .get_updates_read_timeout(app_settings.telegram_timeout)
        .get_updates_write_timeout(app_settings.telegram_timeout)
    )

    if app_settings.telegram_base_url:
        application_builder = application_builder.base_url(
            app_settings.telegram_base_url
        )

    if app_settings.telegram_proxy:
        application_builder = application_builder.get_updates_proxy(
            app_settings.telegram_proxy
        ).proxy(app_settings.telegram_proxy)

    application = application_builder.build()

    users_filter: filters.BaseFilter = filters.ALL

    if app_settings.selected_users:
        users_filter = filters.User(app_settings.selected_users)

    application.add_handler(
        CommandHandler(command="start", callback=start, filters=users_filter)
    )
    application.add_handler(
        CommandHandler(command="userid", callback=get_user_id, filters=users_filter)
    )
    application.add_handler(
        CommandHandler(command="users", callback=list_users, filters=users_filter)
    )
    # application.add_handler(
    #     MessageHandler(
    #         # filters=users_filter & filters.TEXT & ~filters.COMMAND,
    #         filters=None,
    #         callback=handle_grammar_message,
    #     )
    # )
    application.add_handler(
        MessageHandler(
            # filters=users_filter & filters.TEXT & ~filters.COMMAND,
            filters=None,
            callback=handle_general_message,
        )
    )

    if application.job_queue is not None:
        application.job_queue.run_repeating(send_liveness_status, interval=10)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    start_the_bot()
