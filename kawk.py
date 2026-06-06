#!/usr/bin/env python3
"""
kawk -- a tiny K-flavored language that transpiles to AWK.

Slab 2: the scalar layer on top of the expression core.
  - verbs: + - * % / (arith), < > = (compare, = is equality), & | (logical/or)
  - monadic: ! iota, | reverse, # length
  - fold adverb: +/  */ ...
  - ternary: c?t:f   (right-associative, nests)
  - assignment: a:expr   ($0:expr prints the value, AWK's auto-print idiom)
  - grouping: ( ... )   -- needed because eval is right-to-left, no precedence
  - bare expr = a pattern (prints $0 if truthy = a filter)
Still TODO: strings, for/while loops, functions, pattern-action chains.
"""
import sys, subprocess

VERBS   = set("+-*%|!#<>=&")     # / handled specially (divide vs fold)
ADVERBS = set("/\\'")
DYADIC_AWK = {'+':'+','-':'-','*':'*','%':'%','/':'/','<':'<','>':'>',
              '=':'==','&':'&&','|':'||'}

# ---------- lex ----------
def lex(src):
    toks, i = [], 0
    while i < len(src):
        c = src[i]
        if c.isspace(): i += 1
        elif c == '$':
            j = i+1
            while j < len(src) and src[j].isdigit(): j += 1
            toks.append(('field', src[i+1:j])); i = j
        elif c.isdigit():
            j = i
            while j < len(src) and src[j].isdigit(): j += 1
            toks.append(('int', src[i:j])); i = j
        elif c == '(': toks.append(('lp', '(')); i += 1
        elif c == ')': toks.append(('rp', ')')); i += 1
        elif c == '?': toks.append(('q', '?')); i += 1
        elif c == ':': toks.append(('colon', ':')); i += 1
        elif c in VERBS: toks.append(('verb', c)); i += 1
        elif c in ADVERBS: toks.append(('adv', c)); i += 1
        else: raise SyntaxError(f"unknown glyph {c!r}")
    return toks

# ---------- fold-merge: adverb glues to verb on its left ----------
def fold_adverbs(toks):
    out, i = [], 0
    while i < len(toks):
        k, v = toks[i]
        if k == 'adv':
            if v == '/': out.append(('verb2', '/', None))      # bare / = divide
            else: raise SyntaxError(f"adverb {v!r} needs a verb on its left")
            i += 1
        elif k == 'verb':
            adv = None
            if i+1 < len(toks) and toks[i+1][0] == 'adv':
                adv = toks[i+1][1]; i += 1
            out.append(('verb2', v, adv)); i += 1
        else:
            out.append((k, v)); i += 1
    return out

# ---------- group parens into nested atoms ----------
def group(toks):
    def rec(it):
        atoms = []
        for t in it:
            atoms.append(t)
        return atoms
    out, stack = [], []
    cur = out
    for t in toks:
        if t[0] == 'lp':
            new = []
            stack.append(cur); cur.append(('group', new)); cur = new
        elif t[0] == 'rp':
            if not stack: raise SyntaxError("unbalanced )")
            cur = stack.pop()
        else:
            cur.append(t)
    if stack: raise SyntaxError("unbalanced (")
    return out

# ---------- parse: ternary > assignment > verb-expr ----------
def parse(atoms):
    if not atoms: raise SyntaxError("empty expression")
    # The earliest top-level control token settles the : collision:
    #   a ':' reached before any '?'  => assignment  (binds looser than ternary)
    #   a '?'                          => ternary     (it owns its matching ':')
    first = next((i for i,a in enumerate(atoms) if a[0] in ('q','colon')), None)
    if first is not None and atoms[first][0] == 'colon':
        return ('assign', atoms[:first], parse(atoms[first+1:]))
    if first is not None:                          # atoms[first] is '?'
        qi = first; rest = atoms[qi+1:]
        depth = 0; ci = None
        for i,a in enumerate(rest):
            if a[0]=='q': depth += 1
            elif a[0]=='colon':
                if depth==0: ci = i; break
                depth -= 1
        if ci is None: raise SyntaxError("ternary ? without :")
        return ('tern', parse(atoms[:qi]), parse(rest[:ci]), parse(rest[ci+1:]))
    return verb_expr(atoms)

def verb_expr(atoms):
    a = atoms[0]
    if a[0] == 'group':
        sub = parse(a[1])
        if len(atoms) == 1: return sub
        v = atoms[1]
        if v[0] != 'verb2': raise SyntaxError("expected verb after group")
        return ('dyad', v, sub, verb_expr(atoms[2:]))
    if a[0] in ('field','int'):
        if len(atoms) == 1: return ('noun', a)
        v = atoms[1]
        if v[0] != 'verb2': raise SyntaxError("two nouns in a row")
        return ('dyad', v, ('noun', a), verb_expr(atoms[2:]))
    if a[0] == 'verb2':
        return ('monad', a, verb_expr(atoms[1:]))
    raise SyntaxError(f"cannot start expression with {a}")

# ---------- emit: inline (scalar) with materialize fallback (vector) ----------
class NotInline(Exception): pass

