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
import sys, subprocess, os, re, math

VERBS   = set("+-*%|!#<>=&_~,")  # / special (divide vs fold); _ split; ~ match/sub; , join/enlist
ADVERBS = set("/\\'")
DYADIC_AWK = {'+':'+','-':'-','*':'*','%':'%','/':'/','<':'<','>':'>',
              '=':'==','&':'&&','|':'||'}
SCALAR_DYADS = set("+-*%/<>=&|~")  # dyads that yield a scalar (so a bare one can filter); _ excluded

# ---------- lex ----------
def lex(src):
    toks, i = [], 0
    while i < len(src):
        c = src[i]
        if c.isspace(): i += 1
        elif c == '$':
            if i+1 < len(src) and src[i+1].isdigit():
                j = i+1
                while j < len(src) and src[j].isdigit(): j += 1
                toks.append(('field', src[i+1:j])); i = j      # $0 $12 -> literal field
            else:
                toks.append(('dollar',)); i += 1               # bare $ or $i, $(e)
        elif c.isdigit():
            j = i
            while j < len(src) and src[j].isdigit(): j += 1
            toks.append(('int', src[i:j])); i = j
        elif c == '(': toks.append(('lp', '(')); i += 1
        elif c == ')': toks.append(('rp', ')')); i += 1
        elif c == '{': toks.append(('lbrace', '{')); i += 1
        elif c == '}': toks.append(('rbrace', '}')); i += 1
        elif c == '[': toks.append(('lbrack', '[')); i += 1   # ~[re;rep] arg bundle
        elif c == ']': toks.append(('rbrack', ']')); i += 1
        elif c == '?': toks.append(('q', '?')); i += 1
        elif c == ':': toks.append(('colon', ':')); i += 1
        elif c == '@': toks.append(('at',)); i += 1            # a@i -> index into a named array
        elif c == ';': toks.append(('semi',)); i += 1          # statement separator
        elif c == '^': toks.append(('caret',)); i += 1         # ^expr = END: run once at EOF, auto-print
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
        elif 'A' <= c <= 'Z': toks.append(('special', c)); i += 1  # uppercase = AWK special var
        elif c in VERBS: toks.append(('verb', c)); i += 1
        elif c in ADVERBS: toks.append(('adv', c)); i += 1
        else: raise SyntaxError(f"unknown glyph {c!r}")
    return toks

# ---------- fold-merge: an adverb glues to the verb/lambda on its LEFT ----------
def fold_adverbs(atoms):
    out = []
    for a in atoms:
        if a[0] in ('group', 'lam', 'bracket'):
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
        elif t[0] == 'lbrack':
            new = []; stack.append(cur); cur.append(('bracket', new)); cur = new
        elif t[0] in ('rp', 'rbrace', 'rbrack'):
            if not stack: raise SyntaxError("unbalanced close")
            cur = stack.pop()
        else:
            cur.append(t)
    if stack: raise SyntaxError("unbalanced open")
    return out

# ---------- resolve $: bare $ = the field vector; $<index> = one field ----------
def resolve_dollars(atoms):
    out, i = [], 0
    while i < len(atoms):
        a = atoms[i]
        if a[0] in ('group', 'lam', 'bracket'):
            out.append((a[0], resolve_dollars(a[1]))); i += 1
        elif a[0] == 'dollar':
            nxt = atoms[i+1] if i+1 < len(atoms) else None
            if nxt and nxt[0] in ('var', 'int'):
                out.append(('fieldat', [nxt])); i += 2                 # $i  $3-as-var
            elif nxt and nxt[0] == 'group':
                out.append(('fieldat', resolve_dollars(nxt[1]))); i += 2  # $(i+1)
            else:
                out.append(('fields',)); i += 1                        # bare $ = vector
        else:
            out.append(a); i += 1
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

