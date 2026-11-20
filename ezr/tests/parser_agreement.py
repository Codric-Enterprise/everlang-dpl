#!/usr/bin/env python3
"""
parser_agreement.py — one golden corpus, every front end, one answer.

The forge (7-forge/) already pairs four lexers with five parsers, but
only over its own cases: snippets from the docs, probes and fuzz. The
programs people actually write -- tests/parser_corpus/ and examples/ --
were never put to the matrix, and the two parsers that run real code
were never in it at all: syntax.py (what ezrun.py executes) and the Java
runtime's Parser.java.

This suite puts every corpus file through all of them:

  forge     4 lexers x (4 hand-written parsers + P5 generated) = 20 pairs
  syntax    the incumbent Python front end (2-interpreter-python/syntax.py)
  java      5-runtime-java's Parser, via AstDump (skipped if no javac)

and checks four things per file:

  tokens   the four forge lexers produce the identical token stream
  verdict  every witness gives the same canonical answer: the program's
           s-expression (contract.py), or FAIL[stage/defect]
  golden   the forge's answer matches tests/parser_corpus/EXPECTED, so a
           change every witness makes at once still shows up
  where    every witness that reports a refusal position reports the same
           one (contract.py: a failure is *that* it fails, *where*, and
           which defect)

A disagreement is a finding about the language, not noise. Known ones are
listed in tests/parser_corpus/KNOWN_SPLITS.json with the exact groups
observed and are printed on every run. The suite fails on any split not
listed there, and on a listed split that has healed or changed shape, so
the file can only ever describe the current state.

    python3 tests/parser_agreement.py           check
    python3 tests/parser_agreement.py -v        print every file's answer
    python3 tests/parser_agreement.py --write   rewrite EXPECTED from the
                                                forge's unanimous answers
                                                (refuses if the pairs split)

Codric Enterprise
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "tests" / "parser_corpus"
EXPECTED = CORPUS / "EXPECTED"
KNOWN = CORPUS / "KNOWN_SPLITS.json"

sys.path.insert(0, str(ROOT / "7-forge"))
sys.path.insert(0, str(ROOT / "2-interpreter-python"))

with warnings.catch_warnings():
    warnings.simplefilter("ignore", SyntaxWarning)
    import forge  # noqa: E402
    import syntax  # noqa: E402
from contract import _num  # noqa: E402
from ever import Defect  # noqa: E402

VERBOSE = "-v" in sys.argv
WRITE = "--write" in sys.argv

passed = failed = 0
findings: List[str] = []


def ok(cond: bool, msg: str) -> None:
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  ✗ {msg}")


# ── the corpus ────────────────────────────────────────────────────────


def corpus_files() -> List[Path]:
    files = sorted(CORPUS.glob("*.ezr")) + sorted((ROOT / "examples").glob("*.ezr"))
    return files


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT))


# ── witness 1: the forge's twenty pairs ───────────────────────────────


class ForgeAnswer:
    def __init__(self, lex_canon: str, verdict: str, where: Optional[str]):
        self.lex_canon = lex_canon
        self.verdict = verdict
        self.where = where


def forge_answers(src: str) -> Dict[str, ForgeAnswer]:
    out: Dict[str, ForgeAnswer] = {}
    for pair in forge.build_pairs():
        try:
            lo = pair.lex(src)
        except Exception as exc:  # a crash is an answer too
            out[pair.name] = ForgeAnswer("CRASH", f"CRASH[{type(exc).__name__}]", None)
            continue
        if not lo.ok:
            f = lo.fail
            out[pair.name] = ForgeAnswer(lo.canon(), f"FAIL[lex/{f.defect}]", f"@{f.pos}L{f.line}")
            continue
        try:
            po = pair.parse(lo.toks)
        except Exception as exc:
            out[pair.name] = ForgeAnswer(lo.canon(), f"CRASH[{type(exc).__name__}]", None)
            continue
        if po.fail:
            f = po.fail
            out[pair.name] = ForgeAnswer(
                lo.canon(), f"FAIL[parse/{f.defect}]", f"@{f.pos}L{f.line}"
            )
        else:
            out[pair.name] = ForgeAnswer(lo.canon(), po.canon(), None)
    return out


# ── witness 2: syntax.py, the parser ezrun actually runs ──────────────


def _incumbent_sexp(n) -> str:
    S = syntax
    if isinstance(n, S.Num):
        return str(_num(n.value))
    if isinstance(n, S.Str):
        return f"(str {n.value!r})"
    if isinstance(n, S.Bool):
        return "true" if n.value else "false"
    if isinstance(n, S.Var):
        return str(n.name)
    if isinstance(n, S.BinOp):
        return f"({n.op} {_incumbent_sexp(n.left)} {_incumbent_sexp(n.right)})"
    if isinstance(n, S.If):
        return (
            f"(if {_incumbent_sexp(n.cond)} {_incumbent_sexp(n.then)} " f"{_incumbent_sexp(n.els)})"
        )
    if isinstance(n, S.Call):
        return f"(call {n.name}" + "".join(" " + _incumbent_sexp(a) for a in n.args) + ")"
    if isinstance(n, S.FnDef):
        return f"(def {n.name} ({' '.join(n.params)}) {_incumbent_sexp(n.body)})"
    if isinstance(n, S.ListLit):
        return "(list" + "".join(" " + _incumbent_sexp(i) for i in n.items) + ")"
    if isinstance(n, S.LetIn):
        return f"(let {n.name} {_incumbent_sexp(n.value)} {_incumbent_sexp(n.body)})"
    # Anything else is outside the core grammar (CORE.md section 1):
    # statements, records, loops, `not`. Rendered so it cannot match.
    return f"(beyond-core {type(n).__name__})"


def incumbent_verdict(src: str) -> str:
    node, err = syntax.parse(src)
    if err is not None:
        stage = err.ident if err.ident in ("lex", "parse") else "parse"
        return f"FAIL[{stage}/{Defect(err.defect).name.lower()}]"
    if node is None:
        return "FAIL[parse/unbounded]"
    if isinstance(node, syntax.Program):
        stmts = list(node.statements)
        defs = [s for s in stmts if isinstance(s, syntax.FnDef)]
        rest = [s for s in stmts if not isinstance(s, syntax.FnDef)]
        if len(rest) > 1 or (rest and defs):
            # The core is `def+` or one expression, never a mixture.
            return "(prog " + " ".join(_incumbent_sexp(s) for s in stmts) + " (beyond-core mixed))"
        parts = [_incumbent_sexp(d) for d in defs] + [_incumbent_sexp(r) for r in rest]
        return "(prog " + " ".join(parts) + ")"
    return f"(prog {_incumbent_sexp(node)})"


# ── witness 3: the Java runtime ───────────────────────────────────────


def java_verdicts(files: List[Path]) -> Optional[Dict[str, str]]:
    if not shutil.which("javac") or not shutil.which("java"):
        return None
    layer = ROOT / "5-runtime-java"
    b = subprocess.run(["bash", str(layer / "build.sh")], capture_output=True, text=True)
    if b.returncode != 0:
        ok(False, f"java: build.sh failed\n{b.stderr[-800:]}")
        return {}
    env = dict(os.environ)
    env.pop("JAVA_TOOL_OPTIONS", None)  # it prints a banner on stderr
    r = subprocess.run(
        ["java", "-cp", str(layer / "out"), "com.codric.ezr.AstDump"] + [str(f) for f in files],
        capture_output=True,
        text=True,
        env=env,
    )
    if r.returncode != 0:
        ok(False, f"java: AstDump exited {r.returncode}\n{r.stderr[-800:]}")
        return {}
    out: Dict[str, str] = {}
    for line in r.stdout.splitlines():
        path, _, verdict = line.partition("\t")
        out[str(Path(path).resolve())] = verdict
    return out


# ── bookkeeping ───────────────────────────────────────────────────────


def load_expected() -> Dict[str, str]:
    if not EXPECTED.exists():
        return {}
    out = {}
    for line in EXPECTED.read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            name, _, verdict = line.partition("\t")
            out[name] = verdict
    return out


def load_known() -> Dict[str, Dict[str, Any]]:
    """KNOWN_SPLITS.json lists each distinct split once, with every corpus
    file that shows it; this turns that into file -> aspect -> groups."""
    if not KNOWN.exists():
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for entry in json.loads(KNOWN.read_text()).get("splits", []):
        for name in entry["files"]:
            per_file = out.setdefault(name, {"why": []})
            per_file[entry["aspect"]] = entry["groups"]
            per_file["why"] = sorted(set(per_file["why"]) | set(entry["why"]))
    return out


def groups_of(answers: Dict[str, str]) -> Dict[str, List[str]]:
    g: Dict[str, List[str]] = {}
    for who, ans in sorted(answers.items()):
        g.setdefault(ans, []).append(who)
    return g


def _lexer_names() -> List[str]:
    return sorted(forge.all_lexers())


def compact(who: List[str]) -> List[str]:
    """Witness names with every full row of lexers folded: the four pairs
    "L1 x P2" .. "L4 x P2" become "* x P2". KNOWN_SPLITS.json stores the
    folded form, so it records the groups, not twenty names per group."""
    lexers = set(_lexer_names())
    by_parser: Dict[str, set] = {}
    rest = []
    for w in who:
        if " x " in w:
            lx, px = w.split(" x ")
            by_parser.setdefault(px, set()).add(lx)
        else:
            rest.append(w)
    out = []
    for px, lxs in by_parser.items():
        if lxs == lexers:
            out.append(f"* x {px}")
        else:
            out += [f"{lx} x {px}" for lx in lxs]
    return sorted(out + rest)


def expand(names: List[str]) -> List[str]:
    """Inverse of compact."""
    out: List[str] = []
    for n in names:
        if n.startswith("* x "):
            out += [f"{lx} x {n[4:]}" for lx in _lexer_names()]
        else:
            out.append(n)
    return sorted(out)


def summarise(g: Dict[str, List[str]]) -> str:
    return "; ".join(f"{ans} <- {len(who)}: {', '.join(compact(who))}" for ans, who in g.items())


def passed_only_without(name: str, aspect: str) -> None:
    """The split needs a witness that is absent this run; nothing to say."""
    ok(True, f"{name} [{aspect}]")


def check_split(
    name: str,
    aspect: str,
    answers: Dict[str, str],
    known: Dict[str, Dict[str, Any]],
    seen_known: set,
) -> None:
    g = groups_of(answers)
    listed = known.get(name, {}).get(aspect)
    if listed is not None:
        # A witness that did not run this time (no javac) cannot be part
        # of the split it was recorded in.
        listed = {ans: [w for w in expand(who) if w in answers] for ans, who in listed.items()}
        listed = {ans: who for ans, who in listed.items() if who}
        seen_known.add((name, aspect))
        same = {k: sorted(v) for k, v in g.items()} == {k: sorted(v) for k, v in listed.items()}
        if len(g) == 1 and len(listed) > 1:
            ok(
                False,
                f"{name}: the known {aspect} split has healed -- remove it from KNOWN_SPLITS.json",
            )
        elif len(g) == 1:
            passed_only_without(name, aspect)
        else:
            ok(same, f"{name}: {aspect} split changed shape\n      now:   {summarise(g)}")
            if same:
                why = ", ".join(known.get(name, {}).get("why", [])) or "no reason given"
                findings.append(f"{name} [{aspect}] ({why}) {summarise(g)}")
        return
    ok(len(g) == 1, f"{name}: witnesses disagree on {aspect}\n      {summarise(g)}")


# ── main ──────────────────────────────────────────────────────────────


def main() -> int:
    files = corpus_files()
    expected = load_expected()
    known = load_known()
    seen_known: set = set()
    java = java_verdicts(files)
    unanimous: Dict[str, str] = {}

    print(
        f"  corpus: {len(files)} programs; witnesses: 20 forge pairs, syntax.py, "
        + ("Java" if java is not None else "Java SKIPPED (no javac)")
    )

    for f in files:
        name = rel(f)
        src = f.read_text()
        fa = forge_answers(src)

        # tokens: the four lexers, one stream
        lex_streams = {p.split(" x ")[0]: a.lex_canon for p, a in fa.items()}
        check_split(name, "tokens", lex_streams, known, seen_known)

        # verdict: every witness
        verdicts = {p: a.verdict for p, a in fa.items()}
        verdicts["syntax.py"] = incumbent_verdict(src)
        if java is not None:
            jv = java.get(str(f.resolve()))
            ok(jv is not None, f"{name}: Java printed no verdict")
            if jv is not None:
                verdicts["Java"] = jv
        check_split(name, "verdict", verdicts, known, seen_known)

        # where: refusal position, among witnesses that report one
        wheres = {p: a.where for p, a in fa.items() if a.where is not None}
        if wheres:
            check_split(name, "where", wheres, known, seen_known)

        # golden: the core grammar's answer, i.e. the forge's twenty pairs.
        # syntax.py and Java are held to it through the verdict check above,
        # so a dissent of theirs is reported once, not twice.
        forge_verdicts = {p: a.verdict for p, a in fa.items()}
        core = next(iter(forge_verdicts.values()))
        if len(set(forge_verdicts.values())) == 1:
            unanimous[name] = core
        if not WRITE:
            want = expected.get(name)
            ok(want is not None, f"{name}: no EXPECTED entry (run --write)")
            if want is not None:
                bad = sorted(w for w, v in forge_verdicts.items() if v != want)
                ok(
                    not bad,
                    f"{name}: {len(bad)} forge pair(s) differ from EXPECTED {want!r}: "
                    f"{', '.join(bad[:5])}",
                )
        if VERBOSE:
            print(f"    {name:48} {core}")

    for name, aspect in [(n, a) for n, aspects in known.items() for a in aspects if a != "why"]:
        ok(
            (name, aspect) in seen_known,
            f"KNOWN_SPLITS.json lists {name} [{aspect}], which is not in the corpus",
        )

    if WRITE:
        split = [rel(f) for f in files if rel(f) not in unanimous]
        if split:
            print("  --write refused: the forge pairs are split on " + ", ".join(split))
            return 1
        lines = [
            "# file<TAB>canonical verdict -- written by parser_agreement.py --write",
            "# from a run where all twenty forge pairs agreed. Edit only by re-running that.",
        ]
        lines += [f"{n}\t{v}" for n, v in sorted(unanimous.items())]
        EXPECTED.write_text("\n".join(lines) + "\n")
        print(f"  wrote {EXPECTED.relative_to(ROOT)} ({len(unanimous)} entries)")

    if findings:
        print(
            f"\n  Known splits ({len(findings)}), listed in tests/parser_corpus/KNOWN_SPLITS.json:"
        )
        for line in findings:
            print(f"    ! {line}")

    print(f"\n=== Parser agreement: {passed} passed, {failed} failed ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
