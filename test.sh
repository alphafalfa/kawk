#!/bin/sh
# kawk regression + feature tests. Runs each program through awk and checks output.
# Usage: ./test.sh            (run all)
#        ./test.sh -v         (also print the generated awk for failures)
# Exit status is the number of failures (0 = all green).
cd "$(dirname "$0")"
KAWK="python3 kawk.py"
pass=0; fail=0; verbose=0
[ "$1" = "-v" ] && verbose=1

# t NAME  PROGRAM  INPUT  EXPECTED  [FS]
t() {
  name="$1"; prog="$2"; input="$3"; exp="$4"; fsarg="$5"
  if [ -n "$fsarg" ]; then
    got=$(printf '%s' "$input" | $KAWK -F"$fsarg" -e "$prog" 2>/tmp/kawk_err)
  else
    got=$(printf '%s' "$input" | $KAWK -e "$prog" 2>/tmp/kawk_err)
  fi
  if [ "$got" = "$exp" ]; then
    pass=$((pass+1)); printf '  ok   %s\n' "$name"
  else
    fail=$((fail+1)); printf 'FAIL   %s\n' "$name"
    printf '         prog: %s\n' "$prog"
    printf '         in:   %s\n' "$(printf '%s' "$input" | tr '\n' '|')"
    printf '         want: %s\n' "$(printf '%s' "$exp" | tr '\n' '|')"
    printf '         got:  %s\n' "$(printf '%s' "$got" | tr '\n' '|')"
    [ -s /tmp/kawk_err ] && printf '         err:  %s\n' "$(cat /tmp/kawk_err)"
    if [ "$verbose" = 1 ]; then printf '         awk:  %s\n' "$($KAWK --emit-awk -e "$prog" 2>&1)"; fi
  fi
}

# noleak: a bad program must fail with a clean "kawk:" message and NObody
# Python exception class leaking through. Guards against regressions in the
# adversarial-hardening pass (trailing operators, div0, huge ints, etc).
noleak() {
  name="$1"; prog="$2"
  err=$(printf '5\n' | $KAWK -e "$prog" 2>&1 | head -1)
  case "$err" in
    *Error:*|*"index out of range"*|*"domain error"*|*"Exceeds the limit"*|*"NoneType"*|*"not subscriptable"*|*"unsupported operand"*)
      fail=$((fail+1)); printf 'FAIL   %s  (python leaked: %s)\n' "$name" "$err" ;;
    kawk:*)
      pass=$((pass+1)); printf '  ok   %s\n' "$name" ;;
    *)
      fail=$((fail+1)); printf 'FAIL   %s  (no clean error: %s)\n' "$name" "$err" ;;
  esac
}

echo "== robustness (no python leaks) =="
noleak "bare-bang"      '!'
noleak "bare-caret"     '$0:^'
noleak "trailing-fold"  '$0:+/'
noleak "trailing-gather" '$0:$@'
noleak "div-zero"       '$0:1/0'
noleak "mod-zero"       '$0:1%0'
noleak "sqrt-neg"       '$0:.s-9'
noleak "huge-power"     '$0:5^999999'
noleak "two-nouns"      '$0 $0'
noleak "unbalanced"     '(('

t "add"            '$0:2+3'              'x'        '5'
t "rtl-no-prec"    '$0:2*3+4'            'x'        '14'      # 2*(3+4)
t "paren-group"    '$0:(2*3)+4'          'x'        '10'
t "ternary"        '$0:$0>2?9:1'         '5'        '9'
t "field-eq"       '$0:$1=$2'            '3 3'      '1'       # = compiles to ==
t "fizz-3"         '$0:$0%3?$0%5?$0:"Buzz":$0%5?"Fizz":"FizzBuzz"' '9'  'Fizz'
t "fizz-5"         '$0:$0%3?$0%5?$0:"Buzz":$0%5?"Fizz":"FizzBuzz"' '10' 'Buzz'
t "fizz-15"        '$0:$0%3?$0%5?$0:"Buzz":$0%5?"Fizz":"FizzBuzz"' '15' 'FizzBuzz'
t "fizz-7"         '$0:$0%3?$0%5?$0:"Buzz":$0%5?"Fizz":"FizzBuzz"' '7'  '7'

