#!/usr/bin/env python3
"""
lanes_test.py — four lanes, one language.

7-forge/lanes.py carries a program down four independent pipelines,
each with its own lexer, parser and executor, and 7-forge/merge.py
reconciles them. This suite checks:

  1. algebra     the shared value rules, one operation at a time
  2. executors   all four executors on hand-written programs, each
                 against a stated answer
  3. oracle      the four executors against syntax.eval_ast -- what
                 ezrun runs -- on every program the incumbent accepts
  4. fuzz        the four executors against each other on thousands of
                 generated programs, called with generated arguments
  5. corpus      the golden parser corpus and examples/ through the full
                 merge: unanimous at every stage, trees equal to EXPECTED
  6. faults      a deliberately broken lexer, parser and executor, one
                 at a time: the merge must keep the majority's answer,
                 lower its confidence, and name the broken part
  7. withholding two broken lanes: the merge must refuse to answer
  8. cli         merge.py's command line and exit codes

    python3 tests/lanes_test.py

Codric Enterprise
"""

from __future__ import annotations

import contextlib
import io
import sys
import warnings
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "7-forge"))
sys.path.insert(0, str(ROOT / "2-interpreter-python"))

with warnings.catch_warnings():
    warnings.simplefilter("ignore", SyntaxWarning)
    import algebra  # noqa: E402
    import executors  # noqa: E402
    import lanes  # noqa: E402
    import merge  # noqa: E402
    import syntax  # noqa: E402
    from algebra import Config, Trust, canon, show_line  # noqa: E402
    from contract import Tok  # noqa: E402
    from corpus import Fuzzer  # noqa: E402
    from ever import E_CERTAIN, Defect, State, e_val, e_z  # noqa: E402

passed = failed = 0


def ok(name: str, cond: bool) -> None:
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  ✗ {name}")


def section(title: str) -> None:
    print(f"\n{title}")


def tree_of(src: str):
    _lexed, po = lanes.front("L1-regex-master", "P1-recdescent", src)
    return po.ast if po is not None and po.ok else None


def outcomes(
    src: str, call: Optional[str] = None, cfg: Optional[Config] = None
) -> Dict[str, str]:
    prog = tree_of(src)
    entry = tree_of(call) if call else None
    return {x: executors.run(x, prog, entry, cfg).canon() for x in executors.EXECUTORS}


print("\n=== Lanes — four pipelines, one language ===")

# ═════════════════════════════════════════════════════════════════════
section("1. ALGEBRA")
# ═════════════════════════════════════════════════════════════════════

