"""Split a module that defines several top-level classes into one file per class (BE-0411).

Applies the item's rules 1-3 and 5 mechanically, so a batch never hand-copies a class body or
forgets an intra-package import. It parses with `libcst` rather than the standard library's `ast`
because `ast` drops comments, and many of the classes this moves carry a leading comment that
explains a design choice the split must not lose.

The two calls the item leaves to human judgment stay human: rule 4's ownership question for
module-level code with more than one referencing owner, and the `D100` module docstring each new
file under a `DOCSTRING_PATHS` entry needs. This script decides only the unambiguous half of rule 4
(no referencing owner, or exactly one) and reports the rest.
"""

from __future__ import annotations

import argparse
import keyword
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import libcst as cst

# Where a split file's non-class, non-function pieces land. `_functions.py` is rule 2's group file.
# `_shared.py` holds module-level code with no single owner, which the item's rule 4 places in
# `__init__.py`: that placement cannot work here, because `__init__.py` re-exports the very modules
# that would read the name back, so the name has to be bound before those imports run — and an
# assignment above them trips ruff's E402. A sibling module has neither problem.
FUNCTIONS_MODULE = "_functions"
SHARED_MODULE = "_shared"
# Module-level code that binds nothing — a `model_rebuild()` call, a registration — is rule 4's
# no-single-owner case in its purest form, and `__init__.py` is where the item's design puts it.
# Binding nothing is exactly what makes that placement safe: appended after the re-export imports it
# trips neither E402 nor a cycle, and every name it reads is already in scope.
INIT_MODULE = "__init__"
RESERVED_STEMS = frozenset({"__init__", FUNCTIONS_MODULE, SHARED_MODULE})


class SplitError(Exception):
    """A file this script refuses to split, because doing so would need a judgment call."""


def snake_case(name: str) -> str:
    """Render a class name as its module filename, keeping any leading underscores.

    A name that lands on a Python keyword takes PEP 8's trailing underscore: the scenario schema's
    `If` step would otherwise want `if.py`, which no import statement can name.
    """
    underscores = len(name) - len(name.lstrip("_"))
    core = name[underscores:]
    core = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", core)
    core = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", core)
    stem = "_" * underscores + core.lower()
    return f"{stem}_" if keyword.iskeyword(stem) else stem


class _OwnImports(cst.CSTVisitor):
    """The names one function imports for itself, excluding the nested scopes that own their own."""

    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Import(self, node: cst.Import) -> bool:
        self._record(node)
        return False

    def visit_ImportFrom(self, node: cst.ImportFrom) -> bool:
        self._record(node)
        return False

    def visit_FunctionDef(self, _node: cst.FunctionDef) -> bool:
        return False

    def visit_ClassDef(self, _node: cst.ClassDef) -> bool:
        return False

    def _record(self, node: cst.Import | cst.ImportFrom) -> None:
        names = node.names
        if isinstance(names, cst.ImportStar):
            return
        for alias in names:
            self.names.add(_alias_binding(alias))


def _own_imports(node: cst.FunctionDef) -> set[str]:
    collected = _OwnImports()
    node.body.visit(collected)
    return collected.names


# The subscript heads whose arguments are types, so a quoted argument under one is a forward
# reference. Anywhere else — `row["Beta"]` — the string is a value, and reading it as a name
# fabricates a reference to a sibling the file never uses.
_TYPING_HEADS = frozenset(
    {
        "Annotated",
        "Awaitable",
        "Callable",
        "ClassVar",
        "Coroutine",
        "Dict",
        "Final",
        "FrozenSet",
        "Generator",
        "Iterable",
        "Iterator",
        "List",
        "Mapping",
        "Optional",
        "Sequence",
        "Set",
        "Tuple",
        "Type",
        "TypeGuard",
        "Union",
        "dict",
        "frozenset",
        "list",
        "set",
        "tuple",
        "type",
    }
)


def _subscript_head(node: cst.Subscript) -> str | None:
    """The rightmost name of a subscript's target — `Literal` for both `Literal[…]` forms."""
    value: cst.BaseExpression = node.value
    if isinstance(value, cst.Attribute):
        value = value.attr
    return value.value if isinstance(value, cst.Name) else None


