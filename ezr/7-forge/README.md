# Layer 7 — the forge

Four lexers, five parsers, twenty front ends, one language.
Four executors, four lanes, one answer.

EZR V3.0 arrived with a working front end and no specification of it.
`syntax.py` was the lexer, the parser and — by default — the only
statement of what the language accepted. That is a stable arrangement
right up to the moment somebody writes a second implementation, at
which point there is nothing to check it against.

So this layer writes the second implementation. And the third, and the
fourth, and then pairs every scanner with every parser and runs the
matrix against the same cases. Where twenty front ends, assembled from
nine independently derived parts, agree, the answer belongs to the
language. Where they split, the language never said, and the split is
the finding.

## Running it

```bash
python3 forge_test.py                 # verify the settled core (81 assertions)
python3 forge.py                      # continue from the current rulings
python3 forge.py --reset              # start again from unratified
python3 grammar_doc.py                # re-emit GRAMMAR.ebnf
python3 quantum.py --stanzas 8        # a rhyming corpus, put to the matrix
python3 quantum_test.py               # the rhyming corpus (35 assertions)
python3 merge.py program.ezr          # run a program down all four lanes
python3 merge.py -e "1 + 2" -v        # every lane, every stage
python3 ../tests/lanes_test.py        # the lanes, the executors and the merge
```

`--floor` sets how many programs must be fuzzed before convergence may
be declared; the budget doubles after every clean generation, so a
quiet run keeps getting more expensive to stay quiet.

## The pieces

| File | What it is |
|---|---|
| `contract.py` | tokens, AST, canonical forms — the only thing shared |
| `lexers.py` | L1 master regex · L2 hand scanner · L3 DFA table · L4 operator trie |
| `parsers.py` | P1 recursive descent · P2 Pratt · P3 shunting-yard · P4 Earley |
| `corpus.py` | golden cases from the published docs, probes, the fuzzer |
| `laws.py` | the seven properties every input must satisfy |
| `coverage.py` | asks stage 4 what it holds that stages 1–3 cannot say |
| `spec.py` | the ratified decision table |
| `forge.py` | the loop, and the four tiers of arbitration |
| `grammar_doc.py` | emits `GRAMMAR.ebnf` from the parser's own rules |
| `selfgen.py` | emits P5 from that grammar, so the code cannot drift from the document either |
| `repair.py` | localises a defect, synthesises a fix, adopts it only if it breaks nothing known |
| `selfheal.py` | puts a real defect back in the matrix and shows the loop closing on it |
| `quantum.py` | generation addressed by rhyme instead of seeded by a draw |
| `algebra.py` | the value rules every executor shares, and nothing about evaluation order |
| `executors.py` | X1 tree walker · X2 compiled closures · X3 stack machine · X4 bytecode |
| `lanes.py` | four pipelines, each one lexer, one parser and one executor end to end |
| `merge.py` | where the lanes meet: votes at tokens, tree, position and result |
| `ratified.json` | the rulings, with the reason for each |
| `ledger.json` | what the last run did, generation by generation |
| `counterexamples.json` | what the forge found; every later generation must still satisfy them |
| `repairs.json` | repairs in force, so a fix found once stays found |

## Why four of each, and then a fifth

Precedence lives somewhere different in each parser: in P1 it is the
call graph, in P2 a number in a table, in P3 a stack discipline, in P4
the shape of the rules. Four encodings of one claim. Agreement between
them is evidence about the claim; agreement between four copies of one
parser is evidence about copying.

The same goes for the scanners, and it paid immediately: the four
disagreed about which token kind carries a bare `<` before a single
test had been written, because nobody had ever written that down.

P5 is not a fifth opinion. `selfgen.py` emits it from the ratified
grammar and nothing else, so it is the witness that decides whether
`GRAMMAR.ebnf` *describes* the four or merely resembles them. When it
disagrees, exactly one of two things is wrong — the grammar is not what
the parsers implement, or a parser is not what the grammar says — and
the forge has to say which. Consensus cannot produce that finding,
which is why the matrix is four by five rather than four by four. A
generator that cannot emit is itself a finding, and the other four
still have something to say, so it does not take the matrix down with
it.

