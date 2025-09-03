from __future__ import annotations
import re
from dataclasses import dataclass
from typing import List, Union, Optional, Tuple, Set

# -----------------------------
# AST Node Definitions
# -----------------------------
@dataclass
class Term:
    value: str               # normalized form (for FTS MATCH)
    phrase: bool = False     # render inside quotes for MATCH
    original: Optional[str] = None  # original user token (used in LIKE)

@dataclass
class NotNode:
    node: "Node"

@dataclass
class AndNode:
    nodes: List["Node"]

@dataclass
class OrNode:
    nodes: List["Node"]

Node = Union[Term, NotNode, AndNode, OrNode]

# -----------------------------
# Normalization Maps
# -----------------------------
_DASHES = {
    "\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2015",
    "\u2212", "\u2043", "\uFE63", "\uFF0D", "\u00AD"
}
_QUOTE_MAP = {
    "“": '"', "”": '"', "«": '"', "»": '"',
    "„": '"', "‟": '"', "‹": '"', "›": '"'
}

COMPOUND_RE = re.compile(r'^[A-Za-zА-Яа-я0-9]+(?:-[A-Za-zА-Яа-я0-9]+)+$')
ALNUM_RE = re.compile(r'^[A-Za-zА-Яа-я0-9]+$')

def normalize_input(s: str) -> str:
    out = []
    for ch in s:
        if ch in _DASHES:
            out.append('-')
        elif ch in _QUOTE_MAP:
            out.append(_QUOTE_MAP[ch])
        else:
            out.append(ch)
    return "".join(out)

# -----------------------------
# Tokenization
# -----------------------------
def tokenize(q: str):
    """
    Returns sequence of tuples:
      ("OP", value)         - AND / OR / NOT
      ("PHRASE", text)      - quoted phrase
      ("PHRASE_TERM", normalized_space_join, original_with_hyphens)
      ("TERM", text)
    """
    q = normalize_input(q)
    tokens = []
    i, n = 0, len(q)
    while i < n:
        if q[i].isspace():
            i += 1
            continue
        if q[i] == '"':
            j = q.find('"', i + 1)
            if j == -1:
                phrase = q[i + 1:]
                i = n
            else:
                phrase = q[i + 1:j]
                i = j + 1
            phrase = phrase.strip()
            if phrase:
                tokens.append(("PHRASE", phrase))
            continue
        j = i
        while j < n and (not q[j].isspace()) and q[j] != '"':
            j += 1
        word = q[i:j]
        i = j
        if not word:
            continue
        U = word.upper()
        if U in ("AND", "OR", "NOT"):
            tokens.append(("OP", U))
            continue
        if word.startswith('-') and len(word) > 1 and ALNUM_RE.match(word[1:]):
            tokens.append(("OP", "NOT"))
            tokens.append(("TERM", word[1:]))
            continue
        if COMPOUND_RE.match(word):
            parts = [p for p in word.split('-') if p]
            if parts:
                tokens.append(("PHRASE_TERM", " ".join(parts), word))
            continue
        tokens.append(("TERM", word))
    return tokens

# -----------------------------
# AST Construction
# -----------------------------
def build_ast(seq: List[Union[str, Term]]) -> Optional[Node]:
    if not seq:
        return None
    processed: List[Union[str, Node]] = []
    skip = False
    for idx, item in enumerate(seq):
        if skip:
            skip = False
            continue
        if item == "NOT":
            nxt = seq[idx + 1] if idx + 1 < len(seq) else None
            if isinstance(nxt, Term):
                processed.append(NotNode(nxt))
                skip = True
        else:
            processed.append(item)
    groups: List[List[Node]] = [[]]
    for item in processed:
        if item == "OR":
            groups.append([])
        else:
            if isinstance(item, (Term, NotNode)):
                groups[-1].append(item)
    or_nodes: List[Node] = []
    for g in groups:
        if not g:
            continue
        if len(g) == 1:
            or_nodes.append(g[0])
        else:
            or_nodes.append(AndNode(g))
    if not or_nodes:
        return None
    if len(or_nodes) == 1:
        return or_nodes[0]
    return OrNode(or_nodes)

def parse_query(q: str) -> Optional[Node]:
    raw = tokenize(q)
    seq: List[Union[str, Term]] = []
    for t in raw:
        kind = t[0]
        if kind == "OP":
            seq.append(t[1])
        elif kind == "PHRASE":
            seq.append(Term(t[1], phrase=True, original=t[1]))
        elif kind == "PHRASE_TERM":
            seq.append(Term(t[1], phrase=True, original=t[2]))
        elif kind == "TERM":
            seq.append(Term(t[1], phrase=False, original=t[1]))
    return build_ast(seq)

# Compatibility alias
def parse_user_query(q: str) -> Optional[Node]:
    return parse_query(q)

# -----------------------------
# Term Collection (positives / negatives)
# -----------------------------
def collect_terms(node: Optional[Node]) -> List[Term]:
    res: List[Term] = []
    def walk(n: Node):
        if isinstance(n, Term):
            res.append(n)
        elif isinstance(n, NotNode):
            walk(n.node)
        elif isinstance(n, AndNode):
            for c in n.nodes: walk(c)
        elif isinstance(n, OrNode):
            for c in n.nodes: walk(c)
    if node:
        walk(node)
    return res