class _References(cst.CSTVisitor):
    """Collect the names a subtree reads, separating annotation-only reads from runtime ones.

    The separation is what lets rule 5 hold without a special case: a sibling referenced only from
    an annotation can be imported under `TYPE_CHECKING`, where a cycle between two split-off
    classes cannot form.
    """

    def __init__(self) -> None:
        self.all: set[str] = set()
        self.runtime: set[str] = set()
        self.rebound: set[str] = set()
        self.annotated: set[str] = set()
        self.uses_file: bool = False
        self.uses_name: bool = False
        self._annotation_depth = 0
        # One frame per enclosing function, holding the names that function imports for itself.
        self._local_imports: list[set[str]] = []

    def visible(self) -> set[str]:
        """The names this file still needs a module-level import for."""
        return self.all

    def _satisfied_locally(self, name: str) -> bool:
        """Whether an enclosing function already imports *name* where this read happens.

        Scoped to the enclosing frames, never file-wide: a name one method imports for itself says
        nothing about a read in a sibling method or at module level, and suppressing the import for
        those too leaves the generated file reading a name nothing binds. An annotation is never
        satisfied this way — it is evaluated where the `def` is, not where the local import runs.
        """
        return self._annotation_depth == 0 and any(name in frame for frame in self._local_imports)

    def _record(self, name: str) -> None:
        if self._satisfied_locally(name):
            return
        self.all.add(name)
        if self._annotation_depth == 0:
            self.runtime.add(name)
        else:
            self.annotated.add(name)

    def visit_Name(self, node: cst.Name) -> bool:
        if node.value == "__file__":
            self.uses_file = True
        elif node.value == "__name__":
            self.uses_name = True
        self._record(node.value)
        return False

    def visit_Attribute(self, node: cst.Attribute) -> bool:
        # `.attr` is a member name, never a module-level binding this file has to import.
        node.value.visit(self)
        return False

    def visit_Arg(self, node: cst.Arg) -> bool:
        # Same for a keyword argument's name.
        node.value.visit(self)
        return False

    def visit_Param(self, node: cst.Param) -> bool:
        # A parameter binds its own name; only its annotation and default read outer names.
        if node.annotation is not None:
            node.annotation.visit(self)
        if node.default is not None:
            node.default.visit(self)
        return False

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        for decorator in node.decorators:
            decorator.visit(self)
        if node.type_parameters is not None:
            # A PEP 695 bound reads names too: `def f[F: Callable[..., Any]](…)` needs `Callable`.
            node.type_parameters.visit(self)
        node.params.visit(self)
        if node.returns is not None:
            node.returns.visit(self)
        # The signature is evaluated where the `def` is, so only the body sees this frame.
        self._local_imports.append(_own_imports(node))
        node.body.visit(self)
        self._local_imports.pop()
        return False

    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        for decorator in node.decorators:
            decorator.visit(self)
        if node.type_parameters is not None:
            node.type_parameters.visit(self)
        for base in node.bases:
            base.visit(self)
        for kwarg in node.keywords:
            kwarg.visit(self)
        node.body.visit(self)
        return False

    def visit_Subscript(self, node: cst.Subscript) -> bool:
        """Read a subscript's elements where the subscript itself is evaluated.

        Only a quoted forward reference is annotation context. A type alias writes one straight
        into a subscript, as `Callable[[Driver], "AlertEvent | None"]` does, so those are read the
        way an annotation is. Treating the *whole* subscript as annotation context instead would
        class `table[Beta.KEY]`'s `Beta` as annotation-only, which the cycle breaker is then free to
        defer under `TYPE_CHECKING` — leaving the name undefined at the moment it is used.
        """
        node.value.visit(self)
        # `Literal["Beta"]`'s string is a value, not a forward reference. So is `row["Beta"]`'s.
        # Parsing either as one imports a sibling the file never uses, and two such strings can
        # close a cycle out of nothing.
        head = _subscript_head(node)
        # `Literal` is never a forward-reference position, however deep in an annotation it sits.
        typed = head != "Literal" and (self._annotation_depth > 0 or head in _TYPING_HEADS)
        for element in node.slice:
            index = element.slice
            if isinstance(index, cst.Index) and isinstance(index.value, cst.SimpleString):
                if not typed:
                    continue
                self._annotation_depth += 1
                self._visit_quoted(index.value)
                self._annotation_depth -= 1
            else:
                # `Literal[Colour.RED]` still reads `Colour`, so a non-string element is never
                # skipped, whatever the subscript's head.
                element.visit(self)
        return False

    def visit_TypeAlias(self, node: cst.TypeAlias) -> bool:
        # A PEP 695 alias evaluates its value lazily, so the names in it are annotation reads. Read
        # as runtime ones they would keep a cycle unbreakable that deferral could have broken.
        if node.type_parameters is not None:
            node.type_parameters.visit(self)
        self._annotation_depth += 1
        node.value.visit(self)
        self._annotation_depth -= 1
        return False

    def visit_Annotation(self, node: cst.Annotation) -> bool:
        self._annotation_depth += 1
        inner = node.annotation
        if isinstance(inner, cst.SimpleString):
            # A quoted forward reference still names something this file must import.
            self._visit_quoted(inner)
        else:
            inner.visit(self)
        self._annotation_depth -= 1
        return False

    def _visit_quoted(self, node: cst.SimpleString) -> None:
        text = node.evaluated_value
        if not isinstance(text, str):
            return
        try:
            expression = cst.parse_expression(text)
        except cst.ParserSyntaxError:
            return
        expression.visit(self)

    def visit_AnnAssign(self, node: cst.AnnAssign) -> bool:
        # `x: int = 1` binds `x`; only the annotation and the value read outer names. Without this,
        # a dataclass field that happens to share a sibling's name reads as a reference to it, and
        # the bogus import it produces can close a cycle between two split-off files.
        self._visit_target(node.target)
        node.annotation.visit(self)
        if node.value is not None:
            node.value.visit(self)
        return False

    def visit_AssignTarget(self, node: cst.AssignTarget) -> bool:
        self._visit_target(node.target)
        return False

    def visit_For(self, node: cst.For) -> bool:
        self._visit_target(node.target)
        node.iter.visit(self)
        node.body.visit(self)
        if node.orelse is not None:
            node.orelse.visit(self)
        return False

    def visit_CompFor(self, node: cst.CompFor) -> bool:
        self._visit_target(node.target)
        node.iter.visit(self)
        for condition in node.ifs:
            condition.visit(self)
        if node.inner_for_in is not None:
            node.inner_for_in.visit(self)
        return False

    def visit_AsName(self, _node: cst.AsName) -> bool:
        # `with … as x` / `except … as x` bind `x` rather than reading it.
        return False

    def _visit_target(self, target: cst.BaseExpression) -> None:
        """Record only the reads a binding target performs — `obj.attr` reads `obj`, `x` reads none."""
        if not isinstance(target, cst.Name):
            target.visit(self)

    def visit_Import(self, _node: cst.Import) -> bool:
        # A name an inner `import` binds is not a read. `SqlRepository` imports its ORM models
        # inside the methods that use them — rule 5's treatment, applied before this split — and
        # carrying the module-level import along too leaves it with nothing to annotate, an F401.
        return False

    def visit_ImportFrom(self, _node: cst.ImportFrom) -> bool:
        return False

    def visit_Global(self, node: cst.Global) -> bool:
        for item in node.names:
            self.rebound.add(item.name.value)
        return False


