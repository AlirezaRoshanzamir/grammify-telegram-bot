from pydantic import BaseModel, Field
from openai import OpenAI
from openai_cost_calculator import estimate_cost_typed
import inspect
from decimal import Decimal


class GeneralAgent:
    _SYSTEM_PROMPT = inspect.cleandoc("""
        You are a useful assistant.
    """)

    def __init__(self, client: OpenAI, calculate_cost: bool) -> None:
        self._client = client
        self._calculate_cost = calculate_cost

    def handle(self, text: str) -> tuple[str, Decimal]:
        completion = self._client.chat.completions.create(
            model="gpt-5.4-2026-03-05",
            messages=[
                {"role": "system", "content": self._SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
        )

        reply_message: str | None = completion.choices[0].message.content

        if reply_message is None:
            raise RuntimeError(f"Cannot parse reply message as a {str}.")

        total_cost = Decimal()

        if self._calculate_cost:
            total_cost = estimate_cost_typed(completion).total_cost

        return reply_message, total_cost