def make_noun(a):
    if a[0] == 'fields':  return ('fieldvec',)                       # bare $  -> the vector
    if a[0] == 'fieldat': return ('fieldat', parse(fold_adverbs(a[1])))  # $i, $(e)
    return ('noun', a)

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
        if v[0] == 'at':
            return ('index', sub, verb_expr(atoms[2:]))
        if v[0] != 'verb2': raise SyntaxError("expected verb after group")
        return ('dyad', v, sub, verb_expr(atoms[2:]))
    if a[0] in ('field', 'int', 'var', 'str', 'fields', 'fieldat', 'special'):
        node = make_noun(a)
        if len(atoms) == 1: return node
        v = atoms[1]
        if v[0] == 'at':
            return ('index', node, verb_expr(atoms[2:]))
        if v[0] != 'verb2': raise SyntaxError("two nouns in a row")
        return ('dyad', v, node, verb_expr(atoms[2:]))
    if a[0] == 'verb2':
        if a[1] == '~' and len(atoms) >= 2 and atoms[1][0] == 'bracket':
            args = [parse(g) for g in split_statements(atoms[1][1])]   # [re] strip / [re;rep] gsub
            if not 1 <= len(args) <= 2: raise SyntaxError("~ takes [re] or [re;rep]")
            rep = args[1] if len(args) == 2 else None
            return ('gsub', args[0], rep, verb_expr(atoms[2:]))
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
    if t == 'fieldat':
        return f"$({inline(node[1])})"
    if t == 'index':
        return f"{inline(node[1])}[{inline(node[2])}]"
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
    def __init__(self): self.lines=[]; self.n=0; self.flavor={}; self.arrays={}   # tmp->'field'|'seq'; arrays: var->flavor
    def tmp(self): self.n+=1; return f"_t{self.n}"
    def go(self, node):
        typ = node[0]
        if typ=='fieldvec':                                # bare $ -> $1..$NF as a vector
            t=self.tmp()
            self.lines.append(f"for(_i=1;_i<=NF;_i++){t}[_i]=$_i")
            self.flavor[t]='field'
            return (t, True)
        if typ=='fieldat':                                 # $i, $(e) -> one field (scalar)
            t=self.tmp(); self.lines.append(f"{t}=$({inline(node[1])})"); return (t,False)
        if typ=='noun':
            a=node[1]
            if a[0]=='var' and a[1] in self.arrays:           # a named array referenced as a vector
                self.flavor[a[1]]=self.arrays[a[1]]
                return (a[1], True)
            t=self.tmp()
            if a[0]=='field': val='$'+a[1]
            elif a[0]=='str': val='"'+a[1]+'"'
            else: val=a[1]
            self.lines.append(f"{t}={val}")
            return (t, False)
        if typ=='index':
            t=self.tmp(); self.lines.append(f"{t}={inline(node)}"); return (t,False)
        if typ=='monad':
            sym, adv = node[1][1], node[1][2]
            name, vec = self.go(node[2])
            if adv=='/': return self.fold(sym, name, vec)
            if sym=='_':                                   # _s : split string s on FS -> vector
                t=self.tmp(); self.lines.append(f"split({name},{t})")
                self.flavor[t]='field'; return (t,True)
            if sym=='!':                                   # iota: 1..n  (1-based, AWK-style)
                t=self.tmp(); self.lines.append(f"for(_i=1;_i<={name};_i++){t}[_i]=_i")
                self.flavor[t]='seq'; return (t,True)
            if sym=='|': return self.reverse(name, vec)
            if sym=='#':
                t=self.tmp()
                if vec: self.lines.append(f"{t}=0;for(_k in {name}){t}++")   # count, portable
                else:   self.lines.append(f"{t}=length({name})")
                return (t,False)
            raise SyntaxError(f"monadic {sym!r} not in spike")
        if typ=='dyad':
            if node[1][1]=='_':                            # d_s : split string s on separator d
                sep,_=self.go(node[2]); s,_=self.go(node[3]); t=self.tmp()
                self.lines.append(f"split({s},{t},{sep})")
                self.flavor[t]='field'; return (t,True)
            ln,lv=self.go(node[2]); rn,rv=self.go(node[3]); return self.arith(node[1][1],ln,lv,rn,rv)
        if typ=='whilescan':                               # {cond}{step}\seed -> trajectory
            sn,_=self.go(node[3]); c=inline(node[1]); s=inline(node[2]); t=self.tmp()
            self.lines.append(f"x={sn};_j=1;{t}[1]=x;while({c}){{x=({s});_j++;{t}[_j]=x}}")
            self.flavor[t]='seq'; return (t, True)
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
            self.flavor[t]=self.flavor.get(vn,'seq'); return (t, True)
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
            self.flavor[t]=self.flavor.get(name,'seq'); return (t,True)
        self.lines.append(f'{t}="";for(_i=length({name});_i>=1;_i--){t}={t} substr({name},_i,1)')
        return (t,False)
    def arith(self, sym, ln, lv, rn, rv):
        op = sym  # arithmetic only reaches here as vectors
        t=self.tmp()
        if not lv and not rv: self.lines.append(f"{t}=({ln}){op}({rn})"); return (t,False)
        fl = 'field' if (lv and self.flavor.get(ln)=='field') or (rv and self.flavor.get(rn)=='field') else 'seq'
        if lv and rv: self.lines.append(f"for(_k in {ln}){t}[_k]={ln}[_k]{op}{rn}[_k]")
        elif rv:      self.lines.append(f"for(_k in {rn}){t}[_k]=({ln}){op}{rn}[_k]")
        else:         self.lines.append(f"for(_k in {ln}){t}[_k]={ln}[_k]{op}({rn})")
        self.flavor[t]=fl; return (t,True)