echo "== sequences (iota / fold / map) =="
t "sum-1-n"        '+/!$0'               '5'        '15'
t "product-1-n"    '*/!$0'               '5'        '120'
t "diff-fold"      '$0:-/!5'             'x'        '-13'      # 1-2-3-4-5 (seeded by the first element)
t "exp-fold"       '$0:^/2,3,2'          'x'        '64'       # 2^3 then ^2
t "min-fold"       '$0:&/5,2,8,1'        'x'        '1'        # &/ = min across the vector
t "max-fold"       '$0:|/5,2,8,1'        'x'        '8'        # |/ = max across the vector
t "min-as-and"     '$0:&/3,0,3'          'x'        '0'        # on 0/1, min still reads as "all"
t "max-as-or"      '$0:|/0,0,4'          'x'        '4'        # and max still reads as "any"
t "squares"        "{x*x}'!\$0"          '3'        '1
4
9'
t "iota-col"       '!$0'                 '3'        '1
2
3'
t "collatz"        '{1<x}{x%2?1+x*3:x/2}\$0' '6'    '6
3
10
5
16
8
4
2
1'

echo "== fields as a vector =="
t "field-fold"     '+/$'                 '3 4 5'    '12'
t "field-map"      "{x*x}'$"            '2 3 4'    '4 9 16'
t "field-reverse"  '|$'                  'a b c'    'c b a'
t "field-count"    '#$'                  'a b c d'  '4'
t "dyn-field"      '$0:$(1+1)'           'a b c'    'b'       # $(expr) dynamic field index

echo "== string ops =="
t "str-reverse"    '|$0'                 'abc'      'cba'

echo "== NEW: storable vectors =="
t "bind-echo"      'a:$'                 'x y z'    'x y z'
t "bind-fold"      'a:$;+/a'             '3 4 5'    '12'
t "bind-map"       "a:$;{x*x}'a"        '2 3'      '4 9'
t "bind-reverse"   'a:$;|a'              'p q r'    'r q p'
t "bind-count"     'a:$;#a'             'a b c d e' '5'
t "bind-iota"      'a:!$0;+/a'           '4'        '10'
t "bind-iota-rev"  'a:!$0;|a'            '4'        '4
3
2
1'
t "chain-binds"    'a:$;b:|a;+/b'        '1 2 3'    '6'

echo "== NEW: split (_) =="
t "split-sum"      '+/","_$0'            '3,4,5'    '12'
t "split-bind"     'a:","_$0;+/a'       '10,20,30'  '60'
t "split-fs"       'a:_$0;#a'           'p q r s'  '4'

echo "== NEW: index (@) =="
t "index-lit"      'a:$;a@2'            'x y z'    'y'
t "split-index"    'a:","_$0;a@3'       '10,20,30,40' '30'
t "index-expr"     'a:!$0;a@(2+1)'       '9'        '3'

echo "== NEW: special vars (N R F O) =="
t "nr-print"       '$0:R'                'x
y
z'        '1
2
3'
t "nf-print"       '$0:N'                'a b c'    '3'
t "header-skip"    'R>1'                 'hdr
a
b'      'a
b'
t "ofs-set"        'O:",";$0:$'          'a b c'    'a,b,c'

echo "== NEW: -F separator (CSV) =="
t "csv-sum"        '$0:+/$'              '3,4,5'    '12'   ','
t "csv-field"      '$0:$2'               'a,b,c'    'b'    ','
t "csv-nf"         '$0:N'                'a,b,c,d'  '4'    ','

echo "== NEW: regex (~ match, ~[re;rep] gsub) =="
t "match-filter"   '$0~"err"'            'error
ok
error2'   'error
error2'
t "match-anchor"   '$0~"^[0-9]+$"'       '123
abc
45'       '123
45'
t "match-bool"     '$0:$1~"a"'           'cat'      '1'
t "match-none"     '$0:$1~"z"'           'cat'      '0'
t "match-count"    '$0:$0~"a"'           'banana'   '3'        # ~ returns the match count
t "gsub"           '~["o";"0"]$0'        'foo'      'f00'
t "gsub-class"     '~["[0-9]";"#"]$0'    'a1b2c3'   'a#b#c#'
t "gsub-amp"       '~["o";"<&>"]$0'      'fo'       'f<o>'
t "strip"          '~["a"]$0'            'banana'   'bnn'
t "gsub-fields"    '~["o";"0"]$'         'foo too'  'f00 t00'

echo "== END (;:expr) -- fold over the record stream, print once =="
t "end-const"      ';:42'                'a
b
c'        '42'
t "end-count"      ';:R'                 'x
y
z'        '3'
t "end-sum"        's:s+$0;:s'           '10
20
30'      '60'
t "end-vector"     ';:!R'                'a
b
c'        '1
2
3'
t "end-col-sum"    's:R>1?s+$2:s;:s'     'item,price
Apple,3
Banana,5
Cherry,8'   '16'   ','
t "end-strip-sum"  's:R>1?s+~["[$]"]$2:s;:s'  'item,price
Apple,$3
Banana,$5
Cherry,$8'  '16'  ','
t "end-only-empty" ';:99'                ''         '99'