c = lambda v, conf=E_CERTAIN: e_val("t", v, conf)  # noqa: E731
ok("1 + 2 is 3", canon(algebra.binop("+", c(1), c(2))) == "CERTAIN:3@256")
ok("confidence chains by min", algebra.binop("*", c(2, 200), c(3, 90)).confidence == 90)
ok("7 / 2 is real", canon(algebra.binop("/", c(7), c(2))) == "CERTAIN:3.5@256")
ok(
    "8 / 2 is tidied to an int",
    canon(algebra.binop("/", c(8), c(2))) == "CERTAIN:4@256",
)
ok(
    "division by zero is misbound",
    algebra.binop("/", c(1), c(0)).defect == Defect.MISBOUND,
)
ok("text + text concatenates", algebra.binop("+", c("a"), c("b")).value == "ab")
ok("text + num is misbound", algebra.binop("+", c("a"), c(1)).defect == Defect.MISBOUND)
ok(
    "bool arithmetic is misbound",
    algebra.binop("+", c(True), c(1)).defect == Defect.MISBOUND,
)
ok(
    "list arithmetic is misbound",
    algebra.binop("+", c([1]), c([2])).defect == Defect.MISBOUND,
)
ok("ordering text with num is misbound", algebra.binop("<", c("a"), c(1)).is_z)
ok("text orders with text", algebra.binop("<", c("a"), c("b")).value is True)
ok("comparing text with bool is misbound", algebra.binop("==", c("a"), c(True)).is_z)
ok("equality of lists", algebra.binop("==", c([1, 2]), c([1, 2])).value is True)
ok("!= is the negation", algebra.binop("!=", c(1), c(2)).value is True)
ok(
    "float overflow is unbounded, not an exception",
    algebra.binop("*", c(1e308), c(10.0)).defect == Defect.UNBOUNDED,
)
ok(
    "a huge literal is unbounded",
    algebra.literal(float("9" * 400)).defect == Defect.UNBOUNDED,
)
ok("a text condition is refused", algebra.truth(c("yes")) is not None)
ok("a number steers a branch", algebra.truth(c(0)) is None)
ok(
    "taken: min of condition and arm",
    algebra.taken(c(True, 100), c(5, 200)).confidence == 100,
)
ok("taken: a Z arm stays Z", algebra.taken(c(True), e_z("x")).is_z)
ok("span: equal arms agree", canon(algebra.span(c(3), c(3, 90))) == "CONFIDENT:3@90")
ok("span: numeric arms span", canon(algebra.span(c(1), c(5))) == "EQUIV:1..5@256")
ok("span: past pi*26 collapses", algebra.span(c(0), c(82)).defect == Defect.UNBOUNDED)
ok(
    "span: text arms cannot span",
    algebra.span(c("a"), c("b")).defect == Defect.UNBOUNDED,
)
ok("span: a Z arm is unbound", algebra.span(e_z("x"), c(1)).defect == Defect.UNBOUND)
ok("an empty list is certain", canon(algebra.gathered([], [])) == "CERTAIN:[]@256")
ok(
    "a list is its weakest element",
    algebra.gathered([1, 2], [256, 40]).confidence == 40,
)
t = Trust({"f": 200})
ok(
    "[APP] floors by the function",
    algebra.applied("f", c(1), [c(2, 230)], t).confidence == 200,
)
ok("an unknown function enters at intake", Trust().of("g") == 120)
ok(
    "anchoring buys depth",
    Trust(anchored={"f"}).ceiling("f", 3) == algebra.E_HARD_DEPTH,
)
log: List = []
ok(
    "show returns its argument",
    algebra.builtin("show", [c(4)], log).value == 4 and len(log) == 1,
)
ok("len of a list", algebra.builtin("len", [c([1, 2, 3], 77)], []).value == 3)
ok(
    "len keeps the list's confidence",
    algebra.builtin("len", [c([1], 77)], []).confidence == 77,
)
ok(
    "head of empty is unbound",
    algebra.builtin("head", [c([])], []).defect == Defect.UNBOUND,
)
ok("tail drops the head", algebra.builtin("tail", [c([1, 2])], []).value == [2])
ok(
    "len of a number is misbound",
    algebra.builtin("len", [c(3)], []).defect == Defect.MISBOUND,
)
ok("a builtin takes one argument", algebra.builtin("len", [c([]), c([])], []).is_z)
ok(
    "an unknown name is unbound",
    algebra.builtin("nope", [], []).defect == Defect.UNBOUND,
)
ok(
    "canon never carries prose",
    canon(e_z("a", "one reason")) == canon(e_z("b", "another")),
)
ok("show_line matches ezrun", show_line(c(15, 120)) == "15  @ 120/256")

# ═════════════════════════════════════════════════════════════════════
section("2. EXECUTORS — each against a stated answer")
# ═════════════════════════════════════════════════════════════════════

FACT = "def fact(n) = if n <= 1 then 1 else n * fact(n - 1)"
DOWN = "def down(n) = if n <= 0 then 0 else down(n - 1)"
#: 1 / 1e-320. eval_ast raises OverflowError tidying the inf (FINDINGS V3).
OVERFLOW = "1 / 0." + "0" * 319 + "1"

