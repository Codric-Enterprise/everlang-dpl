#!/usr/bin/env python3
"""
confidence_properties.py — the two confidence laws, checked by Hypothesis
against every operator in the Python reference implementation.

  POISON   an unknown (Z) input makes the result unknown. It is never
           skipped, never read as 0, never averaged away.
  CEILING  a known result is trusted no more than its weakest input
           (SEMANTICS.md 2.1, the chain rule: conf = min over inputs).

Hypothesis generates the inputs: values of every type the core computes
with, confidences across the whole 0..256 range including the edges
(1, 127, 128, 255, 256), and Z threads carrying every defect. Runs are
derandomized so the gate is reproducible; set EVER_PROPS_EXAMPLES to
search harder.

Operators covered

  ever.combine                + - * /
  syntax.eval_ast             + - * /  < > <= >= == !=   list literal
                              let-in   if (known condition)   user call
                              len head tail
  abstract.chain, branch      the chain rule and the conditional
  vowels.i_interject          observation must not move confidence
  vowels.i_inject             a closure capped by what was injected
  vowels.i_integrate          disagreement (agreement is exempt below)

Deliberately exempt: a_ascend, excel, and i_integrate when its inputs
agree. They are the corroboration operators -- the one path upward
(SEMANTICS.md 2.2) -- and raising confidence on agreeing evidence is
their job, not a breach of the chain rule.

Known violations

Where an operator breaks a law, the law is not weakened. The violation is
pinned instead: Hypothesis must still find a counterexample, the
counterexample is printed on every run, and the suite FAILS the day it
stops finding one, so a fix cannot go unrecorded. Currently:

  V1  if/branch with a Z condition returns a known value (POISON) whose
      confidence ignores the condition entirely (CEILING). This is the
      documented "a Z condition spans both branches" rule, which the two
      laws as stated do not allow. One of them has to give.
  V2  i_integrate on two values that disagree returns e_equiv over their
      *confidences*: the result's value is the midpoint confidence (the
      two real values are dropped) and its confidence is above the weaker
      input. Its docstring says disagreement is not averaged into a
      comfortable middle; it is.
  V3  eval_ast crashes with OverflowError (or ValueError on nan) when
      float arithmetic leaves the finite range, e.g. 1.0 / 5e-324 or
      1e300 * 1e300, because it calls int() on the result to tidy whole
      numbers. The program gets a Python traceback where it was owed a Z.
      The ordinary properties skip only those overflowing inputs, only
      where arithmetic is involved.

Codric Enterprise
"""

from __future__ import annotations

import math
import operator
import os
import sys
from pathlib import Path
from typing import Callable, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "2-interpreter-python"))

try:
    from hypothesis import HealthCheck, given, settings
    from hypothesis import strategies as st
    from hypothesis.errors import NoSuchExample
    from hypothesis import assume, find
except ImportError:
    print("SKIP: hypothesis is not installed (python3 -m pip install hypothesis)")
    sys.exit(0)

import abstract  # noqa: E402
import syntax  # noqa: E402
import vowels  # noqa: E402
from ever import E, Defect, E_CERTAIN, combine, e_val, e_z  # noqa: E402

EXAMPLES = int(os.environ.get("EVER_PROPS_EXAMPLES", "300"))
SETTINGS = settings(max_examples=EXAMPLES, derandomize=True, deadline=None,
                    suppress_health_check=list(HealthCheck), database=None)

passed = failed = 0


def check(name: str, fn: Callable[[], None]) -> None:
    global passed, failed
    try:
        fn()
        passed += 1
        print(f"  ✓ {name}")
    except AssertionError as exc:
        failed += 1
        print(f"  ✗ {name}\n      {str(exc).strip().splitlines()[0][:300]}")
    except Exception as exc:  # an operator that crashes is a failure too
        failed += 1
        print(f"  ✗ {name}: crashed with {type(exc).__name__}: {exc}"[:400])