# ---------- statement splitting & vector type inference ----------
def split_statements(atoms):
    out, cur = [], []
    for a in atoms:
        if a[0]=='semi': out.append(cur); cur=[]
        else: cur.append(a)
    out.append(cur)
    return [s for s in out if s]

def is_vector(tr, arrays):
    """True if the tree evaluates to a vector (so an assigned var becomes an array)."""
    t=tr[0]
    if t=='fieldvec': return True
    if t=='noun': a=tr[1]; return a[0]=='var' and a[1] in arrays
    if t=='monad':
        sym, adv = tr[1][1], tr[1][2]
        if adv=='/': return False                      # fold collapses to a scalar
        if sym in ('!','_'): return True               # iota, split
        if sym=='|': return is_vector(tr[2], arrays)   # reverse of a vector
        return False                                   # # (count) and the rest are scalar
    if t=='dyad':
        if tr[1][1]=='_': return True                  # split
        if tr[1][2]=='/': return False
        return is_vector(tr[2],arrays) or is_vector(tr[3],arrays)
    if t in ('each','whilescan'): return True
    if t=='tern': return is_vector(tr[2],arrays) or is_vector(tr[3],arrays)
    if t=='assign': return is_vector(tr[2],arrays)
    return False

def vec_flavor(tr, arrays):
    """'field' (join with OFS on output) vs 'seq' (one item per line)."""
    t=tr[0]
    if t=='fieldvec': return 'field'
    if t=='noun' and tr[1][0]=='var': return arrays.get(tr[1][1],'seq')
    if t=='monad':
        sym=tr[1][1]
        if sym=='_': return 'field'
        if sym=='|': return vec_flavor(tr[2],arrays)
        return 'seq'
    if t=='dyad':
        if tr[1][1]=='_': return 'field'
        lf = vec_flavor(tr[2],arrays) if is_vector(tr[2],arrays) else None
        rf = vec_flavor(tr[3],arrays) if is_vector(tr[3],arrays) else None
        return 'field' if 'field' in (lf,rf) else 'seq'
    if t=='each': return vec_flavor(tr[2],arrays)
    if t=='tern':
        return vec_flavor(tr[2],arrays) if is_vector(tr[2],arrays) else vec_flavor(tr[3],arrays)
    return 'seq'

# ---------- emit helpers (multi-statement) ----------
def emit_value(e, tr):
    try: return (inline(tr), False)
    except NotInline:
        return e.go(tr)

def print_vector(e, name):
    if e.flavor.get(name,'seq')=='field':
        e.lines.append(f'_o={name}[1];for(_k=2;_k in {name};_k++)_o=_o OFS {name}[_k];print _o')
    else:
        e.lines.append(f"for(_k=1;_k in {name};_k++)print {name}[_k]")