def _references(node: cst.CSTNode) -> _References:
    collected = _References()
    node.visit(collected)
    return collected


def _alias_binding(alias: cst.ImportAlias) -> str:
    """The name an `import` statement's single alias binds in the importing module."""
    if alias.asname is not None:
        target = alias.asname.name
        if isinstance(target, cst.Name):
            return target.value
        raise SplitError(f"unsupported import alias target: {type(target).__name__}")
    name: cst.BaseExpression = alias.name
    while isinstance(name, cst.Attribute):
        # `import a.b.c` binds `a`, not `a.b.c`.
        name = name.value
    if not isinstance(name, cst.Name):
        raise SplitError(f"unsupported import name: {type(name).__name__}")
    return name.value


def _import_of(statement: cst.SimpleStatementLine) -> cst.Import | cst.ImportFrom:
    """The single `import` a header statement line holds."""
    small = statement.body[0]
    if not isinstance(small, (cst.Import, cst.ImportFrom)):
        raise SplitError(f"expected an import, found {type(small).__name__}")
    return small


def _binds_type_checking(statement: _Statement) -> bool:
    """Whether this header statement already imports `TYPE_CHECKING` under that name."""
    if not isinstance(statement, cst.SimpleStatementLine):
        return False
    names = _import_of(statement).names
    if isinstance(names, cst.ImportStar):
        return False
    return any(_alias_binding(alias) == "TYPE_CHECKING" for alias in names)


def _filter_import(
    node: cst.Import | cst.ImportFrom, used: set[str]
) -> cst.Import | cst.ImportFrom | None:
    """Narrow one import statement to the aliases whose bound name is actually read.

    Returns None when none of them is. Dropping the unread aliases is not cosmetic: ruff's `F401`
    is on with no per-file ignore, so carrying an unread import into a split file fails the gate.
    """
    names = node.names
    if isinstance(names, cst.ImportStar):
        raise SplitError("a star import hides which names this module binds")
    aliases = [
        alias.with_changes(comma=cst.MaybeSentinel.DEFAULT)
        for alias in names
        if _alias_binding(alias) in used
    ]
    if not aliases:
        return None
    return node.with_changes(names=aliases)


def _is_main_guard(node: cst.BaseStatement) -> bool:
    """Whether this is the `if __name__ == "__main__":` entry point a package needs to keep.

    Matched by shape, not by text. A substring test also accepts `if not (__name__ == "__main__"):`,
    whose body runs on every import — moved into `__main__.py` its condition is never true again,
    so the statement stops running with nothing raised.
    """
    if not isinstance(node, cst.If) or not isinstance(node.test, cst.Comparison):
        return False
    test = node.test
    if not isinstance(test.left, cst.Name) or test.left.value != "__name__":
        return False
    if len(test.comparisons) != 1:
        return False
    target = test.comparisons[0]
    return isinstance(target.operator, cst.Equal) and (
        isinstance(target.comparator, cst.SimpleString)
        and target.comparator.evaluated_value == "__main__"
    )


def _is_type_checking_block(node: cst.BaseStatement) -> bool:
    """Whether this is a plain `if TYPE_CHECKING:` guard — the test matched by shape, not by text.

    A substring match also accepts `if not TYPE_CHECKING:`, whose runtime shim would then be hoisted
    into a type-checking-only block and never run.
    """
    return (
        isinstance(node, cst.If)
        and isinstance(node.test, cst.Name)
        and (node.test.value == "TYPE_CHECKING")
    )


def _is_future_import(node: cst.CSTNode) -> bool:
    return (
        isinstance(node, cst.ImportFrom)
        and isinstance(node.module, cst.Name)
        and node.module.value == "__future__"
    )


@dataclass
class _Declaration:
    """One top-level class or function, and the file it moves to."""

    name: str
    stem: str
    node: cst.ClassDef | cst.FunctionDef
    is_class: bool


@dataclass
class _ModuleLevel:
    """One module-level statement that is neither an import nor a declaration (rule 4)."""

    node: cst.SimpleStatementLine
    binds: tuple[str, ...]
    reads: _References


@dataclass
class _Parsed:
    """The original module, taken apart into the pieces the split reassembles."""

    module: cst.Module
    docstring: cst.SimpleStatementLine | None = None
    future: cst.SimpleStatementLine | None = None
    imports: list[cst.SimpleStatementLine] = field(default_factory=list)
    type_checking: list[cst.SimpleStatementLine] = field(default_factory=list)
    declarations: list[_Declaration] = field(default_factory=list)
    module_level: list[_ModuleLevel] = field(default_factory=list)
    dunder_all: list[str] | None = None
    main_guard: cst.If | None = None


