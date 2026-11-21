#!/usr/bin/env python3
"""
lanes.py — four pipelines, source text to value, each on its own.

The forge's matrix pairs every lexer with every parser so it can test
the *components*. A lane is the other shape: one lexer, one parser and
one executor fixed together, carrying a program from text to an answer
without borrowing anything from another lane. Four lanes, four
independent claims about what a program means; merge.py is where they
meet.

    Lane 1   L1-regex-master  ->  P1-recdescent  ->  X1-walker
    Lane 2   L2-handrolled    ->  P2-pratt       ->  X2-closures
    Lane 3   L3-dfa           ->  P3-shunting    ->  X3-stack
    Lane 4   L4-trie          ->  P4-earley      ->  X4-bytecode

The pairing follows what each part is: the recursive-descent parser
feeds the recursive walker, the stack-discipline parser feeds the stack
machine, the table-driven ones feed the compiled ones. Nothing depends
on it -- any lexer can feed any parser and any parser any executor,
which is exactly what merge.py uses when it has to find which part of a
dissenting lane drifted.

Lanes use the forge's own registries, with the repairs in force
(repairs.json), so a lane runs the same lexer the matrix tests.

A lane never raises. A part that throws is recorded as a crash at its
stage, and a crash is a dissent like any other.

Codric Enterprise · Ricky (Dreid) · 2026
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Dict, List, Optional, cast

from algebra import Config, Outcome
from contract import LexOut, ParseOut, Prog, Tok
from executors import EXECUTORS
from executors import run as execute

# A lane is total: any exception a part raises is recorded as that part's
# verdict, so catching everything here is the design, not a shortcut.
# pylint: disable=broad-exception-caught

with warnings.catch_warnings():
    warnings.simplefilter("ignore", SyntaxWarning)
    import forge


@dataclass(frozen=True)
class Lane:
    name: str
    lexer: str
    parser: str
    executor: str

    def parts(self) -> str:
        return f"{self.lexer} -> {self.parser} -> {self.executor}"


LANES: List[Lane] = [
    Lane("Lane 1", "L1-regex-master", "P1-recdescent", "X1-walker"),
    Lane("Lane 2", "L2-handrolled", "P2-pratt", "X2-closures"),
    Lane("Lane 3", "L3-dfa", "P3-shunting", "X3-stack"),
    Lane("Lane 4", "L4-trie", "P4-earley", "X4-bytecode"),
]

#: The grammar's own parser. It is not a lane: it is the witness that
#: says whether the lanes' trees are what GRAMMAR.ebnf describes.
WITNESS = "P5-generated"


@lru_cache(maxsize=1)
def lexers() -> Dict[str, Callable[[str], LexOut]]:
    return forge.all_lexers()


@lru_cache(maxsize=1)
def parsers() -> Dict[str, Callable[[List[Tok]], ParseOut]]:
    return forge.REPAIRS.apply(forge.all_parsers(), "parse")


def verdict(lo: Optional[LexOut], po: Optional[ParseOut]) -> str:
    """What the front end concluded: the tree, or FAIL[stage/defect]."""
    if lo is not None and lo.fail is not None:
        return f"FAIL[lex/{lo.fail.defect}]"
    if po is None:
        return "(none)"
    if po.fail is not None:
        return f"FAIL[parse/{po.fail.defect}]"
    return po.canon()


@dataclass
class LaneRun:
    """Everything one lane produced for one program."""

    lane: Lane
    lexed: Optional[LexOut] = None
    parsed: Optional[ParseOut] = None
    call_tree: Optional[Prog] = None
    outcome: Optional[Outcome] = None
    crash: str = ""
    refused: str = ""

    @property
    def tokens(self) -> str:
        if self.lexed is None:
            return self.crash or "(none)"
        return self.lexed.canon()

    @property
    def tree(self) -> str:
        if self.crash.startswith(("CRASH[lex", "CRASH[parse")):
            return self.crash
        return verdict(self.lexed, self.parsed)

    @property
    def where(self) -> Optional[int]:
        """The refusal's position, when the front end refused."""
        if self.lexed is not None and self.lexed.fail is not None:
            return self.lexed.fail.pos
        if self.parsed is not None and self.parsed.fail is not None:
            return self.parsed.fail.pos
        return None

    @property
    def result(self) -> str:
        if self.crash:
            return self.crash
        if self.refused:
            return self.refused
        return self.outcome.canon() if self.outcome else "(none)"


def front(lexer: str, parser: str, src: str) -> tuple:
    """Lex and parse with named parts. Returns (LexOut, ParseOut|None)."""
    lo = lexers()[lexer](src)
    if not lo.ok:
        return lo, None
    return lo, parsers()[parser](lo.toks)


def run_lane(
    lane: Lane, src: str, call: Optional[str] = None, cfg: Optional[Config] = None
) -> LaneRun:
    """Carry one program through one lane, never raising."""
    r = LaneRun(lane)
    try:
        r.lexed = lexers()[lane.lexer](src)
    except Exception as exc:  # a lane is total: a crash is a verdict
        r.crash = f"CRASH[lex/{type(exc).__name__}]"
        return r
    if not r.lexed.ok:
        r.refused = verdict(r.lexed, None)
        return r
    try:
        r.parsed = parsers()[lane.parser](r.lexed.toks)
    except Exception as exc:
        r.crash = f"CRASH[parse/{type(exc).__name__}]"
        return r
    if not r.parsed.ok:
        r.refused = verdict(r.lexed, r.parsed)
        return r
    if call is not None:
        try:
            clo, cpo = front(lane.lexer, lane.parser, call)
        except Exception as exc:
            r.crash = f"CRASH[call/{type(exc).__name__}]"
            return r
        if cpo is None or not cpo.ok:
            r.refused = "REFUSED[call/" + verdict(clo, cpo) + "]"
            return r
        r.call_tree = cast(Prog, cpo.ast)
        if r.call_tree.defs or r.call_tree.expr is None:
            r.refused = "REFUSED[call/not an expression]"
            return r
    try:
        r.outcome = execute(lane.executor, r.parsed.ast, r.call_tree, cfg)
    except Exception as exc:
        r.crash = f"CRASH[execute/{type(exc).__name__}]"
    return r


def run_all(
    src: str, call: Optional[str] = None, cfg: Optional[Config] = None
) -> List[LaneRun]:
    return [run_lane(lane, src, call, cfg) for lane in LANES]


__all__ = [
    "Lane",
    "LANES",
    "WITNESS",
    "LaneRun",
    "run_lane",
    "run_all",
    "front",
    "verdict",
]

assert set(lane.executor for lane in LANES) == set(EXECUTORS)
