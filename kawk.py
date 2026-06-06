#!/usr/bin/env python3
"""
kawk -- a tiny K-flavored language that transpiles to AWK.

This is a SPIKE: it implements the expression core only --
right-to-left evaluation, the verbs we've designed, the fold adverb,
and the "bare expression auto-prints per record" idiom. It is enough
to run three real programs end to end. The imperative/search layer
(ternary, while, pattern-action chains) is deliberately not here yet.

Glyphs implemented:
  $0 $1 ...   field access (a noun)
  123         integer literal (a noun)
  + - * % /   dyadic arithmetic (scalar+vector broadcasts)
  !           monadic: iota  -> 0 .. n-1   (a vector)
  |           monadic: reverse (a vector OR a string)
  #           monadic: length/count
  +/  */ ...  fold: a verb followed by '/' folds it over a vector
"""

import sys, subprocess

# ---------- 1. LEX ----------
# Split the source into atoms. Decide '/' = fold-adverb (after a verb)
# vs '/' = divide (after a noun): the 3-state rule, in miniature.

VERBS = set("+-*%|!#")        # plain verbs (/ handled specially)
ADVERBS = set("/\\'")         # fold, scan, each

def lex(src):
    toks, i = [], 0
    while i < len(src):
        c = src[i]
        if c.isspace():
            i += 1
        elif c == '$':                       # field: $0, $12
            j = i + 1
            while j < len(src) and src[j].isdigit():
                j += 1
            toks.append(('field', src[i+1:j])); i = j
        elif c.isdigit():                    # integer literal
            j = i
            while j < len(src) and src[j].isdigit():
                j += 1
            toks.append(('int', src[i:j])); i = j
        elif c in VERBS:
            toks.append(('verb', c)); i += 1
        elif c in ADVERBS:
            toks.append(('adv', c)); i += 1
        else:
            raise SyntaxError(f"unknown glyph {c!r}")
    return toks

# ---------- 2. FOLD MERGE ----------
# An adverb glued to the verb on its LEFT becomes part of that verb.
# A '/' whose left neighbour is a noun (or nothing) is divide instead.

def fold_adverbs(toks):
    atoms, i = [], 0
    while i < len(toks):
        kind, val = toks[i]
        if kind == 'adv':
            # adverb with no verb to its left: only '/' is meaningful -> divide
            if val == '/':
                atoms.append({'k': 'verb', 'sym': '/', 'adv': None})
            else:
                raise SyntaxError(f"adverb {val!r} needs a verb on its left")
            i += 1
        elif kind == 'verb':
            adv = None
            if i + 1 < len(toks) and toks[i+1][0] == 'adv':   # verb + adverb -> folded verb
                adv = toks[i+1][1]; i += 1
            atoms.append({'k': 'verb', 'sym': val, 'adv': adv})
            i += 1
        else:
            atoms.append({'k': 'noun', 'kind': kind, 'val': val})
            i += 1
    return atoms

# ---------- 3. PARSE (right to left, no precedence) ----------
# value := noun | noun verb value | verb value

def parse(atoms):
    if not atoms:
        raise SyntaxError("empty expression")
    a = atoms[0]
    if a['k'] == 'noun':
        if len(atoms) == 1:
            return ('noun', a)
        v = atoms[1]
        if v['k'] != 'verb':
            raise SyntaxError("two nouns in a row")
        return ('dyad', v, ('noun', a), parse(atoms[2:]))
    else:  # verb in head position == monadic application
        return ('monad', a, parse(atoms[1:]))

# ---------- 4. EMIT AWK ----------
# Every node materialises into a temp: a scalar var or an array.
# We track .is_vec so folds/broadcasts know what they are working on.

class Emit:
    def __init__(self):
        self.lines = []
        self.n = 0
    def tmp(self):
        self.n += 1
        return f"_t{self.n}"

    def go(self, node):
        """returns (name, is_vec)"""
        typ = node[0]
        if typ == 'noun':
            a = node[1]
            t = self.tmp()
            expr = f"${a['val']}" if a['kind'] == 'field' else a['val']
            self.lines.append(f"{t}={expr}")
            return (t, False)

        if typ == 'monad':
            verb, arg = node[1]['sym'], node[2]
            adv = node[1]['adv']
            name, vec = self.go(arg)
            if adv == '/':                       # fold:  +/  */
                return self.fold(verb, name, vec)
            if verb == '!':                      # iota
                t = self.tmp()
                self.lines.append(f"for(_i=0;_i<{name};_i++){t}[_i+1]=_i")
                return (t, True)
            if verb == '|':                      # reverse
                return self.reverse(name, vec)
            if verb == '#':                      # length
                t = self.tmp()
                if vec:
                    self.lines.append(f"{t}=length({name})")
                else:
                    self.lines.append(f"{t}=length({name})")
                return (t, False)
            raise SyntaxError(f"monadic {verb!r} not in spike")

        if typ == 'dyad':
            verb = node[1]['sym']
            ln, lv = self.go(node[2])
            rn, rv = self.go(node[3])
            return self.arith(verb, ln, lv, rn, rv)

    def fold(self, verb, name, vec):
        if not vec:
            raise SyntaxError("fold needs a vector")
        t = self.tmp()
        seed = "1" if verb == '*' else "0"
        self.lines.append(f"{t}={seed};for(_k in {name}){t}={t}{verb}{name}[_k]")
        return (t, False)

    def reverse(self, name, vec):
        t = self.tmp()
        if vec:
            self.lines.append(
                f"_n=0;for(_k in {name})_n++;"
                f"for(_k=1;_k<=_n;_k++){t}[_n-_k+1]={name}[_k]")
            return (t, True)
        # string reverse -- this is the entire job of your recursive r()
        self.lines.append(
            f'{t}="";for(_i=length({name});_i>=1;_i--){t}={t} substr({name},_i,1)')
        return (t, False)

    def arith(self, verb, ln, lv, rn, rv):
        t = self.tmp()
        if not lv and not rv:                    # scalar op scalar
            self.lines.append(f"{t}=({ln}){verb}({rn})")
            return (t, False)
        if lv and rv:                            # vector op vector
            self.lines.append(f"for(_k in {ln}){t}[_k]={ln}[_k]{verb}{rn}[_k]")
            return (t, True)
        if rv:                                   # scalar op vector  (broadcast)
            self.lines.append(f"for(_k in {rn}){t}[_k]=({ln}){verb}{rn}[_k]")
            return (t, True)
        # vector op scalar
        self.lines.append(f"for(_k in {ln}){t}[_k]={ln}[_k]{verb}({rn})")
        return (t, True)


def transpile(src):
    tree = parse(fold_adverbs(lex(src)))
    e = Emit()
    name, vec = e.go(tree)
    body = ";".join(e.lines)
    if vec:                                      # print a vector joined by spaces
        out = (f'_o="";for(_k=1;_k in {name};_k++)'
               f'_o=_o (_k>1?" ":"") {name}[_k];print _o')
    else:
        out = f"print {name}"
    return "{" + body + ";" + out + "}"


def main():
    args = sys.argv[1:]
    run = False
    if args and args[0] == "-r":
        run = True; args = args[1:]
    src = args[0]
    awk = transpile(src)
    if run:
        data = sys.stdin.read()
        subprocess.run(["awk", awk], input=data, text=True)
    else:
        print(awk)

if __name__ == "__main__":
    main()
