"""Ready-made guards that every machine file can use, without writing a plugin.

They are always available. A guard with the same name in your own plugin replaces them.

    transitions:
      BRIEF:
        - to: OPENING
          guard: {when: "BRIEF < 2"}         # the first two times
        - to: SKIPPED                         # the last alternative has no guard
      ANSWER:
        - to: URGENT
          guard: {reply_matches: "\\bpain\\b"}
        - to: NEXT
"""

from __future__ import annotations

import ast
import operator
import re

from fsm_llm_venser.engine.context import Context
from fsm_llm_venser.plugins.base import Plugin

plugin = Plugin("builtin")

_COMPARE = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}


def _evaluate(node: ast.AST, names: dict[str, int]):
    """Evaluate a parsed expression. Only comparisons, and/or/not, names and integers pass.

    Args:
        node (ast.AST): The parsed expression node.
        names (dict[str, int]): Value of each name allowed in the expression.
    """
    if isinstance(node, ast.BoolOp):
        values = (_evaluate(v, names) for v in node.values)
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _evaluate(node.operand, names)
    if isinstance(node, ast.Compare):
        left = _evaluate(node.left, names)
        for op, right_node in zip(node.ops, node.comparators, strict=True):
            if type(op) not in _COMPARE:
                raise ValueError("operator not allowed in a `when` expression")
            right = _evaluate(right_node, names)
            if not _COMPARE[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Name):
        if node.id not in names:
            raise ValueError(f"unknown name {node.id!r} (use an event, retries or turns)")
        return names[node.id]
    if isinstance(node, ast.Constant) and type(node.value) is int:
        return node.value
    raise ValueError("a `when` expression is made of comparisons, and/or/not, names and integers")


@plugin.guard
def when(ctx: Context, expression: str) -> bool:
    """True if a counter expression holds, e.g. `ANSWER == 0 and retries < 2`.

    Args:
        ctx (Context): The context of the turn.
        expression (str): Comparisons joined by and/or/not. Names are the events (how many times each
            happened before this turn), `retries` (retries in the current state) and `turns`.

    Returns:
        bool: The value of the expression.

    Raises:
        ValueError: If the expression is invalid or uses an unknown name.
    """
    names = {**ctx.counts, "retries": ctx.retries, "turns": ctx.turns}
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"invalid `when` expression {expression!r}: {exc.msg}") from None
    return bool(_evaluate(tree.body, names))


@plugin.guard
def reply_matches(ctx: Context, pattern: str) -> bool:
    """True if the last reply of the person matches a regular expression (ignoring case).

    Args:
        ctx (Context): The context of the turn.
        pattern (str): A regular expression.

    Returns:
        bool: True if it is found in the reply.
    """
    return re.search(pattern, ctx.last_reply, re.IGNORECASE) is not None