def _target_names(target: cst.BaseExpression) -> list[str]:
    """Every name one assignment target binds, unpacking `a, b = …` down to its elements."""
    if isinstance(target, cst.Name):
        return [target.value]
    if isinstance(target, (cst.Tuple, cst.List)):
        return [name for element in target.elements for name in _target_names(element.value)]
    if isinstance(target, cst.StarredElement):
        return _target_names(target.value)
    return []  # `obj.attr` / `seq[i]` bind nothing this module re-exports


def _assign_targets(statement: cst.SimpleStatementLine) -> tuple[str, ...]:
    names: list[str] = []
    for small in statement.body:
        if isinstance(small, cst.Assign):
            for target in small.targets:
                names.extend(_target_names(target.target))
        elif isinstance(small, (cst.AnnAssign, cst.AugAssign)):
            names.extend(_target_names(small.target))
        elif isinstance(small, cst.TypeAlias):
            names.append(small.name.value)
    return tuple(names)


def _literal_string_list(statement: cst.SimpleStatementLine) -> list[str] | None:
    """Read an `__all__ = [...]` of plain string literals, or None if it is computed."""
    small = statement.body[0]
    if not isinstance(small, (cst.Assign, cst.AnnAssign)):
        return None
    value = small.value
    if value is None:
        return None
    if not isinstance(value, (cst.List, cst.Tuple)):
        return None
    names: list[str] = []
    for element in value.elements:
        if not isinstance(element.value, cst.SimpleString):
            return None
        text = element.value.evaluated_value
        if not isinstance(text, str):
            return None
        names.append(text)
    return names


def _type_checking_imports(block: cst.If) -> list[cst.SimpleStatementLine]:
    """The import lines of an `if TYPE_CHECKING:` block, refusing any shape the split can't carry."""
    if block.orelse is not None:
        raise SplitError("a TYPE_CHECKING block with an `else` needs a runtime shim kept")
    lines: list[cst.SimpleStatementLine] = []
    for inner in block.body.body:
        if not isinstance(inner, cst.SimpleStatementLine):
            raise SplitError("a TYPE_CHECKING block holds more than plain imports")
        if len(inner.body) != 1:
            raise SplitError("a `;`-joined import line is ambiguous to split")
        lines.append(inner)
    return lines


def _parse(source: str) -> _Parsed:
    module = cst.parse_module(source)
    parsed = _Parsed(module=module)
    for index, statement in enumerate(module.body):
        if isinstance(statement, (cst.ClassDef, cst.FunctionDef)):
            is_class = isinstance(statement, cst.ClassDef)
            stem = snake_case(statement.name.value) if is_class else FUNCTIONS_MODULE
            parsed.declarations.append(
                _Declaration(
                    name=statement.name.value, stem=stem, node=statement, is_class=is_class
                )
            )
            continue
        if _is_main_guard(statement):
            assert isinstance(statement, cst.If)
            # `python -m pkg` runs `__main__.py`, never `__init__.py`, so the guard has to move
            # there or the entry point disappears without a word — `scripts/install.sh` and the
            # web-e2e job both invoke one this way.
            parsed.main_guard = statement
            continue
        if _is_type_checking_block(statement):
            assert isinstance(statement, cst.If)
            parsed.type_checking.extend(_type_checking_imports(statement))
            continue
        if not isinstance(statement, cst.SimpleStatementLine):
            raise SplitError(
                f"unsupported module-level {type(statement).__name__} statement; split by hand"
            )
        small = statement.body[0]
        if index == 0 and isinstance(small, cst.Expr) and isinstance(small.value, cst.SimpleString):
            parsed.docstring = statement
            continue
        if isinstance(small, (cst.Import, cst.ImportFrom)):
            if len(statement.body) != 1:
                raise SplitError("a `;`-joined import line is ambiguous to split")
            if _is_future_import(small):
                parsed.future = statement
            else:
                parsed.imports.append(statement)
            continue
        targets = _assign_targets(statement)
        if targets == ("__all__",):
            names = _literal_string_list(statement)
            if names is None:
                raise SplitError(
                    "an `__all__` that is not a plain list of string literals cannot be carried "
                    "across the split"
                )
            parsed.dunder_all = names
            continue
        parsed.module_level.append(
            _ModuleLevel(node=statement, binds=targets, reads=_references(statement))
        )
    return parsed


def _check_splittable(parsed: _Parsed, name: str, *, allow_file_paths: bool) -> None:
    if parsed.dunder_all is not None:
        bound = {declaration.name for declaration in parsed.declarations}
        bound |= {n for statement in parsed.module_level for n in statement.binds}
        listed = sorted(set(parsed.dunder_all) - bound)
        if listed:
            # `__init__.py` re-exports what the module *defined*; an `__all__` naming something it
            # only imported would list a name nothing brings in, which ruff reads as `F822`.
            raise SplitError(
                f"`__all__` names {', '.join(listed)}, which {name} imports rather than defines"
            )
    classes = [d for d in parsed.declarations if d.is_class]
    if len(classes) < 2:
        raise SplitError(f"{name} defines {len(classes)} top-level class(es); nothing to split")
    stems: dict[str, str] = {}
    for declaration in parsed.declarations:
        if declaration.stem in RESERVED_STEMS and declaration.is_class:
            raise SplitError(f"class {declaration.name} would collide with {declaration.stem}.py")
        if declaration.is_class:
            if declaration.stem in stems:
                raise SplitError(
                    f"{declaration.name} and {stems[declaration.stem]} both map to "
                    f"{declaration.stem}.py"
                )
            stems[declaration.stem] = declaration.name
    if allow_file_paths:
        return
    scanned = [statement.reads for statement in parsed.module_level]
    scanned += [_references(declaration.node) for declaration in parsed.declarations]
    if parsed.main_guard is not None:
        scanned.append(_references(parsed.main_guard))
    for reads in scanned:
        if reads.uses_file:
            raise SplitError(
                "`__file__` here resolves one directory deeper after the split, and would break "
                "silently; adjust its parent count by hand first"
            )


