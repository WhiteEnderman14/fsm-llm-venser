"""LLM providers behind one small interface (`classify` and `write`).

Only llama3.2 on Ollama is the intended model; the other providers and models are available but
untested. A provider is a function that builds a LangChain chat model. The three built-in ones are
registered below, and `register_provider` adds more (from a plugin file too), so supporting a new
LLM never needs a change in the engine.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel


@dataclass(frozen=True)
class Provider:
    """A way to reach a family of models.

    Args:
        name: Name used with --provider.
        make: Builds the LangChain chat model from (model name, temperature, extra options).
        models: Known model names mapped to a flag ("default" or "untested"). Only a hint:
            any other name the provider accepts can still be used.
        default: Model used when none is given (the first of `models` if empty).
        extra: Name of the pip extra that installs the package, for the error message.
    """

    name: str
    make: Callable[[str, float, dict[str, Any]], Any]
    models: dict[str, str] = field(default_factory=dict)
    default: str = ""
    extra: str | None = None


PROVIDERS: dict[str, Provider] = {}
ALIASES = {"anthropic": "claude", "google": "gemini"}


def register_provider(
    name: str,
    make: Callable[[str, float, dict[str, Any]], Any],
    models: dict[str, str] | None = None,
    default: str | None = None,
    extra: str | None = None,
) -> Provider:
    """Add a provider, or replace one with the same name.

    Call it from your own code or at the top of a plugin file, so the provider is known when the
    command line asks for it.

    Args:
        name (str): Name used with --provider.
        make (Callable): Takes (model name, temperature, options) and returns a LangChain chat model.
        models (dict[str, str] | None): Known model names mapped to "default" or "untested".
        default (str | None): Model used when none is given (the first of `models` if None).
        extra (str | None): Name of the pip extra that installs the package, for the error message.

    Returns:
        Provider: The registered provider.

    Raises:
        ValueError: If there is no default model (no `default` and no `models`).
    """
    models = dict(models or {})
    default = default or next(iter(models), "")
    if not default:
        raise ValueError(f"provider {name!r} needs a default model name")
    models.setdefault(default, "default")
    PROVIDERS[name] = Provider(name, make, models, default, extra)
    return PROVIDERS[name]


def _ollama(name: str, temperature: float, options: dict[str, Any]) -> Any:
    """Local models served by Ollama (it reads the server address from OLLAMA_HOST).

    Args:
        name (str): Model name.
        temperature (float): Sampling temperature.
        options (dict[str, Any]): Extra arguments for ChatOllama.

    Returns:
        Any: The chat model.
    """
    from langchain_ollama import ChatOllama

    return ChatOllama(model=name, temperature=temperature, **options)


def _gemini(name: str, temperature: float, options: dict[str, Any]) -> Any:
    """Google Gemini through the Google API.

    Args:
        name (str): Model name.
        temperature (float): Sampling temperature.
        options (dict[str, Any]): Extra arguments for ChatGoogleGenerativeAI.

    Returns:
        Any: The chat model.
    """
    from langchain_google_genai import ChatGoogleGenerativeAI

    key = _key("GOOGLE_API_KEY", "GEMINI_API_KEY")
    return ChatGoogleGenerativeAI(
        model=name, temperature=temperature, google_api_key=key, **options
    )


def _claude(name: str, temperature: float, options: dict[str, Any]) -> Any:
    """Anthropic Claude through the Anthropic API.

    Args:
        name (str): Model name.
        temperature (float): Sampling temperature.
        options (dict[str, Any]): Extra arguments for ChatAnthropic.

    Returns:
        Any: The chat model.
    """
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(
        model=name, temperature=temperature, api_key=_key("ANTHROPIC_API_KEY"), **options
    )


# Only the first Ollama model is the intended one; the rest has never been run with this project.
register_provider(
    "ollama",
    _ollama,
    {
        "llama3.2": "default",
        "llama3.2:1b": "untested",
        "llama3.1:8b": "untested",
        "llama3.3:70b": "untested",
        "qwen3:4b": "untested",
        "qwen3:8b": "untested",
        "qwen2.5:7b": "untested",
        "qwen2.5:14b": "untested",
    },
    extra="ollama",
)
register_provider(
    "gemini",
    _gemini,
    {
        "gemini-2.5-flash": "default",
        "gemini-2.5-flash-lite": "untested",
        "gemini-2.5-pro": "untested",
        "gemini-3-flash-preview": "untested",
        "gemini-3.1-pro-preview": "untested",
        "gemini-3.5-flash": "untested",
        "gemini-3.5-flash-lite": "untested",
        "gemini-3.6-flash": "untested",
        "gemini-flash-latest": "untested",
        "gemini-flash-lite-latest": "untested",
        "gemini-pro-latest": "untested",
    },
    extra="gemini",
)
register_provider(
    "claude",
    _claude,
    {
        "claude-sonnet-5-5": "default",
        "claude-haiku-5-5": "untested",
        "claude-opus-5-5": "untested",
        "claude-fable-5-1": "untested",
    },
    extra="claude",
)


class LLMClient(Protocol):
    """What the engine needs from a model: a structured answer and free text."""

    def classify(self, system: str, user: str, output_model: type[BaseModel]) -> BaseModel:
        """Ask for an answer shaped like `output_model`.

        Args:
            system (str): The system prompt.
            user (str): The user prompt.
            output_model (type[BaseModel]): The class of the structured answer.

        Returns:
            BaseModel: The answer, as an instance of `output_model`.
        """
        ...

    def write(self, system: str, user: str) -> str:
        """Ask for free text.

        Args:
            system (str): The system prompt.
            user (str): The user prompt.

        Returns:
            str: The text of the answer.
        """
        ...


THINK = re.compile(r"<think>.*?</think>", re.DOTALL)  # reasoning models (qwen3, ...) write this


def _text(content: Any) -> str:
    """The text of a model answer, without the reasoning block some models add.

    Args:
        content (Any): The `content` of a LangChain message: a string, or a list of blocks.

    Returns:
        str: The plain text.
    """
    if isinstance(content, list):  # some providers return a list of content blocks
        content = "".join(p if isinstance(p, str) else p.get("text", "") for p in content)
    return THINK.sub("", content).strip()


class LangChainClient:
    """Adapter from a LangChain chat model to LLMClient.

    `classify` is built to work with most models: it first asks for structured output (retrying
    once), and if the model cannot do that it asks for plain JSON and parses it itself.

    Args:
        model: A LangChain chat model.
        retries: Extra attempts of the structured request before the JSON fallback.
    """

    def __init__(self, model: Any, retries: int = 1) -> None:
        """Create the object."""
        self.model = model
        self.retries = retries

    def classify(self, system: str, user: str, output_model: type[BaseModel]) -> BaseModel:
        """Ask the model for an answer shaped like `output_model`.

        Args:
            system (str): The system prompt.
            user (str): The user prompt.
            output_model (type[BaseModel]): The Pydantic class the answer must fit.

        Returns:
            BaseModel: An instance of `output_model`.

        Raises:
            Exception: The error of the structured request, if the JSON fallback fails too.
        """
        error: Exception | None = None
        for _ in range(self.retries + 1):
            try:
                return self._structured(system, user, output_model)
            except Exception as exc:  # unsupported, bad schema, timeout: try again, then fall back
                error = exc
        try:
            return self._json(system, user, output_model)
        except Exception:
            raise error from None

    def _structured(self, system: str, user: str, output_model: type[BaseModel]) -> BaseModel:
        """One request with the structured output of the provider.

        Args:
            system (str): The system prompt.
            user (str): The user prompt.
            output_model (type[BaseModel]): The Pydantic class the answer must fit.

        Returns:
            BaseModel: An instance of `output_model`.
        """
        out = self.model.with_structured_output(output_model).invoke(_messages(system, user))
        return out if isinstance(out, output_model) else output_model(**out)

    def _json(self, system: str, user: str, output_model: type[BaseModel]) -> BaseModel:
        """Fallback: ask for a JSON object in plain text and parse it.

        Args:
            system (str): The system prompt.
            user (str): The user prompt.
            output_model (type[BaseModel]): The Pydantic class the answer must fit.

        Returns:
            BaseModel: An instance of `output_model`.

        Raises:
            ValueError: If the answer has no JSON object.
        """
        schema = json.dumps(output_model.model_json_schema())
        system += (
            "\n\nAnswer with ONE JSON object and nothing else (no explanation, no code fence). "
            f"It must follow this JSON schema:\n{schema}"
        )
        text = _text(self.model.invoke(_messages(system, user)).content)
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < start:
            raise ValueError(f"no JSON object in the answer: {text[:80]!r}")
        return output_model.model_validate(json.loads(text[start : end + 1]))

    def write(self, system: str, user: str) -> str:
        """Ask the model for free text.

        Args:
            system (str): The system prompt.
            user (str): The user prompt.

        Returns:
            str: The text of the answer.
        """
        return _text(self.model.invoke(_messages(system, user)).content)


def _messages(system: str, user: str) -> list:
    """The two messages sent to the model.

    Args:
        system (str): The system prompt.
        user (str): The user prompt.

    Returns:
        list: The messages in the LangChain format.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    return [SystemMessage(content=system), HumanMessage(content=user)]