def split_positive_negative(node: Optional[Node]) -> Tuple[List[Term], List[Term]]:
    positives: List[Term] = []
    negatives: List[Term] = []
    def walk(n: Node, neg_ctx: bool = False):
        if isinstance(n, Term):
            (negatives if neg_ctx else positives).append(n)
        elif isinstance(n, NotNode):
            walk(n.node, True)
        elif isinstance(n, AndNode):
            for c in n.nodes:
                walk(c, neg_ctx)
        elif isinstance(n, OrNode):
            for c in n.nodes:
                walk(c, neg_ctx)
    if node:
        walk(node)
    return positives, negatives

# -----------------------------
# BUILD FTS MATCH (positives only)
# -----------------------------
def build_match_from_positives(positives: List[Term]) -> str:
    """
    Simple AND of positives (space). We ignore original OR structure for simplicity
    (можно усложнить при необходимости).
    """
    if not positives:
        return ""
    parts = []
    for t in positives:
        if t.phrase:
            v = t.value.replace('"', '""')
            parts.append(f"\"{v}\"")
        else:
            parts.append(t.value)
    return " ".join(parts)

def build_fts_query(node: Optional[Node]) -> str:
    positives, _ = split_positive_negative(node)
    return build_match_from_positives(positives)

# -----------------------------
# LIKE fragments & params
# (расширено: добавляем дефисный вариант для фраз без дефиса)
# -----------------------------
def _phrase_to_hyphen_variant(original: str) -> Optional[str]:
    # Если в оригинале пробелы и нет дефисов — предполагаем,
    # что это мог быть исходный F-16, введённый как "F 16".
    if " " in original and "-" not in original:
        candidate = original.replace(" ", "-")
        if COMPOUND_RE.match(candidate):
            return candidate
    return None

def build_like_fragments(node: Optional[Node]) -> List[str]:
    terms = collect_terms(node)
    seen = set()
    frags = []
    for t in terms:
        if not t.original:
            continue
        lows = [t.original.lower()]
        hv = _phrase_to_hyphen_variant(t.original)
        if hv:
            lows.append(hv.lower())
        for key in lows:
            if key in seen:
                continue
            seen.add(key)
            frags.append("(LOWER(title) LIKE ? OR LOWER(summary) LIKE ?)")
    return frags

def build_like_params(node: Optional[Node]) -> List[str]:
    terms = collect_terms(node)
    seen = set()
    params: List[str] = []
    for t in terms:
        if not t.original:
            continue
        variants = [t.original.lower()]
        hv = _phrase_to_hyphen_variant(t.original)
        if hv:
            variants.append(hv.lower())
        for key in variants:
            if key in seen:
                continue
            seen.add(key)
            pat = f"%{key}%"
            params.extend([pat, pat])
    return params

# Полный LIKE SQL (с учётом NOT)
def build_like_sql(node: Optional[Node],
                   title_col: str = "title",
                   summary_col: str = "summary") -> Tuple[str, List[str]]:
    if node is None:
        return "0=1", []
    positives, negatives = split_positive_negative(node)

    pos_patterns: List[str] = []
    neg_patterns: List[str] = []

    def add_patterns(dst: List[str], term: Term):
        variants = [term.original or term.value]
        hv = _phrase_to_hyphen_variant(term.original or term.value)
        if hv:
            variants.append(hv)
        for v in variants:
            pat = f"%{v.lower()}%"
            dst.append(pat)

    for t in positives:
        add_patterns(pos_patterns, t)
    for t in negatives:
        add_patterns(neg_patterns, t)

    def col_expr():
        return f"(LOWER({title_col}) LIKE ? OR LOWER({summary_col}) LIKE ?)"

    parts = []
    params: List[str] = []

    if pos_patterns:
        or_sub = [col_expr() for _ in pos_patterns]
        parts.append("(" + " OR ".join(or_sub) + ")")
        for p in pos_patterns:
            params.extend([p, p])
    else:
        # Только отрицательные — начнём с 1=1
        if neg_patterns:
            parts.append("1=1")
        else:
            return "0=1", []

    for p in neg_patterns:
        parts.append("NOT " + col_expr())
        params.extend([p, p])

    where_sql = " AND ".join(parts) if parts else "0=1"
    return where_sql, params

# -----------------------------
# Debug
# -----------------------------
def ast_to_debug(node: Optional[Node]) -> str:
    if node is None:
        return "<EMPTY>"
    def r(n: Node) -> str:
        if isinstance(n, Term):
            return f"TERM(phrase={n.phrase}, value='{n.value}', original='{n.original}')"
        if isinstance(n, NotNode):
            return f"NOT({r(n.node)})"
        if isinstance(n, AndNode):
            return "AND(" + ", ".join(r(c) for c in n.nodes) + ")"
        if isinstance(n, OrNode):
            return "OR(" + ", ".join(r(c) for c in n.nodes) + ")"
        return "?"
    return r(node)

__all__ = [
    "Term", "NotNode", "AndNode", "OrNode",
    "parse_query", "parse_user_query",
    "build_fts_query",
    "build_like_fragments", "build_like_params", "build_like_sql",
    "ast_to_debug", "normalize_input",
    "split_positive_negative"
]