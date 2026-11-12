"""A small static scanner for C# test sources (no build, no Roslyn).

Good enough for xUnit test files: finds namespaces, classes, attribute lists and the methods they
decorate, with comments removed and string/char literals masked for the structural parse. Used by
the .NET adapter for traceability (`[Trait("Req", ...)]`), test smells and the review-test filter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: attribute names that make a method a test (suffix match covers SkippableFact, StaFact, ...)
TEST_ATTR_SUFFIXES = ("Fact", "Theory")
TEST_ATTR_NAMES = {"Property"}  # FsCheck.Xunit

_IDENT = re.compile(r"[A-Za-z_]\w*")
_TYPE_DECL = re.compile(
    r"\b(?:class|struct|interface|record(?:\s+(?:class|struct))?)\s+([A-Za-z_]\w*)"
)
_NAMESPACE = re.compile(r"\bnamespace\s+([\w.]+)\s*([;{])")
_STRING_ARG = re.compile(r'^\s*@?"((?:[^"\\]|\\.)*)"\s*$', re.S)
_NOT_TYPE_NAMES = {"where", "new", "class", "struct"}


@dataclass
class CsAttribute:
    name: str  # last segment, "Attribute" suffix removed: Fact, Trait, InlineData
    args: str  # raw argument text inside the parentheses ("" if none), strings intact

    def string_args(self) -> list[str] | None:
        """The arguments as string literals, or None if any argument is not a plain literal."""
        parts = split_top_level(self.args, ",") if self.args.strip() else []
        out = []
        for part in parts:
            m = _STRING_ARG.match(part)
            if not m:
                return None
            out.append(m.group(1))
        return out


@dataclass
class CsClass:
    name: str
    full_name: str  # Namespace.Outer+Inner (xUnit fully qualified name style)
    attrs: list[CsAttribute]
    start: int
    end: int  # body end (closing brace index)
    parent: CsClass | None = None


@dataclass
class CsMethod:
    name: str
    attrs: list[CsAttribute]
    line: int
    body: str  # body text with comments removed and literals masked ("" if none)
    cls: CsClass | None = None

    @property
    def is_test(self) -> bool:
        return any(
            a.name in TEST_ATTR_NAMES or a.name.endswith(TEST_ATTR_SUFFIXES) for a in self.attrs
        )

    @property
    def qualified(self) -> str:
        return f"{self.cls.name}.{self.name}" if self.cls else self.name

    def class_chain(self) -> list[CsClass]:
        chain = []
        c = self.cls
        while c is not None:
            chain.append(c)
            c = c.parent
        return chain


@dataclass
class CsFile:
    classes: list[CsClass] = field(default_factory=list)
    methods: list[CsMethod] = field(default_factory=list)

    @property
    def tests(self) -> list[CsMethod]:
        return [m for m in self.methods if m.is_test]


# ---------- lexing ----------
def _skip_string(text: str, i: int) -> int:
    """i points at the start of a string literal (prefix $ / @ or the quote). Returns the index
    just after it. Handles regular, verbatim, interpolated and raw (\"\"\") strings."""
    n = len(text)
    j = i
    dollars = 0
    verbatim = False
    while j < n and text[j] in "$@":
        if text[j] == "$":
            dollars += 1
        else:
            verbatim = True
        j += 1
    q = j
    while q < n and text[q] == '"':
        q += 1
    quotes = q - j
    if quotes >= 3:  # raw string literal: ends at the same number of quotes
        end = text.find('"' * quotes, q)
        return n if end < 0 else end + quotes
    if quotes == 2:  # empty string ""
        return q
    j += 1  # past the opening quote
    while j < n:
        ch = text[j]
        if verbatim:
            if ch == '"':
                if j + 1 < n and text[j + 1] == '"':
                    j += 2
                    continue
                return j + 1
        else:
            if ch == "\\":
                j += 2
                continue
            if ch == '"':
                return j + 1
            if ch == "\n":
                return j
        if dollars and ch == "{":
            if j + 1 < n and text[j + 1] == "{":
                j += 2
                continue
            j = _skip_hole(text, j + 1)
            continue
        j += 1
    return n


def _skip_hole(text: str, j: int) -> int:
    """Inside an interpolation hole: skip to just after the matching `}`."""
    n = len(text)
    depth = 1
    while j < n:
        ch = text[j]
        if ch == '"' or (ch in "$@" and j + 1 < n and text[j + 1] in '"$@'):
            j = _skip_string(text, j)
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return n


def enclosed_lines(text: str) -> set[int]:
    """1-based numbers of the lines that start inside a string literal or a block comment
    (a line inserted before them would change the literal or end up in the comment)."""
    spans: list[tuple[int, int]] = []
    strip(text, spans)
    starts = [0] + [i + 1 for i, ch in enumerate(text) if ch == "\n"]
    return {n for n, s in enumerate(starts, 1) if any(a < s < b for a, b in spans)}


def strip(text: str, spans: list[tuple[int, int]] | None = None) -> tuple[str, str]:
    """(code, mask): code = comments and preprocessor lines blanked; mask = code with string and
    char literal contents blanked too. Both keep every offset and newline of the input. `spans`
    (if given) receives the (start, end) offsets of every string literal and block comment."""
    n = len(text)
    code = list(text)
    mask = list(text)

    def blank(buf: list[str], a: int, b: int) -> None:
        for k in range(a, min(b, n)):
            if buf[k] != "\n":
                buf[k] = " "

    i = 0
    line_start = True
    while i < n:
        ch = text[i]
        if ch == "\n":
            line_start = True
            i += 1
            continue
        if line_start and ch in " \t":
            i += 1
            continue
        if line_start and ch == "#":  # preprocessor directive
            end = text.find("\n", i)
            end = n if end < 0 else end
            blank(code, i, end)
            blank(mask, i, end)
            i = end
            continue
        line_start = False
        nxt = text[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            end = text.find("\n", i)
            end = n if end < 0 else end
            blank(code, i, end)
            blank(mask, i, end)
            i = end
        elif ch == "/" and nxt == "*":
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            blank(code, i, end)
            blank(mask, i, end)
            if spans is not None:
                spans.append((i, end))
            i = end
        elif ch == '"' or (ch in "$@" and nxt in '"$@' and _starts_string(text, i)):
            end = _skip_string(text, i)
            blank(mask, i, end)
            if spans is not None:
                spans.append((i, end))
            i = end
        elif ch == "'":
            end = i + 1
            while end < n and text[end] not in "'\n":
                end += 2 if text[end] == "\\" else 1
            end = min(end + 1, n)
            blank(mask, i, end)
            i = end
        else:
            i += 1
    return "".join(code), "".join(mask)


def _starts_string(text: str, i: int) -> bool:
    j = i
    while j < len(text) and text[j] in "$@":
        j += 1
    return j < len(text) and text[j] == '"' and j - i <= 4


# ---------- structure helpers ----------
_OPEN = {"(": ")", "[": "]", "{": "}"}
_CLOSE = {v: k for k, v in _OPEN.items()}


def match_forward(mask: str, i: int) -> int:
    """Index of the bracket closing the one at i (any of ([{ nesting), or len(mask)."""
    stack = [mask[i]]
    j = i + 1
    while j < len(mask):
        ch = mask[j]
        if ch in _OPEN:
            stack.append(ch)
        elif ch in _CLOSE:
            if stack and stack[-1] == _CLOSE[ch]:
                stack.pop()
            if not stack:
                return j
        j += 1
    return len(mask)


def match_backward(mask: str, i: int) -> int:
    """Index of the bracket opening the one closed at i, or -1."""
    stack = [mask[i]]
    j = i - 1
    while j >= 0:
        ch = mask[j]
        if ch in _CLOSE:
            stack.append(ch)
        elif ch in _OPEN:
            if stack and stack[-1] == _OPEN[ch]:
                stack.pop()
            if not stack:
                return j
        j -= 1
    return -1


def split_top_level(text: str, sep: str) -> list[str]:
    """Split on `sep` outside brackets and string literals."""
    _, mask = strip(text)
    parts, depth, last = [], 0, 0
    for k, ch in enumerate(mask):
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            depth -= 1
        elif ch == sep and depth == 0:
            parts.append(text[last:k])
            last = k + 1
    parts.append(text[last:])
    return parts


def parse_attribute_section(code: str) -> list[CsAttribute]:
    """`[Fact, Trait("Req", "X")]` (brackets included) -> attributes. Targets like
    `assembly:` / `return:` are dropped."""
    inner = code.strip()[1:-1]
    inner = re.sub(r"^\s*\w+\s*:(?!:)", "", inner)
    attrs = []
    for part in split_top_level(inner, ","):
        part = part.strip()
        if not part:
            continue
        paren = part.find("(")
        name = (part if paren < 0 else part[:paren]).strip()
        args = "" if paren < 0 else part[paren + 1 : part.rfind(")")]
        name = name.split(".")[-1].split("::")[-1]
        name = re.sub(r"<.*>$", "", name)
        if name.endswith("Attribute") and name != "Attribute":
            name = name[: -len("Attribute")]
        attrs.append(CsAttribute(name, args))
    return attrs


def _prev_nonspace(mask: str, i: int) -> int:
    j = i - 1
    while j >= 0 and mask[j].isspace():
        j -= 1
    return j


def attributes_before(code: str, mask: str, pos: int) -> list[CsAttribute]:
    """Attribute sections right before a declaration starting at `pos` (modifiers allowed in
    between)."""
    j = _prev_nonspace(mask, pos)
    # skip modifiers between the attributes and the declaration keyword
    while j >= 0 and (mask[j].isalnum() or mask[j] == "_"):
        k = j
        while k >= 0 and (mask[k].isalnum() or mask[k] == "_"):
            k -= 1
        j = _prev_nonspace(mask, k + 1)
    sections = []
    while j >= 0 and mask[j] == "]":
        start = match_backward(mask, j)
        if start < 0:
            break
        sections.append(code[start : j + 1])
        j = _prev_nonspace(mask, start)
    attrs: list[CsAttribute] = []
    for sec in reversed(sections):
        attrs += parse_attribute_section(sec)
    return attrs


def _namespace_spans(mask: str) -> list[tuple[int, int, str]]:
    spans = []
    for m in _NAMESPACE.finditer(mask):
        if m.group(2) == ";":
            spans.append((m.end(), len(mask), m.group(1)))
        else:
            spans.append((m.end() - 1, match_forward(mask, m.end() - 1), m.group(1)))
    return spans


def _attribute_groups(mask: str) -> list[tuple[int, int]]:
    """(start, end) of runs of attribute sections in member position: a `[` whose previous
    non-space character is `{`, `}`, `;`, `]` or the start of the file."""
    groups = []
    i = 0
    n = len(mask)
    while i < n:
        if mask[i] == "[":
            p = _prev_nonspace(mask, i)
            if p < 0 or mask[p] in "{};]":
                start = i
                end = match_forward(mask, i)
                while True:
                    q = end + 1
                    while q < n and mask[q].isspace():
                        q += 1
                    if q < n and mask[q] == "[":
                        end = match_forward(mask, q)
                    else:
                        break
                groups.append((start, end + 1))
                i = end + 1
                continue
        i += 1
    return groups


_HEADER_END = re.compile(r"[({;=]")


def scan(text: str) -> CsFile:
    code, mask = strip(text)
    result = CsFile()
    namespaces = _namespace_spans(mask)

    # classes (with their body span)
    for m in _TYPE_DECL.finditer(mask):
        name = m.group(1)
        if name in _NOT_TYPE_NAMES:
            continue
        brace = mask.find("{", m.end())
        semi = mask.find(";", m.end())
        if brace < 0 or 0 <= semi < brace:
            continue  # no body (e.g. `record Point(int X, int Y);`)
        end = match_forward(mask, brace)
        attrs = attributes_before(code, mask, m.start())
        result.classes.append(CsClass(name, name, attrs, brace, end))
    for c in result.classes:
        outer = [o for o in result.classes if o is not c and o.start < c.start and c.end <= o.end]
        c.parent = max(outer, key=lambda o: o.start) if outer else None
    for c in result.classes:
        chain = []
        o: CsClass | None = c
        while o is not None:
            chain.append(o.name)
            o = o.parent
        ns = ".".join(name for a, b, name in namespaces if a <= c.start <= b)
        c.full_name = ".".join(x for x in (ns, "+".join(reversed(chain))) if x)

    # methods: attribute group, then a header ending in `(`
    for start, end in _attribute_groups(mask):
        m2 = _HEADER_END.search(mask, end)
        if not m2 or m2.group() != "(":
            continue
        header = mask[end : m2.start()]
        if _TYPE_DECL.search(header) or "[" in header:
            continue
        idents = _IDENT.findall(re.sub(r"<[^<>]*>", "", header))
        if not idents:
            continue
        close = match_forward(mask, m2.start())
        body = ""
        k = close + 1
        while k < len(mask) and mask[k].isspace():
            k += 1
        # skip generic constraints (`where T : ...`) up to the body
        rest = re.match(r"[^{;=]*", mask[k:])
        k += rest.end() if rest else 0
        if k < len(mask) and mask[k] == "{":
            body = mask[k + 1 : match_forward(mask, k)]
        elif mask.startswith("=>", k):
            semi = mask.find(";", k)
            body = mask[k + 2 : semi if semi >= 0 else len(mask)]
        attrs = []
        for sec_start, sec_end in _sections(mask, start, end):
            attrs += parse_attribute_section(code[sec_start:sec_end])
        owners = [c for c in result.classes if c.start < start < c.end]
        owner = max(owners, key=lambda c: c.start) if owners else None
        line = text.count("\n", 0, m2.start()) + 1
        result.methods.append(CsMethod(idents[-1], attrs, line, body, owner))
    return result


def _sections(mask: str, start: int, end: int) -> list[tuple[int, int]]:
    out = []
    i = start
    while i < end:
        if mask[i] == "[":
            j = match_forward(mask, i)
            out.append((i, j + 1))
            i = j + 1
        else:
            i += 1
    return out


# ---------- assertion detection ----------
#: a call that checks something: Assert.X / AssertX(...) helpers, FluentAssertions / Shouldly,
#: Assert.Throws / Record.Exception, Moq Verify, explicit Fail.
ASSERTION = re.compile(
    r"\bAssert\w*\s*[.(<]|\.Should\w*\s*\(|\bShould\w*\s*\(|\bThrows\w*\s*[(<]"
    r"|\bVerify\w*\s*\(|\bFail\s*\("
)
_CALL = re.compile(r"\b([A-Za-z_]\w*)\s*(?:<[^<>()]*>)?\s*\(")
_NOT_METHODS = {
    "if",
    "while",
    "for",
    "foreach",
    "switch",
    "catch",
    "using",
    "lock",
    "fixed",
    "return",
    "new",
    "nameof",
    "typeof",
    "sizeof",
    "default",
    "when",
    "checked",
    "unchecked",
    "await",
    "throw",
    "base",
    "this",
}
_NOT_DECL_PREV = {
    "new",
    "return",
    "await",
    "throw",
    "else",
    "in",
    "is",
    "as",
    "case",
    "yield",
    "when",
    "out",
    "ref",
    "not",
    "and",
    "or",
}


def method_bodies(mask: str) -> dict[str, list[str]]:
    """Every method declared in a (masked) file: name -> bodies. A heuristic: `Type Name(...)`
    followed by `{` or `=>`."""
    found: dict[str, list[str]] = {}
    for m in _CALL.finditer(mask):
        name = m.group(1)
        if name in _NOT_METHODS:
            continue
        before = mask[: m.start()].rstrip()
        if not before or not (before[-1].isalnum() or before[-1] in "_>]?"):
            continue
        prev = re.search(r"(\w+)$", before)
        if prev and prev.group(1) in _NOT_DECL_PREV:
            continue
        close = match_forward(mask, m.end() - 1)
        k = close + 1
        rest = re.match(r"\s*(?:where\b[^{;=]*)?", mask[k:])
        k += rest.end() if rest else 0
        if mask.startswith("{", k):
            body = mask[k + 1 : match_forward(mask, k)]
        elif mask.startswith("=>", k):
            semi = mask.find(";", k)
            body = mask[k + 2 : semi if semi >= 0 else len(mask)]
        else:
            continue
        found.setdefault(name, []).append(body)
    return found


def has_assertion(body: str, index: dict[str, list[str]], depth: int = 3) -> bool:
    """True if the body asserts directly or calls a helper (by name, from `index`) that does,
    following helpers up to `depth` levels."""
    seen: set[str] = set()

    def visit(text: str, level: int) -> bool:
        if ASSERTION.search(text):
            return True
        if level == 0:
            return False
        for name in {c.group(1) for c in _CALL.finditer(text)} - seen:
            seen.add(name)
            if any(visit(b, level - 1) for b in index.get(name, [])):
                return True
        return False

    return visit(body, depth)
