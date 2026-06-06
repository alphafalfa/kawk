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
        elif c == '{': toks.append(('lbrace', '{')); i += 1
        elif c == '}': toks.append(('rbrace', '}')); i += 1
        elif c == '?': toks.append(('q', '?')); i += 1
        elif c == ':': toks.append(('colon', ':')); i += 1
        elif c == '"':                                   # string literal
            j = i+1; buf = ''
            while j < len(src) and src[j] != '"':
                if src[j] == '\\' and j+1 < len(src):
                    buf += src[j:j+2]; j += 2            # keep escapes verbatim (AWK shares them)
                else:
                    buf += src[j]; j += 1
            if j >= len(src): raise SyntaxError("unterminated string")
            toks.append(('str', buf)); i = j+1
        elif 'a' <= c <= 'z': toks.append(('var', c)); i += 1   # lowercase = a variable
        elif c in VERBS: toks.append(('verb', c)); i += 1
        elif c in ADVERBS: toks.append(('adv', c)); i += 1
        else: raise SyntaxError(f"unknown glyph {c!r}")
    return toks

# ---------- fold-merge: an adverb glues to the verb/lambda on its LEFT ----------
def fold_adverbs(atoms):
    out = []
    for a in atoms:
        if a[0] in ('group', 'lam'):
            out.append((a[0], fold_adverbs(a[1])))            # recurse into bodies
        elif a[0] == 'verb':
            out.append(('verb2', a[1], None))
        elif a[0] == 'adv':
            prev = out[-1] if out else None
            if prev and prev[0] == 'verb2' and prev[2] is None:
                out[-1] = ('verb2', prev[1], a[1])            # +/  */  f'   (adverb on verb)
            elif prev and prev[0] in ('group', 'lam'):
                out[-1] = ('adverbed', a[1], prev)            # each/over a function
            elif a[1] == '/':
                out.append(('verb2', '/', None))              # bare / with no verb = divide
            else:
                raise SyntaxError(f"adverb {a[1]!r} needs a verb or function on its left")
        else:
            out.append(a)
    return out

# ---------- group parens (group) and braces (lambda) into nested atoms ----------
def group(toks):
    out, stack = [], []
    cur = out
    for t in toks:
        if t[0] == 'lp':
            new = []; stack.append(cur); cur.append(('group', new)); cur = new
        elif t[0] == 'lbrace':
            new = []; stack.append(cur); cur.append(('lam', new)); cur = new
        elif t[0] in ('rp', 'rbrace'):
            if not stack: raise SyntaxError("unbalanced close")
            cur = stack.pop()
        else:
            cur.append(t)
    if stack: raise SyntaxError("unbalanced open")
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
    # while:  {cond}{step}\seed  -> trajectory (scan)   /   {cond}{step}/seed -> final (over)
    if a[0] == 'lam' and len(atoms) >= 2 and atoms[1][0] == 'adverbed':
        adv = atoms[1][1]; step_lam = atoms[1][2]
        cond = parse(a[1]); step = parse(step_lam[1]); seed = verb_expr(atoms[2:])
        if adv == '\\': return ('whilescan', cond, step, seed)
        if adv == '/':  return ('whileover', cond, step, seed)
        raise SyntaxError(f"adverb {adv!r} after a condition not in spike")
    if a[0] == 'adverbed':                       # a function carrying an adverb
        adv, inner = a[1], a[2]
        body = parse(inner[1])                   # the lambda/group body, as a tree
        if adv == "'":
            return ('each', body, verb_expr(atoms[1:]))   # map body over the vector at right
        raise SyntaxError(f"adverb {adv!r} on a function not in spike")
    if a[0] == 'group':
        sub = parse(a[1])
        if len(atoms) == 1: return sub
        v = atoms[1]
        if v[0] != 'verb2': raise SyntaxError("expected verb after group")
        return ('dyad', v, sub, verb_expr(atoms[2:]))
    if a[0] in ('field', 'int', 'var', 'str'):
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
        a = node[1]
        if a[0] == 'field': return f"${a[1]}"
        if a[0] == 'str':   return '"' + a[1] + '"'
        return a[1]                                # int or var
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
            if a[0]=='field': val='$'+a[1]
            elif a[0]=='str': val='"'+a[1]+'"'
            else: val=a[1]
            self.lines.append(f"{t}={val}")
            return (t, False)
        if typ=='monad':
            sym, adv = node[1][1], node[1][2]
            name, vec = self.go(node[2])
            if adv=='/': return self.fold(sym, name, vec)
            if sym=='!':
                t=self.tmp(); self.lines.append(f"for(_i=0;_i<{name};_i++){t}[_i+1]=_i"); return (t,True)
            if sym=='|': return self.reverse(name, vec)
            if sym=='#':
                t=self.tmp()
                if vec: self.lines.append(f"{t}=0;for(_k in {name}){t}++")   # count, portable
                else:   self.lines.append(f"{t}=length({name})")
                return (t,False)
            raise SyntaxError(f"monadic {sym!r} not in spike")
        if typ=='dyad':
            ln,lv=self.go(node[2]); rn,rv=self.go(node[3]); return self.arith(node[1][1],ln,lv,rn,rv)
        if typ=='whilescan':                               # {cond}{step}\seed -> trajectory
            sn,_=self.go(node[3]); c=inline(node[1]); s=inline(node[2]); t=self.tmp()
            self.lines.append(f"x={sn};_j=1;{t}[1]=x;while({c}){{x=({s});_j++;{t}[_j]=x}}")
            return (t, True)
        if typ=='whileover':                               # {cond}{step}/seed -> final value
            sn,_=self.go(node[3]); c=inline(node[1]); s=inline(node[2]); t=self.tmp()
            self.lines.append(f"x={sn};while({c})x=({s});{t}=x")
            return (t, False)
        if typ=='each':                                    # {body}'vec  -> map, binding x
            lam, vec = node[1], node[2]
            vn, vv = self.go(vec)
            if not vv: raise SyntaxError("each needs a vector on its right")
            expr = inline(lam)                             # body as an expression in x
            t = self.tmp()
            self.lines.append(f"for(_k in {vn}){{x={vn}[_k];{t}[_k]=({expr})}}")
            return (t, True)
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
    tree = parse(fold_adverbs(group(lex(src))))
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
            return "{"+body+f";for(_k=1;_k in {name};_k++)print {name}[_k]}}"
        return "{"+body+";print "+name+"}"

def main():
    args=sys.argv[1:]; run=False
    if args and args[0]=="-r": run=True; args=args[1:]
    awk=transpile(args[0])
    if run: subprocess.run(["awk", awk], input=sys.stdin.read(), text=True)
    else: print(awk)

if __name__=="__main__": main()
