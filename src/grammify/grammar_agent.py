from pydantic import BaseModel, Field
from openai import OpenAI
from openai_cost_calculator import estimate_cost_typed
import inspect
from decimal import Decimal


class GrammarAgentResponse(BaseModel):
    needs_correction: bool = Field(
        description="If the input text needs any correction?"
    )
    final_corrected_text: str = Field(
        description="The corrected text without any change in the meaning."
    )
    diff_text: str = Field(
        description="The text showing changes: removed parts inside <s>content</s> "
        "tag and added parts in <b>content</b> tag. If the part is replaced with some "
        "other value, there shouldn't be a space between </s> and <b> tags. "
        "Make the diff parts as short as possible. For example, prefer "
        "'But <s>the</s> HR told' over '<s>But the HR told</s><b>But the HR told</b>'."
    )
    correction_notes: list[str] = Field(
        description="Additional notes about the corrections or any suggestion for improvement."
    )

    answered_question: str | None = Field(
        "If the input text is a question related to English language, the answer should be here."
    )


class GrammarAgent:
    _SYSTEM_PROMPT = inspect.cleandoc("""
        You are a grammar-correction assistant.

        Correct grammar, punctuation, and spelling only.

        Example: 
        Input: "He go to school."
        final_corrected_text: He goes to school.
        diff_text: "He <s>go</s><b>goes</b> to school."

        Also, if the user asked a question related to English language, answer the question.

        - Do NOT ask any questions.
        - Do NOT add new ideas or rewrite for style beyond what is required to correct errors.
        """)

    def __init__(self, client: OpenAI, calculate_cost: bool) -> None:
        self._client = client
        self._calculate_cost = calculate_cost

    def handle(self, text: str) -> tuple[GrammarAgentResponse, Decimal]:
        completion = self._client.chat.completions.parse(
            model="gpt-5.2-2025-12-11",
            messages=[
                {"role": "system", "content": self._SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            response_format=GrammarAgentResponse,
        )

        reply_message: GrammarAgentResponse | None = completion.choices[
            0
        ].message.parsed

        if reply_message is None:
            raise RuntimeError(
                f"Cannot parse reply message as a {GrammarAgentResponse.__name__}."
            )

        total_cost = Decimal()

        if self._calculate_cost:
            total_cost = estimate_cost_typed(completion).total_cost

        return reply_message, total_cost
