#!/usr/bin/env python3
"""
algebra.py — the value rules every executor in the forge shares.

The forge used to stop at the tree. Four lexers and five parsers argued
about what a program *says*, and nothing ran what they built. The four
executors in executors.py close that gap: each lane now carries a
program from source text all the way to a value, so the lanes can also
argue about what a program *means*.

For four executors to argue usefully they have to share a contract, in
exactly the way contract.py makes four lexers comparable. This module
is that contract, and it fixes three things and nothing else:

  1. what one operation does to two values   binop, truth, span
  2. what a call is worth                     applied, builtins, Trust
  3. how an outcome is written down           canon

What it leaves out is the point. It says nothing about *when* anything
is evaluated: the order of operands, which branch runs, where Z stops
the work, how deep a call may go, how arguments are collected. That is
evaluation strategy, it is where an executor's bugs live, and each
executor in executors.py encodes it its own way -- a recursive walk,
compiled closures, an explicit stack, a bytecode machine. Sharing the
strategy would make four executors one executor four times, and
agreement between copies is evidence about copying (README.md, "Why
four of each").

The rules are the ones syntax.eval_ast applies, which is what ezrun
runs, so a program the incumbent accepts gets the same answer here.
Three departures, each because a lane has no stage 3 in front of it to
refuse the input first, and evaluation is total (SEMANTICS.md G1):

  - arithmetic on a bool or a list, comparing text with a bool, and a
    text condition are Z(misbound) here. Semantic refuses all of them
    before eval_ast could see them; eval_ast itself would let Python
    answer (true + 1 = 2).
  - a float that overflows, or a literal too large to hold, is
    Z(unbounded). eval_ast raises OverflowError there (FINDINGS: V3).
  - `show` appends to a log instead of printing, so four lanes running
    one program print nothing four times, and what each lane showed is
    itself part of the answer the merge compares.

Codric Enterprise · Ricky (Dreid) · 2026
"""

from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

HERE = os.path.dirname(os.path.abspath(__file__))
INTERP = os.path.abspath(os.path.join(HERE, "..", "2-interpreter-python"))
if INTERP not in sys.path:
    sys.path.insert(0, INTERP)

from ever import (  # noqa: E402
    E_CERTAIN,
    E_INTAKE,
    E_PI_WIDTH_WARN,
    Defect,
    E,
    State,
    e_equiv,
    e_val,
    e_z,
)

#: The ceiling an anchored function with a proven measure may recurse to
#: (syntax.E_HARD_DEPTH). Everything else keeps the run's own limit.
E_HARD_DEPTH = 4000

#: ezrun's default recursion ceiling, and so the lanes' default too.
DEFAULT_DEPTH = 100

#: CORE.md's four builtins. A user definition of the same name wins.
BUILTINS = ("show", "len", "head", "tail")

ARITH = ("+", "-", "*", "/")
ORDER = ("<", ">", "<=", ">=")
EQUALITY = ("==", "!=")


# ═════════════════════════════════════════════
# Configuration a run carries
# ═════════════════════════════════════════════


@dataclass
class Trust:
    """How far each definition is believed, as syntax.Trust has it.

    A name absent from `confidence` sits at E_INTAKE: a definition is a
    claim until evidence says otherwise.
    """

    confidence: Dict[str, int] = field(default_factory=dict)
    anchored: Set[str] = field(default_factory=set)

    def of(self, name: str) -> int:
        return int(self.confidence.get(name, E_INTAKE))

    def ceiling(self, name: str, limit: int) -> int:
        return E_HARD_DEPTH if name in self.anchored else limit


@dataclass
class Config:
    depth: int = DEFAULT_DEPTH
    trust: Trust = field(default_factory=Trust)


# ═════════════════════════════════════════════
# Values
# ═════════════════════════════════════════════


def kind(v: Any) -> str:
    """The language's name for a host value. bool first: Python counts
    True as an int, and Ever does not."""
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "num"
    if isinstance(v, str):
        return "text"
    if isinstance(v, list):
        return "list"
    return "foreign"


def _tidy(v: Any) -> Optional[Any]:
    """An integral float becomes an int; a non-finite one becomes None,
    which the caller turns into Z(unbounded)."""
    if isinstance(v, float):
        if not math.isfinite(v):
            return None
        if v == int(v):
            return int(v)
    return v


