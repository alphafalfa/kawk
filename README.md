# kawk

A small array language: K's ergonomics on AWK's data model.

AWK has no `reverse`, no `sum`, no `sort`, no `map` — so you spell every one of
them as a loop. kawk gives them back as single glyphs, so the `for` loop goes
back to meaning *a loop* instead of "AWK forgot to ship this primitive."

Evaluation is **right-to-left with no precedence** — every operator just eats
the thing on its right. `kawk.py` interprets a `.kk` program directly: it keeps
AWK's *data model* (records, fields, the implicit per-line loop, filter
patterns) but walks the syntax tree itself rather than compiling to anything.
The original AWK backend is retired to `--emit-awk` (see below).

## A taste

Fizzbuzz, 44 bytes, and not a `for` in sight:

```
{x%3?x%5?x:"Buzz":x%5?"Fizz":"FizzBuzz"}'!$0
```

Read it back to front: `!$0` makes the range `1..n`, `'` maps the lambda
across it, and the lambda is a nested ternary.

| task | kawk | the AWK you'd otherwise write |
|---|---|---|
| sum `1..n` | `+/!$0` | `{for(;$0;)a+=$0--}$0=a` |
| factorial | `*/!$0` | `{for(x=1;$0;)x*=$0--}$0=x` |
| reverse a string | `\|$0` | a recursive helper, every time |
| collatz trajectory | `{1<x}{x%2?1+x*3:x/2}\$0` | `for(x=$1;x>1;)...` |
| sum a CSV row | `+/","_$0` | `{n=split($0,a,",");for(i=1;i<=n;i++)s+=a[i]}$0=s` |

## Running it

Needs `python3`. (No `awk` required anymore — the interpreter is the engine.)

```sh
chmod +x kawk

./kawk prog.kk            # run, reading stdin
./kawk -F, prog.kk        # set the field separator (read CSV)
./kawk prog.kk data.txt   # run on a file
echo 15 | ./kawk prog.kk  # or pipe
./kawk -d prog.kk         # also dump the would-be AWK (retired backend) to stderr
```

`kawk.py` runs the program directly (`-F sep` and `-e '<program>'` both work). The
old transpiler is frozen at the point we switched engines and lives on as a party
trick — `--emit-awk` prints the AWK a program *would* have compiled to. It predates
the special vars and the text-era features, so it only knows the core:

```sh
python3 kawk.py prog.kk             # interpret, input on stdin
python3 kawk.py -e '+/!$0'          # interpret a literal program
python3 kawk.py --emit-awk prog.kk  # print the retired AWK translation
```

## The language (what runs today)

**How to read it.** Right-to-left, no precedence. `2*3+4` is `2*(3+4)`.
Use `( ... )` to override.

**Character classes**

| class | role | example |
|---|---|---|
| digits | numeric literal | `42` |
| lowercase | a variable | `a` `x` |
| UPPERCASE | an AWK special var | `N` `R` `F` `O` |
| `"..."` | string literal | `"Fizz"` |
| symbols | verbs / adverbs | `+` `!` `/` |
| `{ ... }` | a lambda; `x` is its argument | `{x*x}` |
| `$0 $1 ...` | input fields | `$0` |

**Special vars** (uppercase — read them anywhere, set `F`/`O` to configure I/O)

| var | is | use |
|---|---|---|
| `N` | `NF` | field count of this record |
| `R` | `NR` | record number — `R>1` skips a header |
| `F` | `FS` | input separator; set `F:","` (takes effect on the *next* record) or pass `-F,` |
| `O` | `OFS` | output separator; set `O:","` to join fields with commas |

**Verbs** (each has a monadic and a dyadic face, K-style)

| glyph | monadic | dyadic |
|---|---|---|
| `+ - * / %` | | arithmetic |
| `< > =` | | compare (`=` is equality) |
| `& \|` | (`\|` = reverse) | logical and / or |
| `!` | iota: `1..n` (1-based, AWK-style) | |
| `#` | length / count | |
| `_` | split a string on `FS` → vector | `d_s`: split `s` on separator `d` |
| `@` | | `a@i`: index element `i` of array `a` |
| `~` | `~[re;rep]s`: gsub (`~[re]s` strips) | `a~b`: does `a` match regex `b`? → `1`/`0` |
| `,` | enlist: wrap as a 1-element list (a row) | join: concatenate, flattening one level |

**Adverbs** — iteration lives here, not in a loop statement

| form | meaning |
|---|---|
| `f/v` | **fold** — reduce a vector (`+/` is sum, `*/` is product) |
| `f'v` | **each** — map a verb or `{lambda}` over a vector |
| `{c}{f}\s` | **while-scan** — apply `f` to `s` while `c` holds; keep the whole trajectory |
| `{c}{f}/s` | **while-over** — same, but keep only the final value |

