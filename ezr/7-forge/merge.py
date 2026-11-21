#!/usr/bin/env python3
"""
merge.py — where the four lanes become one language.

Each lane in lanes.py carries a program from text to an answer on its
own. The merge is the only place they meet, and it asks the same
question at every boundary a program crosses:

    tokens   did the four lexers read the same words?
    tree     did the four parsers build the same program?
    where    when they refused, did they refuse at the same place?
    result   did the four executors compute the same value, at the same
             confidence, showing the same things along the way?

The answer is settled by VOWELS.md's oracle, unchanged: at least
floor(pi) = 3 lanes agreeing and at least two-thirds of those that
answered. Then the agreement itself becomes part of the answer's trust:

    4 of 4   the value keeps its own confidence. Agreement between
             implementations cannot add trust the program never earned;
             it can only stop the implementations from taking it away.
    3 of 4   the majority's value, its confidence scaled by 3/4, and the
             lane that dissented is named.
    2 of 4   withheld. The answer is Z, and the report says where the
    or less  lanes split. Inventing one here would teach a fact nobody
             verified, which is the one thing Ever exists to prevent.

The dynamic part. A clean run costs four lanes and nothing more. Only
when a lane dissents does the merge start swapping parts: the
dissenting lane's parser is handed the majority's tokens, its executor
the majority's tree. Whichever part still disagrees when given clean
input is the one that drifted, and the report names it -- lexer,
parser or executor -- rather than just naming the lane.

P5, the parser emitted from GRAMMAR.ebnf, is not a lane. It reads the
majority's tokens and says whether the tree the lanes agreed on is the
one the published grammar describes.

    python3 merge.py program.ezr                 run a file
    python3 merge.py -e "1 + 2 * 3"              run an expression
    python3 merge.py file.ezr --call "f(4)"      choose the entry point
    python3 merge.py file.ezr -v                 every lane, every stage

Exit status: 0 a value, 1 a refusal or Z, 3 withheld because the lanes
split, 2 bad usage.

Codric Enterprise · Ricky (Dreid) · 2026
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional

from algebra import DEFAULT_DEPTH, Config, Outcome, Trust, canon, show_line
from ever import E_CERTAIN, Defect, E, State, e_z
from executors import run as execute
from lanes import LANES, WITNESS, LaneRun, parsers, run_all, verdict

# A lane is total: any exception a part raises is recorded as that part's
# verdict, so catching everything here is the design, not a shortcut.
# pylint: disable=broad-exception-caught

#: VOWELS.md's bar: floor(pi) agreeing, and two-thirds of those answering.
QUORUM = 3

EXIT_OK, EXIT_REFUSED, EXIT_USAGE, EXIT_WITHHELD = 0, 1, 2, 3


# ═════════════════════════════════════════════
# Votes
# ═════════════════════════════════════════════


@dataclass
class Vote:
    stage: str
    answers: Dict[str, str]
    groups: Dict[str, List[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for lane, answer in self.answers.items():
            self.groups.setdefault(answer, []).append(lane)
        self.groups = dict(sorted(self.groups.items(), key=lambda kv: -len(kv[1])))

    @property
    def top(self) -> str:
        return next(iter(self.groups)) if self.groups else ""

    @property
    def agree(self) -> int:
        return len(self.groups[self.top]) if self.groups else 0

    @property
    def total(self) -> int:
        return len(self.answers)

    @property
    def unanimous(self) -> bool:
        return len(self.groups) <= 1

    @property
    def consensus(self) -> bool:
        return self.agree >= QUORUM and 3 * self.agree >= 2 * self.total

    def dissenters(self) -> List[str]:
        return [lane for lane, a in self.answers.items() if a != self.top]

    def line(self) -> str:
        if self.unanimous:
            return f"{self.stage:<7}{self.agree}/{self.total}"
        split = " | ".join(f"{', '.join(v)}" for v in self.groups.values())
        return f"{self.stage:<7}{self.agree}/{self.total}  split: {split}"


# ═════════════════════════════════════════════
# Drift — which part of a dissenting lane moved
# ═════════════════════════════════════════════


@dataclass
class Drift:
    lane: str
    stage: str  # lexer | parser | executor | where | combination
    part: str
    detail: str

    def line(self) -> str:
        return f"{self.lane}: {self.stage} {self.part} -- {self.detail}"


def _first_token_split(a: LaneRun, b: LaneRun) -> str:
    ta = a.lexed.toks if a.lexed else []
    tb = b.lexed.toks if b.lexed else []
    for i, (x, y) in enumerate(zip(ta, tb)):
        if x.canon() != y.canon():
            return f"token {i}: {x.canon()} where {b.lane.name} reads {y.canon()}"
    if len(ta) != len(tb):
        return f"{len(ta)} tokens where {b.lane.name} reads {len(tb)}"
    return f"{a.tokens} where {b.lane.name} reads {b.tokens}"


def localise(run: LaneRun, ref: LaneRun, cfg: Optional[Config]) -> List[Drift]:
    """Swap the dissenting lane's parts onto the reference lane's input,
    one stage at a time, and keep the parts that still disagree."""
    lane = run.lane
    found: List[Drift] = []
    if run.tokens != ref.tokens:
        found.append(
            Drift(lane.name, "lexer", lane.lexer, _first_token_split(run, ref))
        )
    if ref.lexed is not None and ref.lexed.ok:
        try:
            po = parsers()[lane.parser](ref.lexed.toks)
            got = verdict(ref.lexed, po)
        except Exception as exc:
            got = f"CRASH[parse/{type(exc).__name__}]"
        if got != ref.tree:
            found.append(
                Drift(
                    lane.name,
                    "parser",
                    lane.parser,
                    f"given {ref.lane.name}'s tokens it builds {got}; "
                    f"the majority built {ref.tree}",
                )
            )
    if ref.parsed is not None and ref.parsed.ok and not ref.refused:
        try:
            got = execute(lane.executor, ref.parsed.ast, ref.call_tree, cfg).canon()
        except Exception as exc:
            got = f"CRASH[execute/{type(exc).__name__}]"
        if got != ref.result:
            found.append(
                Drift(
                    lane.name,
                    "executor",
                    lane.executor,
                    f"given {ref.lane.name}'s tree it computes {got}; "
                    f"the majority computed {ref.result}",
                )
            )
    if not found:
        found.append(
            Drift(
                lane.name,
                "combination",
                lane.parts(),
                "each part agrees with the majority when swapped in alone",
            )
        )
    return found


# ═════════════════════════════════════════════
# The merge
# ═════════════════════════════════════════════


@dataclass
class Merged:
    src: str
    call: Optional[str]
    runs: List[LaneRun]
    tokens: Vote
    tree: Vote
    where: Optional[Vote]
    result: Vote
    answer: str = ""
    value: Optional[E] = None
    shown: List[E] = field(default_factory=list)
    withheld: str = ""
    witness: str = ""
    drift: List[Drift] = field(default_factory=list)

    @property
    def unanimous(self) -> bool:
        return all(v.unanimous for v in (self.tokens, self.tree, self.result)) and (
            self.where is None or self.where.unanimous
        )

    @property
    def witness_agrees(self) -> bool:
        return not self.witness or self.witness == self.tree.top

    def exit_code(self) -> int:
        if self.withheld:
            return EXIT_WITHHELD
        if self.value is None:
            return EXIT_OK if self.answer.startswith("NOENTRY") else EXIT_REFUSED
        return EXIT_REFUSED if self.value.is_z else EXIT_OK


def _scaled(value: E, agree: int, total: int) -> E:
    """The majority's value, trusted in proportion to the lanes behind it."""
    if value.is_z or agree == total:
        return value
    conf = value.confidence * agree // total
    if conf <= 0:
        return e_z(value.ident, "confidence collapsed to zero")
    state = value.state
    if state == State.CERTAIN and conf < E_CERTAIN:
        state = State.CONFIDENT
    out = replace(value, confidence=conf, state=state)
    if state != State.EQUIV:
        out.lo = out.hi = conf
    out.reason = f"{agree} of {total} lanes agree"
    return out