def _assign_owners(parsed: _Parsed) -> tuple[dict[int, str], list[str], set[str]]:
    """Place each module-level statement (rule 4) and say which placements a human must confirm."""
    # Keyed by target file, not by declaration: rule 2 sends every top-level function to one
    # `_functions.py`, so their reads have to merge or only the last one would count as an owner.
    declaration_reads: dict[str, _References] = {}
    for declaration in parsed.declarations:
        merged = declaration_reads.setdefault(declaration.stem, _References())
        reads = _references(declaration.node)
        merged.all |= reads.all
        merged.runtime |= reads.runtime
        merged.rebound |= reads.rebound
    rebound: set[str] = set()
    for stem, reads in declaration_reads.items():
        if stem != FUNCTIONS_MODULE and reads.rebound:
            names = ", ".join(sorted(reads.rebound))
            raise SplitError(
                f"{stem}.py rebinds {names} with `global`, which cannot reach a name the split "
                "moves to another file; move the rebinding to a top-level function first"
            )
        rebound |= reads.rebound
    placement: dict[int, str] = {}
    notes: list[str] = []
    for index, statement in enumerate(parsed.module_level):
        if not statement.binds:
            placement[index] = INIT_MODULE
            continue
        bound = set(statement.binds)
        owners = sorted({stem for stem, reads in declaration_reads.items() if reads.all & bound})
        # A module-level statement can own another: `oplog.py`'s `_CONTEXT_KEYS` is built from the
        # context variables declared beside it. Counting only classes and functions as owners
        # scatters such a pair across two files that then import each other at module load, which
        # no rule-5 in-method import can break.
        if any(other is not statement and other.reads.all & bound for other in parsed.module_level):
            owners = sorted({*owners, SHARED_MODULE})
        names = ", ".join(statement.binds)
        if rebound & set(statement.binds):
            # `global` cannot reach a name in another module, so the memo has to sit with the
            # function that rebinds it — which rule 2 already sent to `_functions.py`.
            placement[index] = FUNCTIONS_MODULE
            notes.append(f"  {names}: kept with its `global` rebinding in {FUNCTIONS_MODULE}.py")
        elif not owners:
            placement[index] = SHARED_MODULE
        elif len(owners) == 1:
            placement[index] = owners[0]
        else:
            placement[index] = SHARED_MODULE
            notes.append(
                f"  {names}: read by {', '.join(owners)} — placed in {SHARED_MODULE}.py, "
                "confirm that is right"
            )
    return placement, notes, rebound


def _binding_stems(parsed: _Parsed, placement: dict[int, str]) -> dict[str, str]:
    """Map every top-level name the original module bound to the file it now lives in."""
    stems = {d.name: d.stem for d in parsed.declarations}
    for index, statement in enumerate(parsed.module_level):
        for name in statement.binds:
            stems[name] = placement[index]
    return stems


_Statement = cst.SimpleStatementLine | cst.BaseCompoundStatement


def _render(statements: list[_Statement]) -> str:
    return cst.Module(body=statements).code


def _file_reads(
    owned: list[cst.SimpleStatementLine], declarations: list[_Declaration]
) -> _References:
    """The names one split file reads, merged across everything that lands in it."""
    reads = _References()
    for node in [*owned, *(declaration.node for declaration in declarations)]:
        collected = _references(node)
        reads.all |= collected.all
        reads.runtime |= collected.runtime
        reads.annotated |= collected.annotated
        reads.uses_name |= collected.uses_name
    return reads


def _runtime_cycles(graph: dict[str, set[str]]) -> list[tuple[str, ...]]:
    """Every cycle among the split files' runtime imports, which rule 5 has to break by hand."""
    cycles: list[tuple[str, ...]] = []
    seen: set[tuple[str, ...]] = set()

    # `explored` bounds the walk to one visit per node across all start nodes. Without it a densely
    # coupled module re-walks every path from every start, and the tool hangs instead of reporting.
    explored: set[str] = set()

    def walk(stem: str, path: list[str]) -> None:
        for neighbour in sorted(graph.get(stem, ())):
            if neighbour in path:
                cycle = path[path.index(neighbour) :]
                key = tuple(sorted(cycle))
                if key not in seen:
                    seen.add(key)
                    cycles.append(tuple(cycle))
            elif neighbour not in explored:
                explored.add(neighbour)
                walk(neighbour, [*path, neighbour])

    for stem in sorted(graph):
        if stem not in explored:
            explored.add(stem)
            walk(stem, [stem])
    return cycles


def _siblings(stem: str, reads: _References, stems: dict[str, str]) -> dict[str, str]:
    """The other split files this one still needs a module-level import from, keyed by that name."""
    visible = reads.visible()
    return {name: where for name, where in stems.items() if where != stem and name in visible}


