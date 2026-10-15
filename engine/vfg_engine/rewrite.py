"""The brain's Config rewrite, the file side (the game engine spec, "One version for everyone,
rewritten by the brain"): find a class's Config as written, put a new one back in exactly that
place, and check that nothing else changed.

Standard library only, so the toolkit on the PC can load this file by path. The full checks (in
bounds, damage(ctx) at powers 0/10/20, imports) are the engine's reload checks; these are the
ones that need only the text:

    find_config(source, class_name)            -> (start, end), 1-based lines of `class Config`
    splice(source, class_name, new_config)     -> the source with that Config replaced
    static_checks(old_source, new_source, class_name) -> [] or what is wrong, in words:
        it parses; the Config keeps the same names (fields and formulas); nothing outside the
        Config changed; and the change is to values only (`value_changes_only`).

A rewrite may come from anyone on the server, by voice, through the brain, and the engine runs
the class's body and its formulas when it reloads. So a rewrite may change values and nothing else:
every formula (method) is exactly as it was, the class line and its decorators too, and each
field's value is either unchanged or a plain literal (a number, text, true/false, None, a list,
tuple or dict of them, a minus sign on a number, UNTIL_STOPPED) or `Field(...)` whose arguments are
such literals. Comments and layout may change freely.
"""

from __future__ import annotations

import ast
import textwrap


def _config_node(tree: ast.Module, class_name: str) -> ast.ClassDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for sub in node.body:
                if isinstance(sub, ast.ClassDef) and sub.name == "Config":
                    return sub
            raise LookupError("class %s has no Config" % class_name)
    raise LookupError("there is no class %s at the top of the file" % class_name)


def find_config(source: str, class_name: str) -> tuple[int, int]:
    """The first and last lines (1-based, inclusive) of `class_name`'s `class Config`: from its
    `class` line (decorators included) to its last line, comments at its indent included."""
    tree = ast.parse(source)
    node = _config_node(tree, class_name)
    start = min([node.lineno] + [d.lineno for d in node.decorator_list])
    end = node.end_lineno
    lines = source.splitlines()
    indent = len(lines[node.lineno - 1]) - len(lines[node.lineno - 1].lstrip())
    # a comment after the last statement but still inside the Config's block belongs to it
    i = end
    while i < len(lines):
        text = lines[i]
        stripped = text.strip()
        if stripped and not stripped.startswith("#"):
            break
        if stripped and len(text) - len(text.lstrip()) <= indent:
            break
        i += 1
        if stripped:
            end = i
    return start, end


def splice(source: str, class_name: str, new_config_source: str) -> str:
    """`source` with `class_name`'s Config replaced by `new_config_source` (taken as written, at
    any indent; it is put at the old one's indent). Nothing else in the file changes."""
    start, end = find_config(source, class_name)
    lines = source.splitlines(keepends=True)
    first = lines[start - 1]
    indent = first[:len(first) - len(first.lstrip())]
    body = textwrap.dedent(new_config_source.expandtabs(4)).strip("\n")
    if not body.lstrip().startswith(("class Config", "@")):
        raise ValueError("the new Config must start with its `class Config(...):` line")
    new = "".join(indent + line if line.strip() else line
                  for line in (body + "\n").splitlines(keepends=True))
    return "".join(lines[:start - 1]) + new + "".join(lines[end:])


def config_names(config: ast.ClassDef) -> set[str]:
    """The names a Config defines: its fields (annotated or not) and its formulas (methods)."""
    names = set()
    for sub in config.body:
        if isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
            names.add(sub.target.id)
        elif isinstance(sub, ast.Assign):
            for t in sub.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(sub.name)
    return names


def _without_config(source: str, class_name: str) -> list[str]:
    start, end = find_config(source, class_name)
    lines = source.splitlines()
    return lines[:start - 1] + ["<Config>"] + lines[end:]


def static_checks(old_source: str, new_source: str, class_name: str) -> list[str]:
    """What is wrong with `new_source` as a rewrite of `class_name`'s Config in `old_source`,
    in words; [] when it may go on to the engine's reload checks."""
    try:
        new_tree = ast.parse(new_source)
    except SyntaxError as exc:
        return ["it does not parse: %s (line %s)" % (exc.msg, exc.lineno)]
    try:
        old_tree = ast.parse(old_source)
        old_config = _config_node(old_tree, class_name)
    except (SyntaxError, LookupError) as exc:
        return ["the old file cannot be read for %s: %s" % (class_name, exc)]
    try:
        new_config = _config_node(new_tree, class_name)
    except LookupError as exc:
        return [str(exc)]
    problems = []
    old_names, new_names = config_names(old_config), config_names(new_config)
    if old_names - new_names:
        problems.append("the Config dropped %s: a rewrite keeps every field" % ", ".join(sorted(old_names - new_names)))
    if new_names - old_names:
        problems.append("the Config added %s: a rewrite changes values, not which fields there are"
                        % ", ".join(sorted(new_names - old_names)))
    if _without_config(old_source, class_name) != _without_config(new_source, class_name):
        problems.append("something outside %s's Config changed: a rewrite changes only the Config" % class_name)
    problems.extend(value_changes_only(old_config, new_config))
    return problems