def do_binding(e, tr):                                  # an assignment, evaluated for its effect
    lv, rhs = tr[1], tr[2]
    if len(lv)==1 and lv[0][0]=='var':
        name=lv[0][1]
        if name in e.arrays:                            # vector -> build into a named array
            n,_=e.go(rhs)
            e.lines.append(f'split("",{name})')         # clear (portable) before refilling
            e.lines.append(f"for(_k in {n}){name}[_k]={n}[_k]")
            e.flavor[name]=e.arrays[name]
        else:                                           # scalar variable
            val,_=emit_value(e, rhs); e.lines.append(f"{name}=({val})")
    elif len(lv)==1 and lv[0][0]=='field':
        f=lv[0][1]; val,vec=emit_value(e, rhs)
        if vec:
            e.lines.append(f'_o={val}[1];for(_k=2;_k in {val};_k++)_o=_o OFS {val}[_k]')
            e.lines.append(f"${f}=_o")
        else:
            e.lines.append(f"${f}=({val})")
    else:
        raise SyntaxError("bad assignment target")

def do_output(e, tr):                                   # the final statement: produce output
    if tr[0]=='assign' and len(tr[1])==1 and tr[1][0][0]=='var':
        do_binding(e, tr); name=tr[1][0][1]
        if name in e.arrays: print_vector(e, name)      # echo a bound vector
        else: e.lines.append(f"print {name}")
        return
    if tr[0]=='assign' and len(tr[1])==1 and tr[1][0][0]=='field':
        do_binding(e, tr); e.lines.append("print $0"); return   # modify a field, print the record
    val,vec=emit_value(e, tr)
    if vec: print_vector(e, val)
    else: e.lines.append(f"print ({val})")

def transpile_multi(trees):
    e=Emit()
    for tr in trees:                                    # pass 1: which vars hold vectors -> arrays
        if tr[0]=='assign' and len(tr[1])==1 and tr[1][0][0]=='var' and is_vector(tr[2], e.arrays):
            e.arrays[tr[1][0][1]] = vec_flavor(tr[2], e.arrays)
    for idx,tr in enumerate(trees):                     # pass 2: emit; last statement prints
        if idx==len(trees)-1: do_output(e, tr)
        elif tr[0]=='assign': do_binding(e, tr)
        else: emit_value(e, tr)                          # non-final bare expr: side effect only
    return "{"+";".join(e.lines)+"}"

def transpile_single(tree):
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
            if e.flavor.get(name)=='field':                  # fields go back out as a record
                j=f'_o={name}[1];for(_k=2;_k in {name};_k++)_o=_o OFS {name}[_k];print _o'
                return "{"+body+";"+j+"}"
            return "{"+body+f";for(_k=1;_k in {name};_k++)print {name}[_k]}}"   # a sequence -> a column
        return "{"+body+";print "+name+"}"

def transpile(src):
    atoms = fold_adverbs(resolve_dollars(group(lex(src))))
    trees = [parse(s) for s in split_statements(atoms)]
    if not trees: raise SyntaxError("empty program")
    if len(trees)==1:
        tr=trees[0]
        # a bare var<-vector binding is only useful via the multi path (bind, then echo it)
        if tr[0]=='assign' and len(tr[1])==1 and tr[1][0][0]=='var':
            return transpile_multi(trees)
        return transpile_single(tr)
    return transpile_multi(trees)

def read_source(arg):
    if arg == "-":          return sys.stdin.read()
    if os.path.isfile(arg): return open(arg).read()
    sys.exit(f"kawk.py: no such file: {arg!r}  (use -e '<program>' for a literal, - for stdin)")

# ======================================================================
# Interpreter -- the engine. Reuses the whole front end (lex..parse) and
# walks the tree directly. AWK's *data model* (records, fields, the
# per-line loop, filter-patterns) lives here; AWK-as-a-target is retired
# to `--emit-awk`.
# ======================================================================