def _choose_deferred(
    reads_by_stem: dict[str, _References], stems: dict[str, str]
) -> tuple[dict[str, set[str]], list[str]]:
    """Decide which sibling imports move under `TYPE_CHECKING`, and report the cycles left over.

    Every sibling import is a runtime one by default. A name a class mentions only in an annotation
    still needs a runtime binding whenever something resolves those annotations at run time —
    Pydantic rebuilds a model from them, and a `TYPE_CHECKING` import leaves the name undefined. So
    an import is deferred only where a cycle forces it, and only on an edge that is annotation-only,
    which is rule 5's lazy treatment expressed as an import placement.
    """
    deferred: dict[str, set[str]] = {}
    notes: list[str] = []
    while True:
        graph = {
            stem: {
                where
                for name, where in _siblings(stem, reads, stems).items()
                if name not in deferred.get(stem, set())
            }
            for stem, reads in reads_by_stem.items()
        }
        cycles = _runtime_cycles(graph)
        if not cycles:
            return deferred, notes
        cycle = cycles[0]
        for index, stem in enumerate(cycle):
            target = cycle[(index + 1) % len(cycle)]
            reads = reads_by_stem[stem]
            names = {
                name
                for name, where in _siblings(stem, reads, stems).items()
                if where == target and name not in deferred.get(stem, set())
            }
            if names and not (names & reads.runtime):
                deferred.setdefault(stem, set()).update(names)
                break
        else:
            # Every cycle this walk found, not just the first: a batch operator fixing them one
            # re-run at a time pays a round trip per cycle for no reason. The walk reports one back
            # edge per path rather than enumerating every simple cycle, which is enough to work
            # from and cheaper than Johnson's algorithm.
            listed = "; ".join(" -> ".join([*each, each[0]]) for each in cycles)
            raise SplitError(
                f"circular import {listed}.py, with no annotation-only edge to defer — break it "
                "with rule 5's in-method import first"
            )


def _render_file(
    parsed: _Parsed,
    stem: str,
    owned: list[cst.SimpleStatementLine],
    declarations: list[_Declaration],
    stems: dict[str, str],
    deferred: set[str],
) -> str:
    reads = _file_reads(owned, declarations)
    siblings = _siblings(stem, reads, stems)
    runtime_siblings = {n: w for n, w in siblings.items() if n not in deferred}
    deferred_siblings = {n: w for n, w in siblings.items() if n in deferred}

    used = reads.visible()

    type_checking: list[_Statement] = []
    for statement in parsed.type_checking:
        kept = _filter_import(_import_of(statement), used)
        if kept is not None:
            # Blank leading lines go, comments stay. Dropping both would lose the very thing this
            # item chose `libcst` over `ast` for — the note explaining why an import is deferred.
            comments = [line for line in statement.leading_lines if line.comment is not None]
            type_checking.append(statement.with_changes(body=[kept], leading_lines=comments))
    type_checking.extend(
        cst.parse_statement(f"from .{deferred_siblings[name]} import {name}")
        for name in sorted(deferred_siblings)
    )

    if type_checking:
        used.add("TYPE_CHECKING")

    body: list[_Statement] = []
    if parsed.future is not None:
        body.append(parsed.future.with_changes(leading_lines=[]))
    imports: list[_Statement] = []
    for statement in parsed.imports:
        kept = _filter_import(_import_of(statement), used)
        if kept is not None:
            imports.append(statement.with_changes(body=[kept]))
    already = any(_binds_type_checking(statement) for statement in imports)
    if type_checking and not already:
        imports.append(cst.parse_statement("from typing import TYPE_CHECKING"))
    body.extend(imports)
    body.extend(
        cst.parse_statement(f"from .{runtime_siblings[name]} import {name}")
        for name in sorted(runtime_siblings)
    )
    if type_checking:
        body.append(
            cst.parse_statement("if TYPE_CHECKING:\n    pass\n").with_changes(
                body=cst.IndentedBlock(body=type_checking)
            )
        )
    # A statement derived from its own owner has to follow it: `_ASSERTION_KINDS` reads
    # `Assertion.model_fields`, so emitting it above the class would raise a NameError on import.
    declared = {declaration.name for declaration in declarations}
    body.extend(statement for statement in owned if not (_references(statement).all & declared))
    body.extend(declaration.node for declaration in declarations)
    body.extend(statement for statement in owned if _references(statement).all & declared)
    return _render(body)


def _trailing_imports(parsed: _Parsed, trailing: list[cst.SimpleStatementLine]) -> list[_Statement]:
    """The module's own imports that the trailing statements read, each narrowed to those names.

    A trailing statement reads the module's own imports as readily as its re-exports, so the
    `__init__.py` has to carry the ones it uses.
    """
    reads = _References()
    for trailer in trailing:
        reads.all |= _references(trailer).all
    header: list[_Statement] = []
    for line in parsed.imports:
        kept = _filter_import(_import_of(line), reads.all)
        if kept is not None:
            header.append(line.with_changes(body=[kept], leading_lines=[]))
    return header


