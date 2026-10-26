"""Plugins: the Python side of a machine.

The YAML file only names guards and actions; their code lives in a plugin file passed with
--plugin (or to Runner). A machine file never imports or runs code.

Three kinds of function can be registered:
  guard     (ctx, *args) -> bool       read-only; decides if an alternative of a transition applies
  action    (ctx, *args) -> None       runs in do / entry / exit / on_any_transition; the only
                                       kind that may change ctx.vars
  listener  (ctx, hook) -> None        read-only observer of a hook point (see HOOKS)
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

# points of a run where a listener can be called
HOOKS = ("start", "enter", "exit", "classified", "transition", "utterance", "final", "end")


@dataclass(frozen=True)
class Listener:
    """A function subscribed to a hook, optionally only for some states or events."""

    hook: str
    fn: Callable[..., Any]
    state: str | None = None  # qualified name or glob, e.g. "EMOTIONS.*"
    event: str | None = None

    def matches(self, hook: str, state: str | None = None, event: str | None = None) -> bool:
        """Tell if the listener wants to be called for this hook, state and event.

        Args:
            hook (str): The hook that is firing.
            state (str | None): The state it fires for.
            event (str | None): The event of the turn, if any.

        Returns:
            bool: True if hook, state and event all match.
        """
        if hook != self.hook:
            return False
        if self.state is not None and (state is None or not fnmatchcase(state, self.state)):
            return False
        return self.event is None or event == self.event


class Plugin:
    """A registry of guards, actions and listeners.

    Args:
        name: A label, used in messages.
        vars_model: Optional Pydantic model for ctx.vars (the variables kept between turns).
        analysis_model: Optional subclass of Analysis with the extra fields the classifier
            must fill (for example a note about the reply).
    """

    def __init__(
        self, name: str, vars_model: type | None = None, analysis_model: type | None = None
    ) -> None:
        """Create the object."""
        self.name = name
        self.vars_model = vars_model
        self.analysis_model = analysis_model
        self.guards: dict[str, Callable[..., bool]] = {}
        self.actions: dict[str, Callable[..., None]] = {}
        self.listeners: list[Listener] = []

    @staticmethod
    def _register(registry: dict[str, Callable], fn: Callable | None, name: str | None):
        """Decorator helper: add a function to a registry under its own name or `name`.

        Args:
            registry (dict[str, Callable]): Dictionary where the function is stored.
            fn (Callable | None): The function (when used without parentheses).
            name (str | None): Name to register (default: the name of the function).
        """

        def decorator(func: Callable) -> Callable:
            """Register the function."""
            key = name or func.__name__
            if key in registry:
                raise ValueError(f"{key!r} is already registered")
            registry[key] = func
            return func

        return decorator(fn) if fn is not None else decorator

    def guard(self, fn: Callable | None = None, *, name: str | None = None):
        """Register a guard. Use as `@plugin.guard` or `@plugin.guard(name="other_name")`.

        Args:
            fn (Callable | None): The function (when used without parentheses).
            name (str | None): Name to register (default: the name of the function).
        """
        return self._register(self.guards, fn, name)

    def action(self, fn: Callable | None = None, *, name: str | None = None):
        """Register an action. Use as `@plugin.action` or `@plugin.action(name="other_name")`.

        Args:
            fn (Callable | None): The function (when used without parentheses).
            name (str | None): Name to register (default: the name of the function).
        """
        return self._register(self.actions, fn, name)

    def listener(self, hook: str, *, state: str | None = None, event: str | None = None):
        """Register a listener for a hook, optionally only for a state (glob) or an event.

        Raises:
            ValueError: If the hook does not exist.

        Args:
            hook (str): The hook point.
            state (str | None): Only for this state (qualified name or glob).
            event (str | None): Only for this event.
        """
        if hook not in HOOKS:
            raise ValueError(f"unknown hook {hook!r}; expected one of {HOOKS}")

        def decorator(func: Callable) -> Callable:
            """Register the function."""
            self.listeners.append(Listener(hook, func, state, event))
            return func

        return decorator


def load_plugin(path: str | Path) -> Plugin:
    """Import a plugin file and return the `plugin` object it defines.

    Args:
        path (str | Path): Path of the Python file.

    Returns:
        Plugin: The module-level `plugin` of the file.

    Raises:
        ImportError: If the file cannot be loaded or has no `plugin = Plugin(...)`.
    """
    path = Path(path)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load plugin from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    plugin = getattr(module, "plugin", None)
    if not isinstance(plugin, Plugin):
        raise ImportError(f"{path} must define a module-level `plugin = Plugin(...)`")
    return plugin