# ── strategies ────────────────────────────────────────────────────────

EDGES = st.sampled_from([1, 2, 119, 120, 127, 128, 129, 254, 255, 256])
CONF = st.one_of(EDGES, st.integers(min_value=1, max_value=E_CERTAIN))
INTS = st.integers(min_value=-10_000, max_value=10_000)
NUMS = st.one_of(INTS, st.floats(allow_nan=False, allow_infinity=False))
SCALARS = st.one_of(NUMS, st.booleans(), st.text(max_size=8))
DEFECTS = st.sampled_from([d for d in Defect if d != Defect.NONE])


@st.composite
def known(draw, values=SCALARS) -> E:
    return e_val("x", draw(values), draw(CONF))


@st.composite
def unknown(draw) -> E:
    return e_z("x", "generated unknown", draw(DEFECTS))


def thread(values=SCALARS):
    """A known value or a Z, weighted so both are common."""
    return st.one_of(known(values), unknown())


def weakest(inputs: List[E]) -> int:
    return min(i.confidence for i in inputs)


def laws(op: str, inputs: List[E], out: E) -> None:
    """Assert POISON and CEILING for one application of an operator."""
    if any(i.is_z for i in inputs):
        assert out.is_z, (f"POISON: {op} returned {out!r} although an input was Z: "
                          f"{inputs!r}")
        return
    if not out.is_z:
        assert out.confidence <= weakest(inputs), (
            f"CEILING: {op} returned confidence {out.confidence} above the weakest "
            f"input {weakest(inputs)}: {inputs!r} -> {out!r}")
        assert out.lo <= weakest(inputs) or out.state.name == "EQUIV", (
            f"CEILING: {op} lo={out.lo} above the weakest input {weakest(inputs)}")


def ev(src: str, env: dict, fns: Optional[dict] = None) -> E:
    node, err = syntax.parse(src)
    assert err is None, f"test program did not parse: {src!r}: {err!r}"
    return syntax.eval_ast(node, env, fns or {})


# ── 1. ever.combine ───────────────────────────────────────────────────

def combine_laws(op: str) -> Callable[[], None]:
    @SETTINGS
    @given(a=thread(NUMS), b=thread(NUMS))
    def prop(a: E, b: E) -> None:
        laws(f"combine({op})", [a, b], combine(a, b, op, "r"))
    return prop


# ── 2. syntax.eval_ast, the evaluator ezrun runs ──────────────────────

ARITH = {"+": operator.add, "-": operator.sub, "*": operator.mul, "/": operator.truediv}


def overflows(op: str, a: E, b: E) -> bool:
    """True when float arithmetic on these two would leave the finite
    range. eval_ast crashes on that (V3, pinned below); the laws are
    checked everywhere else."""
    if op not in ARITH or a.is_z or b.is_z:
        return False
    x, y = a.value, b.value
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (x, y)):
        return False
    if op == "/" and y == 0:
        return False
    try:
        r = ARITH[op](x, y)
    except (OverflowError, ZeroDivisionError):
        return True
    return isinstance(r, float) and not math.isfinite(r)


def binop_laws(op: str) -> Callable[[], None]:
    @SETTINGS
    @given(a=thread(), b=thread())
    def prop(a: E, b: E) -> None:
        assume(not overflows(op, a, b))
        laws(f"eval_ast x {op} y", [a, b], ev(f"x {op} y", {"x": a, "y": b}))
    return prop


@SETTINGS
@given(a=thread(), b=thread(), c=thread())
def list_laws(a: E, b: E, c: E) -> None:
    laws("eval_ast [x, y, z]", [a, b, c], ev("[x, y, z]", {"x": a, "y": b, "z": c}))


@SETTINGS
@given(a=thread(NUMS), b=thread(NUMS))
def let_laws(a: E, b: E) -> None:
    assume(not overflows("+", a, b))
    laws("eval_ast let v = x in v + y", [a, b],
         ev("let v = x in v + y", {"x": a, "y": b}))