STATED: List[Tuple[str, Optional[str], Optional[Config], str]] = [
    ("1 + 2 * 3", None, None, "CERTAIN:7@256"),
    ("(1 + 2) * 3", None, None, "CERTAIN:9@256"),
    ("10 - 4 - 3", None, None, "CERTAIN:3@256"),
    ("-3 + 2", None, None, "CERTAIN:-1@256"),
    ("7 / 2", None, None, "CERTAIN:3.5@256"),
    ("1 / 0", None, None, "Z[misbound]"),
    ("x", None, None, "Z[unbound]"),
    ("x + 1 / 0", None, None, "Z[unbound]"),
    ("1 / 0 + x", None, None, "Z[misbound]"),
    ("2 <= 2", None, None, "CERTAIN:true@256"),
    ('"a" + "b"', None, None, "CERTAIN:'ab'@256"),
    ("true + 1", None, None, "Z[misbound]"),
    ("if 1 < 2 then 10 else 1 / 0", None, None, "CERTAIN:10@256"),
    ("if 1 > 2 then 1 / 0 else 20", None, None, "CERTAIN:20@256"),
    ("if missing then 1 else 2", None, None, "EQUIV:1..2@256"),
    ("if missing then 1 else 200", None, None, "Z[unbounded]"),
    ("if missing then 3 else 3", None, None, "CERTAIN:3@256"),
    ("if missing then 1 / 0 else 2", None, None, "Z[unbound]"),
    ('if "t" then 1 else 2', None, None, "Z[misbound]"),
    ("if 0 then 1 else 2", None, None, "CERTAIN:2@256"),
    ("[1, 2, 3]", None, None, "CERTAIN:[1, 2, 3]@256"),
    ("[]", None, None, "CERTAIN:[]@256"),
    ("[1, x, 1 / 0]", None, None, "Z[unbound]"),
    ("[[1], [2, 3]]", None, None, "CERTAIN:[[1], [2, 3]]@256"),
    ("let x = 5 in x * x", None, None, "CERTAIN:25@256"),
    ("let x = 1 / 0 in x", None, None, "Z[misbound]"),
    ("let a = 1 in let b = a + 1 in a * b", None, None, "CERTAIN:2@256"),
    ("head([])", None, None, "Z[unbound]"),
    ("len(3)", None, None, "Z[misbound]"),
    ("tail([1, 2, 3])", None, None, "CERTAIN:[2, 3]@256"),
    ("nope(1)", None, None, "Z[unbound]"),
    ("nope(1 / 0)", None, None, "Z[misbound]"),
    (FACT, "fact(5)", None, "CONFIDENT:120@120"),
    (FACT, "fact(3)", Config(depth=3), "CONFIDENT:6@120"),
    (FACT, "fact(6)", Config(depth=3), "Z[unbounded]"),
    (FACT, "fact(5)", Config(trust=Trust({"fact": 230})), "CONFIDENT:120@230"),
    ("def f(a) = a", "f(1, 2)", None, "Z[misbound]"),
    ("def f(a) = a", "f(1 / 0, x)", None, "Z[misbound]"),
    (DOWN, "down(500)", None, "Z[unbounded]"),
    (DOWN, "down(500)", Config(trust=Trust(anchored={"down"})), "CONFIDENT:0@120"),
    (DOWN, "down(3500)", Config(trust=Trust(anchored={"down"})), "CONFIDENT:0@120"),
    (OVERFLOW, None, None, "Z[unbounded]"),
    ("def len(x) = 99", "len([1])", None, "CONFIDENT:99@120"),
    (
        "def main() = show(1) + show(2)",
        None,
        None,
        "CONFIDENT:3@120 | show CERTAIN:1@256 CERTAIN:2@256",
    ),
    (
        "if missing then show(1) else show(2)",
        None,
        None,
        "EQUIV:1..2@256 | show CERTAIN:1@256 CERTAIN:2@256",
    ),
    ("def f(n) = n\ndef g(n) = n", None, None, "NOENTRY(f g)"),
    ("def f(n) = n\ndef f(n) = n + 1", "f(1)", None, "CONFIDENT:2@120"),
    ("def main(n) = n", None, None, "NOENTRY(main)"),
    ("40 + 2", "1 + 1", None, "CERTAIN:2@256"),
]

for src, call, cfg, stated in STATED:
    for x, answer in outcomes(src, call, cfg).items():
        ok(f"{x}: {src!r} {call or ''} -> {stated} (got {answer})", answer == stated)

# ═════════════════════════════════════════════════════════════════════
section("3. ORACLE — against syntax.eval_ast, what ezrun runs")
# ═════════════════════════════════════════════════════════════════════


def incumbent(
    src: str, call: Optional[str], depth: int
) -> Optional[Tuple[str, List[str]]]:
    """What ezrun's path computes, or None when the incumbent refuses the
    program before stage 4 (so there is nothing to compare)."""
    c = syntax.compile_ever(src)
    if not c.ok or c.ast is None:
        return None
    prog = c.ast
    stmts = prog.statements if isinstance(prog, syntax.Program) else [prog]
    if not stmts:
        return None
    fns = {s.name: s for s in stmts if isinstance(s, syntax.FnDef)}
    node = None
    if call is not None:
        cc = syntax.compile_ever(call, {k: len(v.params) for k, v in fns.items()})
        if not cc.ok:
            return None
        node = cc.ast
    elif not isinstance(stmts[-1], syntax.FnDef):
        node = stmts[-1]
    elif "main" in fns and not fns["main"].params:
        node = syntax.Call("main", [])
    if node is None:
        return None
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = syntax.eval_ast(node, {}, dict(fns), 0, depth, syntax.Trust())
    return canon(r), [ln for ln in buf.getvalue().splitlines() if ln.strip()]