echo "== , join/enlist + matrices =="
t "join-flat"      ';:1,2,3'             'x'        '1
2
3'
t "enlist-row"     ';:,$'                'p q'      'p q'
t "matrix-echo"    'm:m,,$;:m'           'a b
c d
e f'      'a b
c d
e f'
t "matrix-revrows" 'm:m,,$;:|m'          'a b
c d'      'c d
a b'
t "col-sums"       'm:m,,$;:+/m'         '1 2
3 4
5 6'      '9 12'
t "row-count"      'm:m,,$;:#m'          'a
b
c'        '3'
t "row-index"      'm:m,,$;:m@2'         'a b
c d
e f'      'c d'
t "raze"           'm:m,,$;:,/m'         'a b
c d'      'a
b
c
d'
t "collect-col"    'm:m,$2;:m'           'a,1
b,2
c,3'      '1
2
3'   ','
t "filter-collect" 'm:$0~"y"?m,,$:m;:m'  'xx
yy
zy
ab'       'yy
zy'

echo "== transpose (monadic +) =="
t "transpose"      'm:m,,$;:+m'          '1 2 3
4 5 6'    '1 4
2 5
3 6'
t "transpose-twice" 'm:m,,$;:++m'        '1 2 3
4 5 6'    '1 2 3
4 5 6'
t "column-extract" 'm:m,,$;:(+m)@1'      'a b
c d
e f'      'a c e'

echo "== NEW: exponent (^) =="
t "pow"            '$0:2^10'             'x'        '1024'
t "pow-rtl"        '$0:2^3^2'            'x'        '512'      # 2^(3^2)=2^9
t "pow-field"      '$0:$1^2'             '7'        '49'
t "pow-col"        's:s+$1^2;:s'         '3
4'         '25'       # 9 + 16

echo "== NEW: .u / .l (upper / lower) =="
t "upper"          '$0:.u$0'             'hi there' 'HI THERE'
t "lower"          '$0:.l$0'             'LOUD'     'loud'
t "upper-var"      'a:$0;$0:.ua'         'cat'      'CAT'      # .ua = .u applied to var a
t "upper-fields"   '.u$'                 'a b c'    'A B C'    # broadcasts over the row

echo "== NEW: min / max (& |) and floor / ceil / round =="
t "min"            '$0:3&5'              'x'        '3'        # & = min
t "max"            '$0:3|5'              'x'        '5'        # | = max
t "min-keeps-and"  '$0:0&5'              'x'        '0'        # 0 is the min, and falsy -> still "and"
t "max-keeps-or"   '$0:0|5'              'x'        '5'        # 5 is the max, and truthy -> still "or"
t "floor"          '$0:.f$0'             '3.7'      '3'        # .f floor
t "ceil"           '$0:.c$0'             '3.2'      '4'        # .c ceil
t "round"          '$0:.r$0'             '2.5'      '3'        # .r round (half up)
t "abs"            '$0:.a$0'             '-7'       '7'        # .a absolute value
t "abs-float"      '$0:.a$0'             '-3.5'     '3.5'      # keeps the fraction
t "sqrt"           '$0:.s$0'             '16'       '4'        # .s square root
t "floor-fields"   '.f$'                 '1.9 2.1'  '1 2'      # broadcasts over the row

echo "== NEW: strings are char-vectors =="
t "str-index"      '$0:$0@2'             'hello'    'e'        # @ indexes a string's chars (1-based)
t "str-each"       "{.u x}'\$0"          'cat'      'CAT'      # each maps over chars, result re-glues
t "digit-sum"      '$0:+/$0'             '1234'     '10'       # fold over a string folds its chars
t "explode-count"  '$0:#""_$0'          'hello'    '5'        # ""_ explodes to chars, # counts them
t "concat"         '.j"hi ",$0'         'bob'      'hi bob'   # .j implodes a list into one string
t "round-trip"     '$0:.j"=",+/$0'      '1234'     '=10'      # string -> math -> string, one breath
t "str-reverse"    '$0:|$0'             'abc'      'cba'      # | already saw chars; still does
t "verb-each"      ".u'\$"               'a b c'    'A B C'    # .u'$ : map a builtin over each, no lambda
t "verb-each-len"  "\$0:#'\$"            'hi there' '2 5'      # #'$ : length of each field
t "verb-each-str"  "\$0:.u'\$0"          'cat'      'CAT'      # map over each char, re-glues to a string