@SETTINGS
@given(c=known(st.booleans()), a=thread(), b=thread())
def if_known_laws(c: E, a: E, b: E) -> None:
    # Under a known condition only the taken branch is an input: the
    # other is never evaluated (abstract.branch explains why it must not
    # be, or every recursive function diverges).
    taken = a if c.value else b
    laws("eval_ast if c then x else y (c known)", [c, taken],
         ev("if c then x else y", {"c": c, "x": a, "y": b}))


@SETTINGS
@given(a=thread(NUMS), b=thread(NUMS), trust=CONF)
def call_laws(a: E, b: E, trust: int) -> None:
    if not (a.is_z or b.is_z):
        prod = e_val("pq", a.value * b.value, 1)
        assume(not overflows("*", a, b) and not overflows("+", prod, a))
    node, err = syntax.parse("def f(p, q) = p * q + p")
    assert err is None
    fns = {d.name: d for d in node.statements} if isinstance(node, syntax.Program) \
        else {node.name: node}
    t = syntax.Trust(confidence={"f": trust})
    out = syntax.eval_ast(syntax.parse("f(x, y)")[0], {"x": a, "y": b}, fns, trust=t)
    # [APP]: the function's own standing is an input too.
    fn_thread = e_val("f", "fn", trust)
    laws("eval_ast user call f(x, y)", [a, b, fn_thread], out)


def builtin_laws(name: str) -> Callable[[], None]:
    @SETTINGS
    @given(xs=thread(st.lists(INTS, min_size=1, max_size=6)))
    def prop(xs: E) -> None:
        laws(f"eval_ast {name}(xs)", [xs], ev(f"{name}(xs)", {"xs": xs}))
    return prop


# ── 3. abstract.py ────────────────────────────────────────────────────

@SETTINGS
@given(parts=st.lists(thread(), min_size=1, max_size=6))
def chain_laws(parts: List[E]) -> None:
    c = abstract.chain(parts)
    out = e_val("chain", 0, c)  # e_val collapses 0 to Z, as the runtime does
    laws("abstract.chain", parts, out)


@SETTINGS
@given(c=known(st.booleans()), a=thread(), b=thread())
def branch_known_laws(c: E, a: E, b: E) -> None:
    taken = a if c.value else b
    laws("abstract.branch (c known)", [c, taken],
         abstract.branch(c, lambda: a, lambda: b, "r"))


# ── 4. vowels.py ──────────────────────────────────────────────────────

@SETTINGS
@given(p=thread())
def interject_laws(p: E) -> None:
    out = vowels.i_interject(p, lambda _seen: None, "seen")
    laws("vowels.i_interject", [p], out)
    if not p.is_z:
        assert out.confidence == p.confidence and out.value == p.value, (
            f"i_interject moved {p!r} to {out!r}; observation must be invisible")


@SETTINGS
@given(fn_conf=CONF, bindings=st.lists(thread(), min_size=1, max_size=3))
def inject_laws(fn_conf: int, bindings: List[E]) -> None:
    f = abstract.e_fn("f", ["n"], "n * 2")
    f.confidence = fn_conf
    named = {f"b{i}": b for i, b in enumerate(bindings)}
    laws("vowels.i_inject", [f] + bindings, vowels.i_inject(f, named, "g"))


@st.composite
def disagreeing(draw):
    a = draw(known(INTS))
    b = draw(known(INTS.filter(lambda v: v != a.value)))
    return a, b


@SETTINGS
@given(a=unknown(), b=thread())
def integrate_poison_laws(a: E, b: E) -> None:
    laws("vowels.i_integrate (a Z input)", [a, b], vowels.i_integrate(a, b, "m"))


# ── known violations: pinned, printed, never weakened ─────────────────

