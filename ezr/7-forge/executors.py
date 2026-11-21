#!/usr/bin/env python3
"""
executors.py — four ways to run one tree.

The forge has four lexers and four hand-written parsers, each putting a
claim about the language somewhere different: precedence lives in a
call graph, a table, a stack discipline, the shape of the rules. These
are the four executors that finish the lanes, and they follow the same
discipline. Every one walks the same contract.py tree and applies the
same algebra.py rules, and each keeps its *evaluation strategy* --
which operand first, which arm runs, where Z stops the work, how deep
a call may go -- in a different place:

  X1  walker     a recursive tree walk. Strategy is the call graph.
  X2  closures   the tree is compiled once into nested Python closures,
                 then the closures run. Strategy is fixed at compile
                 time, in what each closure chooses to call.
  X3  stack      an explicit continuation stack and a value stack, no
                 host recursion at all. Strategy is the order tasks are
                 pushed.
  X4  bytecode   the tree is compiled to a flat instruction list with
                 jumps, run by a machine with call frames. Strategy is
                 where the jumps go.

The semantics they must all reproduce are syntax.eval_ast's (algebra.py
has the three departures, and why): chain by min, Z absorbs and stops
the work at the first Z, a known condition runs one arm, an unknown one
runs both and spans, a list stops at its first Z element, every
argument is evaluated before a call is judged, a call past its depth
ceiling never evaluates its arguments, and a result is floored by how
far the function that produced it is believed.

Codric Enterprise · Ricky (Dreid) · 2026
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from algebra import (
    Config,
    Outcome,
    applied,
    arity,
    binop,
    builtin,
    first_z,
    gathered,
    literal,
    span,
    taken,
    too_deep,
    truth,
    unbound,
)
from contract import Bin, Bool, Call, Def, If, Let, Lst, N, Num, Prog, Str, Var
from ever import E_CERTAIN, E, e_val, e_z

Fns = Dict[str, Def]
Env = Dict[str, E]


def _const(node: N) -> Optional[E]:
    """The value of a literal node, or None if it is not one."""
    if isinstance(node, Num):
        return literal(node.value)
    if isinstance(node, (Str, Bool)):
        return e_val("lit", node.value, E_CERTAIN)
    return None


@contextmanager
def _deep() -> Iterator[None]:
    """Room for X1 and X2 to recurse as far as E_HARD_DEPTH allows.

    Both recurse on the host stack, so an anchored function four
    thousand calls deep needs more than CPython's default thousand
    frames. X3 and X4 never touch the host stack and do not need this.
    """
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old, 200_000))
    try:
        yield
    finally:
        sys.setrecursionlimit(old)


class _Executor:
    """What every executor is handed for one run: the function table,
    the configuration, and the log `show` writes to."""

    name = ""

    def __init__(self) -> None:
        self.fns: Fns = {}
        self.cfg = Config()
        self.shown: List[E] = []

    def evaluate(self, node: N, fns: Fns, cfg: Config, shown: List[E]) -> E:
        raise NotImplementedError


# ═════════════════════════════════════════════
# X1 — the walker
# ═════════════════════════════════════════════


class Walker(_Executor):
    name = "X1-walker"

    def evaluate(self, node: N, fns: Fns, cfg: Config, shown: List[E]) -> E:
        self.fns, self.cfg, self.shown = fns, cfg, shown
        with _deep():
            return self.walk(node, {}, 0)

    def walk(self, node: N, env: Env, depth: int) -> E:
        const = _const(node)
        if const is not None:
            return const
        if isinstance(node, Var):
            found = env.get(node.name)
            return found if found is not None else unbound(node.name)
        if isinstance(node, Bin):
            left = self.walk(node.left, env, depth)
            if left.is_z:
                return left
            right = self.walk(node.right, env, depth)
            return right if right.is_z else binop(node.op, left, right)
        if isinstance(node, If):
            return self.branch(node, env, depth)
        if isinstance(node, Lst):
            values: List[Any] = []
            confs: List[int] = []
            for item in node.items:
                got = self.walk(item, env, depth)
                if got.is_z:
                    return got
                values.append(got.value)
                confs.append(got.confidence)
            return gathered(values, confs)
        if isinstance(node, Let):
            bound = self.walk(node.value, env, depth)
            if bound.is_z:
                return bound
            return self.walk(node.body, {**env, node.name: bound}, depth)
        if isinstance(node, Call):
            return self.call(node, env, depth)
        return e_z("eval", f"cannot evaluate {type(node).__name__}")

    def branch(self, node: If, env: Env, depth: int) -> E:
        cond = self.walk(node.cond, env, depth)
        if cond.is_z:
            return span(self.walk(node.then, env, depth), self.walk(node.els, env, depth))
        refused = truth(cond)
        if refused is not None:
            return refused
        arm = node.then if cond.value else node.els
        return taken(cond, self.walk(arm, env, depth))

    def call(self, node: Call, env: Env, depth: int) -> E:
        fn = self.fns.get(node.name)
        if fn is not None:
            ceiling = self.cfg.trust.ceiling(node.name, self.cfg.depth)
            if depth > ceiling:
                return too_deep(node.name, ceiling)
        args = [self.walk(a, env, depth) for a in node.args]
        stop = first_z(args)
        if stop is not None:
            return stop
        if fn is None:
            return builtin(node.name, args, self.shown)
        if len(args) != len(fn.params):
            return arity(node.name, len(fn.params), len(args))
        result = self.walk(fn.body, dict(zip(fn.params, args)), depth + 1)
        return applied(node.name, result, args, self.cfg.trust)


# ═════════════════════════════════════════════
# X2 — closures
# ═════════════════════════════════════════════

Code = Callable[[Env, int], E]


class Closures(_Executor):
    """Compile once, run many. A function body is compiled the first
    time it is called and kept, so recursion reuses the same closure."""

    name = "X2-closures"

    def __init__(self) -> None:
        super().__init__()
        self.bodies: Dict[str, Code] = {}

    def evaluate(self, node: N, fns: Fns, cfg: Config, shown: List[E]) -> E:
        self.fns, self.cfg, self.shown = fns, cfg, shown
        self.bodies = {}
        code = self.compile(node)
        with _deep():
            return code({}, 0)

    def compile(self, node: N) -> Code:
        const = _const(node)
        if const is not None:
            fixed = const
            return lambda env, depth: fixed
        if isinstance(node, Var):
            return self._var(node.name)
        if isinstance(node, Bin):
            return self._bin(node.op, self.compile(node.left), self.compile(node.right))
        if isinstance(node, If):
            return self._if(
                self.compile(node.cond), self.compile(node.then), self.compile(node.els)
            )
        if isinstance(node, Lst):
            return self._list([self.compile(i) for i in node.items])
        if isinstance(node, Let):
            return self._let(node.name, self.compile(node.value), self.compile(node.body))
        if isinstance(node, Call):
            return self._call(node.name, [self.compile(a) for a in node.args])
        what = type(node).__name__
        return lambda env, depth: e_z("eval", f"cannot evaluate {what}")

    @staticmethod
    def _var(name: str) -> Code:
        def step(env: Env, _depth: int) -> E:
            return env[name] if name in env else unbound(name)

        return step

    @staticmethod
    def _bin(op: str, left: Code, right: Code) -> Code:
        def step(env: Env, depth: int) -> E:
            a = left(env, depth)
            if a.is_z:
                return a
            b = right(env, depth)
            if b.is_z:
                return b
            return binop(op, a, b)

        return step

    @staticmethod
    def _if(cond: Code, then: Code, els: Code) -> Code:
        def step(env: Env, depth: int) -> E:
            c = cond(env, depth)
            if c.is_z:
                both = (then(env, depth), els(env, depth))
                return span(*both)
            return truth(c) or taken(c, (then if c.value else els)(env, depth))

        return step

    @staticmethod
    def _list(items: List[Code]) -> Code:
        def step(env: Env, depth: int) -> E:
            got: List[E] = []
            for item in items:
                e = item(env, depth)
                if e.is_z:
                    return e
                got.append(e)
            return gathered([e.value for e in got], [e.confidence for e in got])

        return step

    @staticmethod
    def _let(name: str, value: Code, body: Code) -> Code:
        def step(env: Env, depth: int) -> E:
            v = value(env, depth)
            if v.is_z:
                return v
            inner = dict(env)
            inner[name] = v
            return body(inner, depth)

        return step

    def _body(self, name: str) -> Code:
        if name not in self.bodies:
            self.bodies[name] = self.compile(self.fns[name].body)
        return self.bodies[name]

    def _call(self, name: str, args: List[Code]) -> Code:
        def step(env: Env, depth: int) -> E:
            fn = self.fns.get(name)
            limit = self.cfg.trust.ceiling(name, self.cfg.depth)
            if fn is not None and depth > limit:
                return too_deep(name, limit)
            vals = [a(env, depth) for a in args]
            z = first_z(vals)
            if z is not None:
                return z
            if fn is None:
                return builtin(name, vals, self.shown)
            if len(vals) != len(fn.params):
                return arity(name, len(fn.params), len(vals))
            local = {p: v for p, v in zip(fn.params, vals)}
            return applied(name, self._body(name)(local, depth + 1), vals, self.cfg.trust)

        return step


# ═════════════════════════════════════════════
# X3 — the stack machine
# ═════════════════════════════════════════════


class Stack(_Executor):
    """No host recursion. `todo` holds continuations, `vals` holds
    results; a task either pushes a value or pushes more tasks. Tasks
    come off the end, so a task that must run first is pushed last."""

    name = "X3-stack"

    def __init__(self) -> None:
        super().__init__()
        self.todo: List[Tuple[Any, ...]] = []
        self.vals: List[E] = []

    def evaluate(self, node: N, fns: Fns, cfg: Config, shown: List[E]) -> E:
        self.fns, self.cfg, self.shown = fns, cfg, shown
        self.todo = [("eval", node, {}, 0)]
        self.vals = []
        while self.todo:
            task = self.todo.pop()
            getattr(self, "k_" + task[0])(*task[1:])
        assert len(self.vals) == 1, "stack machine left the value stack uneven"
        return self.vals[0]

    def push(self, *task: Any) -> None:
        self.todo.append(task)

    def k_eval(self, node: N, env: Env, depth: int) -> None:
        const = _const(node)
        if const is not None:
            self.vals.append(const)
        elif isinstance(node, Var):
            self.vals.append(env.get(node.name) or unbound(node.name))
        elif isinstance(node, Bin):
            self.push("bin_left", node, env, depth)
            self.push("eval", node.left, env, depth)
        elif isinstance(node, If):
            self.push("cond", node, env, depth)
            self.push("eval", node.cond, env, depth)
        elif isinstance(node, Lst):
            self.push("item", node.items, 0, [], env, depth)
        elif isinstance(node, Let):
            self.push("bind", node, env, depth)
            self.push("eval", node.value, env, depth)
        elif isinstance(node, Call):
            self.k_call(node, env, depth)
        else:
            self.vals.append(e_z("eval", f"cannot evaluate {type(node).__name__}"))

    def k_bin_left(self, node: Bin, env: Env, depth: int) -> None:
        if not self.vals[-1].is_z:
            self.push("bin_apply", node.op)
            self.push("eval", node.right, env, depth)

    def k_bin_apply(self, op: str) -> None:
        b = self.vals.pop()
        a = self.vals.pop()
        self.vals.append(b if b.is_z else binop(op, a, b))

    def k_cond(self, node: If, env: Env, depth: int) -> None:
        c = self.vals.pop()
        if c.is_z:
            self.push("span")
            self.push("eval", node.els, env, depth)
            self.push("eval", node.then, env, depth)
            return
        refused = truth(c)
        if refused is not None:
            self.vals.append(refused)
            return
        self.push("taken", c)
        self.push("eval", node.then if c.value else node.els, env, depth)

    def k_span(self) -> None:
        b = self.vals.pop()
        a = self.vals.pop()
        self.vals.append(span(a, b))

    def k_taken(self, c: E) -> None:
        self.vals.append(taken(c, self.vals.pop()))

    def k_item(self, items: List[N], i: int, acc: List[E], env: Env, depth: int) -> None:
        if i == len(items):
            self.vals.append(gathered([e.value for e in acc], [e.confidence for e in acc]))
            return
        self.push("item_done", items, i, acc, env, depth)
        self.push("eval", items[i], env, depth)

    def k_item_done(self, items: List[N], i: int, acc: List[E], env: Env, depth: int) -> None:
        e = self.vals.pop()
        if e.is_z:
            self.vals.append(e)
        else:
            self.push("item", items, i + 1, acc + [e], env, depth)

    def k_bind(self, node: Let, env: Env, depth: int) -> None:
        v = self.vals.pop()
        if v.is_z:
            self.vals.append(v)
        else:
            self.push("eval", node.body, {**env, node.name: v}, depth)

    def k_call(self, node: Call, env: Env, depth: int) -> None:
        if node.name in self.fns:
            ceiling = self.cfg.trust.ceiling(node.name, self.cfg.depth)
            if depth > ceiling:
                self.vals.append(too_deep(node.name, ceiling))
                return
        self.push("arg", node, 0, [], env, depth)

    def k_arg(self, node: Call, i: int, acc: List[E], env: Env, depth: int) -> None:
        if i < len(node.args):
            self.push("arg_done", node, i, acc, env, depth)
            self.push("eval", node.args[i], env, depth)
        else:
            self.k_enter(node, acc, depth)

    def k_arg_done(self, node: Call, i: int, acc: List[E], env: Env, depth: int) -> None:
        self.push("arg", node, i + 1, acc + [self.vals.pop()], env, depth)

    def k_enter(self, node: Call, args: List[E], depth: int) -> None:
        stop = first_z(args)
        fn = self.fns.get(node.name)
        if stop is not None:
            self.vals.append(stop)
        elif fn is None:
            self.vals.append(builtin(node.name, args, self.shown))
        elif len(args) != len(fn.params):
            self.vals.append(arity(node.name, len(fn.params), len(args)))
        else:
            self.push("ret", node.name, args)
            self.push("eval", fn.body, dict(zip(fn.params, args)), depth + 1)

    def k_ret(self, name: str, args: List[E]) -> None:
        self.vals.append(applied(name, self.vals.pop(), args, self.cfg.trust))


# ═════════════════════════════════════════════
# X4 — bytecode
# ═════════════════════════════════════════════
#
# One instruction is a tuple whose first element is the opcode. Jump
# targets are absolute indices into the same code list. The operand
# stack is shared across frames; a frame records where it returns to.
#
#   CONST e            push e
#   LOAD name          push env[name], or Z(unbound)
#   ZJUMP t            if top is Z, jump to t and leave it there
#   BINOP op           pop b, a; push b if Z, else binop
#   TEST else span end pop c. Z: jump span. text: push Z, jump end.
#                      otherwise push c back, fall through or jump else
#   TAKEN              pop r, c; push taken(c, r)
#   SPAN               pop b, a; push span(a, b)
#   JUMP t
#   LIST               push an empty accumulator
#   GATHER t           pop e. Z: replace the accumulator with e, jump t.
#                      else append e to the accumulator
#   SEAL               turn the accumulator into a list value
#   BIND name          pop v, open a scope with name = v
#   UNBIND             close the scope
#   GUARD name t       a defined name past its ceiling: push Z, jump t
#   CALL name n        pop n args and call
#   RET                pop r, return it to the caller as applied()


@dataclass
class _Frame:
    code: List[Tuple[Any, ...]]
    scopes: List[Env]
    depth: int
    name: str = ""
    args: List[E] = field(default_factory=list)
    pc: int = 0


class Bytecode(_Executor):
    name = "X4-bytecode"

    def __init__(self) -> None:
        super().__init__()
        self.units: Dict[str, List[Tuple[Any, ...]]] = {}

    def evaluate(self, node: N, fns: Fns, cfg: Config, shown: List[E]) -> E:
        self.fns, self.cfg, self.shown = fns, cfg, shown
        self.units = {}
        return self.machine(self.assemble(node, final=None))

    # ── the assembler ──
    def assemble(self, node: N, final: Optional[str]) -> List[Tuple[Any, ...]]:
        code: List[Tuple[Any, ...]] = []
        self.emit(node, code)
        code.append(("RET",) if final else ("HALT",))
        return code

    def unit(self, name: str) -> List[Tuple[Any, ...]]:
        if name not in self.units:
            self.units[name] = self.assemble(self.fns[name].body, final=name)
        return self.units[name]

    def emit(self, node: N, code: List[Tuple[Any, ...]]) -> None:
        const = _const(node)
        if const is not None:
            code.append(("CONST", const))
        elif isinstance(node, Var):
            code.append(("LOAD", node.name))
        elif isinstance(node, Bin):
            self.emit(node.left, code)
            hole = len(code)
            code.append(("ZJUMP", None))
            self.emit(node.right, code)
            code.append(("BINOP", node.op))
            code[hole] = ("ZJUMP", len(code))
        elif isinstance(node, If):
            self.emit_if(node, code)
        elif isinstance(node, Lst):
            code.append(("LIST",))
            holes = []
            for item in node.items:
                self.emit(item, code)
                holes.append(len(code))
                code.append(("GATHER", None))
            code.append(("SEAL",))
            for h in holes:
                code[h] = ("GATHER", len(code))
        elif isinstance(node, Let):
            self.emit(node.value, code)
            hole = len(code)
            code.append(("ZJUMP", None))
            code.append(("BIND", node.name))
            self.emit(node.body, code)
            code.append(("UNBIND",))
            code[hole] = ("ZJUMP", len(code))
        elif isinstance(node, Call):
            hole = len(code)
            code.append(("GUARD", node.name, None))
            for a in node.args:
                self.emit(a, code)
            code.append(("CALL", node.name, len(node.args)))
            code[hole] = ("GUARD", node.name, len(code))
        else:
            code.append(("CONST", e_z("eval", f"cannot evaluate {type(node).__name__}")))

    def emit_if(self, node: If, code: List[Tuple[Any, ...]]) -> None:
        self.emit(node.cond, code)
        test = len(code)
        code.append(("TEST", None, None, None))
        self.emit(node.then, code)
        code.append(("TAKEN",))
        to_end = [len(code)]
        code.append(("JUMP", None))
        at_else = len(code)
        self.emit(node.els, code)
        code.append(("TAKEN",))
        to_end.append(len(code))
        code.append(("JUMP", None))
        at_span = len(code)
        self.emit(node.then, code)
        self.emit(node.els, code)
        code.append(("SPAN",))
        end = len(code)
        code[test] = ("TEST", at_else, at_span, end)
        for j in to_end:
            code[j] = ("JUMP", end)

    # ── the machine ──
    def machine(self, entry: List[Tuple[Any, ...]]) -> E:
        stack: List[Any] = []
        frames = [_Frame(entry, [{}], 0)]
        while True:
            f = frames[-1]
            op = f.code[f.pc]
            f.pc += 1
            kind = op[0]
            if kind == "CONST":
                stack.append(op[1])
            elif kind == "LOAD":
                scope = f.scopes[-1]
                stack.append(scope[op[1]] if op[1] in scope else unbound(op[1]))
            elif kind == "ZJUMP":
                if stack[-1].is_z:
                    f.pc = op[1]
            elif kind == "BINOP":
                b, a = stack.pop(), stack.pop()
                stack.append(b if b.is_z else binop(op[1], a, b))
            elif kind == "TEST":
                c = stack.pop()
                if c.is_z:
                    f.pc = op[2]
                elif truth(c) is not None:
                    stack.append(truth(c))
                    f.pc = op[3]
                else:
                    stack.append(c)
                    if not c.value:
                        f.pc = op[1]
            elif kind == "TAKEN":
                r, c = stack.pop(), stack.pop()
                stack.append(taken(c, r))
            elif kind == "SPAN":
                b, a = stack.pop(), stack.pop()
                stack.append(span(a, b))
            elif kind == "JUMP":
                f.pc = op[1]
            elif kind == "LIST":
                stack.append([])
            elif kind == "GATHER":
                e = stack.pop()
                if e.is_z:
                    stack[-1] = e
                    f.pc = op[1]
                else:
                    stack[-1].append(e)
            elif kind == "SEAL":
                acc = stack.pop()
                stack.append(gathered([e.value for e in acc], [e.confidence for e in acc]))
            elif kind == "BIND":
                f.scopes.append({**f.scopes[-1], op[1]: stack.pop()})
            elif kind == "UNBIND":
                f.scopes.pop()
            elif kind == "GUARD":
                if op[1] in self.fns:
                    ceiling = self.cfg.trust.ceiling(op[1], self.cfg.depth)
                    if f.depth > ceiling:
                        stack.append(too_deep(op[1], ceiling))
                        f.pc = op[2]
            elif kind == "CALL":
                cut = len(stack) - op[2]
                args = stack[cut:]
                del stack[cut:]
                callee = self.enter(op[1], args, f.depth, stack)
                if callee is not None:
                    frames.append(callee)
            elif kind == "RET":
                done = frames.pop()
                stack.append(applied(done.name, stack.pop(), done.args, self.cfg.trust))
            elif kind == "HALT":
                return stack.pop()
            else:  # pragma: no cover - the assembler emits nothing else
                raise RuntimeError(f"unknown opcode {kind}")

    def enter(self, name: str, args: List[E], depth: int, stack: List[Any]) -> Optional[_Frame]:
        """Judge a call. Pushes the answer and returns None, or returns
        the frame that will compute it."""
        stop = first_z(args)
        fn = self.fns.get(name)
        if stop is not None:
            stack.append(stop)
        elif fn is None:
            stack.append(builtin(name, args, self.shown))
        elif len(args) != len(fn.params):
            stack.append(arity(name, len(fn.params), len(args)))
        else:
            return _Frame(self.unit(name), [dict(zip(fn.params, args))], depth + 1, name, args)
        return None


# ═════════════════════════════════════════════
# Registry, and running a whole program
# ═════════════════════════════════════════════

EXECUTORS: Dict[str, Callable[[], _Executor]] = {
    "X1-walker": Walker,
    "X2-closures": Closures,
    "X3-stack": Stack,
    "X4-bytecode": Bytecode,
}


def entry_of(prog: Prog, call: Optional[Prog]) -> Tuple[Fns, Optional[N]]:
    """The function table and what to evaluate, as ezrun decides them: a
    --call wins, then the program's own trailing expression, then a
    zero-argument main(). A later definition of a name replaces an
    earlier one."""
    fns: Fns = {d.name: d for d in prog.defs}
    if call is not None and call.expr is not None:
        return fns, call.expr
    if prog.expr is not None:
        return fns, prog.expr
    main = fns.get("main")
    if main is not None and not main.params:
        return fns, Call("main", [])
    return fns, None


def run(
    executor: str,
    prog: Optional[N],
    call: Optional[N] = None,
    cfg: Optional[Config] = None,
) -> Outcome:
    """Run a parsed program on one executor. `prog` and `call` are the
    Prog trees a parser returned; anything else is a caller's error."""
    if not isinstance(prog, Prog) or not (call is None or isinstance(call, Prog)):
        raise TypeError("run() takes the Prog trees a parser returns")
    cfg = cfg or Config()
    fns, entry = entry_of(prog, call)
    if entry is None:
        return Outcome(defined=sorted(fns))
    shown: List[E] = []
    value = EXECUTORS[executor]().evaluate(entry, fns, cfg, shown)
    return Outcome(value=value, shown=shown)


__all__ = ["EXECUTORS", "Walker", "Closures", "Stack", "Bytecode", "entry_of", "run"]