def lanes_say(
    src: str, call: Optional[str], depth: int
) -> Optional[Tuple[str, List[str]]]:
    """None when the lanes refuse the program at the front end. Where
    syntax.py accepts a program the forge grammar does not, that split
    belongs to parser_agreement.py, which already lists it."""
    m = merge.merge(src, call, Config(depth=depth))
    if m.value is None:
        return None
    return canon(m.value), [show_line(s) for s in m.shown]


corpus_files = sorted((ROOT / "tests" / "parser_corpus").glob("*.ezr")) + sorted(
    (ROOT / "examples").glob("*.ezr")
)
oracle_cases: List[Tuple[str, Optional[str]]] = [
    (p.read_text(), None) for p in corpus_files
]
oracle_cases += [(s, c) for s, c, cfg, _ in STATED if cfg is None]
fz = Fuzzer(seed=7)
for _ in range(1500):
    oracle_cases.append((fz.expr(4, []), None))
for _ in range(1500):
    src = fz.program(fz.r.randint(1, 4))
    prog = tree_of(src)
    for d in prog.defs if prog is not None else []:
        args = ", ".join(str(fz.r.randint(-2, 6)) for _ in d.params)
        oracle_cases.append((src, f"{d.name}({args})"))

compared = skipped = crashed = departed = 0
for src, call in oracle_cases:
    try:
        want = incumbent(src, call, algebra.DEFAULT_DEPTH)
    except (OverflowError, ValueError):
        crashed += 1  # FINDINGS V3: eval_ast raises; the lanes answer Z
        said = lanes_say(src, call, algebra.DEFAULT_DEPTH)
        ok(
            f"incumbent crash is a Z in the lanes: {src!r}",
            said is not None and said[0] == "Z[unbounded]",
        )
        continue
    before = algebra.DEPARTURES[0]
    got = lanes_say(src, call, algebra.DEFAULT_DEPTH)
    if want is None or got is None:
        skipped += 1
        continue
    if got != want and algebra.DEPARTURES[0] > before:
        departed += 1  # a bool or list operand the lanes refuse on purpose
        continue
    compared += 1
    ok(f"oracle {src!r} {call or ''}: incumbent {want} lanes {got}", got == want)
print(
    f"  {compared} programs agree with eval_ast, {skipped} refused by a front end, "
    f"{departed} on a bool or list operand the lanes refuse, "
    f"{crashed} where the incumbent raises"
)
ok("the incumbent's overflow crash was exercised", crashed >= 1)
ok("the oracle compared a real number of programs", compared >= 800)

# ═════════════════════════════════════════════════════════════════════
section("4. FUZZ — the four executors against each other")
# ═════════════════════════════════════════════════════════════════════

fz = Fuzzer(seed=2026)
programs = 0
splits: List[str] = []
for i in range(2500):
    src = fz.program(fz.r.randint(1, 5))
    prog = tree_of(src)
    if prog is None:
        continue
    calls: List[Optional[str]] = [None]
    for d in prog.defs:
        args = ", ".join(str(fz.r.randint(-2, 6)) for _ in d.params)
        calls.append(f"{d.name}({args})")
    for call in calls:
        entry = tree_of(call) if call else None
        cfg = Config(depth=fz.r.choice([3, 10, 100]))
        answers = {
            x: executors.run(x, prog, entry, cfg).canon() for x in executors.EXECUTORS
        }
        programs += 1
        if len(set(answers.values())) != 1:
            splits.append(f"{src!r} {call}: {answers}")
for s in splits[:10]:
    print("   split:", s)
ok(f"no executor splits over {programs} runs ({len(splits)} found)", not splits)
ok("the fuzz ran a real number of programs", programs >= 2000)

# ═════════════════════════════════════════════════════════════════════
section("5. CORPUS — the golden parser corpus through the full merge")
# ═════════════════════════════════════════════════════════════════════

expected: Dict[str, str] = {}
for line in (ROOT / "tests" / "parser_corpus" / "EXPECTED").read_text().splitlines():
    if line and not line.startswith("#"):
        fname, _tab, fverdict = line.partition("\t")
        expected[fname] = fverdict