def still_violates(label: str, strategy, op: Callable, inputs_of: Callable) -> Callable[[], None]:
    def violated(x) -> bool:
        try:
            laws(label, inputs_of(x), op(x))
            return False
        except AssertionError:
            return True
        except Exception:  # a crash where a Z was owed
            return True

    def prop() -> None:
        try:
            x = find(strategy, violated, settings=settings(
                max_examples=max(EXAMPLES, 500), derandomize=True, database=None,
                suppress_health_check=list(HealthCheck)))
        except NoSuchExample:
            raise AssertionError(f"{label} no longer violates the laws -- it has been "
                                 f"fixed: move it to the passing properties and out "
                                 f"of the known-violation list")
        try:
            laws(label, inputs_of(x), op(x))
        except AssertionError as exc:
            print(f"      still violated, e.g. {str(exc).splitlines()[0][:220]}")
        except Exception as exc:
            print(f"      still violated, e.g. {label} on {x!r} raised "
                  f"{type(exc).__name__}: {exc}"[:260])
    return prop


V1 = still_violates(
    "eval_ast if c then x else y (c is Z)",
    st.tuples(unknown(), known(INTS), known(INTS)),
    lambda t: ev("if c then x else y", {"c": t[0], "x": t[1], "y": t[2]}),
    lambda t: [t[0], t[1], t[2]])

V1b = still_violates(
    "abstract.branch (c is Z)",
    st.tuples(unknown(), known(INTS), known(INTS)),
    lambda t: abstract.branch(t[0], lambda: t[1], lambda: t[2], "r"),
    lambda t: [t[0], t[1], t[2]])

V2 = still_violates(
    "vowels.i_integrate (inputs disagree)",
    disagreeing(),
    lambda ab: vowels.i_integrate(ab[0], ab[1], "m"),
    lambda ab: [ab[0], ab[1]])


@st.composite
def overflowing(draw):
    op = draw(st.sampled_from(sorted(ARITH)))
    big = st.floats(min_value=1e300, max_value=1.7e308)
    tiny = st.floats(min_value=5e-324, max_value=1e-300)
    if op == "/":
        a, b = draw(big), draw(tiny)
    elif op == "*":
        a, b = draw(big), draw(big)
    elif op == "+":
        a, b = draw(big), draw(big)
    else:
        a, b = draw(big), -draw(big)
    return op, e_val("x", a, draw(CONF)), e_val("y", b, draw(CONF))


V3 = still_violates(
    "eval_ast arithmetic past the float range",
    overflowing(),
    lambda t: ev(f"x {t[0]} y", {"x": t[1], "y": t[2]}),
    lambda t: [t[1], t[2]])


def main() -> int:
    print(f"  Hypothesis, {EXAMPLES} examples per property, derandomized\n")
    print("  POISON and CEILING hold:")
    for op in "+-*/":
        check(f"ever.combine {op}", combine_laws(op))
    for op in ["+", "-", "*", "/", "<", ">", "<=", ">=", "==", "!="]:
        check(f"syntax.eval_ast x {op} y", binop_laws(op))
    check("syntax.eval_ast [x, y, z]", list_laws)
    check("syntax.eval_ast let-in", let_laws)
    check("syntax.eval_ast if, known condition", if_known_laws)
    check("syntax.eval_ast user function call", call_laws)
    for name in ("len", "head", "tail"):
        check(f"syntax.eval_ast {name}", builtin_laws(name))
    check("abstract.chain", chain_laws)
    check("abstract.branch, known condition", branch_known_laws)
    check("vowels.i_interject", interject_laws)
    check("vowels.i_inject", inject_laws)
    check("vowels.i_integrate, a Z input", integrate_poison_laws)

    print("\n  Known violations (must still reproduce; see the docstring):")
    check("V1  eval_ast if with a Z condition", V1)
    check("V1  abstract.branch with a Z condition", V1b)
    check("V2  vowels.i_integrate on disagreeing inputs", V2)
    check("V3  eval_ast arithmetic that overflows a float", V3)

    print(f"\n=== Confidence properties: {passed} passed, {failed} failed ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