def _key(*names: str) -> str:
    """Read an API key from the first environment variable that is set.

    Args:
        *names (str): Names of the environment variables to try, in order.

    Returns:
        str: The key.

    Raises:
        RuntimeError: If none of the variables is set.
    """
    for name in names:
        if os.environ.get(name):
            return os.environ[name]
    raise RuntimeError(f"no API key found: set {' or '.join(names)}")


def get_llm(
    provider: str = "ollama", model: str | None = None, temperature: float = 0.2, **options: Any
) -> LangChainClient:
    """Build a client for a registered provider ("ollama", "gemini", "claude" or your own).

    Args:
        provider (str): Name of the provider ("google" and "anthropic" also work).
        model (str | None): The model name; the default of the provider if None.
        temperature (float): Sampling temperature.
        **options (Any): Extra arguments for the LangChain chat model.

    Returns:
        LangChainClient: A client with `classify` and `write`.

    Raises:
        ValueError: If the provider is unknown.
        ImportError: If the package of the provider is not installed.
    """
    provider = provider.lower().strip()
    provider = ALIASES.get(provider, provider)
    if provider not in PROVIDERS:
        raise ValueError(
            f"unknown provider {provider!r}; known: {', '.join(PROVIDERS)} "
            "(a plugin can add one with register_provider)"
        )
    entry = PROVIDERS[provider]
    try:
        chat = entry.make(model or entry.default, temperature, options)
    except ImportError as exc:
        hint = (
            f"run `uv sync --extra {entry.extra}` (or pip install 'fsm-llm-venser[{entry.extra}]')"
        )
        raise ImportError(
            f"the package of provider {provider!r} is missing ({exc.name}): "
            + (hint if entry.extra else "install it")
        ) from None
    return LangChainClient(chat)


def format_models() -> str:
    """List the known models, with the untested ones flagged.

    Returns:
        str: A multi-line text, one block per provider.
    """
    lines = []
    for provider in PROVIDERS.values():
        lines.append(f"{provider.name}:")
        lines += [f"  {name:<28} {flag}" for name, flag in provider.models.items()]
    return "\n".join(lines)