def _pick(runs: List[LaneRun], *votes: Vote) -> Optional[LaneRun]:
    """A lane that sides with the majority at every stage given."""
    for r in runs:
        if all(v.answers.get(r.lane.name) == v.top for v in votes):
            return r
    return None


def merge(src: str, call: Optional[str] = None, cfg: Optional[Config] = None) -> Merged:
    runs = run_all(src, call, cfg)
    tokens = Vote("tokens", {r.lane.name: r.tokens for r in runs})
    tree = Vote("tree", {r.lane.name: r.tree for r in runs})
    result = Vote("result", {r.lane.name: r.result for r in runs})
    refusing = {
        r.lane.name: str(r.where)
        for r in runs
        if r.tree == tree.top and r.where is not None
    }
    where = Vote("where", refusing) if refusing else None
    m = Merged(src, call, runs, tokens, tree, where, result)

    # ── the answer ──
    if not tree.consensus:
        m.withheld = f"the lanes split {_split(tree)} on what the program says"
    elif not result.consensus:
        m.withheld = f"the lanes split {_split(result)} on what the program means"
    if m.withheld:
        m.value = e_z("merge", m.withheld, Defect.UNBOUND)
        m.answer = canon(m.value)
    else:
        majority = next(r for r in runs if r.result == result.top)
        m.answer = result.top
        if majority.outcome is not None:
            m.shown = list(majority.outcome.shown)
            if majority.outcome.value is not None:
                m.value = _scaled(majority.outcome.value, result.agree, result.total)
                m.answer = Outcome(value=m.value, shown=m.shown).canon()

    # ── the grammar's witness ──
    ref = _pick(runs, tokens, tree, result) or _pick(runs, tokens, tree)
    if ref is not None and ref.lexed is not None and ref.lexed.ok:
        try:
            m.witness = verdict(ref.lexed, parsers()[WITNESS](ref.lexed.toks))
        except Exception as exc:
            m.witness = f"CRASH[parse/{type(exc).__name__}]"

    # ── only now, and only if needed: which part drifted ──
    dissent = sorted(set(tokens.dissenters() + tree.dissenters() + result.dissenters()))
    if dissent and ref is None:
        m.drift.append(
            Drift(
                "all",
                "combination",
                "-",
                "no lane sides with the majority at every stage",
            )
        )
    for name in dissent:
        run = next(r for r in runs if r.lane.name == name)
        if ref is not None:
            m.drift.extend(localise(run, ref, cfg))
    if where is not None:
        for name in where.dissenters():
            run = next(r for r in runs if r.lane.name == name)
            part = (
                run.lane.lexer if run.tree.startswith("FAIL[lex") else run.lane.parser
            )
            m.drift.append(
                Drift(
                    name,
                    "where",
                    part,
                    f"refuses at {where.answers[name]}; "
                    f"{where.agree} lanes refuse at {where.top}",
                )
            )
    return m


