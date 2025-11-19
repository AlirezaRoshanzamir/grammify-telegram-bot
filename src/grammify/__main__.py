import logging
import re

from telegram import ForceReply, Update, constants
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from openai import OpenAI
import httpx
import inspect
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


class AppSettings(BaseSettings):
    openai_token: str
    telegram_bot_token: str
    proxy: str
    selected_users: list[int]

    model_config = SettingsConfigDict(env_prefix="GRAMMIFY_", env_file=".env")


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
    api_key=app_settings.openai_token,
    http_client=httpx.Client(proxy=app_settings.proxy) if app_settings.proxy else None,
)


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

    await update.message.reply_text(
        str(update.effective_user.id), reply_to_message_id=update.message.message_id
    )


async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message is not None, "For typing."

    if not app_settings.selected_users:
        text = "All"
    else:
        text = "\n".join(str(user_id) for user_id in app_settings.selected_users)

    await update.message.reply_text(
        text=text, reply_to_message_id=update.message.message_id
    )


class FixGrammarResponse(BaseModel):
    corrected_text: str = Field(
        description="The corrected text without any change in the meaning."
    )
    need_correction: bool = Field(description="If the input text needs any correction?")
    notes: list[str] = Field(
        description="Additional notes about the corrections or any suggestion for improvement."
    )


def format_fix_grammar_response(response: FixGrammarResponse) -> str:
    result = response.corrected_text

    if response.notes:
        result += "\n\n"
        result += "<b>Notes:</b>"
        result += "\n"
        result += "\n".join(f"- {note}" for note in response.notes)

    return result


def should_ignore_message_text(text: str) -> bool:
    normalized_text = text.strip()

    if not normalized_text:
        return False

    if PERSIAN_LETTER_PATTERN.search(normalized_text):
        return True

    compact_text = IGNORABLE_EMOJI_CHARS_PATTERN.sub("", normalized_text)
    return bool(compact_text) and bool(EMOJI_ONLY_PATTERN.fullmatch(compact_text))


async def fix_grammar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message or update.edited_message

    assert message is not None, "For typing."

    message_text = message.text or ""

    if should_ignore_message_text(message_text):
        return

    await message.set_reaction(reaction=constants.ReactionEmoji.EYES)

    system_prompt = inspect.cleandoc(
        """
        You are a grammar-correction assistant.

        Correct grammar, punctuation, and spelling only.

        Do NOT ask any questions.
        Do NOT add new ideas or rewrite for style beyond what is required to correct errors.
        """
    )

    try:
        completion = openai_client.chat.completions.parse(
            model="gpt-5-nano",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message_text},
            ],
            response_format=FixGrammarResponse,
        )

        reply_message: FixGrammarResponse | None = completion.choices[0].message.parsed
    except Exception as e:
        await message.set_reaction(
            reaction=constants.ReactionEmoji.PERSON_WITH_FOLDED_HANDS
        )
        await message.reply_text(text=f"I'm sorry, there's an error with backend: {e}")
        return

    if reply_message is None:
        await message.set_reaction(
            reaction=constants.ReactionEmoji.PERSON_WITH_FOLDED_HANDS
        )
        await message.reply_text(
            text="I'm sorry, there's an error with backend models."
        )
        return

    if not reply_message.need_correction:
        await message.set_reaction(reaction=constants.ReactionEmoji.FIRE)
    else:
        await message.reply_html(
            text=format_fix_grammar_response(reply_message),
            reply_to_message_id=message.message_id,
        )
        await message.set_reaction(reaction=constants.ReactionEmoji.WRITING_HAND)


def start_the_bot() -> None:
    application_builder = Application.builder().token(app_settings.telegram_bot_token)

    if app_settings.proxy:
        application_builder = application_builder.get_updates_proxy(
            app_settings.proxy
        ).proxy(app_settings.proxy)

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
    application.add_handler(
        MessageHandler(
            filters=users_filter & filters.TEXT & ~filters.COMMAND,
            callback=fix_grammar,
        )
    )

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    start_the_bot()
