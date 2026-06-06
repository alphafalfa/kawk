# kawk

A small, K-flavored language that transpiles to AWK.

AWK has no `reverse`, no `sum`, no `sort`, no `map` — so you spell every one of
them as a loop. kawk gives them back as single glyphs, so the `for` loop goes
back to meaning *a loop* instead of "AWK forgot to ship this primitive."

Evaluation is **right-to-left with no precedence** — every operator just eats
the thing on its right. `kawk.py` decodes a `.kk` program into AWK; the `kawk`
wrapper hands that to `awk`, which does the actual work.

## A taste

Fizzbuzz, 46 bytes, and not a `for` in sight:

```
{x%3?x%5?x:"Buzz":x%5?"Fizz":"FizzBuzz"}'1+!$0
```

Read it back to front: `!$0` makes the range `0..n-1`, `1+` shifts it to
`1..n`, `'` maps the lambda across it, and the lambda is a nested ternary.

| task | kawk | the AWK you'd otherwise write |
|---|---|---|
| sum `1..n` | `+/1+!$0` | `{for(;$0;)a+=$0--}$0=a` |
| factorial | `*/1+!$0` | `{for(x=1;$0;)x*=$0--}$0=x` |
| reverse a string | `\|$0` | a recursive helper, every time |
| collatz trajectory | `{1<x}{x%2?1+x*3:x/2}\$0` | `for(x=$1;x>1;)...` |

## Running it

Needs `python3` and any `awk`.

```sh
chmod +x kawk

./kawk prog.kk            # run, reading stdin
./kawk prog.kk data.txt   # run on a file
echo 15 | ./kawk prog.kk  # or pipe
./kawk -d prog.kk         # dump the generated AWK (stderr) and run
```

`kawk.py` stands alone as the pure transpiler if you only want the AWK:

```sh
python3 kawk.py prog.kk   # prints AWK to stdout
python3 kawk.py -         # read a program from stdin
```

## The language (what runs today)

**How to read it.** Right-to-left, no precedence. `2*3+4` is `2*(3+4)`.
Use `( ... )` to override.

**Character classes**

| class | role | example |
|---|---|---|
| digits | numeric literal | `42` |
| lowercase | a variable | `a` `x` |
| `"..."` | string literal | `"Fizz"` |
| symbols | verbs / adverbs | `+` `!` `/` |
| `{ ... }` | a lambda; `x` is its argument | `{x*x}` |
| `$0 $1 ...` | input fields | `$0` |

**Verbs** (each has a monadic and a dyadic face, K-style)

| glyph | monadic | dyadic |
|---|---|---|
| `+ - * / %` | | arithmetic |
| `< > =` | | compare (`=` is equality) |
| `& \|` | (`\|` = reverse) | logical and / or |
| `!` | iota: `0..n-1` | |
| `#` | length / count | |

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
- a bare expression is a **filter**: prints `$0` when it's truthy.
- scalar results print once; vector results print one per line.

## Roadmap (designed, not yet built)

- **UPPERCASE = AWK's special vars**: `N`→`NF`, `R`→`NR`, `F`→`FS`, `O`→`OFS`, …
- More builtins on symbols: `~[re;rep]` for gsub, `_` for split, `.name` escape for the long tail (`.substr`, `.sprintf`).
- Negative indexing: `$-1` for the last field.
- `BEGIN`/`END` as a positional `^{ ... }` (first block / last block).
- Named functions (lambdas exist; named, recursive ones don't yet).
- A two-variable `while` that closes over the seed — the one thing standing between here and palindrome-build.

## Name

`K` + `AWK`. Right-to-left, so the K leaks in from the left.

## License

_Your call._
