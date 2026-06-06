# How kawk processes a program

Every kawk program runs the same pipeline:

```
source -> lex -> group -> fold-adverbs -> parse (right-to-left) -> emit AWK -> awk runs it
```

Two worked examples follow. The point of each stage is called out, because the
*why* is the whole design.

---

## 1. Fizzbuzz

```
{x%3?x%5?x:"Buzz":x%5?"Fizz":"FizzBuzz"}'1+!$0
```

### The one-sentence version

Build the numbers `1..n`, then apply a function to each. There is no loop here
because "do this to each of these" was never a loop — it's a *map*. AWK just
lacks the word for it, so you normally write `for(...)`. kawk has the word: `'`.

### Stage 1 — Lex

The source becomes a flat token stream:

```
{  x % 3 ? x % 5 ? x : "Buzz" : x % 5 ? "Fizz" : "FizzBuzz"  }  '  1 + ! $0
```

Lowercase `x` is a variable, `"Buzz"` is a string, `'` is an adverb, `$0` is a
field, the rest are verbs and digits. Nothing is interpreted yet.

### Stage 2 — Group

Braces collapse into a single **lambda** atom whose body is its own little token
list:

```
LAMBDA(x % 3 ? x % 5 ? x : "Buzz" : x % 5 ? "Fizz" : "FizzBuzz")   '   1 + ! $0
```

*Why:* the `'` needs to attach to the lambda as one unit, and the body has to
parse on its own terms.

### Stage 3 — Fold-adverbs

An adverb glues to the thing on its **left**. The `'` fuses with the lambda:

```
EACH-OF[ LAMBDA(...) ]   1 + ! $0
```

*Why:* an adverb isn't a separate operator — it modifies the function it follows.
`'` turns "this lambda" into "this lambda, applied to each element."

### Stage 4 — Parse (right-to-left, no precedence)

The top level is `EACH` applied to whatever is on its right. That right side,
`1 + ! $0`, parses back to front — every verb grabs the expression to its right:

```
EACH(
  body = ( (x%3) ? ((x%5)?x:"Buzz") : ((x%5)?"Fizz":"FizzBuzz") ),
  over = ( 1 + (iota $0) )
)
```

*Why right-to-left:* there's no precedence table to memorize. `1+!$0` is simply
`1 + (! $0)`. The ternary is the one exception — it binds looser than everything,
so the conditions and branches each parse as their own right-to-left expression.

### Stage 5 — Emit AWK

kawk has no vectors, so each intermediate list becomes an AWK array built in a
temp. The generated program — shown here reformatted with comments and line
breaks, though the real output is one dense line (`./kawk -d fizzbuzz.kk` to see
it), because it's machine output and the golf is the *source*:

```awk
{
  _t1=1; _t2=$0;
  for(_i=0;_i<_t2;_i++) _t3[_i+1]=_i;                 # ! $0   -> iota: 0..n-1
  for(_k in _t3) _t4[_k]=(_t1)+_t3[_k];               # 1 +    -> shift to 1..n
  for(_k in _t4){ x=_t4[_k];                          # '      -> map, binding x
    _t5[_k]=( (x%3) ? ((x%5)?x:"Buzz") : ((x%5)?"Fizz":"FizzBuzz") ) }
  for(_k=1;_k in _t5;_k++) print _t5[_k]              # print the result, one per line
}
```

*Why per-line output:* a vector result prints one element per line, exactly like
the `for(...)print` loop you'd have written by hand.

### Stage 6 — Run (n = 5)

```
iota:        0 1 2 3 4
1+ :         1 2 3 4 5
map fizzbuzz: 1, 2, Fizz, 4, Buzz
```

---

## 2. Collatz trajectory

```
{1<x}{x%2?1+x*3:x/2}\$0
```

### The one-sentence version

Start at the seed; keep applying the collatz step *while* the value is above 1;
record every value along the way. The unbounded loop is the one genuinely
loop-shaped thing in the language — and even it is an adverb: `\` means
"apply-while, keeping each step."

### Stage 1 — Lex / Group

Two lambdas and a `\`, then the seed:

```
LAMBDA(1 < x)   LAMBDA(x % 2 ? 1 + x * 3 : x / 2)   \   $0
```

The first lambda is the **condition**, the second is the **step**.

### Stage 2 — Fold-adverbs

`\` glues to the step lambda on its left:

```
LAMBDA(1<x)   SCAN-WHILE[ LAMBDA(x%2?1+x*3:x/2) ]   $0
```

*Why:* like `'`, the adverb `\` belongs to its function. `\` is scan-while —
collect the whole trajectory. (`/` in the same spot keeps only the final value.)

### Stage 3 — Parse

The condition lambda sits to the **left** of the adverbed step — that's the
dyadic form. Left arg = condition, right arg = seed:

```
WHILE-SCAN(
  cond = (1 < x),
  step = ( (x%2) ? (1+(x*3)) : (x/2) ),
  seed = $0
)
```

*Why two lambdas:* a `while` needs two pieces of logic — when to stop, and what
to do each pass. kawk passes both as functions instead of inventing statement
syntax for them.

### Stage 4 — Emit AWK

```awk
{
  _t1=$0; x=_t1;                                      # seed
  _j=1; _t2[1]=x;                                     # trajectory[1] = seed
  while((1<x)){                                       # cond
    x=( (x%2) ? (1+(x*3)) : (x/2) );                  # step
    _j++; _t2[_j]=x }                                 # record it
  for(_k=1;_k in _t2;_k++) print _t2[_k]              # print the trajectory
}
```

*Why a real `while` here:* this loop has no known count — it runs until the
condition fails. That's the one case `iota` + `each` can't express, so it
compiles to an actual AWK `while`.

### Stage 5 — Run (seed = 6)

```
6 -> 3 -> 10 -> 5 -> 16 -> 8 -> 4 -> 2 -> 1
```

The step count is just the trajectory length minus one — `#` of this gives 9,
so 8 steps.

---

## The thread running through both

- **No precedence** means you read inside-out from the right; the only thing to
  learn is "each verb eats its right."
- **Iteration is adverbs, never statements.** A counting loop is `each` over a
  range; an unbounded loop is `while-scan`/`while-over`. The `for` keyword is
  gone because most "loops" were maps and reduces wearing a loop's clothes.
- **kawk values become AWK arrays.** The emitted AWK looks bulky because it
  materializes every intermediate list — but you never read it. You read the
  source, which is the short part.