# ---- values only ------------------------------------------------------------------------------

_NAMES_ALLOWED = ("UNTIL_STOPPED",)


def _same(a, b) -> bool:
    """The same code, whatever its comments, layout and line numbers."""
    return ast.dump(a) == ast.dump(b)


def is_literal(node) -> bool:
    """A plain value: a number, text, true/false, None, a minus or plus sign on a number, a list,
    tuple, set or dict of literals, or UNTIL_STOPPED."""
    if isinstance(node, ast.Constant):
        return node.value is None or isinstance(node.value, (bool, int, float, str))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        return (isinstance(node.operand, ast.Constant) and not isinstance(node.operand.value, bool)
                and isinstance(node.operand.value, (int, float)))
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(is_literal(e) for e in node.elts)
    if isinstance(node, ast.Dict):
        return all(k is not None and is_literal(k) and is_literal(v) for k, v in zip(node.keys, node.values))
    if isinstance(node, ast.Name):
        return node.id in _NAMES_ALLOWED
    return False


def is_value(node) -> bool:
    """A literal, or Field(...) with only literals for its arguments."""
    if is_literal(node):
        return True
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Field"
            and all(is_literal(a) for a in node.args)
            and all(k.arg is not None and is_literal(k.value) for k in node.keywords))


def _fields(config: ast.ClassDef) -> dict:
    out = {}
    for sub in config.body:
        if isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
            out[sub.target.id] = sub
        elif isinstance(sub, ast.Assign) and len(sub.targets) == 1 and isinstance(sub.targets[0], ast.Name):
            out[sub.targets[0].id] = sub
    return out


def _methods(config: ast.ClassDef) -> dict:
    return {sub.name: sub for sub in config.body if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef))}


def value_changes_only(old: ast.ClassDef, new: ast.ClassDef) -> list[str]:
    """What in the new Config is more than a change of values, in words."""
    problems = []
    if not (_same(ast.Tuple(old.bases, ast.Load()), ast.Tuple(new.bases, ast.Load()))
            and all(_same(a, b) for a, b in zip(old.keywords, new.keywords)) and len(old.keywords) == len(new.keywords)):
        problems.append("the line `class Config(...)` changed: a rewrite keeps its base")
    if len(old.decorator_list) != len(new.decorator_list) or not all(
            _same(a, b) for a, b in zip(old.decorator_list, new.decorator_list)):
        problems.append("the Config's decorators changed: a rewrite changes values only")
    old_fields, old_methods = _fields(old), _methods(old)
    for sub in new.body:
        line = getattr(sub, "lineno", "?")
        if isinstance(sub, ast.Expr) and isinstance(sub.value, ast.Constant) and isinstance(sub.value.value, str):
            continue                                       # a docstring
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
            was = old_methods.get(sub.name)
            if was is None or not _same(was, sub):
                problems.append("the formula %s changed (line %s): a rewrite changes values, never "
                                "code; formulas stay exactly as they are" % (sub.name, line))
            continue
        if isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
            name, value = sub.target.id, sub.value
            was = old_fields.get(name)
            if not (isinstance(was, ast.AnnAssign) and _same(was.annotation, sub.annotation)):
                problems.append("the type of %s changed (line %s): a rewrite changes values only" % (name, line))
            if value is None:
                problems.append("%s has no value (line %s)" % (name, line))
                continue
        elif isinstance(sub, ast.Assign) and len(sub.targets) == 1 and isinstance(sub.targets[0], ast.Name):
            name, value = sub.targets[0].id, sub.value
            if isinstance(old_fields.get(name), ast.AnnAssign):
                problems.append("the type of %s was dropped (line %s): a rewrite changes values only" % (name, line))
        else:
            problems.append("line %s is a %s, not a field's value: a rewrite changes values only (no "
                            "imports, calls, classes or statements)" % (line, type(sub).__name__))
            continue
        was = old_fields.get(name)
        if was is not None and was.value is not None and _same(was.value, value):
            continue                                       # unchanged
        if not is_value(value):
            problems.append("the value of %s (line %s) is not a plain value: a number, text, true or "
                            "false, None, a list or dict of them, or Field(...) with such arguments"
                            % (name, line))
    return problems