def literal(value: float) -> E:
    v = _tidy(value)
    if v is None:
        return e_z("lit", "literal too large to hold", Defect.UNBOUNDED)
    return e_val("lit", v, E_CERTAIN)


#: How many times this process applied the first departure: arithmetic
#: or ordering refused because an operand was a bool or a list. Semantic
#: lets some of those through to eval_ast, which then answers with
#: Python's meaning ([] * 3 is [], true < false is false). The lanes
#: refuse them. The oracle in tests/lanes_test.py reads this to tell a
#: known departure from a real disagreement.
DEPARTURES = [0]


def _misbound(op: str, a: Any, b: Any) -> E:
    if {kind(a), kind(b)} & {"bool", "list"}:
        DEPARTURES[0] += 1
    return e_z("op", f"cannot {op} {kind(a)} with {kind(b)}", Defect.MISBOUND)


def _arith(op: str, x: Any, y: Any) -> Any:
    if op == "+":
        return x + y
    if op == "-":
        return x - y
    if op == "*":
        return x * y
    return x / y


def _order(op: str, x: Any, y: Any) -> bool:
    if op == "<":
        return bool(x < y)
    if op == ">":
        return bool(x > y)
    if op == "<=":
        return bool(x <= y)
    return bool(x >= y)


def binop(op: str, a: E, b: E) -> E:
    """Two known values, one operator. The caller has already stopped on
    a Z operand: Z absorbs, and *when* it stops is strategy."""
    x, y = a.value, b.value
    kx, ky = kind(x), kind(y)
    conf = min(a.confidence, b.confidence)
    if op in ARITH:
        if op == "+" and kx == ky == "text":
            return e_val("op", x + y, conf)
        if not kx == ky == "num":
            return _misbound(op, x, y)
        if op == "/" and y == 0:
            return e_z("div", "division by zero", Defect.MISBOUND)
        try:
            v = _tidy(_arith(op, x, y))
        except OverflowError:
            v = None
        if v is None:
            return e_z("op", f"{op} overflowed", Defect.UNBOUNDED)
        return e_val("op", v, conf)
    if op in ORDER:
        if kx != ky or kx not in ("num", "text"):
            return _misbound(op, x, y)
        return e_val("op", _order(op, x, y), conf)
    if op in EQUALITY:
        if {kx, ky} == {"text", "bool"}:
            return _misbound(op, x, y)
        same = x == y
        return e_val("op", same if op == "==" else not same, conf)
    return e_z("op", f"unknown operator {op}", Defect.MISBOUND)


def truth(cond: E) -> Optional[E]:
    """None when the condition can steer a branch; a Z when it cannot.
    Only call this on a known condition."""
    if kind(cond.value) == "text":
        return e_z("if", "condition is text, not a truth value", Defect.MISBOUND)
    return None


def taken(cond: E, result: E) -> E:
    """A known condition picked one arm and that arm answered."""
    if result.is_z:
        return result
    return e_val("if", result.value, min(cond.confidence, result.confidence))


def span(a: E, b: E) -> E:
    """An unknown condition: both arms ran, and the answer covers both.
    SEMANTICS.md 6: equal arms agree, numeric arms span up to pi*26,
    anything else is not spannable."""
    if a.is_z or b.is_z:
        return e_z("if", "condition unknown and a branch is Z", Defect.UNBOUND)
    conf = min(a.confidence, b.confidence)
    if a.value == b.value:
        return e_val("if", a.value, conf)
    if not isinstance(a.value, (int, float)) or not isinstance(b.value, (int, float)):
        return e_z("if", "branches are not spannable", Defect.UNBOUNDED)
    lo, hi = sorted([int(a.value), int(b.value)])
    if hi - lo > E_PI_WIDTH_WARN:
        return e_z(
            "if", f"branches span {hi - lo}, past the pi threshold", Defect.UNBOUNDED
        )
    r = e_equiv("if", lo, hi)
    r.confidence = conf
    return r