def _render_init(
    parsed: _Parsed,
    stems: dict[str, str],
    trailing: list[cst.SimpleStatementLine],
    rebound: set[str],
) -> str:
    exported: list[tuple[str, str]] = []
    seen: set[str] = set()
    for statement in parsed.module_level:
        for name in statement.binds:
            # A `global`-rebound name must not be re-exported: `from .x import NAME` copies the
            # value once, so the package attribute then freezes at whatever it held on import while
            # the real binding moves on. A test asserting on the package's copy silently stops
            # testing anything (BE-0411).
            if name not in seen and name not in rebound:
                exported.append((name, stems[name]))
                seen.add(name)
    for declaration in parsed.declarations:
        if declaration.name not in seen:
            exported.append((declaration.name, declaration.stem))
            seen.add(declaration.name)

    public = [name for name, _ in exported if not name.startswith("_")]
    dunder_all = parsed.dunder_all if parsed.dunder_all is not None else public

    body: list[_Statement] = []
    if parsed.docstring is not None:
        body.append(parsed.docstring)
    grouped: dict[str, list[str]] = {}
    order: list[str] = []
    for name, where in exported:
        if where not in grouped:
            grouped[where] = []
            order.append(where)
        grouped[where].append(name)
    for where in order:
        names = ", ".join(
            # `X as X` is ruff's explicit-re-export form: a name outside `__all__` needs it, or
            # `F401` reads the import as dead and the gate fails.
            name if name in dunder_all else f"{name} as {name}"
            for name in grouped[where]
        )
        body.append(cst.parse_statement(f"from .{where} import {names}"))
    listed = ", ".join(f'"{name}"' for name in dunder_all)
    body.append(cst.parse_statement(f"__all__ = [{listed}]"))
    if trailing:
        # The carried imports go above the re-exports, where ruff's E402 wants every import.
        start = 1 if parsed.docstring is not None else 0
        body[start:start] = _trailing_imports(parsed, trailing)
    body.extend(trailing)
    return _render(body)


def _render_main(parsed: _Parsed, stems: dict[str, str]) -> str:
    """The package's `__main__.py`: the original entry-point guard, over package-level imports."""
    assert parsed.main_guard is not None
    reads = _references(parsed.main_guard)
    body: list[_Statement] = []
    # The guard reads the module's own imports as readily as its declarations — `sys.exit(main())`
    # needs both — so carry the header imports it uses, not only the package names.
    for statement in parsed.imports:
        kept = _filter_import(_import_of(statement), reads.all)
        if kept is not None:
            body.append(statement.with_changes(body=[kept], leading_lines=[]))
    imported = sorted(name for name in reads.all if name in stems)
    if imported:
        body.append(cst.parse_statement(f"from . import {', '.join(imported)}"))
    body.append(parsed.main_guard.with_changes(leading_lines=[]))
    return _render(body)


@dataclass(frozen=True)
class SplitPlan:
    """The files one split writes, and the placements a human still has to confirm."""

    files: dict[str, str]
    notes: tuple[str, ...]


def plan_split(source: str, *, name: str = "<module>", allow_file_paths: bool = False) -> SplitPlan:
    """Work out the package one multi-class module becomes, without touching the filesystem.

    Args:
        source: The module's text.
        name: The module's path, used only in refusal messages.
        allow_file_paths: Split even though the module reads `__file__`, because its parent count
            has already been corrected for the directory level the split adds.

    Returns:
        The new package's files, keyed by filename, plus the rule-4 placements to review.

    Raises:
        SplitError: The file needs a judgment call this script will not make for you.
    """
    parsed = _parse(source)
    _check_splittable(parsed, name, allow_file_paths=allow_file_paths)
    placement, notes, rebound = _assign_owners(parsed)
    stems = _binding_stems(parsed, placement)

    owned: dict[str, list[cst.SimpleStatementLine]] = {}
    for index, statement in enumerate(parsed.module_level):
        owned.setdefault(placement[index], []).append(statement.node)
    grouped: dict[str, list[_Declaration]] = {}
    for declaration in parsed.declarations:
        grouped.setdefault(declaration.stem, []).append(declaration)

    reads_by_stem = {
        stem: _file_reads(owned.get(stem, []), grouped.get(stem, []))
        for stem in sorted((set(owned) | set(grouped)) - {INIT_MODULE})
    }
    public = sorted(
        name
        for name in rebound
        # `name in stems`: a name only a `global` ever binds was never on the package to begin with,
        # so there is nothing for the split to take away and nothing to refuse.
        if name in stems and (not name.startswith("_") or name in (parsed.dunder_all or []))
    )
    if public:
        # Dropping it from `__init__.py` would take a public name off the package's surface, and
        # re-exporting it would freeze a copy — so neither placement is the split's to choose.
        raise SplitError(
            f"{', '.join(public)} is public and rebound with `global`; a package cannot re-export "
            "a name whose binding moves, so make it private or move the rebinding out first"
        )
    scanned = dict(reads_by_stem)
    scanned[INIT_MODULE] = _file_reads(owned.get(INIT_MODULE, []), [])
    for stem, reads in scanned.items():
        borrowed = sorted(
            # `FUNCTIONS_MODULE` as the default: a name only a `global` binds has no entry in
            # `stems`, and defaulting to `stem` would read as "this file owns it" for every file.
            name
            for name in reads.all & rebound
            if stems.get(name, FUNCTIONS_MODULE) != stem
        )
        if borrowed:
            raise SplitError(
                f"{stem}.py reads {', '.join(borrowed)}, which another file rebinds with `global`; "
                "importing the name copies its value once and the copy never updates"
            )
    deferred, cycle_notes = _choose_deferred(reads_by_stem, stems)
    if deferred and parsed.future is None:
        raise SplitError(
            "breaking a cycle needs a deferred annotation, which evaluates eagerly without "
            "`from __future__ import annotations`; add it to the module first"
        )
    files = {
        f"{stem}.py": _render_file(
            parsed,
            stem,
            owned.get(stem, []),
            grouped.get(stem, []),
            stems,
            deferred.get(stem, set()),
        )
        for stem in reads_by_stem
    }
    files["__init__.py"] = _render_init(parsed, stems, owned.get(INIT_MODULE, []), rebound)
    renamed = sorted(stem for stem, reads in reads_by_stem.items() if reads.uses_name)
    if renamed:
        # Not a refusal: `logging.getLogger(__name__)` is in most of these modules, and the new name
        # is a child of the old one, so a handler or level set on the package still reaches it. Only
        # the name a record prints changes, which is worth saying once per file.
        notes.append(
            f"  __name__ moves one level deeper in {', '.join(renamed)}.py — a logger "
            "named from it prints the new, longer name"
        )
    if parsed.main_guard is not None:
        files["__main__.py"] = _render_main(parsed, stems)
    return SplitPlan(files=files, notes=tuple(notes + cycle_notes))