So every `for` loop is one of four shapes: reduce (`/`), map (`'`), or
while (`\` `/`).

**Control & output**

- `c?t:f` — ternary, right-associative, nests.
- `a:expr` — assignment. `$0:expr` assigns to `$0` and prints the value.
- `s1;s2;…` — **statements**, run left-to-right per record. The last one is
  the output; earlier ones are setup (bindings).
- `^expr` — an **END** statement: runs once after the last record and prints its
  value. When a program has an `^`, the per-record statements go silent (pure
  accumulation), so you fold the whole stream and print a single result. It's the
  record-axis `fold` to the per-record loop's implicit `each`.
- a bare expression is a **filter**: prints `$0` when it's truthy. (After a
  `;`, a trailing bare expression prints its *value* instead — you're past
  filtering and into computing.)
- scalar results print once; vector results print one per line.

**Storable vectors.** Bind a vector to a variable and it becomes an array you
can fold, map, reverse, count, and index — the same adverbs, now over named
storage:

```
a:$;+/a              sum of this row's fields           (bind $, then fold)
a:","_$0;a@3         3rd comma-separated value          (split, then index)
a:!$0;|a             1..n reversed                      (bind a range, reverse it)
a:$;b:|a;+/b         fold a reversed copy               (chain bindings)
```

A variable becomes an array the moment it's assigned a vector (`$`, `!n`, a
split, a map, …); used anywhere else it's an ordinary scalar.

**Regex & text.** Patterns are plain strings (so there's no `/re/` literal and
no divide-vs-regex lexer puzzle — a string already holds the regex). `~` matches;
`~[…]` substitutes:

```
$0~"err"             grep: keep lines matching err     (a bare match filters)
$0~"^[0-9]+$"        keep fully-numeric lines
~["o";"0"]$0         gsub: every o -> 0
~["[0-9]";"#"]$0     gsub a character class
~["o";"<&>"]$0       & in the replacement = the whole match
~["a"]$0             one arg = strip (delete the matches)
~["o";"0"]$          broadcasts: gsub each field of the row
```

Putting it together — sum a `$`-prefixed price column past the header, one line out:

```
s:R>1?s+~["[$]"]$2:s;^s        # strip $, coerce, accumulate, print once; -F,
```

**Folding the stream.** `^` turns per-record accumulation into a single result —
the classic END-block reductions, terse:

```
^R                   count the records
s:s+$0;^s            sum a column
c:c+$0~"err";^c      count lines matching err
m:N>m?N:m;^m         widest row's field count
```

**Matrices.** Collect rows with `,` (join) + `,` (enlist), `^`-print the result
as a table. The adverbs you already have *are* the matrix algebra — fold reduces
down the rows, each maps over them, reverse flips row order, `@` picks a row:

```
m:m,,$;^m            echo the table back               (m,,$ = add this row)
m:m,,$;^+/m          column sums  (fold + down rows)
m:m,,$;^{+/x}'m      row sums     (each row -> its sum)
m:m,,|$;^m           reverse every row, keep the table
m:$0~"y"?m,,$:m;^m   collect only matching rows
m:m,,$;^,/m          raze the matrix flat
m:m,$2;^m            collect one column (atoms join flat)
```

An untouched `m` acts as an empty list the first time you join into it, so no
seed line is needed.

## Roadmap (designed, not yet built)

- `BEGIN` setup as a leading `^{ … }` (END's mirror) — e.g. set `F` before the
  first record without the `-F` flag.
- A **transpose** verb — the one matrix move the adverbs don't give for free.
- `/re/` **literal sugar** for bare-pattern grep (`/err/` vs `$0~"err"`). Low
  priority: string patterns already cover the power, and this is the one feature
  that needs the three-state `/` lexer, so it has to earn its complexity.
- The long tail on a `.name` escape: `.substr`, `.upper`, `.sprintf`, …
- Negative indexing: `$-1` for the last field.
- Named functions (lambdas exist; named, recursive ones don't yet).
- A two-variable `while` that closes over the seed — the one thing standing
  between here and palindrome-build.
- **The text era** (well underway): coercion, `FS`/`OFS`, regex, `^` END, and
  `,` matrices all work — mixed-CSV filter / map / reduce / collect runs end to
  end. String builtins (`.substr`, `.upper`, `.sprintf`) are the main gap left.

## Tests

`./test.sh` runs the behavioral suite (every program through the interpreter,
output checked). `./test.sh -v` also dumps the retired AWK translation for any
failure. Keep it
green.

## Name

`K` + `AWK`. Right-to-left, so the K leaks in from the left.

## License

_Your call._