t "decimal"        '$0:.5*$0'           '4'        '2'        # .5 is a number, not a dot-builtin
t "decimal-add"    '$0:1.5+2.5'          'x'        '4'        # decimals lex & add
t "dot-after-int"  '$0:0.25*4'           'x'        '1'        # 0.25 one float token
t "empty-input"    '$0:"ok"'             ''         'ok'       # empty input still runs once (awk <<< "")

echo "== implicit x (single provable hole) =="
t "implicit-left"  '$0:{>9}'"'"'!12'     'x'        '0 0 0 0 0 0 0 0 0 1 1 1'  # {>9}->{x>9}: mask of >9
t "implicit-right" "\$0:.j{z_}'\$0"       'ab'       'ab'       # {z_} -> {z_x} : explode each (right hole)
t "implicit-mono"  "\$0:.j{.u}'\$0"       'cat'      'CAT'      # {.u} -> {.u x} : lone monadic
t "implicit-root"  '$0:{>9}{+/z_}/$0'    '12345'    '6'        # digital root, fully tacit
t "named-x-intact" "\$0:{x*x}'!3"         'x'        '1 4 9'    # naming x is untouched by the sugar

t "compress"       '$0:({0=x%2}'"'"'!$)#!$'  '10'  '2 4 6 8 10'  # mask#v keeps where mask truthy
t "compress-none"  '$0:({x>9}'"'"'!$)#!$'    '5'   ''           # nothing passes -> empty
t "primes-to-n"    '$0:({2=+/0=x%!x}'"'"'!$)#!$'  '13'  '2 3 5 7 11 13'  # primes via compress
t "pred-filter"    '$0:{0=x%2}#!$'      '10'  '2 4 6 8 10'  # {pred}#v filter-by-predicate
t "pred-primes"    '$0:{2=+/0=x%!x}#!$'  '13'  '2 3 5 7 11 13'  # primes, single $
t "grade-up"       '$0:<$'              '30 10 20'  '2 3 1'      # < = sort indices (ascending)
t "sort-num"       '$0:$@<$'           '3 1 20 100'  '1 3 20 100'  # $@<$ sorts numerically
t "sort-alpha"     '$0:$@<$'           'cat ant bee'  'ant bee cat'  # text sorts alphabetically
t "sort-desc"      '$0:$@>$'           '5 3 8 1'    '8 5 3 1'    # > = descending
t "gather"         '$0:$@3,1'          'a b c d'    'c a'        # @ with a vector gathers
t "negate"         '$0:-$'             '3 -1 4'     '-3 1 -4'    # monadic - negates
t "distinct"       '$0:=$'             'b a b c a'  'b a c'  # = distinct, first-occurrence order
t "distinct-count" '$0:#=$'            'x y x z y'  '3'      # #= : how many distinct
t "all-distinct"   '$0:(#$)=#=$'       '1 2 1'      '0'      # count vs distinct-count

echo "== comments (# at line start) =="
t "comment-line"   '# sum 1..n
+/!$0'                '5'        '15'      # a full-line comment is dropped
t "hash-is-count"  '#$0'                 'hello'    '5'       # '#' before non-space is still the count verb

echo "== help system =="
# c NAME  CMD  SUBSTRING  : command output must contain SUBSTRING (and never a Python traceback)
c() {
  name="$1"; out=$(eval "$2" 2>&1)
  if printf '%s' "$out" | grep -q "Traceback"; then
    fail=$((fail+1)); printf 'FAIL   %s  (leaked a traceback)\n' "$name"
  elif printf '%s' "$out" | grep -qF "$3"; then
    pass=$((pass+1)); printf '  ok   %s\n' "$name"
  else
    fail=$((fail+1)); printf 'FAIL   %s  (missing %s)\n' "$name" "$3"; printf '         got: %s\n' "$out"
  fi
}
c "help-glyph"     "$KAWK -h '~'"                 "match"
c "explain-group"  "$KAWK --explain 'x@2,1'"      "(x@(2,1))"
c "explain-rtl"    "$KAWK --explain '2*3+4'"      "(2*(3+4))"
c "err-clean"      "printf 'x\n' | $KAWK -e '+/5'" "not a sequence"
c "err-two-nouns"  "printf 'x\n' | $KAWK -e '\$0 \$1'" "two nouns"

echo
printf 'RESULT: %d passed, %d failed\n' "$pass" "$fail"
exit "$fail"
