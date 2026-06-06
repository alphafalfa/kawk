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

echo "== scalars & control =="
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
t "gsub"           '~["o";"0"]$0'        'foo'      'f00'
t "gsub-class"     '~["[0-9]";"#"]$0'    'a1b2c3'   'a#b#c#'
t "gsub-amp"       '~["o";"<&>"]$0'      'fo'       'f<o>'
t "strip"          '~["a"]$0'            'banana'   'bnn'
t "gsub-fields"    '~["o";"0"]$'         'foo too'  'f00 t00'

echo "== NEW: END (^expr) -- fold over the record stream, print once =="
t "end-const"      '^42'                 'a
b
c'        '42'
t "end-count"      '^R'                  'x
y
z'        '3'
t "end-sum"        's:s+$0;^s'           '10
20
30'      '60'
t "end-vector"     '^!R'                 'a
b
c'        '1
2
3'
t "end-col-sum"    's:R>1?s+$2:s;^s'     'item,price
Apple,3
Banana,5
Cherry,8'   '16'   ','
t "end-strip-sum"  's:R>1?s+~["[$]"]$2:s;^s'  'item,price
Apple,$3
Banana,$5
Cherry,$8'  '16'  ','
t "end-only-empty" '^99'                 ''         '99'

echo "== NEW: , join/enlist + matrices =="
t "join-flat"      '^1,2,3'              'x'        '1
2
3'
t "enlist-row"     '^,$'                 'p q'      'p q'
t "matrix-echo"    'm:m,,$;^m'           'a b
c d
e f'      'a b
c d
e f'
t "matrix-revrows" 'm:m,,$;^|m'          'a b
c d'      'c d
a b'
t "col-sums"       'm:m,,$;^+/m'         '1 2
3 4
5 6'      '9 12'
t "row-count"      'm:m,,$;^#m'          'a
b
c'        '3'
t "row-index"      'm:m,,$;^m@2'         'a b
c d
e f'      'c d'
t "raze"           'm:m,,$;^,/m'         'a b
c d'      'a
b
c
d'
t "collect-col"    'm:m,$2;^m'           'a,1
b,2
c,3'      '1
2
3'   ','
t "filter-collect" 'm:$0~"y"?m,,$:m;^m'  'xx
yy
zy
ab'       'yy
zy'

echo
printf 'RESULT: %d passed, %d failed\n' "$pass" "$fail"
exit "$fail"