## Rhyming couplets, and what they are for

`corpus.py`'s fuzzer is reproducible in the sense that the same seed
replays the same draws. That is not the same as addressable: case
`fz-well-137` cannot be regenerated without regenerating the hundred
and thirty-six before it, and its text is a fact about CPython's
Mersenne Twister as much as about EZR.

`quantum.py` generates the same kind of material with no random source.
Every construction site is collapsed by its *address* -- the path from
the root, hashed with a hand-written FNV-1a -- so the choice at
`2/cond/left` is a function of the phrase and that path and nothing
else. Any subtree can be regenerated alone, and `PYTHONHASHSEED` cannot
change the corpus.

Programs come out in **couplets**: two programs whose last `k` token
kinds match, and whose skeletons do not. Same ending, different sense.
That constraint is worth having because a foot is a claim about
*tokenization*, and it is the one part of a program all four scanners
must compute identically for anything downstream to mean what it says
-- this README records the four splitting over a bare `<` before a test
existed. `law_rhyme` asks that question directly.

When no answering program is found inside the budget, the couplet
**withholds**, the same thing the forge does below its fourth tier and
for the same reason.

## Four lanes, run to a value

The matrix tests parts. It pairs every lexer with every parser, and it
stops at the tree: nothing it builds is ever run. The lanes are the
other shape. Each fixes one lexer, one parser and one executor together
and carries a program all the way to a value, so four independent
pipelines answer the same question and only meet at the end:

```
Lane 1   L1-regex-master  ->  P1-recdescent  ->  X1-walker
Lane 2   L2-handrolled    ->  P2-pratt       ->  X2-closures
Lane 3   L3-dfa           ->  P3-shunting    ->  X3-stack
Lane 4   L4-trie          ->  P4-earley      ->  X4-bytecode
```

The four executors share `algebra.py` -- what an operator does to two
values, what a call is worth, how an outcome is written down -- and
nothing else. When each operand is evaluated, which arm of an `if`
runs, where a Z stops the work and how deep a call may go is strategy,
and each executor keeps it somewhere different: in the call graph, in
closures fixed at compile time, in the order tasks are pushed, in where
the jumps go.

`merge.py` asks at every boundary whether the lanes agree, and settles
it with the consensus tier below: at least three of the four, and two
thirds of those answering.

| Lanes agreeing | Answer |
|---|---|
| 4 of 4 | the value, at its own confidence |
| 3 of 4 | the majority's value, confidence scaled by 3/4, the dissenting lane named |
| 2 of 4 or fewer | withheld: Z, and where the lanes split |

Agreement never *raises* confidence. Four implementations agreeing says
the implementations are sound, not that the program earned trust it
did not have.

The swap is the dynamic part, and it only runs when a lane dissents.
The dissenting lane's parser is handed the majority's tokens and its
executor the majority's tree; whichever part still disagrees on clean
input is the one that drifted, and the report names that part rather
than the whole lane. P5 reads the majority's tokens as a witness: it
says whether the tree the lanes agreed on is the one `GRAMMAR.ebnf`
describes.

## The four tiers

A disagreement is not settled by a vote if something better is
available.

1. **Doctrine** — SEMANTICS.md or PIPELINE.md already says. A vote
   cannot overturn a published document, so this tier runs first.
2. **Coverage** — stage 4 demonstrably holds something stages 1–3
   cannot express. Consensus cannot see this: every front end can
   agree, sincerely, on a limitation the rest of the language does not
   have.
3. **Finding** — one of the laws settles it. A law violation is
   evidence about the language, not about one implementation.
4. **Consensus** — VOWELS.md's oracle, unchanged: at least ⌊π⌋ = 3
   agreeing and at least two thirds of those that answered.

Below all four it withholds and says so. Inventing an answer there
would teach the corpus a fact nobody verified.

See `../CORE.md` for what the forge settled and why.
