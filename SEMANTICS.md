# kawk semantics

The `kawk -h` glyph table tells you *what each symbol does*. This page tells you
the three *rules of the game* that make the table make sense — the things that
bite you if you don't know them. Every example is a real program you can run:

    printf 'INPUT' | ./kawk -e 'PROGRAM'      # -> OUTPUT

kawk runs your program once per input line (AWK's model). `$0` is the line, `$`
is its fields as a vector, `N` the field count. A bare expression prints; `a:e`
assigns; `$0:e` assigns and prints.

---

## Rule 1 — right to left, and verbs are greedy

There is **no operator precedence**. Evaluation runs right to left, and every
verb grabs *everything to its right* as its argument. This is the single biggest
source of surprise. When in doubt, run `./kawk --explain 'PROG'` — it prints the
program fully parenthesized so you can see what grabbed what.

    echo 5 | ./kawk -e '$0:2*3+4'      -> 14     not 10:  2*(3+4), not (2*3)+4
    ./kawk --explain '2*3+4'           -> (2*(3+4))

The same greed applies to monadic verbs and dot-builtins — they eat rightward:

    ./kawk --explain '.o"d"-64'        -> (.o("d"-64))    .o ate the subtraction!
    echo 5 | ./kawk -e '$0:(.o"d")-64' -> 36              parenthesize to stop it
    echo 5 | ./kawk -e '$0:.o"d"-64'   -> (ord of "d"-64), almost never what you want

Folds and filters are greedy too. If a `#` or `+/` swallows more than you meant,
fence it with parens:

    ./kawk --explain '(#"xx")*#"yyyy"' -> ((#"xx")*(#"yyyy"))   2*4 = 8, fenced
    ./kawk --explain '#"xx"*#"yyyy"'   -> (#("xx"*(#"yyyy")))   # ate the whole product

**Rule of thumb:** if a verb should act on only *part* of what's to its right,
put parens around that part. `--explain` settles every argument.

---

## Rule 2 — truthiness: 0 and "" are false, everything else is true

One rule, used everywhere — in `?:`, in bare-expression filtering, and in `#`.
A "mask" is just any vector read through this rule.

    echo 5 | ./kawk -e '$0:0?"yes":"no"'   -> no      0 is false
    echo 5 | ./kawk -e '$0:5?"yes":"no"'   -> yes     any nonzero is true
    printf '' | ./kawk -e '$0:""?"yes":"no"' -> no    "" is false

Because of this, a mask doesn't have to be 0/1 — any vector works, tested
element by element:

    echo 10 | ./kawk -e 'a:!$;$0:(a%3)#a'  -> 1 2 4 5 7 8 10   keeps where a%3 != 0

This is also why the truth machine halts on 0: the loop condition `{x}` reads the
seed through this same rule, so seed 0 fails immediately.

---

## Rule 3 — arity picks the face

Most glyphs have two meanings: one with an argument only on the right (monadic),
one with arguments on both sides (dyadic). kawk chooses by counting operands —
never by guessing. So one symbol safely carries two jobs.

    echo hello | ./kawk -e '$0:#$0'          -> 5        # monadic = count/length
    echo 'a b c d' | ./kawk -e '$0:(0=!N%2)#!N' (dyadic = compress, mask on left)

    echo '3 1 2' | ./kawk -e '$0:|$'         -> 2 1 3    | monadic = reverse
    echo '3 1 2' | ./kawk -e '$0:&/$'        (| dyadic in a fold = max)

`#` actually has three faces, all chosen by what sits to its left:

    #v          nothing on the left   -> count        ( #"hello" -> 5 )
    mask # v    a vector on the left  -> compress      ( keep where mask is true )
    {pred} # v  a lambda on the left  -> filter        ( keep where pred holds )

Nothing collides because "nothing", "a vector", and "a {lambda}" are three
distinguishable shapes to the parser.

---

## The fold, and the scan

`+/v` folds a vector to one value:

    echo '1 2 3 4' | ./kawk -e '$0:+/$'   -> 10            fold: final value

`\` is the **seeded while-scan** — `{cond}{step}\seed`. Starting from the seed,
it applies the step while the condition holds, and returns the whole trajectory.
Its sibling `{cond}{step}/seed` (seeded *over*) returns only the final value.

    echo 5 | ./kawk -e '$0:{x}{x-1}/$0'   -> 0             loop, final value
    echo 5 | ./kawk -e '$0:{x}{x-1}\$0'   -> 5 4 3 2 1 0    loop, whole path

So `/` keeps the end, `\` keeps every step — the same distinction whether folding
a vector or iterating a seed.

When you use a loop only for a side effect (e.g. a `.p` tap) and discard the
result, `/` and `\` behave identically — pick `/`, it reads as "loop".

> Note: kawk's `\` does *not* yet do running-accumulate over an existing vector
> (`+\v` for running sums). The only scan is the seeded while-form above. A
> vector-scan is a candidate future primitive.

---

## Implicit x

A one-argument lambda may drop `x` when there's exactly one provable hole:

    {>9}    means {x>9}      (left hole)
    {.u}    means {.u x}     (lone monadic)
    {~"e"}  means {x~"e"}    (left operand of a dyad)

A named `x` is left alone (`{x*x}`). A body with holes on *both* sides (`{*}`,
`{>}`) is ambiguous and rejected with an error — kawk never guesses.

Known gap: implicit x does not reach inside the bracket forms, so `{~["o"]}`
does not desugar. Use a named `x` there: `{x~["o"]}`.

---

## Edges worth knowing

- **Bare string literals don't print.** `'"hi"'` outputs nothing; a bare *number*
  or vector does print. To emit a string, assign it: `$0:"hi"`. (This is why the
  truth machine prints via `.p` and the seed, not a bare literal.)

      printf '' | ./kawk -e '"hi"'      -> (nothing)
      printf '' | ./kawk -e '$0:"hi"'   -> hi

- **A bare lambda can't start a line** — except the two blessed forms, the
  while-scan `{c}{s}\seed` / `{c}{s}/seed` and the predicate-filter `{pred}#v`.
  Anywhere else, give the lambda a home: `$0:{...}` or wrap in parens.

- **`-F ''` makes characters be fields.** With an empty field separator, `$` is
  the vector of characters and `N` is the string length, so every fields idiom
  works per-character with no `""_$0` prefix.

      echo banana | python3 kawk.py -F '' -e '$0:.j=$'   -> ban    (distinct chars)

  (Wrapper quirk: `./kawk -F '' -e '...'` mis-parses the bare `-F` before `-e`.
  Use `python3 kawk.py` for inline char-mode programs, or put the program in a
  `.kk` file — `./kawk -F '' prog.kk` works fine.)

- **Errors fail clean, never with a Python traceback.** Trailing operators,
  divide/modulo by zero, sqrt of a negative, and absurd numbers all give a
  `kawk:`-prefixed message. (Set `KAWK_PATIENCE=0` for... a different experience.)

- **`.p` / `.P` are taps:** print the value and pass it through unchanged. `.p`
  to stdout, `.P` to stderr (debug, stays out of real output). Drop one mid-
  expression to see what's flowing through that point without changing the result.

---

## When stuck

1. `./kawk --explain 'PROG'` — shows the grouping. Fixes 90% of surprises.
2. Peel the onion — run each inner sub-expression as its own program, outward in,
   and watch where the value stops matching what you expected.
3. Splice in `.P` — tap a sub-expression to stderr to see it stream by.
4. Shrink the input — feed a tiny vector you can read element by element.