where_splits = 0
for p in corpus_files:
    rel = str(p.relative_to(ROOT))
    m = merge.merge(p.read_text())
    listed = expected.get(rel)
    ok(f"{rel}: listed in EXPECTED", listed is not None)
    ok(f"{rel}: tokens unanimous", m.tokens.unanimous)
    ok(f"{rel}: trees unanimous", m.tree.unanimous)
    ok(f"{rel}: results unanimous", m.result.unanimous)
    ok(f"{rel}: tree is EXPECTED ({m.tree.top} vs {listed})", m.tree.top == listed)
    ok(f"{rel}: the grammar's witness agrees ({m.witness})", m.witness_agrees)
    ok(f"{rel}: nothing withheld", not m.withheld)
    if m.where is not None and not m.where.unanimous:
        where_splits += 1
        ok(
            f"{rel}: a where-split is Earley's known early-EOF position",
            m.where.dissenters() == ["Lane 4"]
            and all(d.part == "P4-earley" for d in m.drift),
        )
    else:
        ok(f"{rel}: no drift on a clean run", not m.drift)
print(
    f"  {len(corpus_files)} files, {where_splits} with the known Earley position split"
)

# ═════════════════════════════════════════════════════════════════════
section("6. FAULTS — one broken part, named")
# ═════════════════════════════════════════════════════════════════════


@contextlib.contextmanager
def patched(table: Dict, key: str, replacement) -> Iterator[None]:
    old = table[key]
    table[key] = replacement
    try:
        yield
    finally:
        table[key] = old


class OffByOne(executors.Stack):
    """X3 with a bug: every sum comes out one high."""

    def k_bin_apply(self, op: str) -> None:
        super().k_bin_apply(op)
        top = self.vals[-1]
        if op == "+" and not top.is_z:
            self.vals[-1] = e_val("op", top.value + 1, top.confidence)


with patched(executors.EXECUTORS, "X3-stack", OffByOne):
    m = merge.merge("1 + 2")
ok("a broken executor is outvoted", canon(m.value).endswith(":3@192"))
ok(
    "3 of 4 scales Certain to 192",
    m.value.confidence == 192 and m.value.state == State.CONFIDENT,
)
ok(
    "the result vote is 3 of 4",
    m.result.agree == 3 and m.result.dissenters() == ["Lane 3"],
)
ok("tokens and trees still agree", m.tokens.unanimous and m.tree.unanimous)
ok(
    "the drift names X3-stack and only X3-stack",
    [(d.lane, d.stage, d.part) for d in m.drift]
    == [("Lane 3", "executor", "X3-stack")],
)
ok("the report says 3 of 4", "(3 of 4 lanes)" in merge.report(m))

with patched(executors.EXECUTORS, "X3-stack", OffByOne):
    m = merge.merge("def inc(n) = n + 1", "inc(1)")
ok("scaling applies to an earned confidence too: 120 -> 90", m.value.confidence == 90)


def swapped_minus(parse: Callable) -> Callable:
    """P2 with a bug: subtraction comes out back to front."""

    def run(toks: List[Tok]):
        out = parse(toks)
        if (
            out.ok
            and out.ast.expr is not None
            and getattr(out.ast.expr, "op", "") == "-"
        ):
            e = out.ast.expr
            e.left, e.right = e.right, e.left
        return out

    return run


table = lanes.parsers()
with patched(table, "P2-pratt", swapped_minus(table["P2-pratt"])):
    m = merge.merge("5 - 3")
ok("a broken parser is outvoted", canon(m.value) == "CONFIDENT:2@192")
ok("the tree vote names Lane 2", m.tree.dissenters() == ["Lane 2"])
ok(
    "the drift names P2-pratt and not its executor",
    [(d.lane, d.stage, d.part) for d in m.drift] == [("Lane 2", "parser", "P2-pratt")],
)


def misread_two(lex: Callable) -> Callable:
    """L3 with a bug: it reads every 2 as a 7."""

    def run(src: str):
        out = lex(src)
        out.toks = [
            Tok(t.kind, "7", t.pos, t.line) if t.text == "2" else t for t in out.toks
        ]
        return out

    return run


ltable = lanes.lexers()
with patched(ltable, "L3-dfa", misread_two(ltable["L3-dfa"])):
    m = merge.merge("2 * 3")
ok("a broken lexer is outvoted", canon(m.value) == "CONFIDENT:6@192")
ok("the token vote names Lane 3", m.tokens.dissenters() == ["Lane 3"])
ok(
    "the drift names L3-dfa alone: its parser and executor are clean",
    [(d.lane, d.stage, d.part) for d in m.drift] == [("Lane 3", "lexer", "L3-dfa")],
)
ok("the drift says which token", "token 0" in m.drift[0].detail)


