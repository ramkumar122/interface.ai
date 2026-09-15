"""All Gemini access lives here. No other file imports ``google.genai``.

Encapsulates: AFC disabled, ``mode="ANY"``, temperature 0,
``generate_content`` (not streaming), model name from env. Thought signatures
are preserved in the returned ``content`` so callers can echo them back —
Gemini 3 hard-fails (400) without them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from google import genai
from google.genai import types

from surface.base import action_function_declarations


@dataclass(frozen=True)
class BrainResponse:
    """Result of one ``decide()`` call.

    ``content`` is the full model response Content (including any
    thought-signature parts). Callers must echo it back into history
    unmodified — dropping a thought signature breaks Gemini 3.
    """

    function_call_name: str | None
    function_call_args: dict | None
    content: Any
    prompt_tokens: int
    output_tokens: int


class GeminiBrain:
    """Gemini client for the discovery loop.

    Constructed once per run. ``configure()`` builds the tool declarations
    from the ``Action`` types and sets up the ``GenerateContentConfig``.
    ``decide()`` sends the current contents and returns the model's function
    call plus the full response content for history.
    """

    def __init__(self, model: str, api_key: str) -> None:
        self._model = model
        self._client = genai.Client(api_key=api_key)
        self._config: types.GenerateContentConfig | None = None

    def configure(
        self,
        input_names: list[str],
        output_names: list[str],
        rules: str,
    ) -> None:
        """Build tool declarations and model config. Call once before the loop."""
        decls = action_function_declarations(input_names, output_names)
        self._config = types.GenerateContentConfig(
            temperature=0,
            system_instruction=rules,
            tools=[
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(**d) for d in decls
                    ]
                )
            ],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="ANY")
            ),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        )

    def decide(self, contents: list) -> BrainResponse:
        """Send *contents* to Gemini and return the function call.

        The returned ``content`` preserves thought signatures and must be
        appended to history as-is. Retries on 429 (rate limit) up to 3 times
        with exponential backoff.
        """
        import time as _time

        if self._config is None:
            raise RuntimeError("call configure() before decide()")

        last_exc = None
        for attempt in range(5):
            try:
                resp = self._client.models.generate_content(
                    model=self._model, contents=contents, config=self._config
                )
                break
            except Exception as exc:
                if "429" in str(exc) and attempt < 4:
                    # Free-tier rolling window can be up to 60s. Back off
                    # aggressively: 10s, 30s, 60s, 90s.
                    wait = [10, 30, 60, 90][attempt]
                    _time.sleep(wait)
                    last_exc = exc
                    continue
                raise
        else:
            raise last_exc  # type: ignore[misc]
        um = resp.usage_metadata
        prompt_tokens = (um.prompt_token_count or 0) if um else 0
        output_tokens = (um.candidates_token_count or 0) if um else 0
        cand = resp.candidates[0]

        fc = next(
            (pt.function_call for pt in cand.content.parts if pt.function_call),
            None,
        )
        return BrainResponse(
            function_call_name=fc.name if fc else None,
            function_call_args=dict(fc.args) if fc else None,
            content=cand.content,
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
        )

    # -- Content building (keeps genai types inside brain.py) -------------- #

    @staticmethod
    def make_user_content(text: str) -> Any:
        """Build a user-role Content from plain text.

        Exists so ``loop.py`` never imports ``google.genai`` — all SDK types
        stay inside this module.
        """
        return types.Content(
            role="user",
            parts=[types.Part.from_text(text=text)],
        )

    @staticmethod
    def make_function_response_content(name: str, response: dict) -> Any:
        """Build a user-role Content containing a function response."""
        return types.Content(
            role="user",
            parts=[types.Part.from_function_response(name=name, response=response)],
        )