def _refuse_if_ignored(package: Path) -> None:
    """Refuse a package path `.gitignore` would swallow, before anything is written.

    A module becoming a directory can walk straight into an unanchored directory pattern —
    `uploads/` caught `bajutsu/serve/uploads/` this way. `git add` then skips the whole package
    without a word, so the split survives every local check and only fails once CI clones it.
    """
    try:
        ignored = subprocess.run(
            # The trailing slash matters: a directory-only pattern such as `uploads/` matches a
            # path git can see is a directory, and the package does not exist yet.
            ["git", "check-ignore", "-q", f"{package.name}/"],
            # Asked of the repository that owns the path, not of the process's own working
            # directory, which `git check-ignore` would otherwise measure the path against.
            cwd=package.parent,
            check=False,
        ).returncode
    except OSError:
        return  # No git here to ask; the split is no worse off than before the check existed.
    if ignored == 0:
        raise SplitError(f"{package}/ is gitignored; `git add` would skip the whole package")


def apply_split(path: Path, plan: SplitPlan) -> Path:
    """Replace `path` with the package `plan` describes, and return the new directory.

    Every generated file is compiled before the original is deleted. The original is the only copy
    of what the split rewrote, so a file that does not even parse must not cost it.
    """
    package = path.with_suffix("")
    _refuse_if_ignored(package)
    package.mkdir()
    try:
        for filename, source in plan.files.items():
            target = package / filename
            target.write_text(source, encoding="utf-8")
            try:
                compile(source, str(target), "exec")
            except SyntaxError as error:
                raise SplitError(f"generated {target} does not parse: {error}") from error
    except SplitError:
        # Clear the half-written directory, or the retry fails on `mkdir` instead of on the defect.
        shutil.rmtree(package, ignore_errors=True)
        raise
    path.unlink()
    return package


def _tidy(paths: list[Path]) -> None:
    """Sort each new file's imports and format it, so the gate sees the same text a human would."""
    targets = [str(path) for path in paths]
    # `I` sorts the imports and `RUF022` the generated `__all__`. Both are left to ruff rather than
    # reproduced here, so the script cannot disagree with the gate about what sorted means.
    for command in (
        ["uv", "run", "ruff", "check", "--select", "I,RUF022", "--fix", "-q", *targets],
        ["uv", "run", "ruff", "format", "-q", *targets],
    ):
        # `ruff format` fails only on source it cannot parse, which is the loudest signal available
        # that the split emitted something wrong. Reporting success over it would bury that.
        if subprocess.run(command, check=False).returncode != 0:
            raise SplitError(f"ruff rejected the generated files: {' '.join(command[3:5])}")


def main(argv: list[str] | None = None) -> int:
    """Split every module named on the command line, reporting what still needs a human."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", type=Path, help="modules to split into packages")
    parser.add_argument(
        "--dry-run", action="store_true", help="report the plan without writing anything"
    )
    parser.add_argument(
        "--allow-file-paths",
        action="store_true",
        help="split modules reading `__file__` whose parent count is already corrected",
    )
    args = parser.parse_args(argv)

    written: list[Path] = []
    refused: list[Path] = []
    try:
        for path in args.paths:
            try:
                plan = plan_split(
                    path.read_text(encoding="utf-8"),
                    name=str(path),
                    allow_file_paths=args.allow_file_paths,
                )
                if not args.dry_run:
                    written.append(apply_split(path, plan))
            except (SplitError, OSError, cst.ParserSyntaxError) as error:
                # Reported per file rather than raised, so one module needing a human does not
                # strand the rest of the batch — but never as success, and never silently.
                print(f"REFUSED {path}: {error}", file=sys.stderr)
                refused.append(path)
                continue
            print(f"{path} -> {len(plan.files)} files")
            for note in plan.notes:
                print(note)
    finally:
        # Always in a `finally`: an abandoned batch still leaves formatted packages behind, so the
        # gate fails on their content rather than on their whitespace.
        if written:
            _tidy([path for package in written for path in sorted(package.glob("*.py"))])
    if refused:
        print(f"{len(refused)} of {len(args.paths)} module(s) refused:", file=sys.stderr)
        for path in refused:
            print(f"  {path}", file=sys.stderr)
    return 1 if refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