class Crashes(executors.Walker):
    def evaluate(self, node, fns, cfg, shown):
        raise RuntimeError("boom")


with patched(executors.EXECUTORS, "X1-walker", Crashes):
    m = merge.merge("2 + 2")
ok("a crashing lane is a dissent, not a crash", canon(m.value) == "CONFIDENT:4@192")
ok(
    "its result is recorded as a crash",
    m.result.answers["Lane 1"] == "CRASH[execute/RuntimeError]",
)
ok("the drift names the crashing executor", m.drift[0].part == "X1-walker")


def other_tree(parse: Callable) -> Callable:
    def run(toks: List[Tok]):
        return parse([t for t in toks if t.text != "+" and t.text != "1"] or toks)

    return run


with patched(table, "P5-generated", other_tree(table["P5-generated"])):
    m = merge.merge("1 + 2")
ok("the grammar witness disagreeing is reported", not m.witness_agrees)
ok("but it does not change the answer", canon(m.value) == "CERTAIN:3@256")
ok("and the report says so", "grammar witness" in merge.report(m))

# ═════════════════════════════════════════════════════════════════════
section("7. WITHHOLDING — two broken lanes")
# ═════════════════════════════════════════════════════════════════════


class AlsoOff(executors.Walker):
    def evaluate(self, node, fns, cfg, shown):
        r = super().evaluate(node, fns, cfg, shown)
        return r if r.is_z else e_val("op", 999, r.confidence)


with (
    patched(executors.EXECUTORS, "X3-stack", OffByOne),
    patched(executors.EXECUTORS, "X1-walker", AlsoOff),
):
    m = merge.merge("1 + 2")
ok("2 of 4 is withheld", bool(m.withheld) and m.value.is_z)
ok("the withheld answer is Z(unbound)", m.value.defect == Defect.UNBOUND)
ok("the exit code says withheld", m.exit_code() == merge.EXIT_WITHHELD)
ok("the report says why", "withheld" in merge.report(m))

with (
    patched(executors.EXECUTORS, "X3-stack", OffByOne),
    patched(executors.EXECUTORS, "X1-walker", OffByOne),
):
    m = merge.merge("1 + 2")
ok("two lanes sharing one bug still cannot outvote two", bool(m.withheld))

with (
    patched(table, "P2-pratt", swapped_minus(table["P2-pratt"])),
    patched(table, "P4-earley", swapped_minus(table["P4-earley"])),
):
    m = merge.merge("9 - 4")
ok("a 2-2 split on the tree is withheld there", "what the program says" in m.withheld)

# ═════════════════════════════════════════════════════════════════════
section("8. CLI")
# ═════════════════════════════════════════════════════════════════════


def cli(argv: List[str]) -> Tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = merge.main(argv)
    return code, buf.getvalue()


code, out = cli(["-e", "1 + 2"])
ok("a value exits 0", code == merge.EXIT_OK and "answer 3  @ 256/256" in out)
code, out = cli(["-e", "1 / 0"])
ok("a Z exits 1", code == merge.EXIT_REFUSED and "misbound" in out)
code, out = cli(["-e", "1 +"])
ok("a refusal exits 1", code == merge.EXIT_REFUSED and "FAIL[parse/unbounded]" in out)
code, out = cli([str(ROOT / "examples" / "sum.ezr")])
ok(
    "examples/sum.ezr is 15 @ 120 on all four",
    code == 0 and "answer 15  @ 120/256" in out,
)
code, out = cli([str(ROOT / "examples" / "readings.ezr"), "-v"])
ok("-v shows every lane", out.count("Lane ") >= 4 and "show   6  @ 256/256" in out)
code, out = cli(["-e", FACT, "--call", "fact(4)", "--depth", "2"])
ok("--depth reaches every lane", "unbounded" in out)
code, out = cli(["-e", DOWN, "--call", "down(300)", "-a", "down"])
ok("--anchor reaches every lane", code == 0 and "answer 0  @ 120/256" in out)
code, out = cli(["-e", "def f(n) = n"])
ok("definitions with no entry exit 0", code == 0 and "NOENTRY(f)" in out)
code, out = cli([str(ROOT / "no-such-file.ezr")])
ok("a missing file is a usage error", code == merge.EXIT_USAGE)

print(f"\n=== Lanes: {passed} passed, {failed} failed ===\n")
raise SystemExit(0 if failed == 0 else 1)