def inline(node):
    t = node[0]
    if t == 'noun':
        a = node[1]; return f"${a[1]}" if a[0]=='field' else a[1]
    if t == 'tern':
        return f"({inline(node[1])}?{inline(node[2])}:{inline(node[3])})"
    if t == 'assign':
        lv = node[1]
        if len(lv)==1 and lv[0][0]=='field': lval = f"${lv[0][1]}"
        else: raise NotInline()
        return f"({lval}={inline(node[2])})"
    if t == 'dyad':
        sym = node[1][1]; adv = node[1][2]
        if adv is not None: raise NotInline()         # folds are loops
        if sym not in DYADIC_AWK: raise NotInline()
        return f"({inline(node[2])}{DYADIC_AWK[sym]}{inline(node[3])})"
    if t == 'monad':
        raise NotInline()                              # !, |, # need loops
    raise NotInline()

class Emit:
    def __init__(self): self.lines=[]; self.n=0
    def tmp(self): self.n+=1; return f"_t{self.n}"
    def go(self, node):
        typ = node[0]
        if typ=='noun':
            a=node[1]; t=self.tmp()
            self.lines.append(f"{t}={'$'+a[1] if a[0]=='field' else a[1]}")
            return (t, False)
        if typ=='monad':
            sym, adv = node[1][1], node[1][2]
            name, vec = self.go(node[2])
            if adv=='/': return self.fold(sym, name, vec)
            if sym=='!':
                t=self.tmp(); self.lines.append(f"for(_i=0;_i<{name};_i++){t}[_i+1]=_i"); return (t,True)
            if sym=='|': return self.reverse(name, vec)
            if sym=='#':
                t=self.tmp(); self.lines.append(f"{t}=length({name})"); return (t,False)
            raise SyntaxError(f"monadic {sym!r} not in spike")
        if typ=='dyad':
            ln,lv=self.go(node[2]); rn,rv=self.go(node[3]); return self.arith(node[1][1],ln,lv,rn,rv)
        if typ in ('tern','assign'):
            # scalar control forms: reuse inline
            t=self.tmp(); self.lines.append(f"{t}={inline(node)}"); return (t,False)
    def fold(self, sym, name, vec):
        if not vec: raise SyntaxError("fold needs a vector")
        t=self.tmp(); seed="1" if sym=='*' else "0"
        self.lines.append(f"{t}={seed};for(_k in {name}){t}={t}{sym}{name}[_k]"); return (t,False)
    def reverse(self, name, vec):
        t=self.tmp()
        if vec:
            self.lines.append(f"_n=0;for(_k in {name})_n++;for(_k=1;_k<=_n;_k++){t}[_n-_k+1]={name}[_k]")
            return (t,True)
        self.lines.append(f'{t}="";for(_i=length({name});_i>=1;_i--){t}={t} substr({name},_i,1)')
        return (t,False)
    def arith(self, sym, ln, lv, rn, rv):
        op = sym  # arithmetic only reaches here as vectors
        t=self.tmp()
        if not lv and not rv: self.lines.append(f"{t}=({ln}){op}({rn})"); return (t,False)
        if lv and rv: self.lines.append(f"for(_k in {ln}){t}[_k]={ln}[_k]{op}{rn}[_k]"); return (t,True)
        if rv: self.lines.append(f"for(_k in {rn}){t}[_k]=({ln}){op}{rn}[_k]"); return (t,True)
        self.lines.append(f"for(_k in {ln}){t}[_k]={ln}[_k]{op}({rn})"); return (t,True)

def transpile(src):
    tree = parse(group(fold_adverbs(lex(src))))
    # assignment to $0 -> print the value (the auto-print idiom)
    if tree[0]=='assign':
        lv=tree[1]; is_f0 = len(lv)==1 and lv[0][0]=='field' and lv[0][1]=='0'
        try:
            expr = inline(tree[2])
            if is_f0: return f"{{$0=({expr})}}1"          # action+forced print: fires even when value is 0 or ""
            lval = f"${lv[0][1]}" if (len(lv)==1 and lv[0][0]=='field') else None
            if lval is None: raise NotInline()
            return f"{{{lval}=({expr})}}"
        except NotInline:
            e=Emit(); name,vec=e.go(tree[2]); body=";".join(e.lines)
            if is_f0:
                if vec:
                    j=(f'_o="";for(_k=1;_k in {name};_k++)_o=_o(_k>1?" ":""){name}[_k];$0=_o')
                    return "{"+body+";"+j+"}1"
                return "{"+body+";$0="+name+"}1"
            raise
    # bare expression: a pattern (filter) when scalar; print value when vector
    try:
        return f"({inline(tree)})"
    except NotInline:
        e=Emit(); name,vec=e.go(tree); body=";".join(e.lines)
        if vec:
            j=(f'_o="";for(_k=1;_k in {name};_k++)_o=_o(_k>1?" ":""){name}[_k];print _o')
            return "{"+body+";"+j+"}"
        return "{"+body+";print "+name+"}"

def main():
    args=sys.argv[1:]; run=False
    if args and args[0]=="-r": run=True; args=args[1:]
    awk=transpile(args[0])
    if run: subprocess.run(["awk", awk], input=sys.stdin.read(), text=True)
    else: print(awk)

if __name__=="__main__": main()
