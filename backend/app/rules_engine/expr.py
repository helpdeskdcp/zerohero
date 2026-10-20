"""Restricted expression evaluator for policy conditions. No calls, no attributes, no subscripts, no comprehensions, no imports: only names (from the context),
numbers/strings/bools/None, comparisons, `and/or/not`, + - * /, unary minus, `in` / `not in` against literal lists. Anything else raises ExprError at LOAD time."""
from __future__ import annotations

import ast
import operator as op

_BIN = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv}
_CMP = {ast.Eq: op.eq, ast.NotEq: op.ne, ast.Lt: op.lt, ast.LtE: op.le, ast.Gt: op.gt, ast.GtE: op.ge}


class ExprError(ValueError): pass


class MissingInput(KeyError): pass


def compile_expr(src: str):
    try: tree = ast.parse(src.strip(), mode="eval")
    except SyntaxError as e: raise ExprError(f"syntax error in {src!r}: {e.msg}")
    names = set()

    def check(n):
        if isinstance(n, ast.Expression): return check(n.body)
        if isinstance(n, ast.BoolOp): [check(v) for v in n.values]; return
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.Not, ast.USub)): return check(n.operand)
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN: check(n.left); check(n.right); return
        if isinstance(n, ast.Compare):
            check(n.left)
            for o, c in zip(n.ops, n.comparators):
                if type(o) in _CMP: check(c)
                elif isinstance(o, (ast.In, ast.NotIn)):
                    if not isinstance(c, (ast.List, ast.Tuple)) or not all(isinstance(e, ast.Constant) for e in c.elts): raise ExprError("`in` needs a literal list")
                else: raise ExprError(f"operator {type(o).__name__} not allowed")
            return
        if isinstance(n, ast.Name): names.add(n.id); return
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float, str, bool, type(None))): return
        raise ExprError(f"{type(n).__name__} not allowed in {src!r}")
    check(tree)

    def run(ctx: dict):
        def ev(n):
            if isinstance(n, ast.Expression): return ev(n.body)
            if isinstance(n, ast.BoolOp):
                vals = (ev(v) for v in n.values); return all(vals) if isinstance(n.op, ast.And) else any(vals)
            if isinstance(n, ast.UnaryOp): return (not ev(n.operand)) if isinstance(n.op, ast.Not) else -ev(n.operand)
            if isinstance(n, ast.BinOp): return _BIN[type(n.op)](ev(n.left), ev(n.right))
            if isinstance(n, ast.Compare):
                left = ev(n.left)
                for o, c in zip(n.ops, n.comparators):
                    if isinstance(o, (ast.In, ast.NotIn)): ok = (left in [e.value for e in c.elts]) == isinstance(o, ast.In)
                    else: ok = _CMP[type(o)](left, ev(c))
                    if not ok: return False
                    left = None if isinstance(o, (ast.In, ast.NotIn)) else ev(c)
                return True
            if isinstance(n, ast.Name):
                if n.id not in ctx or ctx[n.id] is None: raise MissingInput(n.id)
                return ctx[n.id]
            return n.value
        return ev(tree)
    run.names = frozenset(names)
    return run