class Vec(list):
    """An ordered kawk vector. flavor decides output: 'field' joins on OFS,
    'seq' prints one item per line."""
    def __new__(cls, items, flavor='seq'):
        self = super().__new__(cls, items); return self
    def __init__(self, items, flavor='seq'):
        super().__init__(items); self.flavor = flavor

_NUMRE = re.compile(r'\s*[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?\s*\Z')

def looks_numeric(v):
    if isinstance(v, bool): return True
    if isinstance(v, (int, float)): return True
    return isinstance(v, str) and v.strip() != '' and _NUMRE.match(v) is not None

def num(v):
    if isinstance(v, bool): return int(v)
    if isinstance(v, (int, float)): return v
    s = str(v).strip()
    try:
        if re.fullmatch(r'[+-]?\d+', s): return int(s)
        return float(s)
    except ValueError:
        return 0

def truthy(v):
    if isinstance(v, (int, float)): return v != 0
    if isinstance(v, list):        return len(v) > 0
    if looks_numeric(v):           return num(v) != 0      # a numeric string "0" is false
    return v != ''

def fmt_scalar(v):
    if isinstance(v, bool):  return str(int(v))
    if isinstance(v, float): return str(int(v)) if v.is_integer() else ('%.6g' % v)
    if isinstance(v, int):   return str(v)
    return str(v)

def gsub(pattern, rep, s):
    """Replace every match of pattern in s. AWK-style replacement: & = whole
    match, \\& = literal &, \\\\ = backslash. rep=None means strip (delete)."""
    if rep is None: rep = ''
    def repl(m):
        out, i = [], 0
        while i < len(rep):
            c = rep[i]
            if c == '\\' and i+1 < len(rep): out.append(rep[i+1]); i += 2
            elif c == '&':                   out.append(m.group(0)); i += 1
            else:                            out.append(c); i += 1
        return ''.join(out)
    return re.sub(pattern, repl, s)