def gathered(values: List[Any], confs: List[int]) -> E:
    """A list literal whose every element was known. The chain rule: no
    more trusted than its least-trusted element; empty is Certain."""
    return e_val("list", values, min([E_CERTAIN] + confs))


def applied(name: str, result: E, args: List[E], trust: Trust) -> E:
    """[APP]: min(c_f, c_result, c_args)."""
    if result.is_z:
        return result
    return e_val(
        name,
        result.value,
        min([trust.of(name), result.confidence] + [a.confidence for a in args]),
    )


def too_deep(name: str, ceiling: int) -> E:
    return e_z(name, f"depth ceiling {ceiling} exceeded", Defect.UNBOUNDED)


def arity(name: str, want: int, got: int) -> E:
    return e_z(name, f"expected {want} argument(s), got {got}", Defect.MISBOUND)


def unbound(name: str) -> E:
    return e_z(name, f"{name} was never bound", Defect.UNBOUND)


def first_z(args: List[E]) -> Optional[E]:
    for a in args:
        if a.is_z:
            return a
    return None


def builtin(name: str, args: List[E], shown: List[E]) -> E:
    """A call to a name with no user definition. Arguments are already
    evaluated and none is Z. Unknown names are unbound."""
    if name not in BUILTINS:
        return e_z(name, f"{name} was never defined", Defect.UNBOUND)
    if len(args) != 1:
        return e_z(name, f"{name} takes 1 argument", Defect.MISBOUND)
    a = args[0]
    if name == "show":
        shown.append(a)
        return a
    xs = a.value
    if not isinstance(xs, list):
        return e_z(name, f"{name} needs a list, got {kind(xs)}", Defect.MISBOUND)
    if name == "len":
        return e_val(name, len(xs), a.confidence)
    if not xs:
        return e_z(name, f"{name} of an empty list", Defect.UNBOUND)
    if name == "head":
        return e_val(name, xs[0], a.confidence)
    return e_val(name, xs[1:], a.confidence)


# ═════════════════════════════════════════════
# Outcomes, and how they are written down
# ═════════════════════════════════════════════


@dataclass
class Outcome:
    """What running one program produced: a value, or nothing to run.

    `value` is None exactly when the program only defines functions and
    names no entry point; `defined` then says what it defines.
    """

    value: Optional[E] = None
    shown: List[E] = field(default_factory=list)
    defined: List[str] = field(default_factory=list)

    def canon(self) -> str:
        if self.value is None:
            head = f"NOENTRY({' '.join(sorted(self.defined))})"
        else:
            head = canon(self.value)
        if self.shown:
            head += " | show " + " ".join(canon(s) for s in self.shown)
        return head


def canon_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        return repr(v)
    if isinstance(v, list):
        return "[" + ", ".join(canon_value(x) for x in v) + "]"
    return f"<{type(v).__name__}>"


def canon(e: E) -> str:
    """A value's canonical form: state, value and confidence, or Z and
    its defect. Like contract.Fail, never the prose reason -- two
    executors that both refuse 1/0 agree even if they word it apart."""
    if e.is_z:
        return f"Z[{Defect(e.defect).name.lower()}]"
    if e.state == State.EQUIV:
        return f"EQUIV:{e.lo}..{e.hi}@{e.confidence}"
    return f"{State(e.state).name}:{canon_value(e.value)}@{e.confidence}"


def show_line(e: E) -> str:
    """How ezrun prints a value, so the merge can print the same."""
    if e.is_z:
        return f"Z ({Defect(e.defect).name.lower()}: {e.reason})"
    if e.state == State.EQUIV:
        return f"{e.lo}..{e.hi}  @ {e.confidence}/{E_CERTAIN}"
    v = e.value
    text = canon_value(v) if isinstance(v, (bool, list)) else str(v)
    return f"{text}  @ {e.confidence}/{E_CERTAIN}"


__all__ = [
    "E_HARD_DEPTH",
    "DEFAULT_DEPTH",
    "BUILTINS",
    "Trust",
    "Config",
    "Outcome",
    "kind",
    "literal",
    "binop",
    "truth",
    "taken",
    "span",
    "gathered",
    "applied",
    "too_deep",
    "arity",
    "unbound",
    "first_z",
    "builtin",
    "canon",
    "canon_value",
    "show_line",
]
