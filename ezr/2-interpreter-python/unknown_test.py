#!/usr/bin/env python3
"""
unknown_test.py — Ever / Tapestry, no null across the C/Python boundary.

The Python half of 0-atom-c/unknown_test.c. It proves that:

  * EValue.UNKNOWN carries a reason and a Span, is worth 0, equals
    nothing, and refuses to hand back a Python value;
  * every way a raw null used to get in -- None, a `null` literal, an
    SQL NULL, an empty or missing buffer, an unknown wire tag, a None
    inside a list -- is refused or becomes UNKNOWN with its reason;
  * the particle ABI (abi.py) refuses to send or accept a particle that
    claims a value and carries none;
  * Python and C write byte-identical UNKNOWNs, and each reads the
    other's: C's lifters are run as a process and their bytes compared
    with what Python's lifters produce for the same inputs.

The C half is skipped, not failed, when gcc is absent.

Codric Enterprise
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from abi import E_PARTICLE_SIZE, decode, encode  # noqa: E402
from evalue import (  # noqa: E402
    EV_INLINE_MAX,
    EV_SPAN_SOURCE_MAX,
    EValue,
    EvType,
    NullAtBoundary,
    Span,
    UnknownValue,
    deserialise,
    lift_literal,
    lift_python,
    lift_sql_row,
    serialise,
)
from ever import State, e_val, e_z  # noqa: E402

passed = failed = 0

#: The raw null these tests push at the boundary on purpose. Typed Any so
#: the type checker lets the test do what the API exists to refuse.
NULL: Any = None


def field(v: Optional[EValue]) -> EValue:
    """A record field or list item that the test knows is there."""
    assert v is not None
    return v


def ok(name: str, cond: bool) -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        print(f"  ✗ {name}")


def refuses(fn, exc=NullAtBoundary) -> bool:
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


def unknown_with(v: EValue, reason: str, source: str, line: int, col: int) -> bool:
    return (
        v.is_unknown
        and v.ev_reason == reason
        and v.ev_span == Span(source, line, col)
        and v.confidence == 0
    )


print("\n=== Ever — no null across the C/Python boundary ===\n")

print("The UNKNOWN variant")
u = EValue.unknown("sensor offline", Span("feed.csv", 4, 2))
ok("tag is UNKNOWN (8), same number as C", u.ev_tag == EvType.UNKNOWN == 8)
ok("carries its reason and span", unknown_with(u, "sensor offline", "feed.csv", 4, 2))
ok(
    "is worth 0, whatever it is given",
    EValue(EvType.UNKNOWN, ev_reason="x", ev_span=Span("s"), confidence=256).confidence == 0,
)
ok("neither scalar nor composite", not u.is_scalar and not u.is_composite)
ok(
    "describes itself with reason and span",
    u.describe() == "unknown (sensor offline at feed.csv:4:2)",
)
ok(".native raises UnknownValue, not None", refuses(lambda: u.native, UnknownValue))
ok("no reason is refused", refuses(lambda: EValue.unknown("", Span("s"))))
ok("a blank reason is refused", refuses(lambda: EValue.unknown("   ", Span("s"))))
ok("no span is refused", refuses(lambda: EValue.unknown("why", NULL)))
ok("a span needs a source", refuses(lambda: Span(""), ValueError))
ok("a span's line and column are not negative", refuses(lambda: Span("s", -1, 0), ValueError))

print("\nEquality: an unknown equals nothing")
u2 = EValue.unknown("sensor offline", Span("feed.csv", 4, 2))
ok("unknown != the same unknown", u != u2)
ok("unknown != itself", not (u == u))
ok("unknown != void", u != EValue.void() and EValue.void() != u)
ok("unknown != 0", u != EValue.from_int(0))
ok('unknown != ""', u != EValue.from_text(""))

print("\nRaw nulls in Python are refused or become UNKNOWN")
ok("lift_python(None) refused", refuses(lambda: lift_python(NULL)))
try:
    lift_python({"a": [1, {"b": NULL}]})
    where = ""
except NullAtBoundary as exc:
    where = str(exc)
ok("a nested None is refused, naming where", where.startswith("None at $.a[1].b"))
ok("an EValue passes through lift_python", lift_python(u) is u)
ok("a list holding None is refused", refuses(lambda: EValue.from_list([EValue.from_int(1), NULL])))
ok("a record holding None is refused", refuses(lambda: EValue.from_record({"a": NULL})))
ok("serialise(None) refused", refuses(lambda: serialise(NULL)))
ok("deserialise(None) refused", refuses(lambda: deserialise(NULL)))
ok("deserialise(b'') refused", refuses(lambda: deserialise(b"")))
ok(
    "an unknown wire tag is refused, not read as void",
    refuses(lambda: deserialise(bytes([99])), ValueError),
)
ok("lift_literal(None) refused", refuses(lambda: lift_literal(NULL)))
for s in ["null", "nil", "None", "NULL", "undefined", "nullptr"]:
    ok(
        f"lift_literal({s!r})",
        unknown_with(lift_literal(s), f"null literal {s!r}", "<literal>", 0, 0),
    )
ok(
    "lift_literal('') is unknown",
    unknown_with(lift_literal(""), "empty literal", "<literal>", 0, 0),
)
ok(
    "lift_literal keeps a span it is given",
    unknown_with(
        lift_literal("nil", span=Span("app.rb", 9, 14)), "null literal 'nil'", "app.rb", 9, 14
    ),
)
ok("a real literal is untouched", lift_literal("42").ev_int == 42)

row = lift_sql_row(["id", "name", "score"], ["7", None, "null"])
ok("SQL: a present value lifts as before", field(row.get("id")).ev_int == 7)
ok(
    "SQL NULL is unknown, with column",
    unknown_with(field(row.get("name")), "SQL NULL in column 'name'", "sql", 0, 2),
)
ok(
    "SQL text 'null' is unknown too",
    unknown_with(field(row.get("score")), "null literal 'null'", "sql", 0, 3),
)
row2 = lift_sql_row(["id", "name", "score"], ["7", None, "null"], source="orders.db", row=12)
ok(
    "lift_sql_row names source and row",
    unknown_with(field(row2.get("name")), "SQL NULL in column 'name'", "orders.db", 12, 2),
)
ok(
    "void is still a deliberate value",
    EValue.void().native is None and not EValue.void().is_unknown,
)

print("\nThe wire (Python alone)")
back, used = deserialise(serialise(u))
ok("unknown survives a round trip", unknown_with(back, "sensor offline", "feed.csv", 4, 2))
ok("and consumes exactly its bytes", used == len(serialise(u)))
lst = EValue.from_list([EValue.from_int(1), u])
lback, _ = deserialise(serialise(lst))
ok("inside a list", unknown_with(field(lback.get(1)), "sensor offline", "feed.csv", 4, 2))
whole = serialise(u)
ok(
    "every cut-off UNKNOWN is refused, not read on",
    all(refuses(lambda k=k: deserialise(whole[:k]), ValueError) for k in range(1, len(whole))),
)
rback, _ = deserialise(serialise(row))
ok(
    "inside a record",
    unknown_with(field(rback.get("name")), "SQL NULL in column 'name'", "sql", 0, 2),
)
long_u = EValue.unknown("é" * 100, Span("s" * 199, 1, 1))
lu, _ = deserialise(serialise(long_u))
ok("source cut to 63 bytes", len(lu.ev_span.source.encode()) == EV_SPAN_SOURCE_MAX)
ok(
    "source and reason fit C's 192 bytes",
    len(lu.ev_span.source.encode()) + 1 + len(lu.ev_reason.encode()) + 1 <= EV_INLINE_MAX,
)
ok("reason cut at a character", lu.ev_reason == "é" * (len(lu.ev_reason.encode()) // 2))

print("\nThe particle ABI refuses a claim about nothing")
ok("encode(None) refused", refuses(lambda: encode(NULL)))
ok("a CONFIDENT particle holding None is refused", refuses(lambda: encode(e_val("n", None, 200))))
ok(
    "a CONFIDENT particle holding a list is refused",
    refuses(lambda: encode(e_val("l", [1, 2], 200))),
)
ok("a Z particle still crosses", decode(encode(e_z("z", "not measured"))).state == State.Z)
ok("a known value still crosses", decode(encode(e_val("k", 5, 200))).value == 5)
buf = bytearray(encode(e_z("z", "not measured")))
buf[208:212] = struct.pack("<i", int(State.CONFIDENT))  # state @ 208
ok("C sending CONFIDENT with no value is refused", refuses(lambda: decode(bytes(buf))))
ok("decode(None) refused", refuses(lambda: decode(NULL)))
ok("buffer size unchanged", len(encode(e_val("k", 5, 200))) == E_PARTICLE_SIZE)

print("\nPython and C agree byte for byte")
gcc = shutil.which("gcc")
if not gcc:
    print("  SKIP: gcc not found; the C half is 0-atom-c/unknown_test.c")
else:
    atom = HERE.parent / "0-atom-c"
    with tempfile.TemporaryDirectory() as tmp:
        exe = str(Path(tmp) / "unknown_probe")
        r = subprocess.run(
            [
                gcc,
                "-std=c99",
                "-Wall",
                "-o",
                exe,
                str(atom / "unknown_test.c"),
                str(atom / "evalue.c"),
                str(atom / "tapestry.c"),
                "-lm",
            ],
            capture_output=True,
            text=True,
        )
        ok("C probe compiles", r.returncode == 0)
        if r.returncode == 0:

            def c_echo(b: bytes) -> bytes:
                return subprocess.run([exe, "--echo"], input=b, capture_output=True).stdout

            samples = [
                u,
                long_u,
                lst,
                row,
                row2,
                EValue.unknown("café — 日本", Span("über.csv", 2**31 - 1, 0)),
            ]
            for i, v in enumerate(samples):
                ok(
                    f"C reads and rewrites Python's bytes unchanged ({v.describe()[:40]})",
                    c_echo(serialise(v)) == serialise(v),
                )
            ok(
                "C reads an empty buffer as unknown, which Python decodes",
                unknown_with(
                    deserialise(c_echo(b""))[0], "empty buffer at ev_deserialise", "c", 0, 0
                ),
            )
            ok(
                "C reads a cut-off UNKNOWN as truncated, within bounds",
                unknown_with(
                    deserialise(c_echo(serialise(u)[:5]))[0],
                    "truncated UNKNOWN at ev_deserialise",
                    "c",
                    0,
                    0,
                ),
            )
            ok(
                "C reads an unknown tag as unknown, not void",
                unknown_with(
                    deserialise(c_echo(bytes([99])))[0],
                    "unrecognised tag 99 at ev_deserialise",
                    "c",
                    0,
                    0,
                ),
            )

            emitted = subprocess.run([exe, "--emit"], capture_output=True, text=True).stdout.split()
            emitted_b = [bytes.fromhex(h) for h in emitted]
            ok("C emitted every lifter's output", len(emitted_b) == 7)
            if len(emitted_b) == 7:
                (
                    c_text_null,
                    c_lit_null,
                    c_lit_empty,
                    c_nullptr,
                    c_row,
                    c_row_at,
                    c_nobuf,
                ) = emitted_b
                ok(
                    "C ev_text(NULL) is unknown in Python",
                    unknown_with(
                        deserialise(c_text_null)[0], "NULL pointer passed to ev_text", "c", 0, 0
                    ),
                )
                ok(
                    "C ev_from_literal(NULL) is unknown in Python",
                    unknown_with(
                        deserialise(c_lit_null)[0],
                        "NULL pointer passed as a literal",
                        "<literal>",
                        0,
                        0,
                    ),
                )
                ok("C and Python lift '' identically", c_lit_empty == serialise(lift_literal("")))
                ok(
                    "C and Python lift 'nullptr' identically",
                    c_nullptr == serialise(lift_literal("nullptr")),
                )
                ok(
                    "C and Python lift an SQL row with NULLs identically",
                    c_row == serialise(lift_sql_row(["id", "name", "score"], ["7", None, "null"])),
                )
                ok(
                    "... and with a source and row",
                    c_row_at
                    == serialise(
                        lift_sql_row(
                            ["id", "name", "score"], ["7", None, "null"], source="orders.db", row=12
                        )
                    ),
                )
                ok(
                    "C deserialise(NULL) is unknown in Python",
                    unknown_with(
                        deserialise(c_nobuf)[0], "NULL buffer at ev_deserialise", "c", 0, 0
                    ),
                )

print(f"\n=== Unknown (Python): {passed} passed, {failed} failed ===")
sys.exit(0 if failed == 0 else 1)