class Interp:
    def __init__(self, prog, fs=None):
        self.pr  = [t for (e, t) in prog if not e]               # per-record statements
        self.end = [t for (e, t) in prog if e]                   # ^-marked: run once at EOF
        self.env = {}                                            # persistent globals
        self.ofs = ' '
        self.fs  = fs
        self.nr  = 0
        self.fields = ['']; self.nf = 0                          # valid empty record (for empty input / END)
        p = self.pr
        self.multi = bool(p) and (len(p) > 1 or (p[0][0]=='assign' and len(p[0][1])==1 and p[0][1][0][0]=='var'))

    # ---- record / field plumbing ----
    def set_record(self, line):
        parts = line.split() if self.fs is None else (re.split(self.fs, line) if line != '' else [])
        self.fields = [line] + parts
        self.nf = len(parts)

    def get_field(self, n):
        if n == 0:                 return self.fields[0]
        if 1 <= n <= self.nf:      return self.fields[n]
        return ''

    def set_field(self, n, val):
        if isinstance(val, list):                                # a vector into a field -> joined record
            val = self.ofs.join(fmt_scalar(x) for x in val)
        s = fmt_scalar(val)
        if n == 0:
            self.set_record(s)
        else:
            while self.nf < n:
                self.fields.append(''); self.nf += 1
            self.fields[n] = s
            self.fields[0] = self.ofs.join(self.fields[1:])

    # ---- evaluation ----
    def eval(self, node):
        t = node[0]
        if t == 'noun':
            a = node[1]
            if a[0] == 'int':   return int(a[1])
            if a[0] == 'str':   return a[1]
            if a[0] == 'field': return self.get_field(int(a[1]))
            if a[0] == 'var':   return self.env.get(a[1], '')
            if a[0] == 'special':
                c = a[1]
                if c == 'N': return self.nf
                if c == 'R': return self.nr
                if c == 'F': return ' ' if self.fs is None else self.fs
                if c == 'O': return self.ofs
                return self.env.get(c, '')                       # other uppercase = extra globals
        if t == 'fieldvec':
            return Vec(self.fields[1:self.nf+1], 'field')
        if t == 'fieldat':
            return self.get_field(int(num(self.eval(node[1]))))
        if t == 'index':
            arr = self.eval(node[1]); i = int(num(self.eval(node[2])))
            if isinstance(arr, list): return arr[i-1] if 1 <= i <= len(arr) else ''
            return ''
        if t == 'tern':
            return self.eval(node[2]) if truthy(self.eval(node[1])) else self.eval(node[3])
        if t == 'assign':
            val = self.eval(node[2]); self.store(node[1], val); return val
        if t == 'monad':     return self.eval_monad(node)
        if t == 'dyad':      return self.eval_dyad(node)
        if t == 'gsub':      return self.eval_gsub(node)
        if t == 'each':      return self.eval_each(node)
        if t == 'whilescan': return self.eval_while(node, scan=True)
        if t == 'whileover': return self.eval_while(node, scan=False)
        raise SyntaxError(f"cannot eval {node!r}")

    def store(self, lv, val):
        if len(lv) == 1 and lv[0][0] == 'var':       self.env[lv[0][1]] = val
        elif len(lv) == 1 and lv[0][0] == 'field':   self.set_field(int(lv[0][1]), val)
        elif len(lv) == 1 and lv[0][0] == 'special': self.set_special(lv[0][1], val)
        else: raise SyntaxError("bad assignment target")

    def set_special(self, c, val):
        if   c == 'F': self.fs  = fmt_scalar(val)                # affects records read *after* this
        elif c == 'O': self.ofs = fmt_scalar(val)
        elif c == 'R': self.nr  = int(num(val))
        elif c == 'N':
            n = int(num(val))
            while self.nf < n: self.fields.append(''); self.nf += 1
            self.fields = self.fields[:n+1]; self.nf = n
            self.fields[0] = self.ofs.join(self.fields[1:])
        else: self.env[c] = val

    def eval_monad(self, node):
        sym, adv = node[1][1], node[1][2]
        if adv == '/':                                   # fold
            return self.fold(sym, self.eval(node[2]))
        arg = self.eval(node[2])
        if sym == '_':                                   # split on FS -> vector
            return Vec(self.split_str(arg, None), 'field')
        if sym == '!':                                   # iota 1..n
            return Vec(list(range(1, int(num(arg))+1)), 'seq')
        if sym == '|':                                   # reverse: vector or string
            if isinstance(arg, list): return Vec(list(reversed(arg)), getattr(arg, 'flavor', 'seq'))
            return fmt_scalar(arg)[::-1]
        if sym == '#':                                   # count / length
            return len(arg) if isinstance(arg, list) else len(fmt_scalar(arg))
        if sym == ',':                                   # enlist: wrap as a 1-element list (a row)
            return Vec([arg], 'seq')
        raise SyntaxError(f"monadic {sym!r} not supported")

    def eval_dyad(self, node):
        sym = node[1][1]
        if sym == '_':                                   # d_s : split s on separator d
            sep = self.eval(node[2]); s = self.eval(node[3])
            return Vec(self.split_str(s, fmt_scalar(sep)), 'field')
        l = self.eval(node[2]); r = self.eval(node[3])
        if sym == ',':                                   # join: concatenate, flattening one level
            return self.join(l, r)
        if isinstance(l, list) or isinstance(r, list):
            return self.broadcast(sym, l, r)
        return self.binop(sym, l, r)

    def join(self, l, r):
        def elems(x):
            if isinstance(x, list): return list(x)       # a list contributes its elements
            if x == '': return []                        # lazy: an untouched accumulator is the empty list
            return [x]                                   # a scalar contributes itself
        return Vec(elems(l) + elems(r), 'seq')

    def eval_gsub(self, node):                           # ~[re;rep]target  /  ~[re]target (strip)
        pat = fmt_scalar(self.eval(node[1]))
        rep = None if node[2] is None else fmt_scalar(self.eval(node[2]))
        target = self.eval(node[3])
        if isinstance(target, list):
            return Vec([gsub(pat, rep, fmt_scalar(x)) for x in target], getattr(target, 'flavor', 'field'))
        return gsub(pat, rep, fmt_scalar(target))

    def eval_each(self, node):
        body, vec = node[1], self.eval(node[2])
        if not isinstance(vec, list): raise SyntaxError("each needs a vector on its right")
        saved = self.env.get('x'); out = []
        for el in vec:
            self.env['x'] = el; out.append(self.eval(body))
        if saved is None: self.env.pop('x', None)
        else: self.env['x'] = saved
        return Vec(out, getattr(vec, 'flavor', 'seq'))

    def eval_while(self, node, scan):
        cond, step, seed = node[1], node[2], node[3]
        saved = self.env.get('x')
        x = self.eval(seed); self.env['x'] = x; traj = [x]
        while truthy(self.eval(cond)):
            x = self.eval(step); self.env['x'] = x; traj.append(x)
        if saved is None: self.env.pop('x', None)
        else: self.env['x'] = saved
        return Vec(traj, 'seq') if scan else x

    def fold(self, sym, v):
        if not isinstance(v, list): raise SyntaxError("fold needs a vector")
        if sym == ',':                                   # ,/ = raze: flatten one level
            acc = Vec([], 'seq')
            for el in v: acc = self.join(acc, el)
            return acc
        acc = 1 if sym == '*' else 0
        for el in v:
            if isinstance(acc, list) or isinstance(el, list):
                acc = self.broadcast(sym, acc, el)       # rows -> column-wise reduction
            else:
                acc = self.binop(sym, acc, el)
        return acc

    def broadcast(self, sym, l, r):
        if isinstance(l, list) and isinstance(r, list):
            n = min(len(l), len(r)); out = [self.binop(sym, l[i], r[i]) for i in range(n)]
            fl = 'field' if 'field' in (getattr(l,'flavor','seq'), getattr(r,'flavor','seq')) else 'seq'
        elif isinstance(l, list):
            out = [self.binop(sym, x, r) for x in l]; fl = getattr(l,'flavor','seq')
        else:
            out = [self.binop(sym, l, x) for x in r]; fl = getattr(r,'flavor','seq')
        return Vec(out, fl)

    def binop(self, sym, l, r):
        if sym in '+-*/%':
            a, b = num(l), num(r)
            if sym == '+': return a + b
            if sym == '-': return a - b
            if sym == '*': return a * b
            if sym == '/': return a / b
            if sym == '%': return math.fmod(a, b)
        if sym == '<': return 1 if self.cmp(l, r) < 0 else 0
        if sym == '>': return 1 if self.cmp(l, r) > 0 else 0
        if sym == '=': return 1 if self.cmp(l, r) == 0 else 0
        if sym == '&': return 1 if (truthy(l) and truthy(r)) else 0
        if sym == '|': return 1 if (truthy(l) or  truthy(r)) else 0
        if sym == '~': return 1 if re.search(fmt_scalar(r), fmt_scalar(l)) is not None else 0
        raise SyntaxError(f"dyadic {sym!r} not supported")

    def cmp(self, l, r):
        if looks_numeric(l) and looks_numeric(r):
            a, b = num(l), num(r)
        else:
            a, b = fmt_scalar(l), fmt_scalar(r)
        return -1 if a < b else (1 if a > b else 0)

    def split_str(self, s, sep):
        s = fmt_scalar(s)
        if sep is None: return s.split()
        return re.split(sep, s) if s != '' else []

    def is_inlineable(self, tree):
        """Mirrors the old transpiler's inline() success set: a bare scalar
        expression of these forms is a *filter*; anything else prints its value."""
        t = tree[0]
        if t == 'noun':    return True
        if t == 'fieldat': return self.is_inlineable(tree[1])
        if t == 'index':   return self.is_inlineable(tree[1]) and self.is_inlineable(tree[2])
        if t == 'tern':    return all(self.is_inlineable(x) for x in tree[1:4])
        if t == 'dyad':
            sym, adv = tree[1][1], tree[1][2]
            return adv is None and sym in SCALAR_DYADS and self.is_inlineable(tree[2]) and self.is_inlineable(tree[3])
        if t == 'assign':
            return len(tree[1])==1 and tree[1][0][0]=='field' and self.is_inlineable(tree[2])
        return False

    # ---- output ----
    def emit(self, val, out):
        if isinstance(val, list):
            if any(isinstance(x, list) for x in val):            # a matrix: one OFS-joined row per line
                for row in val:
                    out.append(self.ofs.join(fmt_scalar(x) for x in row) if isinstance(row, list)
                               else fmt_scalar(row))
            elif getattr(val, 'flavor', 'seq') == 'field':
                out.append(self.ofs.join(fmt_scalar(x) for x in val))
            else:
                out.extend(fmt_scalar(x) for x in val)
        else:
            out.append(fmt_scalar(val))

    def exec_record(self, line):
        self.set_record(line)
        out = []
        if self.end:                                             # accumulator mode: per-record stmts are silent
            for tr in self.pr:
                if tr[0] == 'assign': self.store(tr[1], self.eval(tr[2]))
                else: self.eval(tr)                              # effect only
            return out
        trees = self.pr
        if self.multi:
            for i, tr in enumerate(trees):
                if i < len(trees) - 1:
                    if tr[0] == 'assign': self.store(tr[1], self.eval(tr[2]))
                    else: self.eval(tr)                          # side effect only
                else:
                    self.output_last(tr, out)
        else:
            tr = trees[0]
            if tr[0] == 'assign':
                lv = tr[1]; is_f0 = len(lv)==1 and lv[0][0]=='field' and lv[0][1]=='0'
                val = self.eval(tr[2]); self.store(lv, val)
                if is_f0: out.append(self.fields[0])
                # $n (n!=0) / special single assignment: set, print nothing
            else:
                if self.is_inlineable(tr):                       # bare scalar pattern -> filter
                    if truthy(self.eval(tr)): out.append(self.fields[0])
                else:
                    self.emit(self.eval(tr), out)
        return out

    def output_last(self, tr, out):
        if tr[0] == 'assign':
            lv = tr[1]; val = self.eval(tr[2]); self.store(lv, val)
            if len(lv)==1 and lv[0][0]=='var':   self.emit(self.env[lv[0][1]], out)
            elif len(lv)==1 and lv[0][0]=='field': out.append(self.fields[0])
        else:
            self.emit(self.eval(tr), out)

    def run(self, data):
        records = data.split('\n')
        if records and records[-1] == '': records.pop()          # drop final-newline empty
        lines = []
        for rec in records:
            self.nr += 1
            lines.extend(self.exec_record(rec))
        for tr in self.end:                                      # END: once, after EOF, auto-printed
            self.emit(self.eval(tr), lines)
        return '\n'.join(lines)

