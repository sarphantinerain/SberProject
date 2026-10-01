"""Адаптер настоящего DeepSeek Chat Completions API."""

from copy import deepcopy
import os
from pathlib import Path


DEFAULT_MODEL = "deepseek-flash"
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

class RequestLimitExceeded(RuntimeError):
    """Внешняя страховка от бесконечных платных запросов при отладке."""


class DeepSeekModel:
    def __init__(self, model=DEFAULT_MODEL, *, client=None, watchdog=8, config=None):
        if config is not None:
            watchdog = config.max_steps * 2   # запас поверх max_steps
            self.model_timeout_default = config.model_timeout
        else:
            self.model_timeout_default = 20.0
        if type(watchdog) is not int or watchdog < 1:
            raise ValueError("watchdog должен быть положительным целым")
        if client is None:
            from dotenv import load_dotenv

            load_dotenv(ENV_PATH, override=False, encoding="utf-8-sig")
            api_key = os.environ.get("DEEPSEEK_API_KEY")
            if not api_key:
                raise RuntimeError("Заполните DEEPSEEK_API_KEY в .env в корне проекта агента (образец: .env.example)")
            from openai import OpenAI

            client = OpenAI(
                api_key=api_key,
                base_url="https://api.deepseek.com",
                timeout=20.0,
                max_retries=0,
            )
        self.client = client
        self.model = model
        self.watchdog = watchdog
        self.calls = 0
        self.last_response = None

    def __call__(self, *, messages, tools, timeout=None):
        if self.calls >= self.watchdog:
            raise RequestLimitExceeded(
                f"Достигнут клиентский лимит: {self.watchdog} запросов. Работа не завершена."
            )
        self.calls += 1
        request_timeout = timeout if timeout is not None else self.model_timeout_default
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
            stream=False,
            max_tokens=1024,
            timeout=request_timeout,
            extra_body={"thinking": {"type": "disabled"}},
        )
        choice = response.choices[0]
        message = {"role": "assistant", "content": choice.message.content}
        if choice.message.tool_calls:
            message["tool_calls"] = [
                call.model_dump(exclude_none=True) for call in choice.message.tool_calls
            ]
        reply = {"message": message, "finish_reason": choice.finish_reason}
        self.last_response = deepcopy(reply)
        return reply

    def close(self):
        self.client.close()