def _split(v: Vote) -> str:
    return "-".join(str(len(lanes)) for lanes in v.groups.values())


# ═════════════════════════════════════════════
# Reporting
# ═════════════════════════════════════════════


def report(m: Merged, verbose: bool = False) -> str:
    out: List[str] = []
    if verbose:
        for r in m.runs:
            out.append(f"  {r.lane.name}  {r.lane.parts()}")
            out.append(f"      tree    {r.tree}")
            out.append(f"      result  {r.result}")
        out.append("")
    out.append("  " + m.tokens.line())
    out.append("  " + m.tree.line())
    if m.where is not None and not m.where.unanimous:
        out.append("  " + m.where.line())
    out.append("  " + m.result.line())
    if m.witness and not m.witness_agrees:
        out.append(f"  grammar witness {WITNESS} disagrees: {m.witness}")
    for d in m.drift:
        out.append("  drift  " + d.line())
    for s in m.shown:
        out.append("  show   " + show_line(s))
    if m.withheld:
        out.append(f"  answer withheld: {m.withheld}")
    elif m.value is not None:
        tail = (
            ""
            if m.result.unanimous
            else f"  ({m.result.agree} of {m.result.total} lanes)"
        )
        out.append(f"  answer {show_line(m.value)}{tail}")
    else:
        out.append(f"  answer {m.answer}")
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="merge", description="Run a program down all four lanes."
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("file", nargs="?", help="program to run, or - for stdin")
    src.add_argument("-e", "--eval", metavar="EXPR", help="run one expression")
    ap.add_argument(
        "-c", "--call", metavar="EXPR", help="what to evaluate once defined"
    )
    ap.add_argument(
        "-d", "--depth", type=int, default=DEFAULT_DEPTH, help="recursion ceiling"
    )
    ap.add_argument(
        "-a", "--anchor", action="append", default=[], help="treat NAME as anchored"
    )
    ap.add_argument("-v", "--verbose", action="store_true", help="show every lane")
    args = ap.parse_args(argv)
    if args.eval is not None:
        text = args.eval
    elif args.file == "-":
        text = sys.stdin.read()
    else:
        try:
            with open(args.file, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            print(f"merge: {exc}", file=sys.stderr)
            return EXIT_USAGE
    cfg = Config(depth=args.depth, trust=Trust(anchored=set(args.anchor)))
    m = merge(text, args.call, cfg)
    print(report(m, args.verbose))
    return m.exit_code()


__all__ = ["Vote", "Drift", "Merged", "merge", "localise", "report", "QUORUM", "LANES"]

if __name__ == "__main__":
    sys.exit(main())