def interpret(src, data, fs=None):
    stmts = split_statements(fold_adverbs(resolve_dollars(group(lex(src)))))
    if not stmts: raise SyntaxError("empty program")
    prog = []
    for s in stmts:
        if s[0][0] == 'caret':                                   # ^expr -> an END statement
            if len(s) < 2: raise SyntaxError("^ needs an expression")
            prog.append((True, parse(s[1:])))
        else:
            prog.append((False, parse(s)))
    return Interp(prog, fs).run(data)

def main():
    args = sys.argv[1:]
    emit_awk = False; fs = None
    while args and args[0] in ("--emit-awk",) or (args and args[0].startswith("-F")):
        if args[0] == "--emit-awk": emit_awk = True; args = args[1:]
        elif args[0] == "-F":
            if len(args) < 2: sys.exit("kawk.py: -F needs a separator")
            fs = args[1]; args = args[2:]
        else:  # -F<sep> glued
            fs = args[0][2:]; args = args[1:]
    if not args: sys.exit("usage: kawk.py [--emit-awk] [-F sep] [-e '<program>' | <file.kk> | -]   (input on stdin)")
    if args[0] == "-e":
        if len(args) < 2: sys.exit("kawk.py: -e needs a program string")
        src = args[1]
    else:
        src = read_source(args[0])
    src = src.strip()
    if emit_awk:
        print(transpile(src)); return                            # the retired party trick
    sys.stdout.write(interpret(src, sys.stdin.read(), fs))
    sys.stdout.write('\n')

if __name__=="__main__": main()
