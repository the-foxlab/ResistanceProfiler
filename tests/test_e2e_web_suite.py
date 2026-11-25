"""Opaque-box, requirement-driven End-to-End (E2E) web suite for ResistanceProfiler.

Organized into four top-level test classes for sequential tier execution:
- ``TestTier1FeatureCoverage``: Happy-path coverage across workflows WF1-WF7 (>=5 per WF).
- ``TestTier2BoundaryAndSecurity``: Boundaries, error handling, and security trust boundaries (>=5 per WF).
- ``TestTier3CrossFeature``: Pairwise cross-feature combinations (>=10 tests).
- ``TestTier4RealWorldScenarios``: Multi-step real-world end-to-end user/operator scenarios (6 scenarios).

Every test runs in complete isolation using ``tmp_path``-rooted directories,
``fakeredis`` / in-memory stores, synthetic or copied SQLite DBs, git-tracked
``example_data/*``, and ``pysam``-generated BAMs, never mutating ``data/``,
``/data``, ``web/frontend/dist/``, or external Redis.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import gzip
import hashlib
import http.server
import inspect
import io
import json
import math
import os
import re
import shutil
import signal
import socketserver
import sqlite3
import subprocess
import threading
import time
import uuid
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import fakeredis
import pysam
import pytest
import redis
import tree_sitter
import tree_sitter_lua
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from rq import Queue, SimpleWorker
from rq.serializers import JSONSerializer
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from respro.db.schema import create_schema
from tests.conftest import MINIMAL_BAM_BYTES, TINY_REF_NAME, TINY_REF_SEQ
from web.backend import main as main_module
from web.backend import queue as queue_module
from web.backend import rate_limit as rate_limit_module
from web.backend import worker as worker_module
from web.backend.config import WEB_BACKEND_CONFIG
from web.backend.main import create_app
from web.backend.queue import get_batch_queue, get_queue
from web.backend.services import session as session_service
from web.backend.services.session import (
    SESSION_COOKIE_NAME,
    hash_session_token,
    reset_memory_stores,
)
from web.backend.startup_config import (
    ContactEmailConfig,
    ImprintConfig,
    StartupConfig,
    build_project_db_uuid_index,
    load_startup_config,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = REPO_ROOT / 'web' / 'frontend'
FRONTEND_DIST_DIR = FRONTEND_DIR / 'dist'
EXAMPLE_DATA_DIR = REPO_ROOT / 'example_data'
LOCAL_PROJECT_DBS_DIR = REPO_ROOT / 'data' / 'project_databases'

# Mutated tiny_ref: codon 1 AAA (K) -> GAA (E) matches TestDrug rule K2E.
MUTATED_TINY_FASTA = f'>sample_k2e\nATGGAAGCT{TINY_REF_SEQ[9:]}\n'
VALID_TINY_VCF = (
    '##fileformat=VCFv4.2\n'
    '##INFO=<ID=AF,Number=A,Type=Float,Description="Allele Frequency">\n'
    '##INFO=<ID=DP,Number=1,Type=Integer,Description="Read Depth">\n'
    '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
    f'{TINY_REF_NAME}\t4\t.\tA\tG\t100\tPASS\tAF=0.95;DP=500\n'
    f'{TINY_REF_NAME}\t10\t.\tT\tC\t80\tPASS\tAF=0.30;DP=200\n'
)
VALID_REF_FASTA = f'>{TINY_REF_NAME}\n{TINY_REF_SEQ}\n'


# ─── Shared E2E isolation & fixture helpers ───────────────────────────────────


def _reset_redis_pool_caches() -> None:
    """Clear any module-level Redis client/pool caches across backend modules."""
    reset_memory_stores()
    rate_limit_module._SAMPLE_QUOTA_COUNTER.clear()
    for mod in (session_service, rate_limit_module, queue_module):
        for fn_name in ('reset_redis_pool', 'reset_redis_client'):
            fn = getattr(mod, fn_name, None)
            if callable(fn):
                fn()
        for attr in ('_REDIS_POOL', '_REDIS_CLIENT', '_REDIS_CLIENT_CACHE', '_REDIS_UNAVAILABLE_UNTIL', '_POOL'):
            if hasattr(mod, attr):
                setattr(mod, attr, None)


@pytest.fixture(autouse=True)
def _isolate_e2e_environment(monkeypatch: pytest.MonkeyPatch):
    """Reset process-global session/rate-limit stores and ambient env before & after every test."""
    _reset_redis_pool_caches()

    for key in (
        'REDIS_URL',
        'RESPRO_WEB_DEPLOYMENT_MODE',
        'RESPRO_WEB_TRUSTED_PROXIES',
        'RESPRO_WEB_CORS_ORIGINS',
        'RESPRO_WEB_IMPRINT',
        'RESPRO_WEB_CONTACT_EMAIL',
        'RESPRO_WEB_MAX_BATCH_SIZE',
        'RESPRO_WEB_PORT',
        'RESPRO_WEB_RESULT_TTL',
        'RESPRO_WEB_SESSION_TTL',
        'RESPRO_WEB_ALLOWED_ROOTS',
        'RESPRO_WEB_DATA_DIR',
    ):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv('RESPRO_WEB_MAINTAINED_BOOTSTRAP', 'false')
    monkeypatch.setenv('RESPRO_WEB_UPLOAD_RATE_LIMIT', '1000/minute')
    monkeypatch.setenv('RESPRO_WEB_API_RATE_LIMIT', '1000/minute')
    yield
    _reset_redis_pool_caches()


_LUA_LANGUAGE = tree_sitter.Language(tree_sitter_lua.language())
_LUA_PARSER = tree_sitter.Parser(_LUA_LANGUAGE)
_ORIG_REDIS_SCRIPT_LOAD = redis.Redis.script_load
_ORIG_REDIS_EVAL = redis.Redis.eval
_ORIG_REDIS_EVALSHA = redis.Redis.evalsha


class _LuaTable(dict):
    """Lua table representation supporting 1-indexed array and string keys."""

    @classmethod
    def from_seq(cls, items: list[Any] | tuple[Any, ...]) -> _LuaTable:
        tbl = cls()
        for idx, item in enumerate(items, start=1):
            tbl[idx] = item
        return tbl

    def seq_len(self) -> int:
        idx = 1
        while idx in self and self[idx] is not None:
            idx += 1
        return idx - 1

    def max_int_key(self) -> int:
        int_keys = [k for k in self if isinstance(k, int) and k >= 1]
        return max(int_keys) if int_keys else 0


class _LuaReturnError(Exception):
    def __init__(self, values: list[Any]) -> None:
        super().__init__()
        self.values = values


class _LuaBreakError(Exception):
    pass


def _lua_truthy(val: Any) -> bool:
    return val is not None and val is not False


def _lua_tonumber(val: Any) -> int | float | None:
    if isinstance(val, bool) or val is None:
        return None
    if isinstance(val, (int, float)):
        return val
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        try:
            return int(s)
        except ValueError:
            try:
                return float(s)
            except ValueError:
                return None
    return None


def _lua_num(val: Any) -> int | float:
    num = _lua_tonumber(val)
    if num is None:
        raise redis.ResponseError(f'attempt to perform arithmetic on non-number {val!r}')
    return num


def _lua_tostring(val: Any) -> str:
    if val is None:
        return 'nil'
    if val is True:
        return 'true'
    if val is False:
        return 'false'
    if isinstance(val, bytes):
        return val.decode('utf-8', errors='surrogateescape')
    return str(val)


def _lua_to_redis_result(val: Any, *, decode_responses: bool) -> Any:
    if val is None or val is False:
        return None
    if val is True:
        return 1
    if isinstance(val, float):
        return int(val)
    if isinstance(val, int):
        return val
    if isinstance(val, str):
        return val if decode_responses else val.encode('utf-8')
    if isinstance(val, bytes):
        return val.decode('utf-8', errors='surrogateescape') if decode_responses else val
    if isinstance(val, _LuaTable):
        if 'ok' in val and len(val) == 1:
            ok_val = val['ok']
            return ok_val if decode_responses else str(ok_val).encode('utf-8')
        if 'err' in val and len(val) == 1:
            raise redis.ResponseError(str(val['err']))
        max_k = val.max_int_key()
        return [_lua_to_redis_result(val.get(i), decode_responses=decode_responses) for i in range(1, max_k + 1)]
    return val


def _redis_to_lua_value(val: Any, *, cmd: str) -> Any:
    if val is None:
        return None
    if isinstance(val, bool):
        if cmd in {'SET', 'RENAME', 'FLUSHDB', 'FLUSHALL', 'MSET'}:
            return _LuaTable({'ok': 'OK'}) if val else None
        return 1 if val else 0
    if isinstance(val, bytes):
        return val.decode('utf-8', errors='surrogateescape')
    if isinstance(val, dict):
        flat: list[Any] = []
        for k, v in val.items():
            flat.append(_redis_to_lua_value(k, cmd=cmd))
            flat.append(_redis_to_lua_value(v, cmd=cmd))
        return _LuaTable.from_seq(flat)
    if isinstance(val, (list, tuple)):
        return _LuaTable.from_seq([_redis_to_lua_value(v, cmd=cmd) for v in val])
    return val


def _eval_redis_lua(
    client: redis.Redis,
    script: str | bytes,
    keys: list[Any] | tuple[Any, ...],
    args: list[Any] | tuple[Any, ...],
) -> Any:
    """Execute a Redis Lua script against a FakeStrictRedis client via tree_sitter_lua."""
    src_bytes = script.encode('utf-8') if isinstance(script, str) else script
    tree = _LUA_PARSER.parse(src_bytes)

    def _text(node: tree_sitter.Node) -> str:
        return src_bytes[node.start_byte:node.end_byte].decode('utf-8')

    def _named(node: tree_sitter.Node) -> list[tree_sitter.Node]:
        return [c for c in node.children if c.is_named and c.type != 'comment']

    def _redis_call(cmd_raw: Any, *cmd_args: Any) -> Any:
        cmd = _lua_tostring(cmd_raw).upper()
        norm_args: list[Any] = []
        for a in cmd_args:
            if a is True:
                norm_args.append(1)
            elif a is False or a is None:
                norm_args.append('')
            else:
                norm_args.append(a)
        res = client.execute_command(cmd, *norm_args)
        return _redis_to_lua_value(res, cmd=cmd)

    def _redis_pcall(cmd_raw: Any, *cmd_args: Any) -> Any:
        try:
            return _redis_call(cmd_raw, *cmd_args)
        except Exception as exc:
            return _LuaTable({'err': str(exc)})

    def _unpack_fn(tbl: Any, start: int = 1, end: int | None = None) -> tuple[Any, ...]:
        if not isinstance(tbl, _LuaTable):
            return ()
        s = int(start)
        e = int(end) if end is not None else tbl.max_int_key()
        return tuple(tbl.get(i) for i in range(s, e + 1))

    def _table_insert(tbl: _LuaTable, pos_or_val: Any, val: Any = None) -> None:
        if val is None:
            tbl[tbl.seq_len() + 1] = pos_or_val
        else:
            pos = int(pos_or_val)
            n = tbl.seq_len()
            for idx in range(n, pos - 1, -1):
                tbl[idx + 1] = tbl.get(idx)
            tbl[pos] = val

    globals_scope: dict[str, Any] = {
        'KEYS': _LuaTable.from_seq([_lua_tostring(k) for k in keys]),
        'ARGV': _LuaTable.from_seq([_lua_tostring(a) for a in args]),
        'tonumber': _lua_tonumber,
        'tostring': _lua_tostring,
        'unpack': _unpack_fn,
        'type': lambda v: (
            'nil' if v is None else
            'boolean' if isinstance(v, bool) else
            'number' if isinstance(v, (int, float)) else
            'string' if isinstance(v, str) else
            'table' if isinstance(v, _LuaTable) else
            'function' if callable(v) else 'userdata'
        ),
        'next': lambda t, k=None: next(
            ((kk, vv) for kk, vv in t.items()),
            None,
        ) if k is None else None,
        'ipairs': lambda t: [(i, t[i]) for i in range(1, t.seq_len() + 1)],
        'pairs': lambda t: list(t.items()),
        'math': _LuaTable({
            'floor': math.floor,
            'ceil': math.ceil,
            'min': min,
            'max': max,
            'abs': abs,
        }),
        'table': _LuaTable({
            'insert': _table_insert,
            'unpack': _unpack_fn,
        }),
        'string': _LuaTable({
            'sub': lambda s, i, j=None: s[max(0, int(i) - 1):(int(j) if j is not None and int(j) >= 0 else None)],
            'len': len,
        }),
        'redis': _LuaTable({
            'call': _redis_call,
            'pcall': _redis_pcall,
            'status_reply': lambda msg: _LuaTable({'ok': _lua_tostring(msg)}),
            'error_reply': lambda msg: _LuaTable({'err': _lua_tostring(msg)}),
        }),
    }
    scopes: list[dict[str, Any]] = [globals_scope]

    def _lookup(name: str) -> Any:
        for sc in reversed(scopes):
            if name in sc:
                return sc[name]
        return None

    def _assign_var(name: str, value: Any) -> None:
        for sc in reversed(scopes):
            if name in sc:
                sc[name] = value
                return
        scopes[0][name] = value

    def _decode_string_node(node: tree_sitter.Node) -> str:
        raw = _text(node)
        if raw.startswith('['):
            content = node.child_by_field_name('content')
            return _text(content) if content is not None else ''
        inner = raw[1:-1]
        return (
            inner.replace('\\n', '\n')
            .replace('\\r', '\r')
            .replace('\\t', '\t')
            .replace('\\"', '"')
            .replace("\\'", "'")
            .replace('\\\\', '\\')
        )

    def _eval_multi_expr(expr_nodes: list[tree_sitter.Node]) -> list[Any]:
        out: list[Any] = []
        for idx, n in enumerate(expr_nodes):
            val = _eval_expr(n)
            if idx == len(expr_nodes) - 1 and isinstance(val, tuple):
                out.extend(val)
            else:
                out.append(val[0] if isinstance(val, tuple) else val)
        return out

    def _eval_one(node: tree_sitter.Node) -> Any:
        val = _eval_expr(node)
        return val[0] if isinstance(val, tuple) else val

    def _eval_expr(node: tree_sitter.Node) -> Any:
        ntype = node.type
        if ntype == 'parenthesized_expression':
            inner = _named(node)[0]
            return _eval_one(inner)
        if ntype == 'identifier':
            return _lookup(_text(node))
        if ntype == 'number':
            txt = _text(node)
            return int(txt, 0) if (txt.isdigit() or txt.startswith(('0x', '0X'))) else float(txt)
        if ntype == 'string':
            return _decode_string_node(node)
        if ntype == 'true':
            return True
        if ntype == 'false':
            return False
        if ntype == 'nil':
            return None
        if ntype == 'table_constructor':
            tbl = _LuaTable()
            seq_idx = 1
            for child in _named(node):
                if child.type == 'field':
                    name_node = child.child_by_field_name('name')
                    val_node = child.child_by_field_name('value')
                    assert val_node is not None
                    if name_node is None:
                        val = _eval_expr(val_node)
                        if isinstance(val, tuple):
                            for item in val:
                                tbl[seq_idx] = item
                                seq_idx += 1
                        else:
                            tbl[seq_idx] = val
                            seq_idx += 1
                    elif name_node.type == 'identifier' and '[' not in _text(child).split('=', 1)[0]:
                        tbl[_text(name_node)] = _eval_one(val_node)
                    else:
                        tbl[_eval_one(name_node)] = _eval_one(val_node)
            return tbl
        if ntype == 'dot_index_expression':
            tbl = _eval_one(node.child_by_field_name('table'))  # type: ignore[arg-type]
            field = _text(node.child_by_field_name('field'))  # type: ignore[arg-type]
            return tbl.get(field) if isinstance(tbl, _LuaTable) else None
        if ntype == 'bracket_index_expression':
            tbl = _eval_one(node.child_by_field_name('table'))  # type: ignore[arg-type]
            key = _eval_one(node.child_by_field_name('field'))  # type: ignore[arg-type]
            return tbl.get(key) if isinstance(tbl, _LuaTable) else None
        if ntype == 'unary_expression':
            op = _text(node.children[0])
            operand = _eval_one(node.child_by_field_name('operand'))  # type: ignore[arg-type]
            if op == '-':
                return -_lua_num(operand)
            if op == 'not':
                return not _lua_truthy(operand)
            if op == '#':
                if isinstance(operand, _LuaTable):
                    return operand.seq_len()
                if isinstance(operand, str):
                    return len(operand)
                raise redis.ResponseError(f'attempt to get length of {type(operand).__name__}')
        if ntype == 'binary_expression':
            op = _text(node.children[1])
            left_node = node.child_by_field_name('left')
            right_node = node.child_by_field_name('right')
            assert left_node is not None and right_node is not None
            if op == 'and':
                lv = _eval_one(left_node)
                return _eval_one(right_node) if _lua_truthy(lv) else lv
            if op == 'or':
                lv = _eval_one(left_node)
                return lv if _lua_truthy(lv) else _eval_one(right_node)
            lv = _eval_one(left_node)
            rv = _eval_one(right_node)
            if op == '..':
                return f'{_lua_tostring(lv)}{_lua_tostring(rv)}'
            if op == '==':
                return type(lv) is type(rv) and lv == rv or (
                    isinstance(lv, (int, float)) and not isinstance(lv, bool)
                    and isinstance(rv, (int, float)) and not isinstance(rv, bool)
                    and lv == rv
                )
            if op == '~=':
                eq = type(lv) is type(rv) and lv == rv or (
                    isinstance(lv, (int, float)) and not isinstance(lv, bool)
                    and isinstance(rv, (int, float)) and not isinstance(rv, bool)
                    and lv == rv
                )
                return not eq
            if op in ('<', '<=', '>', '>='):
                if isinstance(lv, str) and isinstance(rv, str):
                    return (lv < rv) if op == '<' else (lv <= rv) if op == '<=' else (lv > rv) if op == '>' else (lv >= rv)
                ln, rn = _lua_num(lv), _lua_num(rv)
                return (ln < rn) if op == '<' else (ln <= rn) if op == '<=' else (ln > rn) if op == '>' else (ln >= rn)
            ln, rn = _lua_num(lv), _lua_num(rv)
            if op == '+':
                return ln + rn
            if op == '-':
                return ln - rn
            if op == '*':
                return ln * rn
            if op == '/':
                return ln / rn
            if op == '//':
                return ln // rn
            if op == '%':
                return ln % rn
            if op == '^':
                return ln ** rn
        if ntype == 'function_call':
            name_node = node.child_by_field_name('name')
            args_node = node.child_by_field_name('arguments')
            assert name_node is not None and args_node is not None
            call_args: list[Any] = []
            if args_node.type == 'arguments':
                call_args = _eval_multi_expr(_named(args_node))
            elif args_node.type in ('string', 'table_constructor'):
                call_args = [_eval_one(args_node)]
            if name_node.type == 'method_index_expression':
                obj = _eval_one(name_node.child_by_field_name('table'))  # type: ignore[arg-type]
                meth = _text(name_node.child_by_field_name('method'))  # type: ignore[arg-type]
                fn = obj.get(meth)
                call_args = [obj, *call_args]
            else:
                fn = _eval_one(name_node)
            if not callable(fn):
                raise redis.ResponseError(f'attempt to call non-function {_text(name_node)!r}')
            return fn(*call_args)
        raise redis.ResponseError(f'Unsupported Lua expression node: {ntype}')

    def _assign_target(target: tree_sitter.Node, value: Any) -> None:
        if target.type == 'identifier':
            _assign_var(_text(target), value)
        elif target.type == 'bracket_index_expression':
            tbl = _eval_one(target.child_by_field_name('table'))  # type: ignore[arg-type]
            key = _eval_one(target.child_by_field_name('field'))  # type: ignore[arg-type]
            tbl[key] = value
        elif target.type == 'dot_index_expression':
            tbl = _eval_one(target.child_by_field_name('table'))  # type: ignore[arg-type]
            field = _text(target.child_by_field_name('field'))  # type: ignore[arg-type]
            tbl[field] = value

    def _exec_block(stmts: list[tree_sitter.Node], *, new_scope: bool = True) -> None:
        if new_scope:
            scopes.append({})
        try:
            for stmt in stmts:
                _exec_stmt(stmt)
        finally:
            if new_scope:
                scopes.pop()

    def _exec_stmt(node: tree_sitter.Node) -> None:
        ntype = node.type
        if ntype in ('comment', 'empty_statement'):
            return
        if ntype == 'block':
            _exec_block(_named(node), new_scope=True)
            return
        if ntype == 'variable_declaration':
            for child in _named(node):
                if child.type == 'variable_list':
                    for var_node in _named(child):
                        scopes[-1][_text(var_node)] = None
                elif child.type == 'assignment_statement':
                    vlist = child.child_by_field_name('variable_list') or _named(child)[0]
                    elist = child.child_by_field_name('expression_list') or _named(child)[1]
                    names = [_text(v) for v in _named(vlist)]
                    vals = _eval_multi_expr(_named(elist))
                    for idx, name in enumerate(names):
                        scopes[-1][name] = vals[idx] if idx < len(vals) else None
            return
        if ntype == 'assignment_statement':
            vlist = _named(node)[0]
            elist = _named(node)[1]
            targets = _named(vlist)
            vals = _eval_multi_expr(_named(elist))
            for idx, target in enumerate(targets):
                _assign_target(target, vals[idx] if idx < len(vals) else None)
            return
        if ntype == 'function_declaration':
            name_node = node.child_by_field_name('name')
            params_node = node.child_by_field_name('parameters')
            body_node = node.child_by_field_name('body')
            assert name_node is not None and params_node is not None and body_node is not None
            param_names = [_text(p) for p in _named(params_node) if p.type == 'identifier']
            captured_scopes = list(scopes)

            def _lua_fn(*fn_args: Any) -> Any:
                fn_scope = {pname: (fn_args[idx] if idx < len(fn_args) else None) for idx, pname in enumerate(param_names)}
                saved = scopes[:]
                scopes[:] = [*captured_scopes, fn_scope]
                try:
                    _exec_block(_named(body_node), new_scope=False)
                except _LuaReturnError as ret:
                    return ret.values[0] if len(ret.values) == 1 else (tuple(ret.values) if ret.values else None)
                finally:
                    scopes[:] = saved
                return None

            is_local = node.parent is not None and node.parent.type == 'chunk' and (
                any(c.type == 'local' for c in node.children)
                or src_bytes[max(0, node.start_byte - 10):node.start_byte].rstrip().endswith(b'local')
            )
            if is_local or len(scopes) > 1:
                scopes[-1][_text(name_node)] = _lua_fn
            else:
                _assign_target(name_node, _lua_fn)
            return
        if ntype == 'return_statement':
            exprs = _named(node)
            vals = _eval_multi_expr(_named(exprs[0])) if exprs else []
            raise _LuaReturnError(vals)
        if ntype == 'break_statement':
            raise _LuaBreakError()
        if ntype == 'if_statement':
            cond = _eval_one(node.child_by_field_name('condition'))  # type: ignore[arg-type]
            if _lua_truthy(cond):
                cons = node.child_by_field_name('consequence')
                if cons is not None:
                    _exec_block(_named(cons), new_scope=True)
                return
            for child in node.children:
                if child.type == 'elseif_statement':
                    e_cond = _eval_one(child.child_by_field_name('condition'))  # type: ignore[arg-type]
                    if _lua_truthy(e_cond):
                        e_cons = child.child_by_field_name('consequence')
                        if e_cons is not None:
                            _exec_block(_named(e_cons), new_scope=True)
                        return
                elif child.type == 'else_statement':
                    body = child.child_by_field_name('body')
                    if body is not None:
                        _exec_block(_named(body), new_scope=True)
                    return
            return
        if ntype == 'for_statement':
            clause = node.child_by_field_name('clause')
            body = node.child_by_field_name('body')
            assert clause is not None and body is not None
            if clause.type == 'for_numeric_clause':
                var_name = _text(clause.child_by_field_name('name'))  # type: ignore[arg-type]
                start_v = _lua_num(_eval_one(clause.child_by_field_name('start')))  # type: ignore[arg-type]
                end_v = _lua_num(_eval_one(clause.child_by_field_name('end')))  # type: ignore[arg-type]
                step_node = clause.child_by_field_name('step')
                step_v = _lua_num(_eval_one(step_node)) if step_node is not None else 1
                cur = start_v
                while (cur <= end_v) if step_v > 0 else (cur >= end_v):
                    scopes.append({var_name: cur})
                    try:
                        _exec_block(_named(body), new_scope=False)
                    except _LuaBreakError:
                        break
                    finally:
                        scopes.pop()
                    cur += step_v
            elif clause.type == 'for_generic_clause':
                vlist = _named(clause)[0]
                elist = _named(clause)[1]
                var_names = [_text(v) for v in _named(vlist)]
                it_vals = _eval_multi_expr(_named(elist))
                iterable = it_vals[0] if it_vals else []
                for pair in iterable:
                    pair_vals = pair if isinstance(pair, (tuple, list)) else (pair,)
                    scopes.append({vn: (pair_vals[i] if i < len(pair_vals) else None) for i, vn in enumerate(var_names)})
                    try:
                        _exec_block(_named(body), new_scope=False)
                    except _LuaBreakError:
                        break
                    finally:
                        scopes.pop()
            return
        if ntype == 'while_statement':
            cond_node = node.child_by_field_name('condition')
            body_node = node.child_by_field_name('body')
            assert cond_node is not None and body_node is not None
            while _lua_truthy(_eval_one(cond_node)):
                try:
                    _exec_block(_named(body_node), new_scope=True)
                except _LuaBreakError:
                    break
            return
        if ntype == 'function_call':
            _eval_expr(node)
            return
        raise redis.ResponseError(f'Unsupported Lua statement node: {ntype}')

    decode_responses = bool(client.connection_pool.connection_kwargs.get('decode_responses', False))
    try:
        _exec_block(_named(tree.root_node), new_scope=False)
    except _LuaReturnError as ret:
        first = ret.values[0] if ret.values else None
        return _lua_to_redis_result(first, decode_responses=decode_responses)
    return None


def _make_dead_redis_pool(error_message: str) -> redis.ConnectionPool:
    """Return a real ``ConnectionPool`` whose connections immediately raise ``redis.ConnectionError``."""

    class _DeadConnection(redis.Connection):
        def connect(self) -> None:
            raise redis.ConnectionError(error_message)

    return redis.ConnectionPool(connection_class=_DeadConnection)


def _patch_fakeredis(
    monkeypatch: pytest.MonkeyPatch,
    *,
    server: fakeredis.FakeServer | None = None,
) -> tuple[fakeredis.FakeStrictRedis, fakeredis.FakeServer]:
    """Wire all backend Redis consumers (pools + Lua scripts) to an isolated FakeRedis server."""
    fake_server = server or fakeredis.FakeServer()
    if not hasattr(fake_server, '_respro_lua_lock'):
        fake_server._respro_lua_lock = threading.Lock()  # type: ignore[attr-defined]
    if not hasattr(fake_server, '_respro_lua_scripts'):
        fake_server._respro_lua_scripts = {}  # type: ignore[attr-defined]
    client = fakeredis.FakeStrictRedis(server=fake_server)

    if hasattr(session_service, '_redis_connection'):
        monkeypatch.setattr(session_service, '_redis_connection', lambda: client)

    monkeypatch.setattr(
        redis.ConnectionPool,
        'from_url',
        classmethod(
            lambda cls, _url, **kwargs: fakeredis.FakeStrictRedis(
                server=fake_server,
                decode_responses=bool(kwargs.get('decode_responses', False)),
            ).connection_pool
        ),
    )
    monkeypatch.setattr(
        redis.Redis,
        'from_url',
        classmethod(
            lambda cls, _url, **kwargs: fakeredis.FakeStrictRedis(
                server=fake_server,
                decode_responses=bool(kwargs.get('decode_responses', False)),
            )
        ),
    )

    def _server_for_client(self_client: redis.Redis) -> fakeredis.FakeServer | None:
        pool = getattr(self_client, 'connection_pool', None)
        kwargs = getattr(pool, 'connection_kwargs', None) or {}
        srv = kwargs.get('server')
        return srv if isinstance(srv, fakeredis.FakeServer) else None

    def _script_load(self_client: redis.Redis, script: str | bytes) -> Any:
        if isinstance(self_client, redis.client.Pipeline):
            return _ORIG_REDIS_SCRIPT_LOAD(self_client, script)
        srv = _server_for_client(self_client)
        if srv is None:
            return _ORIG_REDIS_SCRIPT_LOAD(self_client, script)
        if not srv.connected:
            raise redis.ConnectionError('FakeRedis is emulating a connection error.')
        text = script.decode('utf-8') if isinstance(script, bytes) else script
        sha = hashlib.sha1(text.encode('utf-8')).hexdigest()
        lock: threading.Lock = getattr(srv, '_respro_lua_lock', threading.Lock())
        scripts: dict[str, str] = getattr(srv, '_respro_lua_scripts', {})
        with lock:
            scripts[sha] = text
        return sha

    def _eval(self_client: redis.Redis, script: str | bytes, numkeys: int, *keys_and_args: Any) -> Any:
        if isinstance(self_client, redis.client.Pipeline):
            return _ORIG_REDIS_EVAL(self_client, script, numkeys, *keys_and_args)
        srv = _server_for_client(self_client)
        if srv is None:
            return _ORIG_REDIS_EVAL(self_client, script, numkeys, *keys_and_args)
        if not srv.connected:
            raise redis.ConnectionError('FakeRedis is emulating a connection error.')
        nkeys = int(numkeys)
        lock: threading.Lock = getattr(srv, '_respro_lua_lock', threading.Lock())
        with lock:
            return _eval_redis_lua(self_client, script, keys_and_args[:nkeys], keys_and_args[nkeys:])

    def _evalsha(self_client: redis.Redis, sha: str | bytes, numkeys: int, *keys_and_args: Any) -> Any:
        if isinstance(self_client, redis.client.Pipeline):
            return _ORIG_REDIS_EVALSHA(self_client, sha, numkeys, *keys_and_args)
        srv = _server_for_client(self_client)
        if srv is None:
            return _ORIG_REDIS_EVALSHA(self_client, sha, numkeys, *keys_and_args)
        if not srv.connected:
            raise redis.ConnectionError('FakeRedis is emulating a connection error.')
        sha_str = sha.decode('utf-8') if isinstance(sha, bytes) else str(sha)
        nkeys = int(numkeys)
        lock: threading.Lock = getattr(srv, '_respro_lua_lock', threading.Lock())
        scripts: dict[str, str] = getattr(srv, '_respro_lua_scripts', {})
        with lock:
            script = scripts.get(sha_str)
            if script is None:
                raise redis.exceptions.NoScriptError('No matching script. Please use EVAL.')
            return _eval_redis_lua(self_client, script, keys_and_args[:nkeys], keys_and_args[nkeys:])

    monkeypatch.setattr(redis.Redis, 'script_load', _script_load)
    monkeypatch.setattr(redis.Redis, 'eval', _eval)
    monkeypatch.setattr(redis.Redis, 'evalsha', _evalsha)
    return client, fake_server


def _build_e2e_project_db(
    db_path: Path,
    *,
    project_name: str = 'E2E Project',
    db_uuid: str | None = None,
    references: list[dict[str, Any]] | None = None,
    rules: list[dict[str, Any]] | None = None,
    example_fasta: str = '',
    website: str = 'https://respro.example.org',
    contact: str = 'curator@example.org',
    tsv_checksum: str = 'sha256:e2e-checksum',
    include_formula_component: bool = False,
    publications: list[dict[str, str]] | None = None,
) -> Path:
    """Create a schema-compliant project SQLite DB inside ``tmp_path``."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = create_schema(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            'INSERT INTO project (name, schema_version, uuid, example_fasta, '
            'metadata_website, metadata_contact, metadata_tsv_checksum) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (project_name, 4, db_uuid or str(uuid.uuid4()), example_fasta, website, contact, tsv_checksum),
        )
        ref_specs = references or [
            {'name': TINY_REF_NAME, 'accession': 'ACC_TINY.1', 'organism': 'TestVirus', 'length': len(TINY_REF_SEQ)}
        ]
        ref_ids: dict[str, int] = {}
        feature_ids: dict[tuple[str, str], int] = {}
        for ref in ref_specs:
            conn.execute(
                'INSERT INTO reference (project_id, name, accession, organism, length) VALUES (?, ?, ?, ?, ?)',
                (1, ref['name'], ref.get('accession', ''), ref.get('organism', ''), ref.get('length', len(TINY_REF_SEQ))),
            )
            ref_id = int(conn.execute('SELECT last_insert_rowid()').fetchone()[0])
            ref_ids[ref['name']] = ref_id
            for feat in ref.get('features', [{'name': 'gag', 'protein': 'Gag', 'feature_type': 'CDS'}]):
                conn.execute(
                    'INSERT INTO feature (reference_id, name, protein, start, end, strand, nt_sequence, '
                    'feature_type, has_rules) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (
                        ref_id,
                        feat['name'],
                        feat.get('protein', feat['name']),
                        feat.get('start', 0),
                        feat.get('end', len(TINY_REF_SEQ)),
                        feat.get('strand', '+'),
                        feat.get('nt_sequence', TINY_REF_SEQ),
                        feat.get('feature_type', 'CDS'),
                        1,
                    ),
                )
                feat_id = int(conn.execute('SELECT last_insert_rowid()').fetchone()[0])
                feature_ids[(ref['name'], feat['name'])] = feat_id

        default_ref = ref_specs[0]['name']
        first_features = ref_specs[0].get('features', [{'name': 'gag'}])
        default_feature = first_features[0]['name'] if first_features else 'gag'
        rule_specs = (
            rules
            if rules is not None
            else [
                {
                    'ref_name': default_ref,
                    'feature': default_feature,
                    'drug': 'TestDrug',
                    'position': 1,
                    'reference': 'K',
                    'mutation': 'E',
                    'phenotype': 'resistant',
                    'publication': '',
                }
            ]
        )
        if include_formula_component:
            rule_specs = [
                *rule_specs,
                {
                    'ref_name': default_ref,
                    'feature': default_feature,
                    'drug': '__formula_component__',
                    'position': 2,
                    'reference': 'A',
                    'mutation': 'V',
                    'phenotype': 'resistant',
                    'publication': '',
                },
            ]

        drug_ids: dict[str, int] = {}
        first_rule_id: int | None = None
        for r in rule_specs:
            drug = r['drug']
            if drug not in drug_ids:
                conn.execute('INSERT INTO drug (project_id, name) VALUES (?, ?)', (1, drug))
                drug_ids[drug] = int(conn.execute('SELECT last_insert_rowid()').fetchone()[0])
            feat_id = feature_ids[(r.get('ref_name', default_ref), r.get('feature', default_feature))]
            conn.execute(
                'INSERT INTO resistance_rule '
                '(feature_id, drug_id, position, reference, mutation, phenotype, ic50, fold_ic50, score, publication) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (
                    feat_id,
                    drug_ids[drug],
                    r['position'],
                    r['reference'],
                    r['mutation'],
                    r.get('phenotype', 'resistant'),
                    r.get('ic50', ''),
                    r.get('fold_ic50', ''),
                    r.get('score', ''),
                    r.get('publication', ''),
                ),
            )
            rid = int(conn.execute('SELECT last_insert_rowid()').fetchone()[0])
            if first_rule_id is None:
                first_rule_id = rid

        if publications and first_rule_id is not None:
            for pub in publications:
                conn.execute(
                    'INSERT INTO publication (doi, pubmed_id, raw_input) VALUES (?, ?, ?)',
                    (pub.get('doi', ''), pub.get('pubmed_id', ''), pub.get('raw_input', '')),
                )
                pub_id = int(conn.execute('SELECT last_insert_rowid()').fetchone()[0])
                conn.execute(
                    'INSERT INTO rule_publication (rule_id, publication_id) VALUES (?, ?)',
                    (first_rule_id, pub_id),
                )

        conn.commit()
    finally:
        conn.close()
    return db_path


def _make_startup_config(
    tmp_path: Path,
    *,
    db_builders: list[tuple[str, dict[str, Any]]] | None = None,
    deployment_mode: str = 'local',
    imprint: ImprintConfig | None = None,
    contact_email: ContactEmailConfig | None = None,
) -> StartupConfig:
    """Create an isolated StartupConfig under ``tmp_path/data``."""
    data_dir = tmp_path / 'data'
    project_databases_dir = data_dir / 'project_databases'
    uploads_dir = data_dir / 'uploads'
    results_dir = data_dir / 'results'
    for d in (project_databases_dir, uploads_dir, results_dir):
        d.mkdir(parents=True, exist_ok=True)

    builders = db_builders or [('test_project.db', {'example_fasta': MUTATED_TINY_FASTA})]
    for filename, kwargs in builders:
        _build_e2e_project_db(project_databases_dir / filename, **kwargs)

    return StartupConfig(
        project_databases_dir=project_databases_dir.resolve(),
        uploads_dir=uploads_dir.resolve(),
        results_dir=results_dir.resolve(),
        data_dir=data_dir.resolve(),
        allowed_roots=(project_databases_dir.resolve(), uploads_dir.resolve(), results_dir.resolve()),
        imprint=imprint,
        contact_email=contact_email,
        project_db_uuid_index=build_project_db_uuid_index(project_databases_dir.resolve()),
        deployment_mode=deployment_mode,
    )


def _make_app_and_queue(
    config: StartupConfig,
    *,
    is_async: bool = False,
    redis_server: fakeredis.FakeServer | None = None,
) -> tuple[Any, Queue, Queue]:
    """Build a FastAPI app with isolated RQ queues backed by FakeStrictRedis."""
    conn = fakeredis.FakeStrictRedis(server=redis_server or fakeredis.FakeServer())
    profile_q = Queue(
        WEB_BACKEND_CONFIG.defaults.profile_queue_name,
        connection=conn,
        is_async=is_async,
        serializer=JSONSerializer,
    )
    batch_q = Queue(
        WEB_BACKEND_CONFIG.defaults.batch_queue_name,
        connection=conn,
        is_async=is_async,
        serializer=JSONSerializer,
    )
    app = create_app(startup_config=config)
    app.dependency_overrides[get_queue] = lambda: profile_q
    app.dependency_overrides[get_batch_queue] = lambda: batch_q
    return app, profile_q, batch_q


def _build_pysam_bam(
    bam_path: Path,
    *,
    ref_name: str = TINY_REF_NAME,
    ref_len: int = len(TINY_REF_SEQ),
    cover_end: int = 45,
    create_index: bool = True,
) -> Path:
    """Create a real coordinate-sorted BAM (and optional ``.bai``) with partial reference coverage."""
    header = {'HD': {'VN': '1.6', 'SO': 'coordinate'}, 'SQ': [{'LN': ref_len, 'SN': ref_name}]}
    seq = ('ATGAAAGCTTTTGGCCCCAAATTTGGGCCC' * 10)[:cover_end]
    with pysam.AlignmentFile(bam_path, 'wb', header=header) as out:
        for i in range(15):
            seg = pysam.AlignedSegment()
            seg.query_name = f'read_{i}'
            seg.query_sequence = seq
            seg.flag = 0
            seg.reference_id = 0
            seg.reference_start = 0
            seg.mapping_quality = 60
            seg.cigar = [(0, cover_end)]
            seg.query_qualities = pysam.qualitystring_to_array('I' * cover_end)
            out.write(seg)
    if create_index:
        pysam.index(str(bam_path))
    return bam_path


def _upload_file(
    client: TestClient,
    kind: str,
    filename: str,
    content: bytes,
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    resp = client.post(
        f'/api/upload/{kind}',
        files={'file': (filename, content, 'application/octet-stream')},
        headers=headers or {},
    )
    assert resp.status_code == 200, f'Upload {kind} failed ({resp.status_code}): {resp.text}'
    return resp.json()


def _write_synthetic_results_json(
    path: Path,
    *,
    sample_name: str = 'syn_sample',
    project_name: str = 'E2E Project',
    project_db_path: str = '',
    project_uuid: str = '',
    reference_name: str = TINY_REF_NAME,
    references: list[str] | None = None,
    variants: list[dict[str, Any]] | None = None,
    coverage_gaps: list[dict[str, Any]] | None = None,
) -> Path:
    """Write a valid ``.results.json`` payload for direct comparison / upload tests."""
    run_obj: dict[str, Any] = {
        'project_name': project_name,
        'project_db_path': project_db_path,
        'project_fingerprint': project_uuid,
        'reference_name': reference_name,
        'sample_name': sample_name,
        'vcf_path': 'sample.vcf',
        'total_variants': len(variants or []),
        'variants_in_cds': len(variants or []),
        'resistance_hits': 0,
        'formula_hits': 0,
        'status': 'complete',
        'is_fasta_mode': 0,
        'coverage_assessed': 1 if coverage_gaps is not None else 0,
        'created_at': '2026-11-24T12:00:00Z',
    }
    if references is not None:
        run_obj['references'] = [{'reference_name': r} for r in references]
    payload = {
        'run': run_obj,
        'variant_result': variants if variants is not None else [],
        'coverage_gap': coverage_gaps if coverage_gaps is not None else [],
        'formula_rule_hit': [],
        'sample_classification': [],
    }
    path.write_text(json.dumps(payload), encoding='utf-8')
    return path


def _record_owned_result_json(
    config: StartupConfig,
    session_hash: str,
    filename: str,
    **json_kwargs: Any,
) -> str:
    """Persist a synthetic ``.results.json`` in ``results_dir`` and register ownership."""
    out_path = config.results_dir / filename
    _write_synthetic_results_json(out_path, **json_kwargs)
    return session_service.record_artifact(
        session_hash=session_hash,
        canonical_path=out_path,
        media_type='application/json',
    )


def _run_node(script: str, *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """Execute an ESM Node script from REPO_ROOT."""
    return subprocess.run(
        ['node', '--input-type=module', '-e', script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _start_local_http_server(directory: Path) -> tuple[socketserver.TCPServer, int]:
    """Serve *directory* on an ephemeral localhost port in a daemon thread."""

    class _QuietHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=str(directory), **kwargs)

        def log_message(self, format: str, *args: Any) -> None:
            return

    httpd = socketserver.TCPServer(('127.0.0.1', 0), _QuietHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, port


# ═══════════════════════════════════════════════════════════════════════════════
# TIER 1 — FEATURE COVERAGE (WF1–WF7, >= 5 tests per workflow)
# ═══════════════════════════════════════════════════════════════════════════════


class TestTier1FeatureCoverage:
    """Happy-path opaque-box feature coverage across all 7 user workflows."""

    # ── WF1: App Bootstrap, Navigation, Session, Security Headers & Legal ────

    def test_wf1_health_and_readiness_ok(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        app, _, _ = _make_app_and_queue(config)
        client = TestClient(app)

        health = client.get('/api/health')
        assert health.status_code == 200
        assert health.json() == {
            'status': 'ok',
            'data': {'service': 'respro-web-api'},
            'error': None,
        }

        ready = client.get('/api/readiness')
        assert ready.status_code == 200
        body = ready.json()
        assert body['status'] == 'ok'
        assert body['data']['redis']['connected'] is True
        assert body['data']['project_databases'] == {'ready': True, 'count': 1}
        assert body['data']['workspace'] == {
            'project_databases_dir_ready': True,
            'uploads_dir_ready': True,
            'results_dir_ready': True,
        }
        assert body['data']['diagnostics'] == []

    def test_wf1_ui_config_legal_and_contact_metadata(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config_path = _make_startup_config(
            tmp_path / 'path_imprint',
            imprint=ImprintConfig(kind='path', html='<html><body><h1>Imprint</h1></body></html>'),
            contact_email=ContactEmailConfig(email='support@example.org'),
        )
        app_path, _, _ = _make_app_and_queue(config_path)
        client_path = TestClient(app_path)

        ui_cfg = client_path.get('/api/ui/config')
        assert ui_cfg.status_code == 200
        cfg_data = ui_cfg.json()['data']
        assert cfg_data['batch_max_samples'] >= 1
        assert cfg_data['sample_limit_per_minute'] == cfg_data['batch_max_samples']
        assert cfg_data['artifact_bundle_max_artifacts'] == 4 * cfg_data['batch_max_samples']
        assert isinstance(cfg_data['cli_version'], str) and cfg_data['cli_version']

        assert client_path.get('/api/ui/legal').json()['data'] == {'enabled': True, 'kind': 'path'}
        assert client_path.get('/api/ui/contact').json()['data'] == {
            'enabled': True,
            'email': 'support@example.org',
        }
        legal_html = client_path.get('/legal')
        assert legal_html.status_code == 200
        assert 'text/html' in legal_html.headers['content-type']
        assert 'Imprint' in legal_html.text

        config_url = _make_startup_config(
            tmp_path / 'url_imprint',
            imprint=ImprintConfig(kind='url', url='https://example.org/imprint'),
        )
        app_url, _, _ = _make_app_and_queue(config_url)
        client_url = TestClient(app_url)
        assert client_url.get('/api/ui/legal').json()['data'] == {
            'enabled': True,
            'kind': 'url',
            'url': 'https://example.org/imprint',
        }
        legal_redirect = client_url.get('/legal', follow_redirects=False)
        assert legal_redirect.status_code == 302
        assert legal_redirect.headers['location'] == 'https://example.org/imprint'

    def test_wf1_stateless_endpoints_do_not_mint_sessions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_redis, _ = _patch_fakeredis(monkeypatch)
        config = _make_startup_config(
            tmp_path,
            imprint=ImprintConfig(kind='path', html='<p>Legal</p>'),
        )
        app, _, _ = _make_app_and_queue(config)
        client = TestClient(app)

        for method, path, req_headers in (
            ('OPTIONS', '/api/databases', {'Origin': 'http://localhost:5173', 'Access-Control-Request-Method': 'GET'}),
            ('GET', '/api/health', {}),
            ('GET', '/api/readiness', {}),
            ('GET', '/api/ui/config', {}),
            ('GET', '/api/ui/legal', {}),
            ('GET', '/api/ui/contact', {}),
            ('GET', '/api/branding/logo.svg', {}),
            ('GET', '/api/branding/favicon.svg', {}),
            ('GET', '/legal', {}),
        ):
            resp = client.request(method, path, headers=req_headers)
            if path.startswith('/api/branding/'):
                asset_name = path.removeprefix('/api/branding/')
                expected_status = 200 if (REPO_ROOT / 'respro' / 'report' / 'static' / asset_name).is_file() else 404
                assert resp.status_code == expected_status, f'{method} {path} -> {resp.status_code}'
            else:
                assert resp.status_code in (200, 204, 400), f'{method} {path} -> {resp.status_code}'
            assert SESSION_COOKIE_NAME not in resp.headers.get('set-cookie', ''), (
                f'Stateless {method} {path} unexpectedly set session cookie'
            )

        session_keys = list(fake_redis.scan_iter('respro:session:*'))
        assert session_keys == [], f'Stateless endpoints minted sessions: {session_keys}'

    def test_wf1_stateful_session_mint_and_sliding_ttl(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_redis, _ = _patch_fakeredis(monkeypatch)
        monkeypatch.setenv('RESPRO_WEB_SESSION_TTL', '120')
        config = _make_startup_config(tmp_path)
        app, _, _ = _make_app_and_queue(config)
        client = TestClient(app)

        resp1 = client.get('/api/databases')
        assert resp1.status_code == 200
        set_cookie = resp1.headers.get('set-cookie', '')
        assert f'{SESSION_COOKIE_NAME}=' in set_cookie
        assert 'HttpOnly' in set_cookie
        assert 'SameSite=Lax' in set_cookie
        assert 'Secure' not in set_cookie

        token = client.cookies.get(SESSION_COOKIE_NAME)
        assert token
        session_key = f'respro:session:{hash_session_token(token)}'
        assert fake_redis.exists(session_key)
        fake_redis.expire(session_key, 15)
        assert fake_redis.ttl(session_key) <= 15

        resp2 = client.get('/api/databases')
        assert resp2.status_code == 200
        assert fake_redis.ttl(session_key) > 60, 'Valid session hit must refresh Redis TTL (WA-008)'

    def test_wf1_security_headers_and_cors_allow_configured_origin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        monkeypatch.setenv('RESPRO_WEB_CORS_ORIGINS', 'https://app.example.org')
        config = _make_startup_config(tmp_path)
        app, _, _ = _make_app_and_queue(config)
        client = TestClient(app)

        preflight = client.options(
            '/api/databases',
            headers={
                'Origin': 'https://app.example.org',
                'Access-Control-Request-Method': 'GET',
                'Access-Control-Request-Headers': 'Content-Type',
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers.get('access-control-allow-origin') == 'https://app.example.org'
        assert preflight.headers.get('access-control-allow-credentials') != 'true'

        resp = client.get('/api/health', headers={'Origin': 'https://app.example.org'})
        assert resp.status_code == 200
        assert resp.headers.get('x-content-type-options') == 'nosniff'
        assert 'referrer-policy' in resp.headers
        assert resp.headers.get('access-control-allow-origin') == 'https://app.example.org'

    def test_wf1_docs_enabled_in_local_and_disabled_in_online(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        local_cfg = _make_startup_config(tmp_path / 'local', deployment_mode='local')
        local_client = TestClient(_make_app_and_queue(local_cfg)[0])
        for doc_path in ('/docs', '/redoc', '/openapi.json'):
            assert local_client.get(doc_path).status_code == 200

        monkeypatch.setenv('RESPRO_WEB_TRUSTED_PROXIES', '127.0.0.1')
        monkeypatch.setenv('RESPRO_WEB_CORS_ORIGINS', 'https://respro.example.org')
        online_cfg = _make_startup_config(tmp_path / 'online', deployment_mode='online')
        online_client = TestClient(_make_app_and_queue(online_cfg)[0])
        for doc_path in ('/docs', '/redoc', '/openapi.json'):
            assert online_client.get(doc_path).status_code == 404

    # ── WF2: Single-Sample Analysis & Upload Admission ───────────────────────

    def test_wf2_upload_fasta_and_profile_fasta_end_to_end(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        app, _, _ = _make_app_and_queue(config)
        client = TestClient(app)

        up = _upload_file(client, 'fasta', 'sample.fasta', MUTATED_TINY_FASTA.encode('utf-8'))
        assert up['file_type'] == 'fasta'
        assert up['size_bytes'] == len(MUTATED_TINY_FASTA.encode('utf-8'))

        sub = client.post(
            '/api/profile/fasta',
            json={
                'fasta_id': up['upload_id'],
                'database_id': 'test_project.db',
                'sample': 'Patient_01',
                'input_display_name': 'sample.fasta',
                'threads': 1,
            },
        )
        assert sub.status_code == 200, sub.text
        job_id = sub.json()['job_id']

        status = client.get(f'/api/jobs/{job_id}')
        assert status.status_code == 200
        body = status.json()
        assert body['status'] == 'succeeded', body
        assert body['result']['resistance_hits'] >= 1
        assert len(body['result']['artifact_ids']) == 4

    def test_wf2_profile_fasta_use_example_end_to_end(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        app, _, _ = _make_app_and_queue(config)
        client = TestClient(app)

        sub = client.post(
            '/api/profile/fasta',
            json={'use_example': True, 'database_id': 'test_project.db', 'sample': 'ExampleRun'},
        )
        assert sub.status_code == 200, sub.text
        status = client.get(f"/api/jobs/{sub.json()['job_id']}").json()
        assert status['status'] == 'succeeded'
        assert status['result']['resistance_hits'] >= 1

    def test_wf2_upload_plain_vcf_and_bam_and_profile_vcf_end_to_end(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        app, _, _ = _make_app_and_queue(config)
        client = TestClient(app)

        bam_file = _build_pysam_bam(tmp_path / 'reads.bam', cover_end=45)
        vcf_up = _upload_file(client, 'vcf', 'variants.vcf', VALID_TINY_VCF.encode('utf-8'))
        ref_up = _upload_file(client, 'fasta', 'ref.fasta', VALID_REF_FASTA.encode('utf-8'))
        bam_up = _upload_file(client, 'bam', 'reads.bam', bam_file.read_bytes())

        sub = client.post(
            '/api/profile/vcf',
            json={
                'vcf_id': vcf_up['upload_id'],
                'reference_id': ref_up['upload_id'],
                'bam_id': bam_up['upload_id'],
                'database_id': 'test_project.db',
                'sample': 'VcfBamSample',
                'min_af': 0.05,
                'min_depth': 10,
                'threads': 1,
            },
        )
        assert sub.status_code == 200, sub.text
        status = client.get(f"/api/jobs/{sub.json()['job_id']}").json()
        assert status['status'] == 'succeeded', status
        json_id = status['result']['report_json_path']
        art = client.get('/api/artifact', params={'artifact_id': json_id})
        assert art.status_code == 200
        payload = art.json()
        assert payload['run']['coverage_assessed'] == 1
        assert len(payload['coverage_gap']) >= 1

    def test_wf2_upload_gzipped_vcf_gz_and_profile_vcf_end_to_end(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        app, _, _ = _make_app_and_queue(config)
        client = TestClient(app)

        gz_bytes = gzip.compress(VALID_TINY_VCF.encode('utf-8'))
        vcf_up = _upload_file(client, 'vcf', 'variants.vcf.gz', gz_bytes)
        assert vcf_up['file_type'] == 'vcf'
        ref_up = _upload_file(client, 'fasta', 'ref.fasta', VALID_REF_FASTA.encode('utf-8'))

        sub = client.post(
            '/api/profile/vcf',
            json={
                'vcf_id': vcf_up['upload_id'],
                'reference_id': ref_up['upload_id'],
                'database_id': 'test_project.db',
                'sample': 'GzipVcfSample',
            },
        )
        assert sub.status_code == 200, sub.text
        status = client.get(f"/api/jobs/{sub.json()['job_id']}").json()
        assert status['status'] == 'succeeded', status
        assert status['result']['resistance_hits'] >= 1

    def test_wf2_upload_exact_size_boundary_succeeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        app, _, _ = _make_app_and_queue(config)
        client = TestClient(app)

        max_vcf = WEB_BACKEND_CONFIG.defaults.upload_max_vcf_size
        header = (
            '##fileformat=VCFv4.2\n'
            '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
        )
        row = f'{TINY_REF_NAME}\t4\t.\tA\tG\t100\tPASS\tAF=0.5\n'
        body = header + row * ((max_vcf - len(header)) // len(row))
        pad = max_vcf - len(body)
        if pad > 0:
            body = f'##note={"x" * max(0, pad - 8)}\n' + body
            body = body[:max_vcf]
        # Ensure exact byte length equals max_vcf with valid VCF lines.
        if len(body.encode('utf-8')) < max_vcf:
            diff = max_vcf - len(body.encode('utf-8'))
            body = f'##p={"a" * (diff - 5)}\n' + body if diff >= 5 else body + ('\n' * diff)
        raw = body.encode('utf-8')
        assert len(raw) == max_vcf

        up = _upload_file(client, 'vcf', 'exact_cap.vcf', raw)
        assert up['size_bytes'] == max_vcf

    def test_wf2_cancel_queued_job_returns_204_and_marks_failed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        app, _, _ = _make_app_and_queue(config, is_async=True)
        client = TestClient(app)

        sub = client.post(
            '/api/profile/fasta',
            json={'use_example': True, 'database_id': 'test_project.db', 'sample': 'ToCancel'},
        )
        assert sub.status_code == 200
        job_id = sub.json()['job_id']

        delete_resp = client.delete(f'/api/jobs/{job_id}')
        assert delete_resp.status_code == 204
        assert delete_resp.content == b''

        status = client.get(f'/api/jobs/{job_id}')
        assert status.status_code == 200
        assert status.json()['status'] == 'failed'
        assert status.json()['error'] == 'Job canceled by user.'

    # ── WF3: Batch Analysis, Worker Queues & Rate/Quota Accounting ───────────

    def test_wf3_batch_fasta_enqueue_and_worker_drain(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_redis, fake_server = _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        app, _, batch_q = _make_app_and_queue(config, is_async=True, redis_server=fake_server)
        client = TestClient(app)

        u1 = _upload_file(client, 'fasta', 's1.fasta', MUTATED_TINY_FASTA.encode('utf-8'))['upload_id']
        u2 = _upload_file(client, 'fasta', 's2.fasta', MUTATED_TINY_FASTA.encode('utf-8'))['upload_id']

        batch_resp = client.post(
            '/api/profile/batch/fasta',
            json={
                'fasta_ids': [u1, u2],
                'db_path': 'test_project.db',
                'sample_names': ['BatchA', 'BatchB'],
                'input_display_names': ['s1.fasta', 's2.fasta'],
                'threads': 1,
            },
        )
        assert batch_resp.status_code == 200, batch_resp.text
        entries = batch_resp.json()['samples']
        assert batch_resp.json()['total'] == 2

        worker_module.run_worker(
            queue_names=(batch_q.name,),
            connection=fake_redis,
            burst=True,
            worker_class=SimpleWorker,
        )

        for entry in entries:
            st = client.get(f"/api/jobs/{entry['job_id']}").json()
            assert st['status'] == 'succeeded', st
            assert st['result']['sample_name'] == entry['sample_name']

    def test_wf3_batch_vcf_with_bam_ids_records_all_uploads_and_runs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_redis, fake_server = _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        app, _, batch_q = _make_app_and_queue(config, is_async=True, redis_server=fake_server)
        client = TestClient(app)

        vcf1 = _upload_file(client, 'vcf', 'a.vcf', VALID_TINY_VCF.encode('utf-8'))['upload_id']
        vcf2 = _upload_file(client, 'vcf', 'b.vcf', VALID_TINY_VCF.encode('utf-8'))['upload_id']
        ref_id = _upload_file(client, 'fasta', 'ref.fasta', VALID_REF_FASTA.encode('utf-8'))['upload_id']
        bam_file = _build_pysam_bam(tmp_path / 'batch.bam', cover_end=45)
        bam_id = _upload_file(client, 'bam', 'batch.bam', bam_file.read_bytes())['upload_id']

        resp = client.post(
            '/api/profile/batch/vcf',
            json={
                'vcf_ids': [vcf1, vcf2],
                'reference_id': ref_id,
                'db_path': 'test_project.db',
                'sample_names': ['WithBam', 'NoBam'],
                'bam_ids': [bam_id, None],
            },
        )
        assert resp.status_code == 200, resp.text
        entries = resp.json()['samples']

        job0_rec = session_service.fetch_owned_record('job', entries[0]['job_id'])
        assert job0_rec is not None
        assert bam_id in job0_rec.fields.get('upload_ids', ''), (
            'Batch VCF job ownership record must include bam_id (WA-041)'
        )

        worker_module.run_worker(
            queue_names=(batch_q.name,),
            connection=fake_redis,
            burst=True,
            worker_class=SimpleWorker,
        )
        for entry in entries:
            st = client.get(f"/api/jobs/{entry['job_id']}").json()
            assert st['status'] == 'succeeded', st

    def test_wf3_worker_drains_both_profile_and_batch_queues(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        conn = fakeredis.FakeStrictRedis()
        defaults = replace(
            WEB_BACKEND_CONFIG.defaults,
            profile_queue_name='q-profile',
            batch_queue_name='q-batch',
        )
        monkeypatch.setattr(worker_module, 'WEB_BACKEND_CONFIG', type('Cfg', (), {'defaults': defaults})())
        q_prof = Queue('q-profile', connection=conn, serializer=JSONSerializer)
        q_batch = Queue('q-batch', connection=conn, serializer=JSONSerializer)
        j1 = q_prof.enqueue(sum, [1, 2])
        j2 = q_batch.enqueue(sum, [3, 4])

        worker_module.run_worker(connection=conn, burst=True, worker_class=SimpleWorker)

        from rq.job import Job

        assert Job.fetch(j1.id, connection=conn, serializer=JSONSerializer).return_value() == 3
        assert Job.fetch(j2.id, connection=conn, serializer=JSONSerializer).return_value() == 7

    def test_wf3_batch_at_exact_max_batch_size_succeeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        monkeypatch.setenv('RESPRO_WEB_MAX_BATCH_SIZE', '3')
        config = _make_startup_config(tmp_path)
        app, _, _ = _make_app_and_queue(config, is_async=True)
        client = TestClient(app)

        ids = [
            _upload_file(client, 'fasta', f's{i}.fasta', MUTATED_TINY_FASTA.encode('utf-8'))['upload_id']
            for i in range(3)
        ]
        resp = client.post(
            '/api/profile/batch/fasta',
            json={'fasta_ids': ids, 'db_path': 'test_project.db', 'sample_names': ['S1', 'S2', 'S3']},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()['total'] == 3

    def test_wf3_sample_quota_resets_across_new_minute_window(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        monkeypatch.setenv('RESPRO_WEB_MAX_BATCH_SIZE', '2')
        window = {'minute': 1000}
        monkeypatch.setattr(rate_limit_module, '_current_window_minute', lambda: window['minute'])

        config = _make_startup_config(tmp_path)
        app, _, _ = _make_app_and_queue(config, is_async=True)
        client = TestClient(app)

        for sample in ('A', 'B'):
            ok = client.post(
                '/api/profile/fasta',
                json={'use_example': True, 'database_id': 'test_project.db', 'sample': sample},
            )
            assert ok.status_code == 200

        blocked = client.post(
            '/api/profile/fasta',
            json={'use_example': True, 'database_id': 'test_project.db', 'sample': 'C'},
        )
        assert blocked.status_code == 429
        assert int(blocked.headers['retry-after']) >= 1

        window['minute'] = 1001
        after_reset = client.post(
            '/api/profile/fasta',
            json={'use_example': True, 'database_id': 'test_project.db', 'sample': 'C'},
        )
        assert after_reset.status_code == 200

    # ── WF4: JSON Report Regeneration ────────────────────────────────────────

    def test_wf4_upload_json_and_regenerate_single_by_uuid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        db_uuid = str(uuid.uuid4())
        config = _make_startup_config(
            tmp_path,
            db_builders=[('primary.db', {'db_uuid': db_uuid, 'example_fasta': MUTATED_TINY_FASTA})],
        )
        app, _, _ = _make_app_and_queue(config)
        client = TestClient(app)

        prof = client.post(
            '/api/profile/fasta',
            json={'use_example': True, 'database_id': 'primary.db', 'sample': 'OrigSample'},
        )
        assert prof.status_code == 200
        job_data = client.get(f"/api/jobs/{prof.json()['job_id']}").json()
        json_art_id = job_data['result']['report_json_path']
        raw_json = client.get('/api/artifact', params={'artifact_id': json_art_id}).content

        json_up = _upload_file(client, 'json', 'orig.results.json', raw_json)
        assert json_up['file_type'] == 'json'

        regen = client.post('/api/regenerate/json', json={'json_id': json_up['upload_id']})
        assert regen.status_code == 200, regen.text
        regen_status = client.get(f"/api/jobs/{regen.json()['job_id']}").json()
        assert regen_status['status'] == 'succeeded', regen_status
        assert regen_status['result']['sample_name'] == 'OrigSample'
        assert len(regen_status['result']['artifact_ids']) == 4

    def test_wf4_regenerate_json_legacy_fallback_database_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        app, _, _ = _make_app_and_queue(config)
        client = TestClient(app)

        legacy_json = tmp_path / 'legacy.results.json'
        _write_synthetic_results_json(
            legacy_json,
            sample_name='LegacySample',
            project_uuid='',
        )
        json_up = _upload_file(client, 'json', 'legacy.results.json', legacy_json.read_bytes())

        regen = client.post(
            '/api/regenerate/json',
            json={'json_id': json_up['upload_id'], 'database_id': 'test_project.db'},
        )
        assert regen.status_code == 200, regen.text
        status = client.get(f"/api/jobs/{regen.json()['job_id']}").json()
        assert status['status'] == 'succeeded', status
        assert status['result']['sample_name'] == 'LegacySample'

    def test_wf4_regenerate_batch_end_to_end(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_redis, fake_server = _patch_fakeredis(monkeypatch)
        db_uuid = str(uuid.uuid4())
        config = _make_startup_config(
            tmp_path,
            db_builders=[('test_project.db', {'db_uuid': db_uuid, 'example_fasta': MUTATED_TINY_FASTA})],
        )
        app, _, batch_q = _make_app_and_queue(config, is_async=True, redis_server=fake_server)
        client = TestClient(app)

        j1_path = _write_synthetic_results_json(
            tmp_path / 'r1.results.json', sample_name='RegenA', project_uuid=db_uuid,
        )
        j2_path = _write_synthetic_results_json(
            tmp_path / 'r2.results.json', sample_name='RegenB', project_uuid=db_uuid,
        )
        u1 = _upload_file(client, 'json', 'r1.results.json', j1_path.read_bytes())['upload_id']
        u2 = _upload_file(client, 'json', 'r2.results.json', j2_path.read_bytes())['upload_id']

        resp = client.post(
            '/api/regenerate/batch',
            json={'json_ids': [u1, u2], 'sample_names': ['RegenA', 'RegenB']},
        )
        assert resp.status_code == 200, resp.text
        entries = resp.json()['samples']
        assert resp.json()['total'] == 2

        worker_module.run_worker(
            queue_names=(batch_q.name,),
            connection=fake_redis,
            burst=True,
            worker_class=SimpleWorker,
        )
        for entry in entries:
            st = client.get(f"/api/jobs/{entry['job_id']}").json()
            assert st['status'] == 'succeeded', st
            assert st['result']['sample_name'] == entry['sample_name']

    def test_wf4_regenerate_reflects_updated_app_state_uuid_index(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        app, _, _ = _make_app_and_queue(config)
        client = TestClient(app)

        new_uuid = str(uuid.uuid4())
        new_db = _build_e2e_project_db(
            config.project_databases_dir / 'added_later.db',
            project_name='Added Later',
            db_uuid=new_uuid,
        )
        app.state.project_db_uuid_index = {**app.state.project_db_uuid_index, new_uuid: new_db}

        json_path = _write_synthetic_results_json(
            tmp_path / 'later.results.json', sample_name='LaterSample', project_uuid=new_uuid,
        )
        up = _upload_file(client, 'json', 'later.results.json', json_path.read_bytes())
        regen = client.post('/api/regenerate/json', json={'json_id': up['upload_id']})
        assert regen.status_code == 200, regen.text
        assert client.get(f"/api/jobs/{regen.json()['job_id']}").json()['status'] == 'succeeded'

    def test_wf4_regenerate_json_consumes_sample_quota(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        monkeypatch.setattr(rate_limit_module, '_current_window_minute', lambda: 1_000_000)
        monkeypatch.setenv('RESPRO_WEB_MAX_BATCH_SIZE', '2')
        db_uuid = str(uuid.uuid4())
        config = _make_startup_config(
            tmp_path,
            db_builders=[('test_project.db', {'db_uuid': db_uuid})],
        )
        app, _, _ = _make_app_and_queue(config, is_async=True)
        client = TestClient(app)

        json_path = _write_synthetic_results_json(
            tmp_path / 'q.results.json', sample_name='QuotaSample', project_uuid=db_uuid,
        )
        up_id = _upload_file(client, 'json', 'q.results.json', json_path.read_bytes())['upload_id']

        assert client.post('/api/regenerate/json', json={'json_id': up_id}).status_code == 200
        assert client.post('/api/regenerate/json', json={'json_id': up_id}).status_code == 200
        third = client.post('/api/regenerate/json', json={'json_id': up_id})
        assert third.status_code == 429, 'Single /api/regenerate/json must enforce sample quota (WA-029)'
        assert int(third.headers['retry-after']) >= 1

    # ── WF5: Session Results, Artifacts, Sample Compare & Cleanup ────────────

    def test_wf5_job_poll_returns_redacted_artifact_ids_idempotently(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        app, _, _ = _make_app_and_queue(config)
        client = TestClient(app)

        job_id = client.post(
            '/api/profile/fasta',
            json={'use_example': True, 'database_id': 'test_project.db', 'sample': 'Idem'},
        ).json()['job_id']

        poll1 = client.get(f'/api/jobs/{job_id}').json()
        poll2 = client.get(f'/api/jobs/{job_id}').json()
        assert poll1 == poll2
        for art_id in poll1['result']['artifact_ids']:
            assert '/' not in art_id and '\\' not in art_id

    def test_wf5_open_report_html_and_download_all_artifact_kinds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        app, _, _ = _make_app_and_queue(config)
        client = TestClient(app)

        job_id = client.post(
            '/api/profile/fasta',
            json={
                'use_example': True,
                'database_id': 'test_project.db',
                'sample': 'ArtSample',
                'input_display_name': 'my_isolate.fasta',
            },
        ).json()['job_id']
        res = client.get(f'/api/jobs/{job_id}').json()['result']

        rep = client.get('/api/report', params={'artifact_id': res['report_html_path']})
        assert rep.status_code == 200
        assert 'text/html' in rep.headers['content-type']
        assert '<html' in rep.text.lower()

        expected_mimes = {
            'report_html_path': 'text/html',
            'report_json_path': 'application/json',
            'report_pdf_path': 'application/pdf',
            'report_tsv_path': 'text/tab-separated-values',
        }
        for field, mime in expected_mimes.items():
            dl = client.get('/api/artifact', params={'artifact_id': res[field]})
            assert dl.status_code == 200
            assert mime in dl.headers['content-type']
            assert 'attachment;' in dl.headers.get('content-disposition', '')
            assert len(dl.content) > 0

    def test_wf5_artifact_bundle_zip_and_multi_dot_deduplication(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        app, _, _ = _make_app_and_queue(config)
        client = TestClient(app)

        # Establish session first, then create two multi-dot artifacts with colliding derived names.
        client.get('/api/databases')
        session_hash = hash_session_token(client.cookies.get(SESSION_COOKIE_NAME) or '')
        p1 = config.results_dir / 'sample.v1.20261124000001000000.report.html'
        p2 = config.results_dir / 'sample.v1.20261124000002000000.report.html'
        p1.write_text('<html>report 1</html>', encoding='utf-8')
        p2.write_text('<html>report 2</html>', encoding='utf-8')
        a1 = session_service.record_artifact(session_hash=session_hash, canonical_path=p1, media_type='text/html')
        a2 = session_service.record_artifact(session_hash=session_hash, canonical_path=p2, media_type='text/html')

        bundle = client.post('/api/artifact-bundle', params={'part': 2}, json={'artifact_ids': [a1, a2]})
        assert bundle.status_code == 200, bundle.text
        assert 'application/zip' in bundle.headers['content-type']

        with zipfile.ZipFile(io.BytesIO(bundle.content)) as zf:
            names = sorted(zf.namelist())
        assert names == ['part2-sample.v1.html', 'part2-sample.v1_1.html'], (
            f'Multi-dot stem deduplication failed (S5-B1): got {names}'
        )

    def test_wf5_sample_compare_matrix_and_filters(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        db_file = tmp_path / 'data' / 'project_databases' / 'test_project.db'
        config = _make_startup_config(
            tmp_path,
            db_builders=[
                (
                    'test_project.db',
                    {
                        'references': [
                            {
                                'name': TINY_REF_NAME,
                                'accession': 'ACC_1',
                                'organism': 'TestVirus',
                                'features': [{'name': 'nsp1', 'protein': 'NSP1 Display', 'feature_type': 'mat_peptide'}],
                            }
                        ],
                        'rules': [
                            {'feature': 'nsp1', 'drug': 'TestDrug', 'position': 2, 'reference': 'K', 'mutation': 'E'}
                        ],
                    },
                )
            ],
        )
        app, _, _ = _make_app_and_queue(config)
        client = TestClient(app)

        client.get('/api/databases')
        sh = hash_session_token(client.cookies.get(SESSION_COOKIE_NAME) or '')
        a1 = _record_owned_result_json(
            config,
            sh,
            's1.20261124000001000000.results.json',
            sample_name='S1',
            project_db_path=str(db_file),
            variants=[
                {
                    'feature_name': 'nsp1',
                    'codon_pos': 1,
                    'ref_aa': 'K',
                    'alt_aa': 'E',
                    'allele_freq': 0.9,
                    'consequence': 'missense',
                    'rule_match': 1,
                },
                {
                    'feature_name': 'nsp1',
                    'codon_pos': 3,
                    'ref_aa': 'F',
                    'alt_aa': 'F',
                    'allele_freq': 0.5,
                    'consequence': 'synonymous',
                    'rule_match': 0,
                },
            ],
        )
        a2 = _record_owned_result_json(
            config,
            sh,
            's2.20261124000002000000.results.json',
            sample_name='S2',
            project_db_path=str(db_file),
            variants=[],
        )

        full = client.post('/api/compare', json={'artifact_ids': [a1, a2]}).json()
        assert full['samples'] == ['S1', 'S2']
        assert len(full['mutations']) == 2
        assert full['feature_display_names']['nsp1'] == 'NSP1 Display'

        filtered = client.post(
            '/api/compare',
            json={'artifact_ids': [a1, a2], 'non_synonymous_only': True, 'db_hits_only': True},
        ).json()
        assert len(filtered['mutations']) == 1
        assert filtered['mutations'][0]['alt_aa'] == 'E'
        assert filtered['matrix'][0][0]['db_hit'] is True

    def test_wf5_compare_cell_coverage_gap_vs_null_allele_freq(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        app, _, _ = _make_app_and_queue(config)
        client = TestClient(app)
        client.get('/api/databases')
        sh = hash_session_token(client.cookies.get(SESSION_COOKIE_NAME) or '')

        a1 = _record_owned_result_json(
            config,
            sh,
            'unknown_af.20261124000001000000.results.json',
            sample_name='DetectedNullAF',
            variants=[
                {
                    'feature_name': 'gag',
                    'codon_pos': 4,  # 1-based pos 5
                    'ref_aa': 'G',
                    'alt_aa': 'S',
                    'allele_freq': None,
                    'consequence': 'missense',
                    'rule_match': 0,
                }
            ],
            coverage_gaps=[],
        )
        a2 = _record_owned_result_json(
            config,
            sh,
            'gap.20261124000002000000.results.json',
            sample_name='GapSample',
            variants=[],
            coverage_gaps=[{'feature_name': 'gag', 'codon_start': 3, 'codon_end': 6}],
        )

        resp = client.post('/api/compare', json={'artifact_ids': [a1, a2]})
        assert resp.status_code == 200, resp.text
        matrix = resp.json()['matrix']
        detected_cell = matrix[0][0]
        gap_cell = matrix[1][0]
        assert detected_cell['allele_freq'] is None
        assert detected_cell['coverage_gap'] is False, 'Detected variant with unknown AF must not be marked coverage_gap (WA-016)'
        assert gap_cell['allele_freq'] is None
        assert gap_cell['coverage_gap'] is True, 'Uncovered codon must set coverage_gap=True (WA-016)'

    def test_wf5_session_cleanup_deletes_uploads_bam_bai_and_artifacts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        app, _, _ = _make_app_and_queue(config)
        client = TestClient(app)

        bam_src = _build_pysam_bam(tmp_path / 'sample.bam')
        bam_up = _upload_file(client, 'bam', 'sample.bam', bam_src.read_bytes())['upload_id']
        bam_record = session_service.fetch_owned_record('upload', bam_up)
        assert bam_record is not None and bam_record.canonical_path
        bam_saved = Path(bam_record.canonical_path)
        bai_saved = Path(f'{bam_saved}.bai')
        bai_saved.write_bytes(b'BAI!')

        job_id = client.post(
            '/api/profile/fasta',
            json={'use_example': True, 'database_id': 'test_project.db', 'sample': 'CleanMe'},
        ).json()['job_id']
        art_ids = client.get(f'/api/jobs/{job_id}').json()['result']['artifact_ids']

        cleanup = client.post(
            '/api/session/cleanup',
            json={'upload_ids': [bam_up], 'artifact_ids': art_ids},
        )
        assert cleanup.status_code == 200, cleanup.text
        assert cleanup.json()['deleted_count'] == 1 + len(art_ids)
        assert not bam_saved.exists()
        assert not bai_saved.exists(), 'Cleanup must remove .bam.bai sidecar within uploads_dir (W7/WA-039)'
        assert client.get('/api/artifact', params={'artifact_id': art_ids[0]}).status_code == 404

    # ── WF6: Database Dashboard, Mutation Browser & DB Comparison ────────────

    def test_wf6_databases_catalog_and_metadata_fields(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(
            tmp_path,
            db_builders=[
                (
                    'curated.db',
                    {
                        'project_name': 'Curated DB',
                        'example_fasta': MUTATED_TINY_FASTA,
                        'include_formula_component': True,
                    },
                )
            ],
        )
        app, _, _ = _make_app_and_queue(config)
        client = TestClient(app)

        resp = client.get('/api/databases')
        assert resp.status_code == 200
        body = resp.json()['data']
        assert body['count'] == 1
        item = body['items'][0]
        assert item['id'] == 'curated.db'
        assert item['has_example'] is True
        assert item['mutation_count'] == 1, '__formula_component__ must be excluded from mutation_count (WA-035)'
        assert item['metadata']['website'] == 'https://respro.example.org'

    def test_wf6_rules_and_mutations_browse_and_exact_reference_filter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(
            tmp_path,
            db_builders=[
                (
                    'multi_ref.db',
                    {
                        'references': [
                            {'name': 'REF1', 'accession': 'ACC1', 'organism': 'VirusA'},
                            {'name': 'REF10', 'accession': 'ACC10', 'organism': 'VirusA'},
                        ],
                        'rules': [
                            {'ref_name': 'REF1', 'feature': 'gag', 'drug': 'Drug1', 'position': 1, 'reference': 'K', 'mutation': 'E'},
                            {'ref_name': 'REF10', 'feature': 'gag', 'drug': 'Drug10', 'position': 2, 'reference': 'A', 'mutation': 'V'},
                        ],
                    },
                )
            ],
        )
        app, _, _ = _make_app_and_queue(config)
        client = TestClient(app)

        rules_exact = client.get('/api/rules', params={'database_id': 'multi_ref.db', 'reference': 'REF1'})
        assert rules_exact.status_code == 200, (
            f'Exact reference filter REF1 failed with REF10 present (WA-035): {rules_exact.text}'
        )
        assert rules_exact.json()['data']['count'] == 1
        assert rules_exact.json()['data']['items'][0]['drug'] == 'Drug1'

        mutations_all = client.get('/api/mutations', params={'database_id': 'multi_ref.db', 'reference': 'undefined'})
        assert mutations_all.status_code == 200
        assert mutations_all.json()['data']['count'] == 2

    def test_wf6_databases_shared_references_and_reference_accessions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(
            tmp_path,
            db_builders=[
                ('db1.db', {'references': [{'name': 'RefA', 'accession': 'SHARED_1', 'organism': 'Org1'}]}),
                ('db2.db', {'references': [{'name': 'RefA', 'accession': 'SHARED_1', 'organism': 'Org1'}]}),
            ],
        )
        app, _, _ = _make_app_and_queue(config)
        client = TestClient(app)

        acc = client.get('/api/databases/reference-accessions', params={'ids': 'db1.db,db2.db'})
        assert acc.status_code == 200
        assert 'SHARED_1' in acc.json()['data']['items']['db1.db']

        shared = client.get('/api/databases/shared-references', params={'ids': 'db1.db,db2.db'})
        assert shared.status_code == 200
        assert shared.json()['data']['count'] == 1
        assert shared.json()['data']['items'][0]['accession'] == 'SHARED_1'

    def test_wf6_databases_compare_venn_union_and_publications(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(
            tmp_path,
            db_builders=[
                (
                    'db1.db',
                    {
                        'references': [{'name': 'RefA', 'accession': 'SHARED_1', 'organism': 'Org1'}],
                        'rules': [
                            {'ref_name': 'RefA', 'feature': 'gag', 'drug': 'DrugX', 'position': 1, 'reference': 'K', 'mutation': 'E'},
                            {'ref_name': 'RefA', 'feature': 'gag', 'drug': 'DrugY', 'position': 2, 'reference': 'A', 'mutation': 'T'},
                        ],
                        'publications': [{'doi': '10.1000/z'}, {'doi': '10.1000/a'}],
                    },
                ),
                (
                    'db2.db',
                    {
                        'references': [{'name': 'RefA', 'accession': 'SHARED_1', 'organism': 'Org1'}],
                        'rules': [
                            {'ref_name': 'RefA', 'feature': 'gag', 'drug': 'DrugX', 'position': 1, 'reference': 'K', 'mutation': 'E'},
                        ],
                    },
                ),
            ],
        )
        app, _, _ = _make_app_and_queue(config)
        client = TestClient(app)

        comp = client.post(
            '/api/databases/compare',
            json={'database_ids': ['db1.db', 'db2.db'], 'accession': 'SHARED_1'},
        )
        assert comp.status_code == 200, comp.text
        data = comp.json()['data']
        assert data['counts'] == {'total_unique': 2, 'intersection': 1, 'per_db': {'db1.db': 2, 'db2.db': 1}}
        pub_cell = data['rows'][0]['per_db']['db1.db']['publication']
        assert pub_cell == '10.1000/a; 10.1000/z', f'Expected sorted GROUP_CONCAT citations (WA-038), got {pub_cell!r}'

    def test_wf6_corrupt_db_isolated_from_valid_catalog(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        (config.project_databases_dir / 'aaa_corrupt.db').write_bytes(b'not-a-sqlite-db')
        app, _, _ = _make_app_and_queue(config)
        client = TestClient(app)

        resp = client.get('/api/databases')
        assert resp.status_code == 200, f'Corrupt sibling DB broke /api/databases (WA-034): {resp.text}'
        assert [item['id'] for item in resp.json()['data']['items']] == ['test_project.db']

    @pytest.mark.skipif(
        not (LOCAL_PROJECT_DBS_DIR / 'herpesdrg.db').is_file()
        or not (LOCAL_PROJECT_DBS_DIR / 'simplexvirus-db.db').is_file(),
        reason='Local gitignored project_databases/*.db not present',
    )
    def test_wf6_optional_real_project_databases_smoke(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        data_dir = tmp_path / 'data'
        pd_dir = data_dir / 'project_databases'
        up_dir = data_dir / 'uploads'
        res_dir = data_dir / 'results'
        for d in (pd_dir, up_dir, res_dir):
            d.mkdir(parents=True, exist_ok=True)
        for name in ('herpesdrg.db', 'simplexvirus-db.db'):
            shutil.copy2(LOCAL_PROJECT_DBS_DIR / name, pd_dir / name)

        config = StartupConfig(
            project_databases_dir=pd_dir.resolve(),
            uploads_dir=up_dir.resolve(),
            results_dir=res_dir.resolve(),
            data_dir=data_dir.resolve(),
            allowed_roots=(pd_dir.resolve(), up_dir.resolve(), res_dir.resolve()),
            project_db_uuid_index=build_project_db_uuid_index(pd_dir.resolve()),
        )
        client = TestClient(_make_app_and_queue(config)[0])
        assert client.get('/api/databases').json()['data']['count'] == 2
        comp = client.post(
            '/api/databases/compare',
            json={'database_ids': ['herpesdrg.db', 'simplexvirus-db.db'], 'accession': 'NC_001806'},
        )
        assert comp.status_code == 200
        assert comp.json()['data']['counts']['intersection'] > 0

    # ── WF7: Built SPA, Report Iframe/CSP, Vite Proxy, TSV & Deployment ──────

    def test_wf7_built_spa_serves_routes_and_assets_with_strict_csp(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        dist_dir = FRONTEND_DIST_DIR
        if not dist_dir.is_dir():
            dist_dir = tmp_path / 'dist'
            build = subprocess.run(
                ['npx', 'vite', 'build', '--outDir', str(dist_dir)],
                cwd=str(FRONTEND_DIR),
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            assert build.returncode == 0, build.stderr

        monkeypatch.setattr(main_module, '_frontend_dist_dir', lambda: dist_dir)
        config = _make_startup_config(tmp_path)
        client = TestClient(_make_app_and_queue(config)[0])

        for spa_route in ('/', '/analysis', '/databases', '/about'):
            resp = client.get(spa_route)
            assert resp.status_code == 200
            assert 'text/html' in resp.headers['content-type']
            csp = resp.headers.get('content-security-policy', '')
            directives = {
                parts[0].lower(): parts[1:]
                for clause in csp.split(';')
                if (parts := clause.strip().split())
            }
            assert 'script-src' in directives, f'SPA route {spa_route} missing CSP script-src (WA-047): {csp!r}'
            assert "'self'" in directives['script-src'], f'SPA script-src must include self: {csp!r}'
            assert "'unsafe-inline'" not in directives['script-src'], (
                f'SPA script-src must not allow unsafe-inline (WA-047): {csp!r}'
            )

            soup = BeautifulSoup(resp.text, 'html.parser')
            inline_scripts = [s for s in soup.find_all('script') if not s.get('src')]
            assert inline_scripts == [], f'index.html must have zero inline <script> tags (WA-047): {inline_scripts}'
            script_srcs = [tag['src'] for tag in soup.find_all('script', src=True)]
            assert '/theme-init.js' in script_srcs, f'Expected /theme-init.js script tag in index.html: {script_srcs}'
            for src in script_srcs:
                asset = client.get(src)
                assert asset.status_code == 200
                ct = asset.headers.get('content-type', '').lower()
                assert 'javascript' in ct and 'text/html' not in ct, (
                    f'Script asset {src} must serve JavaScript, not HTML fallback: content-type={ct!r}'
                )
                assert '<!doctype' not in asset.text.lower() and '<html' not in asset.text.lower(), (
                    f'Script asset {src} fell through to HTML fallback'
                )
            for tag in soup.find_all('link', rel='stylesheet', href=True):
                css = client.get(tag['href'])
                assert css.status_code == 200
                assert 'text/css' in css.headers.get('content-type', '').lower(), (
                    f"Stylesheet {tag['href']} must return text/css: {css.headers.get('content-type')!r}"
                )
                assert '<!doctype' not in css.text.lower()

        assert client.get('/api/does-not-exist').status_code == 404

    def test_wf7_vite_build_code_splitting_no_chunk_warning(self, tmp_path: Path) -> None:
        out_dir = tmp_path / 'vite_dist'
        completed = subprocess.run(
            ['npx', 'vite', 'build', '--outDir', str(out_dir)],
            cwd=str(FRONTEND_DIR),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        combined = f'{completed.stdout}\n{completed.stderr}'
        assert 'larger than 500' not in combined, f'Vite chunk-size warning still emitted (W13):\n{combined}'
        js_chunks = list((out_dir / 'assets').glob('*.js'))
        assert len(js_chunks) > 1, f'Expected code-split JS chunks (W13), found: {js_chunks}'

    def test_wf7_vite_dev_proxy_and_same_origin_config(self) -> None:
        script = (
            "import { pathToFileURL } from 'url';\n"
            "import fs from 'fs';\n"
            "import vm from 'vm';\n"
            "const viteMod = await import(pathToFileURL('./web/frontend/vite.config.js').href);\n"
            "const cfg = viteMod.default({ mode: 'development' });\n"
            "const proxy = cfg.server?.proxy || {};\n"
            "if (!proxy['/api'] || !proxy['/legal']) throw new Error('Missing /api or /legal Vite proxy');\n"
            "const rawCfg = fs.readFileSync('./web/frontend/src/config.js', 'utf8')\n"
            "  .replace(/import\\.meta\\.env/g, '({})')\n"
            "  .replace(/^\\s*export\\s+/gm, '');\n"
            "const ctx = { window: { location: { port: '5173', origin: 'http://127.0.0.1:5173' } }, URL };\n"
            "const wrapped = vm.runInNewContext('(function(){ ' + rawCfg + '; return FRONTEND_CONFIG; })()', ctx);\n"
            "console.log(JSON.stringify({ proxyApi: proxy['/api'].target, proxyLegal: proxy['/legal'].target, apiBase: wrapped.apiBase }));\n"
        )
        res = _run_node(script)
        assert res.returncode == 0, f'{res.stdout}\n{res.stderr}'
        parsed = json.loads(res.stdout.strip().splitlines()[-1])
        assert '127.0.0.1:8000' in parsed['proxyApi']
        assert '127.0.0.1:8000' in parsed['proxyLegal']
        assert parsed['apiBase'] == '', f'Expected same-origin default apiBase="" on port 5173 (W5), got {parsed["apiBase"]!r}'

    def test_wf7_report_js_sandbox_null_origin_postmessage_via_node(self) -> None:
        script = (
            "import { createRequire } from 'module';\n"
            "import fs from 'fs';\n"
            "const req = createRequire(import.meta.url);\n"
            "const { JSDOM } = req('./web/frontend/node_modules/jsdom');\n"
            "const reportJs = fs.readFileSync('./respro/report/static/report.js', 'utf8');\n"
            "const dom = new JSDOM('<!doctype html><html><body>"
            "<div id=\"plot-modal\"><div id=\"plot-modal-backdrop\"></div><button id=\"plot-modal-close\"></button>"
            "<img class=\"plot-modal-image\" src=\"data:image/png;base64,AAAA\" alt=\"p\"></div>"
            "<button id=\"plot-modal-open\"></button></body></html>', { url: 'about:blank', runScripts: 'outside-only' });\n"
            "const posted = [];\n"
            "const fakeParent = { postMessage(msg, target) { posted.push({ msg, target }); } };\n"
            "Object.defineProperty(dom.window, 'parent', { value: fakeParent, configurable: true });\n"
            "dom.window.eval(reportJs);\n"
            "const msgEvt = new dom.window.MessageEvent('message', {\n"
            "  data: { type: 'respro:report-theme', theme: 'dark' },\n"
            "  origin: 'http://127.0.0.1:5173',\n"
            "  source: fakeParent,\n"
            "});\n"
            "dom.window.dispatchEvent(msgEvt);\n"
            "dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));\n"
            "dom.window.document.getElementById('plot-modal-open').click();\n"
            "console.log(JSON.stringify({\n"
            "  origin: dom.window.location.origin,\n"
            "  theme: dom.window.document.documentElement.getAttribute('data-theme'),\n"
            "  posted,\n"
            "}));\n"
        )
        res = _run_node(script)
        assert res.returncode == 0, f'{res.stdout}\n{res.stderr}'
        data = json.loads(res.stdout.strip().splitlines()[-1])
        assert data['origin'] == 'null'
        assert data['theme'] == 'dark', 'report.js must accept theme from parent in sandboxed null-origin iframe (WA-009)'
        assert any(p['msg'].get('type') == 'respro:open-plot' and p['target'] == '*' for p in data['posted']), (
            f'report.js must postMessage with targetOrigin="*" from sandboxed null-origin iframe (WA-009): {data["posted"]}'
        )

    def test_wf7_utils_tsv_export_formula_neutralization_via_node(self) -> None:
        script = (
            "import { createRequire } from 'module';\n"
            "const req = createRequire(import.meta.url);\n"
            "const { JSDOM } = req('./web/frontend/node_modules/jsdom');\n"
            "const dom = new JSDOM('<!doctype html><html><body></body></html>');\n"
            "globalThis.window = dom.window; globalThis.document = dom.window.document;\n"
            "let capturedText = '';\n"
            "globalThis.Blob = class { constructor(parts){ capturedText = parts.join(''); } };\n"
            "globalThis.URL = { createObjectURL: () => 'blob:mock', revokeObjectURL: () => {} };\n"
            "dom.window.HTMLAnchorElement.prototype.click = () => {};\n"
            "const utils = await import('./web/frontend/src/utils.js');\n"
            "const display = utils.cleanTsvCell('=HYPERLINK(\"http://evil\")');\n"
            "utils.downloadTsv(['H'], [['=cmd', '+1', '-2', '@SUM', '\\tX', '\\rY', 'safe']], 'out.tsv');\n"
            "console.log(JSON.stringify({ display, tsv: capturedText }));\n"
        )
        res = _run_node(script)
        assert res.returncode == 0, f'{res.stdout}\n{res.stderr}'
        out = json.loads(res.stdout.strip().splitlines()[-1])
        assert out['display'].startswith('='), 'cleanTsvCell must stay pure for HTML rendering (WA-046)'
        data_line = out['tsv'].splitlines()[1]
        cells = data_line.split('\t')
        for idx, cell in enumerate(cells[:-1]):
            assert cell.startswith("'"), f'TSV formula trigger cell[{idx}] not neutralized (WA-046): {cell!r}'
        assert cells[-1] == 'safe'

    def test_wf7_deployment_configs_and_docs_hardening(self) -> None:
        dockerfile = (REPO_ROOT / 'Dockerfile.web').read_text(encoding='utf-8')
        assert 'RESPRO_FRONTEND_DIST' not in dockerfile, 'Dead RESPRO_FRONTEND_DIST must be removed (WA-042)'
        assert 'chown -R respro:respro /app' not in dockerfile, '/app must remain root-owned in Dockerfile.web (WA-042)'

        entrypoint = (REPO_ROOT / 'docker' / 'entrypoint.web.sh').read_text(encoding='utf-8')
        chown_lines = [
            ln.strip()
            for ln in entrypoint.splitlines()
            if ln.strip() and not ln.strip().startswith('#') and re.search(r'\bchown\b', ln)
        ]
        assert chown_lines, 'Expected chown commands in docker/entrypoint.web.sh'
        for ln in chown_lines:
            assert re.search(r'\bchown\b[^#\n]*\s-(?:[A-Za-z]*h[A-Za-z]*)(?:\s|$)', ln), (
                f'Every chown invocation in entrypoint.web.sh must pass -h (WA-042): {ln!r}'
            )
        assert 'exec gosu respro "$@"' in entrypoint

        compose = (REPO_ROOT / 'docker-compose.web.yml').read_text(encoding='utf-8')
        web_section, worker_section = compose.split('respro-worker:', 1)
        assert 'build:' in worker_section, 'respro-worker must include build: parity (WA-042)'
        assert ':/data/imprint.html' in web_section, 'imprint.html mount must live under respro-web (WA-042)'
        assert 'imprint.html' not in worker_section, 'imprint.html mount/comment must not appear under respro-worker (WA-042)'

        readme = (REPO_ROOT / 'web' / 'README.md').read_text(encoding='utf-8')
        assert 'web.backend.worker' in readme, 'web/README.md must document worker startup (WA-041)'
        assert 'redis' in readme.lower()

        webapp_doc = (REPO_ROOT / 'docs' / 'docs' / 'webapp.md').read_text(encoding='utf-8')
        assert 'RESPRO_WEB_API_TOKEN' not in webapp_doc, 'Stale RESPRO_WEB_API_TOKEN docs must be removed (WA-013)'

        for compose_rel in ('docker-compose.web_2.yml', 'production/docker-compose.web.yml'):
            compose_file = REPO_ROOT / compose_rel
            if compose_file.is_file():
                c_text = compose_file.read_text(encoding='utf-8')
                tmp_entries = re.findall(r'-\s+/tmp:[^\s#]+', c_text)
                assert tmp_entries, f'Expected /tmp tmpfs entries in {compose_rel}'
                assert all('size=' in entry for entry in tmp_entries), (
                    f'Every /tmp tmpfs entry in {compose_rel} must specify size= (S2/WA-014): {tmp_entries}'
                )

        prod_md = REPO_ROOT / 'production' / 'deployment.md'
        if prod_md.is_file():
            p_text = prod_md.read_text(encoding='utf-8')
            assert 'with _access' not in p_text and 'with_access' not in p_text, (
                'production/deployment.md must fix broken with _access path (W14)'
            )


# ═══════════════════════════════════════════════════════════════════════════════
# TIER 2 — BOUNDARY, CORNER & SECURITY REJECTION (WF1–WF7, >= 5 per WF)
# ═══════════════════════════════════════════════════════════════════════════════


class TestTier2BoundaryAndSecurity:
    """Boundaries, malformed inputs, error sanitization, and security trust boundaries."""

    # ── WF1: Bootstrap, Session, Security Headers & Online Policy ────────────

    def test_wf1_online_mode_rejects_missing_wildcard_proxies_and_cors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        data_dir = tmp_path / 'data'
        pd_dir = data_dir / 'project_databases'
        pd_dir.mkdir(parents=True, exist_ok=True)
        _build_e2e_project_db(pd_dir / 'test.db')
        monkeypatch.setenv('RESPRO_WEB_DATA_DIR', str(data_dir))
        monkeypatch.setenv('RESPRO_WEB_DEPLOYMENT_MODE', 'online')

        # Missing / empty / wildcard / malformed / 0.0.0.0/0 proxies must fail fast (WA-012).
        for bad_proxy in ('', ',', ' , ', '*', 'not-an-ip', '0.0.0.0/0', '127.0.0.1, not-an-ip'):
            monkeypatch.setenv('RESPRO_WEB_TRUSTED_PROXIES', bad_proxy)
            monkeypatch.setenv('RESPRO_WEB_CORS_ORIGINS', 'https://respro.example.org')
            with pytest.raises((RuntimeError, ValueError)):
                load_startup_config()

        # Wildcard CORS in online mode must fail fast (WA-013).
        monkeypatch.setenv('RESPRO_WEB_TRUSTED_PROXIES', '127.0.0.1')
        monkeypatch.setenv('RESPRO_WEB_CORS_ORIGINS', '*')
        with pytest.raises((RuntimeError, ValueError)):
            load_startup_config()

    def test_wf1_online_mode_redis_outage_returns_503_on_stateful_routes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv('RESPRO_WEB_TRUSTED_PROXIES', '127.0.0.1')
        monkeypatch.setenv('RESPRO_WEB_CORS_ORIGINS', 'https://respro.example.org')
        monkeypatch.setenv('REDIS_URL', 'redis://127.0.0.1:1/0')

        dead_pool = _make_dead_redis_pool('connection refused')
        monkeypatch.setattr(redis.ConnectionPool, 'from_url', classmethod(lambda cls, *_a, **_kw: dead_pool))
        monkeypatch.setattr(
            redis.Redis,
            'from_url',
            classmethod(lambda cls, *_a, **_kw: redis.Redis(connection_pool=dead_pool)),
        )
        _reset_redis_pool_caches()

        config = _make_startup_config(tmp_path, deployment_mode='online')
        app = create_app(startup_config=config)
        client = TestClient(app, raise_server_exceptions=False)

        assert client.get('/api/health').status_code == 200
        readiness = client.get('/api/readiness')
        assert readiness.status_code == 503
        assert 'redis_unreachable' in readiness.json()['data']['diagnostics']

        stateful = client.get('/api/databases')
        assert stateful.status_code == 503, f'online mode must fail-closed with 503 on Redis outage (WA-005), got {stateful.status_code}'

    def test_wf1_online_mode_blocks_cross_site_csrf_mutations(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        monkeypatch.setenv('RESPRO_WEB_TRUSTED_PROXIES', '127.0.0.1')
        monkeypatch.setenv('RESPRO_WEB_CORS_ORIGINS', 'https://respro.example.org')
        config = _make_startup_config(tmp_path, deployment_mode='online')
        app, _, _ = _make_app_and_queue(config)
        client = TestClient(app)

        cross_site = client.post(
            '/api/upload/fasta',
            files={'file': ('s.fasta', MUTATED_TINY_FASTA.encode('utf-8'), 'text/plain')},
            headers={'Sec-Fetch-Site': 'cross-site', 'Origin': 'https://evil.example'},
        )
        assert cross_site.status_code == 403, f'Cross-site POST must be blocked 403 in online mode (WA-009), got {cross_site.status_code}'

        bad_origin = client.post(
            '/api/session/cleanup',
            json={'upload_ids': [], 'artifact_ids': []},
            headers={'Origin': 'https://evil.example'},
        )
        assert bad_origin.status_code == 403

    def test_wf1_redis_url_credentials_redacted_in_logs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    ) -> None:
        secret = 'super-secret-redis-pw-9988'
        monkeypatch.setenv('REDIS_URL', f'redis://default:{secret}@127.0.0.1:1/0')

        dead_pool = _make_dead_redis_pool('Error 111 connecting to 127.0.0.1:1. Connection refused.')
        monkeypatch.setattr(redis.ConnectionPool, 'from_url', classmethod(lambda cls, *_a, **_kw: dead_pool))
        monkeypatch.setattr(
            redis.Redis,
            'from_url',
            classmethod(lambda cls, *_a, **_kw: redis.Redis(connection_pool=dead_pool)),
        )
        _reset_redis_pool_caches()
        config = _make_startup_config(tmp_path)
        client = TestClient(_make_app_and_queue(config)[0], raise_server_exceptions=False)

        with caplog.at_level('DEBUG'):
            client.get('/api/readiness')
            db_resp = client.get('/api/databases')

        assert secret not in caplog.text, 'Redis password leaked in logs (WA-010)'
        assert db_resp.status_code == 200, 'Local mode SlowAPI should fall back in-memory on Redis error (WA-030)'

    def test_wf1_imprint_url_and_port_and_ttl_validation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from web.backend import background as background_module

        data_dir = tmp_path / 'data'
        pd_dir = data_dir / 'project_databases'
        pd_dir.mkdir(parents=True, exist_ok=True)
        _build_e2e_project_db(pd_dir / 'test.db')
        monkeypatch.setenv('RESPRO_WEB_DATA_DIR', str(data_dir))

        for bad_imprint in ('http://', 'javascript://alert(1)'):
            monkeypatch.setenv('RESPRO_WEB_IMPRINT', bad_imprint)
            with pytest.raises((ValueError, FileNotFoundError)):
                load_startup_config()
        monkeypatch.delenv('RESPRO_WEB_IMPRINT', raising=False)

        monkeypatch.setenv('RESPRO_WEB_PORT', '70000')
        with pytest.raises((RuntimeError, ValueError)):
            main_module.run()
        monkeypatch.delenv('RESPRO_WEB_PORT', raising=False)

        monkeypatch.setenv('RESPRO_WEB_RESULT_TTL', '0')
        from web.backend.queue import resolve_queue_runtime_settings

        with pytest.raises(ValueError):
            resolve_queue_runtime_settings()

        # WA-040: background._ttl_sweep_loop must also reject/ignore invalid TTL='0' without deleting fresh files.
        ttl_res = tmp_path / 'ttl_res'
        ttl_up = tmp_path / 'ttl_up'
        ttl_res.mkdir()
        ttl_up.mkdir()
        active = ttl_res / 'active.txt'
        active.write_bytes(b'keep-fresh-file')

        class _StopLoop(BaseException):
            pass

        stop_event = threading.Event()
        sweep_params = inspect.signature(background_module._ttl_sweep_loop).parameters
        sweep_kwargs: dict[str, Any] = {}
        if 'stop_event' in sweep_params:
            sweep_kwargs['stop_event'] = stop_event
        elif 'stop' in sweep_params:
            sweep_kwargs['stop'] = stop_event

        wait_calls = 0

        def _fake_wait(self_event: threading.Event, _timeout: float | None = None) -> bool:
            nonlocal wait_calls
            wait_calls += 1
            if wait_calls == 1:
                return False
            self_event.set()
            raise _StopLoop()

        with monkeypatch.context() as mp:
            mp.setattr(background_module.time, 'sleep', lambda _s: (_ for _ in ()).throw(_StopLoop()))
            mp.setattr(threading.Event, 'wait', _fake_wait)
            with contextlib.suppress(_StopLoop, ValueError):
                background_module._ttl_sweep_loop(ttl_res, ttl_up, 1, **sweep_kwargs)
        assert active.is_file(), 'RESPRO_WEB_RESULT_TTL=0 in _ttl_sweep_loop must not wipe fresh files (WA-040)'

    def test_wf1_set_cookie_appends_without_clobbering_and_memory_ttl_expires(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv('RESPRO_WEB_SESSION_TTL', '1')
        monkeypatch.setattr(main_module, '_frontend_dist_dir', lambda: tmp_path / 'no_dist')
        dead_pool = _make_dead_redis_pool('redis disabled for in-memory TTL test')
        if hasattr(session_service, '_redis_connection'):
            monkeypatch.setattr(session_service, '_redis_connection', lambda: None)
        monkeypatch.setattr(redis.ConnectionPool, 'from_url', classmethod(lambda cls, *_a, **_kw: dead_pool))
        monkeypatch.setattr(
            redis.Redis,
            'from_url',
            classmethod(lambda cls, *_a, **_kw: redis.Redis(connection_pool=dead_pool)),
        )
        _reset_redis_pool_caches()

        class _FakeTime:
            def __init__(self, initial: float = 1_700_000_000.0) -> None:
                self.now = initial

            def time(self) -> float:
                return self.now

            def monotonic(self) -> float:
                return self.now

            def advance(self, seconds: float) -> None:
                self.now += seconds

        fake_clock = _FakeTime()
        monkeypatch.setattr(session_service, 'time', fake_clock)

        config = _make_startup_config(tmp_path)
        app, _, _ = _make_app_and_queue(config, is_async=True)

        @app.get('/api/_test_extra_cookie')
        def _extra_cookie():
            from fastapi.responses import JSONResponse

            resp = JSONResponse({'ok': True})
            resp.set_cookie('extra_cookie', 'val')
            return resp

        client = TestClient(app)
        r = client.get('/api/_test_extra_cookie')
        assert r.status_code == 200
        raw_cookies = r.headers.get_list('set-cookie')
        joined = ' | '.join(raw_cookies)
        assert 'extra_cookie=val' in joined and f'{SESSION_COOKIE_NAME}=' in joined, (
            f'Set-Cookie middleware clobbered route Set-Cookie (WA-008): {raw_cookies}'
        )

        # Create owned uploads under two independent in-memory sessions.
        up1 = _upload_file(client, 'fasta', 's1.fa', MUTATED_TINY_FASTA.encode('utf-8'))['upload_id']
        tok1 = client.cookies.get(SESSION_COOKIE_NAME) or ''
        client2 = TestClient(app)
        up2 = _upload_file(client2, 'fasta', 's2.fa', MUTATED_TINY_FASTA.encode('utf-8'))['upload_id']
        tok2 = client2.cookies.get(SESSION_COOKIE_NAME) or ''
        assert tok1 and tok2 and tok1 != tok2
        assert hash_session_token(tok1) in session_service._MEMORY_STORE
        assert hash_session_token(tok2) in session_service._MEMORY_STORE
        assert session_service.fetch_owned_record('upload', up1) is not None
        assert session_service.fetch_owned_record('upload', up2) is not None

        # Advance past TTL=1s and periodic sweep interval (+120s), then trigger a write from client3.
        fake_clock.advance(120.0)
        client3 = TestClient(app)
        up3 = _upload_file(client3, 'fasta', 's3.fa', MUTATED_TINY_FASTA.encode('utf-8'))['upload_id']

        assert hash_session_token(tok2) not in session_service._MEMORY_STORE, (
            'Un-accessed expired session must be periodically swept from _MEMORY_STORE (WA-036)'
        )
        assert f'respro:upload:{up2}' not in session_service._MEMORY_OWNED_STORE, (
            'Un-accessed expired owned record must be periodically swept from _MEMORY_OWNED_STORE (WA-036)'
        )

        # Accessing expired session/upload must rotate cookie and return 404; records for up1/up2 expire.
        expired_prof = client.post(
            '/api/profile/fasta',
            json={'fasta_id': up1, 'database_id': 'test_project.db', 'sample': 'Expired'},
        )
        assert expired_prof.status_code == 404
        assert client.cookies.get(SESSION_COOKIE_NAME) != tok1
        assert session_service.fetch_owned_record('upload', up1) is None
        assert session_service.fetch_owned_record('upload', up2) is None
        assert session_service.fetch_owned_record('upload', up3) is not None

    def test_wf1_error_responses_sanitize_internal_paths_and_tracebacks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        app, _, _ = _make_app_and_queue(config)
        client = TestClient(app)

        for resp in (
            client.get('/api/rules', params={'database_id': 'nonexistent.db'}),
            client.get('/api/jobs/missing-job-id'),
            client.post('/api/upload/fasta', files={'file': ('bad.fa', b'>only_header\n', 'text/plain')}),
        ):
            assert resp.status_code in (400, 404)
            text = resp.text
            assert str(tmp_path) not in text, f'Internal path leaked in error response (WA-011): {text}'
            assert 'Traceback' not in text

    def test_wf1_trusted_proxy_xff_isolation_for_rate_limit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        monkeypatch.setenv('RESPRO_WEB_TRUSTED_PROXIES', '127.0.0.1')
        monkeypatch.setenv('RESPRO_WEB_CORS_ORIGINS', 'https://respro.example.org')
        monkeypatch.setenv('RESPRO_WEB_MAX_BATCH_SIZE', '1')
        config = _make_startup_config(tmp_path, deployment_mode='online')
        app, _, _ = _make_app_and_queue(config, is_async=True)

        proxy_headers, trusted_hosts = rate_limit_module.resolve_proxy_settings()
        assert proxy_headers is True
        wrapped_app = ProxyHeadersMiddleware(app, trusted_hosts=trusted_hosts)

        # Trusted proxy (127.0.0.1): distinct X-Forwarded-For IPs receive isolated quota buckets.
        trusted_client = TestClient(wrapped_app, base_url='https://respro.example.org', client=('127.0.0.1', 50000))
        common_hdrs = {
            'Host': 'respro.example.org',
            'X-Forwarded-Proto': 'https',
            'Origin': 'https://respro.example.org',
            'Sec-Fetch-Site': 'same-origin',
        }
        r1 = trusted_client.post(
            '/api/profile/fasta',
            json={'use_example': True, 'database_id': 'test_project.db', 'sample': 'P1'},
            headers={**common_hdrs, 'X-Forwarded-For': '203.0.113.10'},
        )
        assert r1.status_code == 200
        r2 = trusted_client.post(
            '/api/profile/fasta',
            json={'use_example': True, 'database_id': 'test_project.db', 'sample': 'P2'},
            headers={**common_hdrs, 'X-Forwarded-For': '203.0.113.11'},
        )
        assert r2.status_code == 200
        r3 = trusted_client.post(
            '/api/profile/fasta',
            json={'use_example': True, 'database_id': 'test_project.db', 'sample': 'P3'},
            headers={**common_hdrs, 'X-Forwarded-For': '203.0.113.10'},
        )
        assert r3.status_code == 429

        # Untrusted peer (198.51.100.99): X-Forwarded-For must be ignored so requests share peer bucket.
        untrusted_client = TestClient(wrapped_app, base_url='https://respro.example.org', client=('198.51.100.99', 50000))
        u1 = untrusted_client.post(
            '/api/profile/fasta',
            json={'use_example': True, 'database_id': 'test_project.db', 'sample': 'U1'},
            headers={**common_hdrs, 'X-Forwarded-For': '203.0.113.20'},
        )
        assert u1.status_code == 200
        u2 = untrusted_client.post(
            '/api/profile/fasta',
            json={'use_example': True, 'database_id': 'test_project.db', 'sample': 'U2'},
            headers={**common_hdrs, 'X-Forwarded-For': '203.0.113.21'},
        )
        assert u2.status_code == 429

    # ── WF2: Upload Admission & Validation Boundaries ────────────────────────

    def test_wf2_pre_parse_upload_rejects_oversized_and_multi_part_early(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        client = TestClient(_make_app_and_queue(config)[0])

        # Extra file parts or fields must be rejected 4xx.
        multi = client.post(
            '/api/upload/fasta',
            files=[
                ('file', ('a.fa', MUTATED_TINY_FASTA.encode('utf-8'), 'text/plain')),
                ('file', ('b.fa', MUTATED_TINY_FASTA.encode('utf-8'), 'text/plain')),
            ],
        )
        assert 400 <= multi.status_code < 500

        oversized_vcf = b'##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n' + (b'x' * 1_100_000)
        over = client.post('/api/upload/vcf', files={'file': ('big.vcf', oversized_vcf, 'text/plain')})
        assert over.status_code in (400, 413)
        assert list(config.uploads_dir.iterdir()) == []

    def test_wf2_line_length_cap_enforced_during_streaming_before_max_size(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        client = TestClient(_make_app_and_queue(config)[0])

        # 100,001 bytes without a newline exceeds upload_max_vcf_line_length (100,000) well under 1 MiB.
        long_line = b'##fileformat=VCFv4.2\n' + (b'A' * 100_005)
        resp = client.post('/api/upload/vcf', files={'file': ('long.vcf', long_line, 'text/plain')})
        assert resp.status_code == 400
        assert list(config.uploads_dir.iterdir()) == []

    def test_wf2_bam_magic_checked_at_18_bytes_during_streaming(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from web.backend.services.upload import save_upload_stream

        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        client = TestClient(_make_app_and_queue(config)[0])

        assert client.post('/api/upload/bam', files={'file': ('tiny.bam', b'12345', 'application/octet-stream')}).status_code == 400
        assert client.post('/api/upload/bam', files={'file': ('zeros.bam', b'\x00' * 65536, 'application/octet-stream')}).status_code == 400
        assert list(config.uploads_dir.iterdir()) == []

        class _TwoChunkBadBam:
            def __init__(self) -> None:
                self.reads = 0

            async def read(self, size: int = -1) -> bytes:
                del size
                self.reads += 1
                if self.reads == 1:
                    return b'\x00' * 32
                if self.reads == 2:
                    return b'\x00' * 65536
                return b''

        stream = _TwoChunkBadBam()
        with pytest.raises(ValueError, match='BGZF|BAM'):
            asyncio.run(save_upload_stream(stream, 'bam', config.uploads_dir, chunk_size=32))
        assert stream.reads == 1, (
            f'BAM magic must reject at byte 18 on chunk 1 before reading chunk 2 (WA-014), read {stream.reads} chunks'
        )
        assert list(config.uploads_dir.iterdir()) == []

    def test_wf2_vcf_gz_bomb_and_corrupt_gzip_rejected_400(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        client = TestClient(_make_app_and_queue(config)[0])

        # Corrupt gzip header/payload
        corrupt = client.post('/api/upload/vcf', files={'file': ('bad.vcf.gz', b'\x1f\x8b\x08\x00corrupt', 'application/gzip')})
        assert corrupt.status_code == 400

        # Highly compressible gzip bomb (~18.88 MiB decompressed > 16 MiB cap from ~57.7 KiB compressed)
        header = b'##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
        row = f'{TINY_REF_NAME}\t4\t.\tA\tG\t100\tPASS\tAF=0.5\n'.encode()
        bomb = gzip.compress(header + row * 600_000, compresslevel=9)
        assert len(bomb) < WEB_BACKEND_CONFIG.defaults.upload_max_vcf_size
        bomb_resp = client.post('/api/upload/vcf', files={'file': ('bomb.vcf.gz', bomb, 'application/gzip')})
        assert bomb_resp.status_code in (400, 413), f'Gzip bomb must be rejected 400/413 (WA-015), got {bomb_resp.status_code}'
        assert list(config.uploads_dir.iterdir()) == []

    def test_wf2_fasta_rejects_del_embedded_cr_and_header_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        client = TestClient(_make_app_and_queue(config)[0])

        for bad_name, payload in (
            ('del.fa', b'>seq1\nATG\x7fAAA\n'),
            ('cr.fa', b'>seq1\nATG\rAAA\n'),
            ('header_only.fa', b'>seq1\n'),
            ('bad_chars.fa', b'>seq1\n123456\n'),
        ):
            resp = client.post('/api/upload/fasta', files={'file': (bad_name, payload, 'text/plain')})
            assert resp.status_code == 400, f'FASTA {bad_name} must be rejected 400 (S5-B4), got {resp.status_code}'

    def test_wf2_vcf_rejects_missing_headers_and_data_before_chrom(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        client = TestClient(_make_app_and_queue(config)[0])

        no_fmt = b'#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\ntiny_ref\t4\t.\tA\tG\t100\tPASS\tAF=0.5\n'
        data_first = b'##fileformat=VCFv4.2\ntiny_ref\t4\t.\tA\tG\t100\tPASS\tAF=0.5\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
        assert client.post('/api/upload/vcf', files={'file': ('nofmt.vcf', no_fmt, 'text/plain')}).status_code == 400
        assert client.post('/api/upload/vcf', files={'file': ('order.vcf', data_first, 'text/plain')}).status_code == 400

    def test_wf2_cross_type_upload_id_rejected_400_on_profile_routes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        client = TestClient(_make_app_and_queue(config, is_async=True)[0])

        bam_id = _upload_file(client, 'bam', 'm.bam', MINIMAL_BAM_BYTES)['upload_id']
        fasta_id = _upload_file(client, 'fasta', 's.fa', MUTATED_TINY_FASTA.encode('utf-8'))['upload_id']
        vcf_id = _upload_file(client, 'vcf', 'v.vcf', VALID_TINY_VCF.encode('utf-8'))['upload_id']

        r1 = client.post('/api/profile/fasta', json={'fasta_id': bam_id, 'database_id': 'test_project.db'})
        r2 = client.post(
            '/api/profile/vcf',
            json={'vcf_id': bam_id, 'reference_id': fasta_id, 'database_id': 'test_project.db'},
        )
        r3 = client.post(
            '/api/profile/vcf',
            json={'vcf_id': vcf_id, 'reference_id': fasta_id, 'bam_id': fasta_id, 'database_id': 'test_project.db'},
        )
        for resp in (r1, r2, r3):
            assert resp.status_code == 400, f'Cross-type upload_id must return 400 (WA-004), got {resp.status_code}: {resp.text}'

    def test_wf2_sample_name_flag_injection_and_opaque_id_bounds_rejected_422(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        client = TestClient(_make_app_and_queue(config, is_async=True)[0])

        assert client.post('/api/profile/fasta', json={'use_example': True, 'sample': '-rf'}).status_code == 422
        assert client.post('/api/profile/fasta', json={'use_example': True, 'sample': 'x' * 129}).status_code == 422
        assert client.post('/api/profile/fasta', json={'use_example': True, 'threads': 5}).status_code == 422
        assert client.get(f"/api/jobs/{'a' * 257}").status_code == 422
        assert client.get('/api/report', params={'artifact_id': 'b' * 257}).status_code == 422

    # ── WF3: Batch, Worker Queues & Quota Boundaries ─────────────────────────

    def test_wf3_validation_runs_before_sample_quota_burn(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        monkeypatch.setattr(rate_limit_module, '_current_window_minute', lambda: 1_000_000)
        monkeypatch.setenv('RESPRO_WEB_MAX_BATCH_SIZE', '1')
        config = _make_startup_config(tmp_path)
        client = TestClient(_make_app_and_queue(config, is_async=True)[0])

        fasta_id = _upload_file(client, 'fasta', 'ok.fa', MUTATED_TINY_FASTA.encode('utf-8'))['upload_id']

        bad_db = client.post(
            '/api/profile/fasta',
            json={'fasta_id': fasta_id, 'database_id': 'missing.db', 'sample': 'BadDb'},
        )
        assert bad_db.status_code == 400

        bad_batch = client.post(
            '/api/profile/batch/fasta',
            json={'fasta_ids': ['nonexistent-id'], 'db_path': 'test_project.db', 'sample_names': ['S1']},
        )
        assert bad_batch.status_code == 404

        # The single valid sample in the minute budget must still succeed (WA-017).
        valid = client.post(
            '/api/profile/fasta',
            json={'fasta_id': fasta_id, 'database_id': 'test_project.db', 'sample': 'Good'},
        )
        assert valid.status_code == 200, f'Invalid request burned quota (WA-017): {valid.text}'

    @pytest.mark.parametrize('use_redis', [False, True])
    def test_wf3_over_quota_batch_does_not_poison_remaining_quota(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, use_redis: bool,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        monkeypatch.setattr(rate_limit_module, '_current_window_minute', lambda: 1_000_000)
        if use_redis:
            monkeypatch.setenv('REDIS_URL', 'redis://127.0.0.1:6379/0')
        monkeypatch.setenv('RESPRO_WEB_MAX_BATCH_SIZE', '3')
        config = _make_startup_config(tmp_path)
        client = TestClient(_make_app_and_queue(config, is_async=True)[0])

        f1 = _upload_file(client, 'fasta', '1.fa', MUTATED_TINY_FASTA.encode('utf-8'))['upload_id']
        f2 = _upload_file(client, 'fasta', '2.fa', MUTATED_TINY_FASTA.encode('utf-8'))['upload_id']
        f3 = _upload_file(client, 'fasta', '3.fa', MUTATED_TINY_FASTA.encode('utf-8'))['upload_id']

        # Consume 2 of 3 samples.
        assert client.post(
            '/api/profile/batch/fasta',
            json={'fasta_ids': [f1, f2], 'db_path': 'test_project.db', 'sample_names': ['A', 'B']},
        ).status_code == 200

        # Requesting 2 more exceeds 3 -> 429 with Retry-After, and must NOT increment quota to 4.
        too_big = client.post(
            '/api/profile/batch/fasta',
            json={'fasta_ids': [f1, f2], 'db_path': 'test_project.db', 'sample_names': ['C', 'D']},
        )
        assert too_big.status_code == 429
        assert int(too_big.headers['retry-after']) >= 1

        # Remaining 1-sample slot must still succeed.
        last_one = client.post(
            '/api/profile/fasta',
            json={'fasta_id': f3, 'database_id': 'test_project.db', 'sample': 'Last'},
        )
        assert last_one.status_code == 200

    def test_wf3_batch_length_mismatch_and_exceeds_max_batch_size_422(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        monkeypatch.setenv('RESPRO_WEB_MAX_BATCH_SIZE', '2')
        config = _make_startup_config(tmp_path)
        client = TestClient(_make_app_and_queue(config, is_async=True)[0])

        assert client.post(
            '/api/profile/batch/fasta',
            json={'fasta_ids': ['a', 'b'], 'db_path': 'test_project.db', 'sample_names': ['only_one']},
        ).status_code == 422

        assert client.post(
            '/api/profile/batch/fasta',
            json={'fasta_ids': ['a', 'b', 'c'], 'db_path': 'test_project.db', 'sample_names': ['1', '2', '3']},
        ).status_code == 422

        assert client.post(
            '/api/profile/batch/vcf',
            json={
                'vcf_ids': ['v1'],
                'reference_id': 'r1',
                'db_path': 'test_project.db',
                'sample_names': ['S1'],
                'bam_ids': ['b1', 'b2'],
            },
        ).status_code == 422

    def test_wf3_upload_rate_limit_429_includes_retry_after(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        monkeypatch.setenv('RESPRO_WEB_UPLOAD_RATE_LIMIT', '2/minute')
        config = _make_startup_config(tmp_path)
        client = TestClient(_make_app_and_queue(config)[0])

        _upload_file(client, 'fasta', '1.fa', MUTATED_TINY_FASTA.encode('utf-8'))
        _upload_file(client, 'fasta', '2.fa', MUTATED_TINY_FASTA.encode('utf-8'))
        third = client.post('/api/upload/fasta', files={'file': ('3.fa', MUTATED_TINY_FASTA.encode('utf-8'), 'text/plain')})
        assert third.status_code == 429
        assert int(third.headers.get('retry-after', '0')) >= 1, '429 rate-limit response must include Retry-After (WA-041)'

    def test_wf3_enqueue_failure_rolls_back_job_record(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_redis, _ = _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        app, _, _ = _make_app_and_queue(config, is_async=True)

        class _BrokenQueue:
            def enqueue(self, *_args: Any, **_kwargs: Any) -> None:
                raise RuntimeError('broker down')

        app.dependency_overrides[get_queue] = lambda: _BrokenQueue()
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post(
            '/api/profile/fasta',
            json={'use_example': True, 'database_id': 'test_project.db', 'sample': 'Rollback'},
        )
        assert resp.status_code >= 500
        assert list(fake_redis.scan_iter('respro:job:*')) == [], 'Failed enqueue must roll back job record (WA-040)'

    def test_wf3_batch_unknown_or_cross_type_bam_id_rejected_without_quota_burn(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        monkeypatch.setattr(rate_limit_module, '_current_window_minute', lambda: 1_000_000)
        monkeypatch.setenv('RESPRO_WEB_MAX_BATCH_SIZE', '1')
        config = _make_startup_config(tmp_path)
        client = TestClient(_make_app_and_queue(config, is_async=True)[0])

        vcf_id = _upload_file(client, 'vcf', 'v.vcf', VALID_TINY_VCF.encode('utf-8'))['upload_id']
        ref_id = _upload_file(client, 'fasta', 'r.fa', VALID_REF_FASTA.encode('utf-8'))['upload_id']

        bad_bam = client.post(
            '/api/profile/batch/vcf',
            json={
                'vcf_ids': [vcf_id],
                'reference_id': ref_id,
                'db_path': 'test_project.db',
                'sample_names': ['S1'],
                'bam_ids': [vcf_id],
            },
        )
        assert bad_bam.status_code == 400

        ok = client.post(
            '/api/profile/fasta',
            json={'fasta_id': ref_id, 'database_id': 'test_project.db', 'sample': 'StillAllowed'},
        )
        assert ok.status_code == 200

    # ── WF4: JSON Regeneration Security & Boundaries ─────────────────────────

    def test_wf4_regenerate_rejects_cross_type_upload_id_400(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        client = TestClient(_make_app_and_queue(config, is_async=True)[0])

        fasta_id = _upload_file(client, 'fasta', 's.fa', MUTATED_TINY_FASTA.encode('utf-8'))['upload_id']
        resp = client.post('/api/regenerate/json', json={'json_id': fasta_id})
        assert resp.status_code == 400, f'Cross-type json_id must return 400 (WA-004), got {resp.status_code}'

    def test_wf4_regenerate_rejects_invalid_json_and_uuid_mismatch_400(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        client = TestClient(_make_app_and_queue(config, is_async=True)[0])

        bad_schema = client.post('/api/upload/json', files={'file': ('bad.json', b'{"hello": "world"}', 'application/json')})
        assert bad_schema.status_code == 400

        mismatch_path = _write_synthetic_results_json(
            tmp_path / 'mismatch.results.json',
            project_uuid=str(uuid.uuid4()),
        )
        up = _upload_file(client, 'json', 'mismatch.results.json', mismatch_path.read_bytes())
        regen = client.post('/api/regenerate/json', json={'json_id': up['upload_id']})
        assert regen.status_code == 400

    def test_wf4_regenerate_rejects_foreign_session_upload_404(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        app, _, _ = _make_app_and_queue(config, is_async=True)
        client_a = TestClient(app)
        client_b = TestClient(app)

        json_path = _write_synthetic_results_json(tmp_path / 'a.results.json')
        up_a = _upload_file(client_a, 'json', 'a.results.json', json_path.read_bytes())['upload_id']

        resp_b = client_b.post('/api/regenerate/json', json={'json_id': up_a, 'database_id': 'test_project.db'})
        assert resp_b.status_code == 404

    def test_wf4_regenerate_batch_partial_invalid_fails_before_enqueue_and_quota(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        monkeypatch.setattr(rate_limit_module, '_current_window_minute', lambda: 1_000_000)
        monkeypatch.setenv('RESPRO_WEB_MAX_BATCH_SIZE', '2')
        db_uuid = str(uuid.uuid4())
        config = _make_startup_config(
            tmp_path,
            db_builders=[('test_project.db', {'db_uuid': db_uuid})],
        )
        app, _, batch_q = _make_app_and_queue(config, is_async=True)
        client = TestClient(app)

        valid_p = _write_synthetic_results_json(tmp_path / 'v.results.json', project_uuid=db_uuid)
        bad_uuid_p = _write_synthetic_results_json(tmp_path / 'bad.results.json', project_uuid=str(uuid.uuid4()))
        u_ok = _upload_file(client, 'json', 'v.results.json', valid_p.read_bytes())['upload_id']
        u_bad = _upload_file(client, 'json', 'bad.results.json', bad_uuid_p.read_bytes())['upload_id']

        resp = client.post(
            '/api/regenerate/batch',
            json={'json_ids': [u_ok, u_bad], 'sample_names': ['S1', 'S2']},
        )
        assert resp.status_code == 400
        assert batch_q.count == 0

        # Full quota of 2 remains available.
        assert client.post('/api/regenerate/json', json={'json_id': u_ok}).status_code == 200

    def test_wf4_regenerate_oversized_or_binary_json_upload_rejected_400(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        client = TestClient(_make_app_and_queue(config)[0])

        for name, payload in (
            ('empty.json', b'   '),
            ('nul.json', b'{"run": "\x00"}'),
            ('latin1.json', b'\xff\xfe{"run": 1}'),
        ):
            assert client.post('/api/upload/json', files={'file': (name, payload, 'application/json')}).status_code == 400

    # ── WF5: Results, Artifacts, Sample Compare & Cleanup Boundaries ─────────

    def test_wf5_foreign_session_isolation_across_jobs_artifacts_compare_cleanup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        app, _, _ = _make_app_and_queue(config)
        client_a = TestClient(app)
        client_b = TestClient(app)

        up_a = _upload_file(client_a, 'fasta', 'a.fa', MUTATED_TINY_FASTA.encode('utf-8'))['upload_id']
        job_id = client_a.post(
            '/api/profile/fasta',
            json={'fasta_id': up_a, 'database_id': 'test_project.db', 'sample': 'OwnerA'},
        ).json()['job_id']
        res_a = client_a.get(f'/api/jobs/{job_id}').json()['result']
        html_id = res_a['report_html_path']
        json_id = res_a['report_json_path']

        assert client_b.get(f'/api/jobs/{job_id}').status_code == 404
        assert client_b.delete(f'/api/jobs/{job_id}').status_code == 404
        assert client_b.get('/api/report', params={'artifact_id': html_id}).status_code == 404
        assert client_b.get('/api/artifact', params={'artifact_id': json_id}).status_code == 404
        assert client_b.post('/api/artifact-bundle', json={'artifact_ids': [html_id]}).status_code == 404
        assert client_b.post('/api/compare', json={'artifact_ids': [json_id]}).status_code == 404

        cleanup_b = client_b.post('/api/session/cleanup', json={'upload_ids': [up_a], 'artifact_ids': [html_id]})
        assert cleanup_b.status_code == 200
        assert cleanup_b.json()['deleted_count'] == 0
        assert client_a.get('/api/artifact', params={'artifact_id': json_id}).status_code == 200

    def test_wf5_compare_rejects_giant_or_negative_or_inverted_coverage_gaps_400(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        client = TestClient(_make_app_and_queue(config)[0])
        client.get('/api/databases')
        sh = hash_session_token(client.cookies.get(SESSION_COOKIE_NAME) or '')

        for idx, bad_gap in enumerate([
            {'feature_name': 'gag', 'codon_start': 0, 'codon_end': 1_000_000_000},
            {'feature_name': 'gag', 'codon_start': -1, 'codon_end': 5},
            {'feature_name': 'gag', 'codon_start': 10, 'codon_end': 5},
        ]):
            art = _record_owned_result_json(
                config,
                sh,
                f'badgap_{idx}.20261124000001000000.results.json',
                coverage_gaps=[bad_gap],
            )
            resp = client.post('/api/compare', json={'artifact_ids': [art]})
            assert resp.status_code == 400, f'Bounded gap check (S1) failed for {bad_gap}: {resp.status_code}'

    def test_wf5_compare_handles_null_variant_result_and_mismatched_refs_400(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        client = TestClient(_make_app_and_queue(config)[0])
        client.get('/api/databases')
        sh = hash_session_token(client.cookies.get(SESSION_COOKIE_NAME) or '')

        # variant_result: null must not raise TypeError / 500 (WA-006).
        null_vr = config.results_dir / 'nullvr.20261124000001000000.results.json'
        null_vr.write_text(
            json.dumps({
                'run': {'project_name': 'E2E Project', 'reference_name': TINY_REF_NAME, 'sample_name': 'NullVR'},
                'variant_result': None,
                'coverage_gap': [],
                'formula_rule_hit': [],
                'sample_classification': [],
            }),
            encoding='utf-8',
        )
        a_null = session_service.record_artifact(session_hash=sh, canonical_path=null_vr, media_type='application/json')
        assert client.post('/api/compare', json={'artifact_ids': [a_null]}).status_code == 200

        # Mismatched second reference in multi-reference runs must return 400 (WA-016).
        m1 = _record_owned_result_json(
            config, sh, 'm1.20261124000001000000.results.json',
            sample_name='M1', references=['Ref1', 'Ref2'],
        )
        m2 = _record_owned_result_json(
            config, sh, 'm2.20261124000002000000.results.json',
            sample_name='M2', references=['Ref1', 'Ref3'],
        )
        assert client.post('/api/compare', json={'artifact_ids': [m1, m2]}).status_code == 400

    def test_wf5_artifact_bundle_rejects_or_deduplicates_duplicate_ids_and_caps(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        monkeypatch.setenv('RESPRO_WEB_MAX_BATCH_SIZE', '1')  # bundle max = 4
        config = _make_startup_config(tmp_path)
        client = TestClient(_make_app_and_queue(config)[0])

        job_id = client.post(
            '/api/profile/fasta',
            json={'use_example': True, 'database_id': 'test_project.db', 'sample': 'DupTest'},
        ).json()['job_id']
        html_id = client.get(f'/api/jobs/{job_id}').json()['result']['report_html_path']

        # Duplicate IDs must either be rejected 400 or deduplicated to 1 ZIP entry (WA-029).
        dup = client.post('/api/artifact-bundle', json={'artifact_ids': [html_id, html_id]})
        assert dup.status_code in (200, 400)
        if dup.status_code == 200:
            with zipfile.ZipFile(io.BytesIO(dup.content)) as zf:
                assert len(zf.namelist()) == 1

        assert client.post('/api/artifact-bundle', json={'artifact_ids': []}).status_code == 400
        assert client.post('/api/artifact-bundle', params={'part': 0}, json={'artifact_ids': [html_id]}).status_code == 422

    def test_wf5_open_report_and_artifact_reject_non_html_and_oversized_ids(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        client = TestClient(_make_app_and_queue(config)[0])

        job_id = client.post(
            '/api/profile/fasta',
            json={'use_example': True, 'database_id': 'test_project.db', 'sample': 'NonHtml'},
        ).json()['job_id']
        json_id = client.get(f'/api/jobs/{job_id}').json()['result']['report_json_path']

        assert client.get('/api/report', params={'artifact_id': json_id}).status_code == 400
        assert client.get('/api/artifact', params={'artifact_id': 'x' * 257}).status_code == 422

    def test_wf5_cleanup_preserves_ownership_on_unlink_error_and_confines_bai(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        client = TestClient(_make_app_and_queue(config)[0])

        # BAM 1: symlinked .bam.bai pointing outside uploads_dir must NOT be unlinked or modify target (W7/WA-039).
        bam1_bytes = _build_pysam_bam(tmp_path / 'b1.bam').read_bytes()
        bam1_up = _upload_file(client, 'bam', 'b1.bam', bam1_bytes)['upload_id']
        bam1_rec = session_service.fetch_owned_record('upload', bam1_up)
        assert bam1_rec is not None and bam1_rec.canonical_path
        bam1_saved = Path(bam1_rec.canonical_path)
        outside_bai = tmp_path / 'outside.bai'
        outside_bai.write_bytes(b'DO_NOT_DELETE')
        bai1 = Path(f'{bam1_saved}.bai')
        bai1.symlink_to(outside_bai)

        c1 = client.post('/api/session/cleanup', json={'upload_ids': [bam1_up], 'artifact_ids': []})
        assert c1.status_code == 200
        assert c1.json()['deleted_count'] == 1
        assert not bam1_saved.exists()
        assert bai1.is_symlink(), 'Symlinked .bam.bai escaping uploads_dir must not be followed/unlinked (WA-039)'
        assert outside_bai.read_bytes() == b'DO_NOT_DELETE'
        bai1.unlink(missing_ok=True)

        # BAM 2: confined regular .bam.bai sidecar must be deleted alongside .bam.
        bam2_up = _upload_file(client, 'bam', 'b2.bam', bam1_bytes)['upload_id']
        bam2_rec = session_service.fetch_owned_record('upload', bam2_up)
        assert bam2_rec is not None and bam2_rec.canonical_path
        bam2_saved = Path(bam2_rec.canonical_path)
        bai2 = Path(f'{bam2_saved}.bai')
        bai2.write_bytes(b'BAI!')

        c2 = client.post('/api/session/cleanup', json={'upload_ids': [bam2_up], 'artifact_ids': []})
        assert c2.status_code == 200
        assert c2.json()['deleted_count'] == 1
        assert not bam2_saved.exists()
        assert not bai2.exists(), 'Confined .bam.bai sidecar must be deleted during cleanup (W7/WA-039)'

        # Unlink failure must preserve ownership record.
        up_id = _upload_file(client, 'fasta', 'keep.fa', MUTATED_TINY_FASTA.encode('utf-8'))['upload_id']
        rec = session_service.fetch_owned_record('upload', up_id)
        assert rec is not None and rec.canonical_path
        saved = Path(rec.canonical_path)

        orig_unlink = Path.unlink

        def _failing_unlink(self: Path, missing_ok: bool = False) -> None:
            if self == saved:
                raise OSError('EACCES')
            orig_unlink(self, missing_ok=missing_ok)

        monkeypatch.setattr(Path, 'unlink', _failing_unlink)
        resp = client.post('/api/session/cleanup', json={'upload_ids': [up_id], 'artifact_ids': []})
        assert resp.status_code == 200
        assert resp.json()['deleted_count'] == 0
        assert session_service.fetch_owned_record('upload', up_id) is not None, (
            'Ownership record must be preserved if unlink fails (WA-039)'
        )

    def test_wf5_sample_compare_disambiguates_colliding_names_and_stems(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        client = TestClient(_make_app_and_queue(config)[0])
        client.get('/api/databases')
        sh = hash_session_token(client.cookies.get(SESSION_COOKIE_NAME) or '')

        a1 = _record_owned_result_json(config, sh, 'same.20261124000001000000.results.json', sample_name='SameName')
        a2 = _record_owned_result_json(config, sh, 'same.20261124000002000000.results.json', sample_name='SameName')
        resp = client.post('/api/compare', json={'artifact_ids': [a1, a2]})
        assert resp.status_code == 200
        labels = resp.json()['samples']
        assert len(set(labels)) == 2, f'Colliding sample names & stems must be disambiguated (S5-B5): {labels}'

    def test_wf5_report_and_legal_strict_csp_headers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(
            tmp_path,
            imprint=ImprintConfig(kind='path', html='<html><body>Legal</body></html>'),
        )
        client = TestClient(_make_app_and_queue(config)[0])

        legal = client.get('/legal')
        assert legal.status_code == 200
        legal_csp = legal.headers.get('content-security-policy', '')
        assert "script-src 'none'" in legal_csp and "frame-ancestors 'none'" in legal_csp, (
            f'/legal missing strict CSP (WA-009): {legal_csp!r}'
        )

        job_id = client.post(
            '/api/profile/fasta',
            json={'use_example': True, 'database_id': 'test_project.db', 'sample': 'CspRep'},
        ).json()['job_id']
        html_id = client.get(f'/api/jobs/{job_id}').json()['result']['report_html_path']
        rep = client.get('/api/report', params={'artifact_id': html_id})
        rep_csp = rep.headers.get('content-security-policy', '')
        for directive in ("frame-ancestors 'self'", "connect-src 'none'", "form-action 'none'", "base-uri 'none'"):
            assert directive in rep_csp, f'/api/report CSP missing {directive!r} (WA-009): {rep_csp!r}'

    # ── WF6: Catalog, DB Compare & Maintained-DB Confinement ─────────────────

    def test_wf6_catalog_rejects_unknown_db_and_ambiguous_reference_without_path_leak(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        client = TestClient(_make_app_and_queue(config)[0])

        r1 = client.get('/api/rules', params={'database_id': 'no_such.db'})
        assert r1.status_code == 400
        assert str(tmp_path) not in r1.text

        r2 = client.get('/api/rules', params={'database_id': 'test_project.db', 'reference': 'missing_ref'})
        assert r2.status_code == 400
        assert str(tmp_path) not in r2.text

    def test_wf6_all_dbs_corrupt_returns_400_not_500(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        for p in config.project_databases_dir.glob('*.db'):
            p.unlink()
        (config.project_databases_dir / 'corrupt.db').write_bytes(b'corrupt-bytes')

        client = TestClient(_make_app_and_queue(config)[0])
        resp = client.get('/api/databases')
        assert resp.status_code == 400, f'All corrupt DBs must return 400 not 500 (WA-034): {resp.text}'
        assert str(tmp_path) not in resp.text

    def test_wf6_databases_compare_rejects_duplicate_empty_or_invalid_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(
            tmp_path,
            db_builders=[
                ('db1.db', {'references': [{'name': 'RefA', 'accession': 'ACC1'}]}),
                ('db2.db', {'references': [{'name': 'RefA', 'accession': 'ACC1'}]}),
            ],
        )
        client = TestClient(_make_app_and_queue(config)[0])

        assert client.get('/api/databases/shared-references', params={'ids': 'db1.db'}).status_code == 400
        assert client.get('/api/databases/shared-references', params={'ids': 'db1.db,db1.db'}).status_code == 400
        assert client.post(
            '/api/databases/compare',
            json={'database_ids': ['db1.db', 'db1.db'], 'accession': 'ACC1'},
        ).status_code == 400
        assert client.post(
            '/api/databases/compare',
            json={'database_ids': ['db1.db', ''], 'accession': 'ACC1'},
        ).status_code == 400

    def test_wf6_maintained_db_rejects_path_traversal_and_symlinks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from respro.io import maintained_db
        from tests.conftest import write_genbank
        from web.backend.services import maintained_bootstrap

        srv_dir = tmp_path / 'srv'
        (srv_dir / 'db').mkdir(parents=True)
        write_genbank(
            srv_dir / 'ACC_EVIL.gb',
            [{'id': 'ACC_EVIL', 'accession': 'ACC_EVIL', 'organism': 'V', 'sequence': TINY_REF_SEQ, 'features': [{'feature': 'gag', 'start': 1, 'end': len(TINY_REF_SEQ), 'strand': '+'}]}],
        )
        (srv_dir / 'db' / 'rules.tsv').write_text(
            'feature\tposition\treference\tmutation\tantiviral\tphenotype\treference_identifier\n'
            'gag\t2\tK\tE\tTestDrug\tresistant\tACC_EVIL\n',
            encoding='utf-8',
        )
        (srv_dir / 'db' / 'metadata.json').write_text(json.dumps({'tsv_checksum': 'sha256:new'}), encoding='utf-8')

        requested_paths: list[str] = []

        class _TrackingHandler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, directory=str(srv_dir), **kwargs)

            def do_GET(self) -> None:
                requested_paths.append(self.path)
                super().do_GET()

            def log_message(self, format: str, *args: Any) -> None:
                return

        httpd = socketserver.TCPServer(('127.0.0.1', 0), _TrackingHandler)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            base_url = f'http://127.0.0.1:{port}'
            monkeypatch.setattr(
                maintained_db,
                'CLI_CONFIG',
                replace(
                    maintained_db.CLI_CONFIG,
                    urls=replace(
                        maintained_db.CLI_CONFIG.urls,
                        github_respro_db_raw=base_url,
                        ncbi_nuccore_efetch=f'{base_url}/{{accession}}.gb',
                    ),
                ),
            )
            pd_dir = tmp_path / 'pds'
            pd_dir.mkdir()

            # 1. Path traversal / multi-component source_names must fail BEFORE downloading db/rules.tsv (S3).
            for bad_name in ('../escape', 'a/b', '/abs', '..', '.'):
                requested_paths.clear()
                (srv_dir / 'manifest.json').write_text(
                    json.dumps({
                        'databases': [
                            {
                                'source_name': bad_name,
                                'rules_path': 'db/rules.tsv',
                                'metadata_path': 'db/metadata.json',
                                'formula_rules_path': '',
                                'metadata': {'tsv_checksum': 'sha256:new'},
                            }
                        ]
                    }),
                    encoding='utf-8',
                )
                with pytest.raises((RuntimeError, ValueError)):
                    maintained_bootstrap.bootstrap_missing_maintained_databases(pd_dir)
                assert '/db/rules.tsv' not in requested_paths, (
                    f'Unsafe source_name {bad_name!r} was not rejected before download (S3)'
                )
            assert not (tmp_path / 'escape.db').exists()
            assert not (pd_dir.parent / 'escape.db').exists()

            # 2. Symlink confinement during bootstrap (S3): pre-existing symlink must not be unlinked/overwritten.
            outside_sentinel = tmp_path / 'outside_sentinel.db'
            outside_sentinel.write_bytes(b'outside-sentinel')
            sym_link = pd_dir / 'sym_db.db'
            sym_link.symlink_to(outside_sentinel)
            (srv_dir / 'manifest.json').write_text(
                json.dumps({
                    'databases': [
                        {
                            'source_name': 'sym_db',
                            'rules_path': 'db/rules.tsv',
                            'metadata_path': 'db/metadata.json',
                            'formula_rules_path': '',
                            'metadata': {'tsv_checksum': 'sha256:new'},
                        }
                    ]
                }),
                encoding='utf-8',
            )
            with contextlib.suppress(RuntimeError, ValueError):
                maintained_bootstrap.bootstrap_missing_maintained_databases(pd_dir)
            assert sym_link.is_symlink(), 'Bootstrap must refuse/not unlink symlinked DB target (S3)'
            assert outside_sentinel.read_bytes() == b'outside-sentinel'

            # 3. Symlink confinement during update (S3): stale DB reached via symlink must not be replaced.
            outside_valid = tmp_path / 'outside_valid.db'
            _build_e2e_project_db(outside_valid, project_name='SymUpd', tsv_checksum='sha256:old')
            sym_upd = pd_dir / 'sym_upd.db'
            sym_upd.symlink_to(outside_valid)
            (srv_dir / 'manifest.json').write_text(
                json.dumps({
                    'databases': [
                        {
                            'source_name': 'sym_upd',
                            'rules_path': 'db/rules.tsv',
                            'metadata_path': 'db/metadata.json',
                            'formula_rules_path': '',
                            'metadata': {'tsv_checksum': 'sha256:new'},
                        }
                    ]
                }),
                encoding='utf-8',
            )
            maintained_bootstrap.check_and_update_maintained_databases(pd_dir)
            assert sym_upd.is_symlink(), 'Update must refuse/not replace symlinked DB target (S3)'
            assert maintained_bootstrap._read_local_tsv_checksum(outside_valid) == 'sha256:old'
        finally:
            httpd.shutdown()

    def test_wf6_maintained_db_rejects_foreign_manifest_urls_and_redirects(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from respro.io import maintained_db

        # 1. Absolute foreign URL or .. traversal in manifest path must be rejected (S4).
        bad_manifest = {
            'databases': [
                {
                    'source_name': 'good_name',
                    'rules_path': 'https://evil.example/rules.tsv',
                    'metadata_path': '../secret.json',
                    'formula_rules_path': '',
                    'metadata': {'tsv_checksum': 'abc'},
                }
            ]
        }
        srv_dir = tmp_path / 'srv'
        srv_dir.mkdir()
        (srv_dir / 'manifest.json').write_text(json.dumps(bad_manifest), encoding='utf-8')
        httpd, port = _start_local_http_server(srv_dir)
        try:
            monkeypatch.setattr(
                maintained_db,
                'CLI_CONFIG',
                replace(
                    maintained_db.CLI_CONFIG,
                    urls=replace(maintained_db.CLI_CONFIG.urls, github_respro_db_raw=f'http://127.0.0.1:{port}'),
                ),
            )
            with pytest.raises((RuntimeError, ValueError)):
                maintained_db.list_output_files('good_name')
        finally:
            httpd.shutdown()

        # 2. HTTP 302 redirect to a foreign origin (127.0.0.1 -> 127.0.0.2) must be rejected (S4).
        foreign_hits: list[str] = []

        class _ForeignServer(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                foreign_hits.append(self.path)
                body = b'feature\tposition\treference\tmutation\tantiviral\tphenotype\treference_identifier\n'
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                return

        srv_b = socketserver.TCPServer(('127.0.0.2', 0), _ForeignServer)
        port_b = srv_b.server_address[1]
        threading.Thread(target=srv_b.serve_forever, daemon=True).start()

        class _RedirectingServer(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path == '/manifest.json':
                    payload = json.dumps({
                        'databases': [
                            {
                                'source_name': 'redir_db',
                                'rules_path': 'db/rules.tsv',
                                'metadata_path': 'db/metadata.json',
                                'formula_rules_path': '',
                                'metadata': {'tsv_checksum': 'abc'},
                            }
                        ]
                    }).encode('utf-8')
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                elif self.path == '/db/metadata.json':
                    payload = b'{"tsv_checksum":"abc"}'
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                elif self.path == '/db/rules.tsv':
                    self.send_response(302)
                    self.send_header('Location', f'http://127.0.0.2:{port_b}/evil.tsv')
                    self.end_headers()
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, format: str, *args: Any) -> None:
                return

        srv_a = socketserver.TCPServer(('127.0.0.1', 0), _RedirectingServer)
        port_a = srv_a.server_address[1]
        threading.Thread(target=srv_a.serve_forever, daemon=True).start()
        try:
            monkeypatch.setattr(
                maintained_db,
                'CLI_CONFIG',
                replace(
                    maintained_db.CLI_CONFIG,
                    urls=replace(maintained_db.CLI_CONFIG.urls, github_respro_db_raw=f'http://127.0.0.1:{port_a}'),
                ),
            )
            with pytest.raises((RuntimeError, ValueError)):
                maintained_db.download_database_files('redir_db', tmp_path / 'dl')
            assert foreign_hits == [], f'Maintained DB followed cross-origin 302 redirect (S4): {foreign_hits}'
        finally:
            srv_a.shutdown()
            srv_b.shutdown()

    def test_wf6_maintained_bootstrap_fail_soft_when_local_db_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        data_dir = tmp_path / 'data'
        pd_dir = data_dir / 'project_databases'
        pd_dir.mkdir(parents=True, exist_ok=True)
        _build_e2e_project_db(pd_dir / 'existing.db')

        monkeypatch.setenv('RESPRO_WEB_DATA_DIR', str(data_dir))
        monkeypatch.setenv('RESPRO_WEB_MAINTAINED_BOOTSTRAP', 'true')
        monkeypatch.setattr(
            'web.backend.startup_config.bootstrap_missing_maintained_databases',
            lambda _pd: (_ for _ in ()).throw(RuntimeError('GitHub offline')),
        )

        cfg = load_startup_config()
        assert len(cfg.project_db_uuid_index) == 1

    # ── WF7: Frontend Security, CSS/A11y, SPA & Disconnect Cleanup ───────────

    def test_wf7_spa_index_has_no_inline_script_and_report_iframe_no_allow_same_origin(self) -> None:
        index_html = (FRONTEND_DIR / 'index.html').read_text(encoding='utf-8')
        soup = BeautifulSoup(index_html, 'html.parser')
        inline = [s for s in soup.find_all('script') if not s.get('src')]
        assert inline == [], f'web/frontend/index.html still contains inline <script> (WA-047): {inline}'

        preview_jsx = (
            FRONTEND_DIR / 'src' / 'components' / 'pages' / 'analyze' / 'ReportPreviewSection.jsx'
        ).read_text(encoding='utf-8')
        assert 'allow-same-origin' not in preview_jsx, (
            'ReportPreviewSection.jsx iframe sandbox must NOT include allow-same-origin (WA-009)'
        )

    def test_wf7_report_preview_rejects_unsafe_postmessage_origins_srcs_and_heights(
        self, tmp_path: Path,
    ) -> None:
        preview_path = FRONTEND_DIR / 'src' / 'components' / 'pages' / 'analyze' / 'ReportPreviewSection.jsx'
        script = f"""
import fs from 'fs';
import path from 'path';
import {{ createRequire }} from 'module';
import {{ pathToFileURL }} from 'url';

const req = createRequire(import.meta.url);
const {{ JSDOM }} = req('./web/frontend/node_modules/jsdom');
const {{ transformSync }} = await import(pathToFileURL('./web/frontend/node_modules/rolldown/dist/experimental-index.mjs').href);

const workDir = {json.dumps(str(tmp_path))};
const reactUrl = pathToFileURL('./web/frontend/node_modules/react/index.js').href;
const jsxRuntimeUrl = pathToFileURL('./web/frontend/node_modules/react/jsx-runtime.js').href;
const reactDomUrl = pathToFileURL('./web/frontend/node_modules/react-dom/client.js').href;
const reactMod = await import(reactUrl);
const React = reactMod.default || reactMod;
const {{ createRoot }} = await import(reactDomUrl);

const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {{
  url: 'https://respro.example.org/analysis',
}});
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.HTMLElement = dom.window.HTMLElement;
globalThis.MessageEvent = dom.window.MessageEvent;
globalThis.IS_REACT_ACT_ENVIRONMENT = false;

const jsxSrc = fs.readFileSync({json.dumps(str(preview_path))}, 'utf8');
const transformed = transformSync({json.dumps(str(preview_path))}, jsxSrc, {{
  lang: 'jsx',
  jsx: {{ runtime: 'automatic', importSource: 'react' }},
}});
const rewritten = transformed.code
  .replace(/['"]react\\/jsx-runtime['"]/g, JSON.stringify(jsxRuntimeUrl))
  .replace(/['"]react['"]/g, JSON.stringify(reactUrl))
  .replace(/['"]\\.\\.\\/\\.\\.\\/\\.\\.\\/hooks\\/useTheme['"]/g, JSON.stringify('./useTheme.mjs'));

fs.writeFileSync(
  path.join(workDir, 'useTheme.mjs'),
  `import React from ${{JSON.stringify(reactUrl)}}; export const ThemeContext = React.createContext('light');`,
  'utf8',
);
const modPath = path.join(workDir, 'ReportPreviewSection.mjs');
fs.writeFileSync(modPath, rewritten, 'utf8');
const {{ isAllowedReportAssetSrc, ReportPreviewSection }} = await import(pathToFileURL(modPath).href);

// 1. Direct unit checks on isAllowedReportAssetSrc
const badPlots = [
  'javascript:alert(1)',
  'data:text/html,<script>alert(1)</script>',
  '//evil.example/api/artifact?id=1',
  'https://evil.example/api/artifact?id=1',
  'blob:https://evil.example/abc',
  'https://pubchem.ncbi.nlm.nih.gov/image/imgsrv.fcgi?cid=1',
];
for (const bad of badPlots) {{
  if (isAllowedReportAssetSrc(bad, {{ allowPubchem: false, reportOrigin: 'https://respro.example.org' }})) {{
    throw new Error('Accepted unsafe plot src: ' + bad);
  }}
}}
const goodPlots = [
  'data:image/png;base64,AAAA',
  'blob:null/1234-5678',
  '/api/artifact?id=abc',
];
for (const good of goodPlots) {{
  if (!isAllowedReportAssetSrc(good, {{ allowPubchem: false, reportOrigin: 'https://respro.example.org' }})) {{
    throw new Error('Rejected safe plot src: ' + good);
  }}
}}
if (!isAllowedReportAssetSrc('https://pubchem.ncbi.nlm.nih.gov/image/imgsrv.fcgi?cid=1', {{ allowPubchem: true }})) {{
  throw new Error('Rejected PubChem structure src when allowPubchem=true');
}}

// 2. Live React MessageEvent checks on <ReportPreviewSection>
const container = dom.window.document.getElementById('root');
const root = createRoot(container);
root.render(React.createElement(ReportPreviewSection, {{
  showFrame: true,
  inlineReportPath: '/api/report?id=1',
  buildReportUrl: (p) => p,
}}));
await new Promise((r) => setTimeout(r, 40));

const iframe = container.querySelector('iframe.workspace-frame');
if (!iframe) throw new Error('Expected workspace iframe');
const fakeFrameWin = {{ postMessage() {{}} }};
Object.defineProperty(iframe, 'contentWindow', {{ value: fakeFrameWin, configurable: true }});

const dispatch = (data, origin = 'null', source = fakeFrameWin) => {{
  dom.window.dispatchEvent(new dom.window.MessageEvent('message', {{ data, origin, source }}));
}};

// Unsafe / non-finite / wrong-origin / wrong-source heights must be ignored
dispatch({{ type: 'respro:report-height', height: Infinity }});
dispatch({{ type: 'respro:report-height', height: NaN }});
dispatch({{ type: 'respro:report-height', height: -5 }});
dispatch({{ type: 'respro:report-height', height: '400' }});
dispatch({{ type: 'respro:report-height', height: 400 }}, 'https://evil.example', fakeFrameWin);
dispatch({{ type: 'respro:report-height', height: 400 }}, 'null', dom.window);
await new Promise((r) => setTimeout(r, 20));
if (iframe.style.height !== '') throw new Error('Accepted invalid height: ' + iframe.style.height);

// Valid null-origin height must apply (+2px border padding)
dispatch({{ type: 'respro:report-height', height: 400 }}, 'null', fakeFrameWin);
await new Promise((r) => setTimeout(r, 20));
if (iframe.style.height !== '402px') throw new Error('Expected height 402px, got: ' + iframe.style.height);

// Unsafe open-plot src must be rejected
dispatch({{ type: 'respro:open-plot', src: 'javascript:alert(1)' }});
dispatch({{ type: 'respro:open-plot', src: 'https://pubchem.ncbi.nlm.nih.gov/image/imgsrv.fcgi?cid=1' }});
await new Promise((r) => setTimeout(r, 20));
if (container.querySelector('.report-preview-plot-modal')) throw new Error('Opened modal for unsafe plot src');

// Valid data:image plot src must open modal
dispatch({{ type: 'respro:open-plot', src: 'data:image/png;base64,AAAA' }});
await new Promise((r) => setTimeout(r, 20));
if (!container.querySelector('.report-preview-plot-modal')) throw new Error('Did not open modal for valid data:image plot');

// Valid PubChem open-structure must open structure panel
dispatch({{ type: 'respro:open-structure', src: 'https://pubchem.ncbi.nlm.nih.gov/image/imgsrv.fcgi?cid=1', title: 'Drug' }});
await new Promise((r) => setTimeout(r, 20));
if (!container.querySelector('.report-preview-structure-panel')) throw new Error('Did not open structure panel for PubChem');

root.unmount();
console.log('OK');
"""
        res = _run_node(script)
        assert res.returncode == 0, f'{res.stdout}\n{res.stderr}'
        assert res.stdout.strip().splitlines()[-1] == 'OK'

    def test_wf7_css_tokens_and_a11y_landmarks_and_svg_dark_mode(self) -> None:
        global_css = (FRONTEND_DIR / 'src' / 'styles' / 'global.css').read_text(encoding='utf-8')
        light_block = re.search(r'(?m)^:root\s*\{([^}]*)\}', global_css)
        dark_block = re.search(r":root\[data-theme=['\"]dark['\"]\]\s*\{([^}]*)\}", global_css)
        assert light_block and dark_block, 'global.css must define :root and :root[data-theme="dark"] blocks'
        light_err = re.search(r'--color-error\s*:\s*([^;]+);', light_block.group(1))
        dark_err = re.search(r'--color-error\s*:\s*([^;]+);', dark_block.group(1))
        assert light_err and dark_err, 'global.css must define --color-error in both :root and :root[data-theme="dark"] (WA-027)'
        assert light_err.group(1).strip() != dark_err.group(1).strip(), (
            'Light and dark --color-error tokens should be distinct for contrast (WA-027)'
        )
        assert 'forced-colors' in global_css, 'global.css must include forced-colors focus styling (WA-052)'

        components_css = (FRONTEND_DIR / 'src' / 'styles' / 'components.css').read_text(encoding='utf-8')
        assert 'align-items: left' not in components_css, 'Invalid CSS align-items: left must be fixed (WA-052)'

        layout_css = (FRONTEND_DIR / 'src' / 'styles' / 'layout.css').read_text(encoding='utf-8')
        assert '--accent-contrast' not in layout_css

        all_css = '\n'.join(p.read_text(encoding='utf-8') for p in (FRONTEND_DIR / 'src' / 'styles').rglob('*.css'))
        rm_bodies: list[str] = []
        for match in re.finditer(r'@media\s*\(\s*prefers-reduced-motion\s*:\s*reduce\s*\)\s*\{', all_css):
            pos = match.end()
            depth = 1
            while pos < len(all_css) and depth > 0:
                ch = all_css[pos]
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                pos += 1
            rm_bodies.append(all_css[match.end():pos - 1])
        combined_rm = '\n'.join(rm_bodies)
        assert rm_bodies, 'CSS must define @media (prefers-reduced-motion: reduce) rules (WA-052)'
        assert re.search(r'\banimation(?:-duration|-name)?\s*:', combined_rm), (
            'prefers-reduced-motion must disable CSS animations (WA-052)'
        )
        assert re.search(r'\btransition(?:-duration)?\s*:', combined_rm), (
            'prefers-reduced-motion must disable CSS transitions (WA-052)'
        )

        app_shell = (FRONTEND_DIR / 'src' / 'components' / 'AppShell.jsx').read_text(encoding='utf-8')
        assert '<main' in app_shell and 'id="main-content"' in app_shell
        # Main landmark must wrap only main content, not header/nav/footer.
        main_pos = app_shell.index('<main')
        header_pos = app_shell.index('<header')
        assert main_pos > header_pos, '<main> must sit inside .dashboard-shell after <header> (WA-053)'

        mutations_jsx = (FRONTEND_DIR / 'src' / 'components' / 'pages' / 'MutationsPage.jsx').read_text(
            encoding='utf-8'
        )
        assert 'setMutationFilterColumn' not in mutations_jsx and 'setFormulaFilterColumn' not in mutations_jsx
        raw_search_img = bool(re.search(r'<img\b[^>]*\bsrc=\{mutationsIconSrc\}', mutations_jsx))
        raw_reset_img = bool(re.search(r'<img\b[^>]*\bsrc=\{resetFilterIconSrc\}', mutations_jsx))
        if raw_search_img:
            has_dark_search = bool(re.search(
                r"\[data-theme=['\"]dark['\"]\][^{]*\.mutation-search[^{]*\{[^}]*\bfilter\s*:",
                all_css,
            ))
            assert has_dark_search, (
                'MutationsPage.jsx search SVG (#000000 stroke) must support dark-mode contrast '
                'via CSS mask/currentColor or [data-theme="dark"] filter (WA-027/S5-SVG)'
            )
        if raw_reset_img:
            has_dark_reset = bool(re.search(
                r"\[data-theme=['\"]dark['\"]\][^{]*\.mutation-reset-button[^{]*\{[^}]*\bfilter\s*:",
                all_css,
            ))
            assert has_dark_reset, (
                'MutationsPage.jsx reset_filter SVG (#000000 stroke) must support dark-mode contrast '
                'via CSS mask/currentColor or [data-theme="dark"] filter (WA-027/S5-SVG)'
            )

    def test_wf7_frontend_url_sanitization_rejects_javascript_schemes(self) -> None:
        node_res = _run_node(
            "const u = await import('./web/frontend/src/utils.js');\n"
            "const badUrls = ['javascript:alert(1)', 'data:text/html,<script>', '//evil.example', 'ftp://files.example', 'https://a.example/x y'];\n"
            "for (const bad of badUrls) {\n"
            "  if (u.sanitizeHttpUrl(bad) !== null) throw new Error('Accepted unsafe URL: ' + bad);\n"
            "}\n"
            "if (u.sanitizeHttpUrl('https://respro.example.org/db') !== 'https://respro.example.org/db') throw new Error('Rejected valid https');\n"
            "if (u.sanitizeEmailAddress('evil@a.com?subject=x') !== null) throw new Error('Accepted unsafe email');\n"
            "if (u.sanitizeEmailAddress('curator@example.org') !== 'curator@example.org') throw new Error('Rejected valid email');\n"
        )
        assert node_res.returncode == 0, f'{node_res.stdout}\n{node_res.stderr}'

        meta_panel = (
            FRONTEND_DIR / 'src' / 'components' / 'pages' / 'database' / 'DatabaseMetaPanel.jsx'
        ).read_text(encoding='utf-8')
        assert 'sanitizeHttpUrl' in meta_panel and 'sanitizeEmailAddress' in meta_panel
        dash_logic = (FRONTEND_DIR / 'src' / 'useDashboardLogic.js').read_text(encoding='utf-8')
        assert 'sanitizeHttpUrl' in dash_logic or ('http:' in dash_logic and 'https:' in dash_logic)
        about_jsx = (FRONTEND_DIR / 'src' / 'components' / 'pages' / 'AboutPage.jsx').read_text(
            encoding='utf-8'
        )
        assert re.search(r'referrerPolicy\s*=\s*["\']no-referrer["\']', about_jsx), (
            'AboutPage.jsx external sponsor logos must set referrerPolicy="no-referrer" (WA-047)'
        )

    def test_wf7_spa_fallback_never_serves_html_on_reserved_prefixes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        monkeypatch.setenv('RESPRO_WEB_TRUSTED_PROXIES', '127.0.0.1')
        monkeypatch.setenv('RESPRO_WEB_CORS_ORIGINS', 'https://respro.example.org')
        dist_dir = tmp_path / 'dist'
        assets_dir = dist_dir / 'assets'
        assets_dir.mkdir(parents=True)
        (dist_dir / 'index.html').write_text('<!doctype html><html><body>SPA</body></html>', encoding='utf-8')
        monkeypatch.setattr(main_module, '_frontend_dist_dir', lambda: dist_dir)

        config = _make_startup_config(tmp_path, deployment_mode='online')
        client = TestClient(_make_app_and_queue(config)[0])

        for reserved in ('/api/nope', '/docs', '/redoc', '/openapi.json'):
            r = client.get(reserved)
            assert r.status_code == 404
            assert 'SPA' not in r.text

    def test_wf7_disconnect_partial_upload_cleanup_on_cancelled_error(self, tmp_path: Path) -> None:
        from web.backend.services.upload import save_upload_stream

        class _AbortStream:
            async def read(self, size: int = -1) -> bytes:
                del size
                raise asyncio.CancelledError

        upload_dir = tmp_path / 'uploads'
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(save_upload_stream(_AbortStream(), 'fasta', upload_dir))

        assert list(upload_dir.iterdir()) == [], 'CancelledError must unlink partial upload temp file (WA-037)'


# ═══════════════════════════════════════════════════════════════════════════════
# TIER 3 — CROSS-FEATURE COMBINATIONS (>= 10 pairwise tests)
# ═══════════════════════════════════════════════════════════════════════════════


class TestTier3CrossFeature:
    """Pairwise cross-feature interaction tests spanning WF1–WF7."""

    def test_t3_vcf_gz_plus_pysam_bam_plus_compare_coverage_gap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        client = TestClient(_make_app_and_queue(config)[0])

        bam_path = _build_pysam_bam(tmp_path / 'partial.bam', cover_end=30)  # codons 0..9 covered; 10..28 gap
        vcf_gz = gzip.compress(VALID_TINY_VCF.encode('utf-8'))
        vcf_id = _upload_file(client, 'vcf', 's.vcf.gz', vcf_gz)['upload_id']
        ref_id = _upload_file(client, 'fasta', 'ref.fa', VALID_REF_FASTA.encode('utf-8'))['upload_id']
        bam_id = _upload_file(client, 'bam', 'partial.bam', bam_path.read_bytes())['upload_id']

        j_vcf = client.post(
            '/api/profile/vcf',
            json={'vcf_id': vcf_id, 'reference_id': ref_id, 'bam_id': bam_id, 'database_id': 'test_project.db', 'sample': 'VcfCov'},
        ).json()['job_id']
        # Second sample covers 1-based codon 21 (0-based codon 20, nt 61 G->A, GCT->ACT / A21T) in FASTA, uncovered in j_vcf.
        seq_pos20 = TINY_REF_SEQ[:60] + 'A' + TINY_REF_SEQ[61:]
        fa2_id = _upload_file(client, 'fasta', 's2.fa', f'>s2\n{seq_pos20}\n'.encode())['upload_id']
        j_fa = client.post(
            '/api/profile/fasta',
            json={'fasta_id': fa2_id, 'database_id': 'test_project.db', 'sample': 'FaFull'},
        ).json()['job_id']

        art_vcf = client.get(f'/api/jobs/{j_vcf}').json()['result']['report_json_path']
        art_fa = client.get(f'/api/jobs/{j_fa}').json()['result']['report_json_path']

        comp = client.post('/api/compare', json={'artifact_ids': [art_vcf, art_fa]})
        assert comp.status_code == 200, comp.text
        data = comp.json()
        # Locate codon 21 (1-based) mutation column where VcfCov has coverage_gap=True.
        col_idx = next(i for i, m in enumerate(data['mutations']) if m['position'] == 21)
        assert data['matrix'][0][col_idx]['coverage_gap'] is True
        assert data['matrix'][1][col_idx]['coverage_gap'] is False

    def test_t3_batch_vcf_plus_worker_plus_artifact_bundle_plus_cleanup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_redis, fake_server = _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        app, _, batch_q = _make_app_and_queue(config, is_async=True, redis_server=fake_server)
        client = TestClient(app)

        v1 = _upload_file(client, 'vcf', 's1.vcf', VALID_TINY_VCF.encode('utf-8'))['upload_id']
        v2 = _upload_file(client, 'vcf', 's2.vcf', VALID_TINY_VCF.encode('utf-8'))['upload_id']
        ref = _upload_file(client, 'fasta', 'r.fa', VALID_REF_FASTA.encode('utf-8'))['upload_id']
        bam_file = _build_pysam_bam(tmp_path / 'b.bam', cover_end=45)
        bam = _upload_file(client, 'bam', 'b.bam', bam_file.read_bytes())['upload_id']

        sub = client.post(
            '/api/profile/batch/vcf',
            json={
                'vcf_ids': [v1, v2],
                'reference_id': ref,
                'db_path': 'test_project.db',
                'sample_names': ['Cohort1', 'Cohort2'],
                'bam_ids': [bam, None],
            },
        )
        assert sub.status_code == 200
        job_ids = [s['job_id'] for s in sub.json()['samples']]
        worker_module.run_worker(queue_names=(batch_q.name,), connection=fake_redis, burst=True, worker_class=SimpleWorker)

        all_arts: list[str] = []
        for jid in job_ids:
            all_arts.extend(client.get(f'/api/jobs/{jid}').json()['result']['artifact_ids'])

        bundle = client.post('/api/artifact-bundle', json={'artifact_ids': all_arts})
        assert bundle.status_code == 200
        with zipfile.ZipFile(io.BytesIO(bundle.content)) as zf:
            assert len(zf.namelist()) == len(all_arts)

        # Create .bam.bai sidecar as CLI coverage step would, then clean everything up.
        bam_rec = session_service.fetch_owned_record('upload', bam)
        assert bam_rec and bam_rec.canonical_path
        bai = Path(f'{bam_rec.canonical_path}.bai')
        if not bai.exists():
            bai.write_bytes(b'BAI')

        clean = client.post('/api/session/cleanup', json={'upload_ids': [v1, v2, ref, bam], 'artifact_ids': all_arts})
        assert clean.status_code == 200
        assert clean.json()['deleted_count'] == 4 + len(all_arts)
        assert not bai.exists()
        assert client.get('/api/artifact', params={'artifact_id': all_arts[0]}).status_code == 404

    def test_t3_regenerate_plus_quota_plus_validation_ordering(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        monkeypatch.setattr(rate_limit_module, '_current_window_minute', lambda: 1_000_000)
        monkeypatch.setenv('RESPRO_WEB_MAX_BATCH_SIZE', '1')
        db_uuid = str(uuid.uuid4())
        config = _make_startup_config(tmp_path, db_builders=[('test.db', {'db_uuid': db_uuid})])
        client = TestClient(_make_app_and_queue(config, is_async=True)[0])

        bad_up = _upload_file(client, 'fasta', 'not_json.fa', MUTATED_TINY_FASTA.encode('utf-8'))['upload_id']
        assert client.post('/api/regenerate/json', json={'json_id': bad_up}).status_code == 400

        j_ok = _write_synthetic_results_json(tmp_path / 'ok.results.json', project_uuid=db_uuid)
        good_up = _upload_file(client, 'json', 'ok.results.json', j_ok.read_bytes())['upload_id']
        assert client.post('/api/regenerate/json', json={'json_id': good_up}).status_code == 200
        over = client.post('/api/regenerate/json', json={'json_id': good_up})
        assert over.status_code == 429
        assert int(over.headers['retry-after']) >= 1

    def test_t3_online_mode_csrf_plus_session_sliding_ttl_plus_redis_outage(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _fake_redis, fake_server = _patch_fakeredis(monkeypatch)
        monkeypatch.setenv('RESPRO_WEB_TRUSTED_PROXIES', '127.0.0.1')
        monkeypatch.setenv('RESPRO_WEB_CORS_ORIGINS', 'https://respro.example.org')
        monkeypatch.setenv('RESPRO_WEB_SESSION_TTL', '120')
        config = _make_startup_config(tmp_path, deployment_mode='online')
        app, _, _ = _make_app_and_queue(config)
        client = TestClient(app, base_url='https://respro.example.org', raise_server_exceptions=False)

        assert client.get('/api/databases').status_code == 200
        assert 'Secure' in client.get('/api/databases').headers.get('set-cookie', '')

        # Foreign origin mutation blocked 403.
        assert client.post(
            '/api/session/cleanup',
            json={'upload_ids': [], 'artifact_ids': []},
            headers={'Origin': 'https://attacker.example'},
        ).status_code == 403

        # Simulate Redis drop post-startup across all FakeServer clients/pools.
        fake_server.connected = False
        assert client.get('/api/health').status_code == 200
        assert client.get('/api/databases').status_code == 503

    def test_t3_upload_cleanup_on_redis_error_plus_sanitized_response(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        monkeypatch.setenv('RESPRO_WEB_TRUSTED_PROXIES', '127.0.0.1')
        monkeypatch.setenv('RESPRO_WEB_CORS_ORIGINS', 'https://respro.example.org')
        config = _make_startup_config(tmp_path, deployment_mode='online')
        client = TestClient(
            _make_app_and_queue(config)[0],
            base_url='https://respro.example.org',
            raise_server_exceptions=False,
        )
        assert client.get('/api/databases').status_code == 200

        orig_hset = redis.Redis.hset
        orig_pipe_exec = redis.client.Pipeline.execute

        def _is_upload_key(key: Any) -> bool:
            text = key.decode('utf-8', 'ignore') if isinstance(key, bytes) else str(key)
            return text.startswith('respro:upload:')

        def _fault_hset(self_r: redis.Redis, name: Any, *args: Any, **kwargs: Any) -> Any:
            if _is_upload_key(name):
                raise redis.ConnectionError(f'redis err at {tmp_path}')
            return orig_hset(self_r, name, *args, **kwargs)

        def _fault_pipe_exec(self_p: redis.client.Pipeline, raise_on_error: bool = True) -> Any:
            for cmd_args, _ in self_p.command_stack:
                if any(_is_upload_key(a) for a in cmd_args):
                    raise redis.ConnectionError(f'redis err at {tmp_path}')
            return orig_pipe_exec(self_p, raise_on_error=raise_on_error)

        monkeypatch.setattr(redis.Redis, 'hset', _fault_hset)
        monkeypatch.setattr(redis.client.Pipeline, 'execute', _fault_pipe_exec)
        resp = client.post(
            '/api/upload/fasta',
            files={'file': ('s.fa', MUTATED_TINY_FASTA.encode('utf-8'), 'text/plain')},
            headers={
                'Host': 'respro.example.org',
                'X-Forwarded-Proto': 'https',
                'Origin': 'https://respro.example.org',
                'Sec-Fetch-Site': 'same-origin',
            },
        )
        assert resp.status_code == 503
        assert str(tmp_path) not in resp.text
        assert list(config.uploads_dir.iterdir()) == [], 'Saved upload file must be unlinked when record_upload fails (WA-005)'

    def test_t3_corrupt_db_plus_browse_plus_db_compare_plus_tsv_neutralization(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(
            tmp_path,
            db_builders=[
                (
                    'db1.db',
                    {
                        'references': [
                            {'name': 'REF1', 'accession': 'ACC_S', 'organism': 'VirusX'},
                            {'name': 'REF10', 'accession': 'ACC_10', 'organism': 'VirusX'},
                        ],
                        'rules': [
                            {
                                'ref_name': 'REF1',
                                'feature': 'gag',
                                'drug': 'DrugA',
                                'position': 1,
                                'reference': 'K',
                                'mutation': 'E',
                                'phenotype': '=CMD()',
                            }
                        ],
                        'include_formula_component': True,
                    },
                ),
                (
                    'db2.db',
                    {
                        'references': [{'name': 'REF1', 'accession': 'ACC_S', 'organism': 'VirusX'}],
                        'rules': [
                            {'ref_name': 'REF1', 'feature': 'gag', 'drug': 'DrugA', 'position': 1, 'reference': 'K', 'mutation': 'E'}
                        ],
                    },
                ),
            ],
        )
        (config.project_databases_dir / 'broken.db').write_bytes(b'broken')
        client = TestClient(_make_app_and_queue(config)[0])

        dbs = client.get('/api/databases').json()['data']
        assert dbs['count'] == 2
        rules = client.get('/api/rules', params={'database_id': 'db1.db', 'reference': 'REF1'}).json()['data']
        assert rules['count'] == 1
        comp = client.post('/api/databases/compare', json={'database_ids': ['db1.db', 'db2.db'], 'accession': 'ACC_S'}).json()['data']
        pheno = comp['rows'][0]['per_db']['db1.db']['phenotype']
        assert pheno == '=CMD()'

        node_res = _run_node(
            "import { createRequire } from 'module';\n"
            "const req = createRequire(import.meta.url);\n"
            "const { JSDOM } = req('./web/frontend/node_modules/jsdom');\n"
            "const dom = new JSDOM('<!doctype html><html><body></body></html>');\n"
            "globalThis.window = dom.window; globalThis.document = dom.window.document;\n"
            "let out = ''; globalThis.Blob = class { constructor(p){ out = p.join(''); } };\n"
            "globalThis.URL = { createObjectURL: () => 'blob:x', revokeObjectURL: () => {} };\n"
            "dom.window.HTMLAnchorElement.prototype.click = () => {};\n"
            "const u = await import('./web/frontend/src/utils.js');\n"
            f"u.downloadTsv(['Phenotype'], [[{json.dumps(pheno)}]], 'db.tsv');\n"
            "console.log(out.split(/\\r?\\n/)[1]);\n"
        )
        assert node_res.returncode == 0
        assert node_res.stdout.strip() == "'=CMD()"

    def test_t3_compare_multi_ref_plus_null_af_plus_duplicate_aa_plus_db_display_names(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        db_file = tmp_path / 'data' / 'project_databases' / 'multi.db'
        hash_db_file = tmp_path / 'data' / 'project_databases' / 'db#1?.db'
        config = _make_startup_config(
            tmp_path,
            db_builders=[
                (
                    'multi.db',
                    {
                        'references': [
                            {'name': 'Ref1', 'accession': 'A1', 'features': [{'name': 'f1', 'protein': 'Prot 1', 'feature_type': 'mat_peptide'}]},
                            {'name': 'Ref2', 'accession': 'A2', 'features': [{'name': 'f2', 'protein': 'Prot 2', 'feature_type': 'mat_peptide'}]},
                        ],
                        'rules': [
                            {'ref_name': 'Ref1', 'feature': 'f1', 'drug': 'D1', 'position': 1, 'reference': 'K', 'mutation': 'E'},
                            {'ref_name': 'Ref2', 'feature': 'f2', 'drug': 'D1', 'position': 2, 'reference': 'A', 'mutation': 'V'},
                        ],
                    },
                ),
                (
                    'db#1?.db',
                    {
                        'references': [
                            {'name': 'Ref1', 'accession': 'A1', 'features': [{'name': 'f1', 'protein': 'ProtHashQ', 'feature_type': 'mat_peptide'}]},
                        ],
                        'rules': [
                            {'ref_name': 'Ref1', 'feature': 'f1', 'drug': 'D1', 'position': 1, 'reference': 'K', 'mutation': 'E'},
                        ],
                    },
                ),
            ],
        )
        client = TestClient(_make_app_and_queue(config)[0])
        client.get('/api/databases')
        sh = hash_session_token(client.cookies.get(SESSION_COOKIE_NAME) or '')

        # Duplicate AA mapping (0.4 first, 0.3 second) + unknown AF + multi-reference display names.
        a1 = _record_owned_result_json(
            config,
            sh,
            'mr1.20261124000001000000.results.json',
            sample_name='MR1',
            project_db_path=str(db_file),
            reference_name='Ref1',
            references=['Ref1', 'Ref2'],
            variants=[
                {'feature_name': 'f1', 'codon_pos': 0, 'ref_aa': 'K', 'alt_aa': 'E', 'allele_freq': 0.4, 'rule_match': 1, 'consequence': 'missense'},
                {'feature_name': 'f1', 'codon_pos': 0, 'ref_aa': 'K', 'alt_aa': 'E', 'allele_freq': 0.3, 'rule_match': 1, 'consequence': 'missense'},
                {'feature_name': 'f2', 'codon_pos': 1, 'ref_aa': 'A', 'alt_aa': 'V', 'allele_freq': None, 'rule_match': 0, 'consequence': 'missense'},
            ],
        )
        a2 = _record_owned_result_json(
            config,
            sh,
            'mr2.20261124000002000000.results.json',
            sample_name='MR2',
            project_db_path=str(db_file),
            reference_name='Ref1',
            references=['Ref1', 'Ref2'],
            variants=[],
        )
        resp = client.post('/api/compare', json={'artifact_ids': [a1, a2]})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data['feature_display_names'].get('f2') == 'Prot 2', (
            f'feature_display_names must include Ref2 features (WA-016): {data["feature_display_names"]}'
        )
        # Duplicate AA must aggregate deterministically (max=0.4 or capped sum <= 1.0, never overwritten to 0.3).
        af = data['matrix'][0][0]['allele_freq']
        assert af is not None and 0.4 <= af <= 1.0, f'Duplicate AA aggregation regressed (WA-016): {af!r}'
        assert data['matrix'][0][1]['allele_freq'] is None and data['matrix'][0][1]['coverage_gap'] is False

        # Secondary reference mismatch must return 400 (WA-016).
        a3 = _record_owned_result_json(
            config,
            sh,
            'mr3.20261124000003000000.results.json',
            sample_name='MR3',
            project_db_path=str(db_file),
            reference_name='Ref1',
            references=['Ref1', 'Ref3'],
            variants=[],
        )
        assert client.post('/api/compare', json={'artifact_ids': [a1, a3]}).status_code == 400

        # WA-033: null project_db_path must not raise 500 TypeError.
        null_db_p = config.results_dir / 'nulldb.20261124000004000000.results.json'
        _write_synthetic_results_json(null_db_p, sample_name='NullDB', project_db_path='')
        null_payload = json.loads(null_db_p.read_text(encoding='utf-8'))
        null_payload['run']['project_db_path'] = None
        null_db_p.write_text(json.dumps(null_payload), encoding='utf-8')
        a_nulldb = session_service.record_artifact(session_hash=sh, canonical_path=null_db_p, media_type='application/json')
        r_null = client.post('/api/compare', json={'artifact_ids': [a_nulldb]})
        assert r_null.status_code == 200
        assert r_null.json()['feature_display_names'] == {}

        # WA-033: outside project_db_path must not be opened.
        outside_db = _build_e2e_project_db(
            tmp_path / 'outside.db',
            references=[{'name': 'Ref1', 'accession': 'A1', 'features': [{'name': 'f1', 'protein': 'LEAKED_OUTSIDE', 'feature_type': 'mat_peptide'}]}],
            rules=[{'ref_name': 'Ref1', 'feature': 'f1', 'drug': 'D1', 'position': 1, 'reference': 'K', 'mutation': 'E'}],
        )
        a_out = _record_owned_result_json(
            config,
            sh,
            'outdb.20261124000005000000.results.json',
            sample_name='OutDB',
            project_db_path=str(outside_db),
            reference_name='Ref1',
            variants=[{'feature_name': 'f1', 'codon_pos': 0, 'ref_aa': 'K', 'alt_aa': 'E', 'allele_freq': 0.5, 'rule_match': 1, 'consequence': 'missense'}],
        )
        r_out = client.post('/api/compare', json={'artifact_ids': [a_out]})
        assert r_out.status_code in (200, 400)
        assert 'LEAKED_OUTSIDE' not in r_out.text

        # WA-033: confined DB containing '#' and '?' must open via as_uri() without creating junk 'db' file.
        a_hash = _record_owned_result_json(
            config,
            sh,
            'hashdb.20261124000006000000.results.json',
            sample_name='HashDB',
            project_db_path=str(hash_db_file),
            reference_name='Ref1',
            variants=[{'feature_name': 'f1', 'codon_pos': 0, 'ref_aa': 'K', 'alt_aa': 'E', 'allele_freq': 0.5, 'rule_match': 1, 'consequence': 'missense'}],
        )
        r_hash = client.post('/api/compare', json={'artifact_ids': [a_hash]})
        assert r_hash.status_code == 200
        assert r_hash.json()['feature_display_names'].get('f1') == 'ProtHashQ'
        assert not (config.project_databases_dir / 'db').exists()

    def test_t3_maintained_db_bootstrap_plus_uuid_index_plus_regenerate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from respro.io import maintained_db
        from web.backend.services import maintained_bootstrap

        _patch_fakeredis(monkeypatch)
        srv_dir = tmp_path / 'remote_repo'
        (srv_dir / 'db1').mkdir(parents=True)
        gb_path = srv_dir / 'ACC_BOOT.gb'
        from tests.conftest import write_genbank

        write_genbank(
            gb_path,
            [
                {
                    'id': 'ACC_BOOT',
                    'accession': 'ACC_BOOT',
                    'organism': 'BootVirus',
                    'sequence': TINY_REF_SEQ,
                    'features': [{'feature': 'gag', 'start': 1, 'end': len(TINY_REF_SEQ), 'strand': '+'}],
                }
            ],
        )
        (srv_dir / 'db1' / 'rules.tsv').write_text(
            'feature\tposition\treference\tmutation\tantiviral\tphenotype\treference_identifier\n'
            'gag\t2\tK\tE\tTestDrug\tresistant\tACC_BOOT\n',
            encoding='utf-8',
        )
        (srv_dir / 'db1' / 'metadata.json').write_text(json.dumps({'tsv_checksum': 'chk1'}), encoding='utf-8')
        manifest = {
            'databases': [
                {
                    'source_name': 'boot_db',
                    'rules_path': 'db1/rules.tsv',
                    'metadata_path': 'db1/metadata.json',
                    'formula_rules_path': '',
                    'metadata': {'tsv_checksum': 'chk1'},
                }
            ]
        }
        (srv_dir / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')

        httpd, port = _start_local_http_server(srv_dir)
        try:
            base_url = f'http://127.0.0.1:{port}'
            monkeypatch.setattr(
                maintained_db,
                'CLI_CONFIG',
                replace(
                    maintained_db.CLI_CONFIG,
                    urls=replace(
                        maintained_db.CLI_CONFIG.urls,
                        github_respro_db_raw=base_url,
                        ncbi_nuccore_efetch=f'{base_url}/{{accession}}.gb',
                    ),
                ),
            )

            data_dir = tmp_path / 'data'
            pd_dir = data_dir / 'project_databases'
            pd_dir.mkdir(parents=True)
            maintained_bootstrap.bootstrap_missing_maintained_databases(pd_dir)
            built = pd_dir / 'boot_db.db'
            assert built.is_file()
            assert not Path(f'{built}-wal').exists(), 'Bootstrapped DB must use journal_mode=DELETE / no leftover WAL (WA-018)'

            monkeypatch.setenv('RESPRO_WEB_DATA_DIR', str(data_dir))
            cfg = load_startup_config()
            db_uuid = next(iter(cfg.project_db_uuid_index.keys()))
            client = TestClient(_make_app_and_queue(cfg)[0])

            rjson = _write_synthetic_results_json(tmp_path / 'boot.results.json', project_uuid=db_uuid)
            up_id = _upload_file(client, 'json', 'boot.results.json', rjson.read_bytes())['upload_id']
            regen = client.post('/api/regenerate/json', json={'json_id': up_id})
            assert regen.status_code == 200
            assert client.get(f"/api/jobs/{regen.json()['job_id']}").json()['status'] == 'succeeded'
        finally:
            httpd.shutdown()

    def test_t3_spa_dist_plus_report_csp_plus_legal_plus_branding_stateless(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_redis, _ = _patch_fakeredis(monkeypatch)
        dist_dir = tmp_path / 'dist'
        assets_dir = dist_dir / 'assets'
        assets_dir.mkdir(parents=True)
        (dist_dir / 'index.html').write_text(
            '<!doctype html><html><head>'
            '<script src="/theme-init.js"></script>'
            '<script type="module" src="/assets/app.js"></script>'
            '</head><body></body></html>',
            encoding='utf-8',
        )
        (dist_dir / 'theme-init.js').write_text('document.documentElement.dataset.theme="light";', encoding='utf-8')
        (assets_dir / 'app.js').write_text('console.log("ok");', encoding='utf-8')
        monkeypatch.setattr(main_module, '_frontend_dist_dir', lambda: dist_dir)

        config = _make_startup_config(
            tmp_path,
            imprint=ImprintConfig(kind='path', html='<html><body>Imprint</body></html>'),
        )
        client = TestClient(_make_app_and_queue(config)[0])

        for p in (
            '/',
            '/theme-init.js',
            '/assets/app.js',
            '/legal',
            '/api/branding/logo.svg',
            '/api/branding/favicon.svg',
        ):
            r = client.get(p)
            if p.startswith('/api/branding/'):
                asset_name = p.removeprefix('/api/branding/')
                expected_status = 200 if (REPO_ROOT / 'respro' / 'report' / 'static' / asset_name).is_file() else 404
                assert r.status_code == expected_status
            else:
                assert r.status_code == 200
            assert SESSION_COOKIE_NAME not in r.headers.get('set-cookie', '')

        theme_js = client.get('/theme-init.js')
        assert 'javascript' in theme_js.headers.get('content-type', '').lower()
        assert '<!doctype' not in theme_js.text.lower()

        spa_csp = client.get('/').headers.get('content-security-policy', '')
        directives = {
            parts[0].lower(): parts[1:]
            for clause in spa_csp.split(';')
            if (parts := clause.strip().split())
        }
        assert 'script-src' in directives
        assert "'self'" in directives['script-src']
        assert "'unsafe-inline'" not in directives['script-src']

        assert list(fake_redis.scan_iter('respro:session:*')) == []
        assert "script-src 'none'" in client.get('/legal').headers.get('content-security-policy', '')

    def test_t3_concurrent_job_poll_atomic_artifact_claim(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import web.backend.routes.jobs as jobs_route_module

        fake_redis, _ = _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        client = TestClient(_make_app_and_queue(config)[0])

        job_id = client.post(
            '/api/profile/fasta',
            json={'use_example': True, 'database_id': 'test_project.db', 'sample': 'Concurrent'},
        ).json()['job_id']

        barrier = threading.Barrier(6, timeout=0.5)
        seen_tls = threading.local()
        orig_record_artifact = jobs_route_module.record_artifact

        def _sync_record_artifact(**kwargs: Any) -> str:
            if not getattr(seen_tls, 'entered', False):
                seen_tls.entered = True
                with contextlib.suppress(threading.BrokenBarrierError):
                    barrier.wait()
            return orig_record_artifact(**kwargs)

        monkeypatch.setattr(jobs_route_module, 'record_artifact', _sync_record_artifact)

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            responses = list(pool.map(lambda _: client.get(f'/api/jobs/{job_id}').json(), range(6)))

        first_ids = responses[0]['result']['artifact_ids']
        assert all(r['result']['artifact_ids'] == first_ids for r in responses)
        art_keys = list(fake_redis.scan_iter('respro:artifact:*'))
        assert len(art_keys) == len(first_ids), (
            f'Concurrent job polls minted duplicate orphan artifacts (WA-019): {len(art_keys)} != {len(first_ids)}'
        )

    def test_t3_batch_compare_and_duplicate_bundle_protection(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_redis, fake_server = _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        app, _, batch_q = _make_app_and_queue(config, is_async=True, redis_server=fake_server)
        client = TestClient(app)

        f1 = _upload_file(client, 'fasta', 's1.v1.fasta', MUTATED_TINY_FASTA.encode('utf-8'))['upload_id']
        f2 = _upload_file(client, 'fasta', 's2.v1.fasta', MUTATED_TINY_FASTA.encode('utf-8'))['upload_id']
        sub = client.post(
            '/api/profile/batch/fasta',
            json={
                'fasta_ids': [f1, f2],
                'db_path': 'test_project.db',
                'sample_names': ['Batch1', 'Batch2'],
                'input_display_names': ['s1.v1.fasta', 's2.v1.fasta'],
            },
        )
        assert sub.status_code == 200
        worker_module.run_worker(queue_names=(batch_q.name,), connection=fake_redis, burst=True, worker_class=SimpleWorker)

        json_ids = [
            client.get(f"/api/jobs/{s['job_id']}").json()['result']['report_json_path']
            for s in sub.json()['samples']
        ]
        comp = client.post('/api/compare', json={'artifact_ids': json_ids})
        assert comp.status_code == 200
        assert comp.json()['samples'] == ['Batch1', 'Batch2']

        dup = client.post('/api/artifact-bundle', json={'artifact_ids': [json_ids[0], json_ids[0], json_ids[1]]})
        assert dup.status_code in (200, 400)
        if dup.status_code == 200:
            with zipfile.ZipFile(io.BytesIO(dup.content)) as zf:
                assert len(zf.namelist()) == 2

    def test_t3_lifespan_starts_and_joins_background_threads_cleanly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_fakeredis(monkeypatch)
        monkeypatch.setenv('RESPRO_WEB_MAINTAINED_BOOTSTRAP', 'true')
        monkeypatch.setenv('RESPRO_WEB_MAINTAINED_DB_UPDATE_INTERVAL_SECONDS', '3600')
        called = threading.Event()
        monkeypatch.setattr(
            'web.backend.background.check_and_update_maintained_databases',
            lambda _pd: called.set(),
        )
        config = _make_startup_config(tmp_path)
        app, _, _ = _make_app_and_queue(config)
        before = set(threading.enumerate())
        with TestClient(app) as client:
            assert client.get('/api/health').status_code == 200
            during_bg = [
                t
                for t in threading.enumerate()
                if t not in before
                and t.is_alive()
                and 'portal' not in t.name.lower()
                and 'anyio' not in t.name.lower()
            ]
            assert len(during_bg) >= 2, (
                f'Expected both TTL sweep and maintained-DB background threads running, got {[t.name for t in during_bg]}'
            )
        # Wait-before-first-tick (WA-018) means update check does NOT block startup immediately,
        # and graceful lifespan shutdown joins background threads cleanly.
        assert not called.is_set(), 'Maintained-DB loop must wait interval_seconds before first tick (WA-018)'
        after = [t for t in threading.enumerate() if t not in before and t.is_alive()]
        assert after == [], f'Lifespan leaked live background threads (WA-018/WA-031): {[t.name for t in after]}'


# ═══════════════════════════════════════════════════════════════════════════════
# TIER 4 — REAL-WORLD APPLICATION SCENARIOS (6 full multi-step scenarios)
# ═══════════════════════════════════════════════════════════════════════════════


class TestTier4RealWorldScenarios:
    """Full end-to-end user & operator scenarios exercising real subprocesses and boundaries."""

    def test_t4_scenario1_clinical_multisample_cohort_vcf_gz_bam_fasta_compare_bundle_cleanup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Scenario 1: .vcf.gz + real pysam BAM + FASTA -> reports + CSP -> /api/compare -> ZIP -> cleanup."""
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        app, _, _ = _make_app_and_queue(config)
        client_a = TestClient(app)
        client_b = TestClient(app)

        # Sample 1: gzipped VCF + partial-coverage BAM (codons 0..9 covered; 10..28 uncovered).
        bam_file = _build_pysam_bam(tmp_path / 'cohort1.bam', cover_end=30)
        vcf_id = _upload_file(client_a, 'vcf', 'cohort1.vcf.gz', gzip.compress(VALID_TINY_VCF.encode('utf-8')))['upload_id']
        ref_id = _upload_file(client_a, 'fasta', 'ref.fasta', VALID_REF_FASTA.encode('utf-8'))['upload_id']
        bam_id = _upload_file(client_a, 'bam', 'cohort1.bam', bam_file.read_bytes())['upload_id']

        j1 = client_a.post(
            '/api/profile/vcf',
            json={'vcf_id': vcf_id, 'reference_id': ref_id, 'bam_id': bam_id, 'database_id': 'test_project.db', 'sample': 'Clinical_Vcf'},
        ).json()['job_id']

        # Sample 2: FASTA with K2E + codon-21 mutation (GCT->ACT / A21T) so codon 21 contrasts coverage_gap vs detected variant.
        fa_seq = 'ATGGAAGCT' + TINY_REF_SEQ[9:60] + 'A' + TINY_REF_SEQ[61:]
        fa_id = _upload_file(client_a, 'fasta', 'cohort2.fasta', f'>Clinical_Fasta\n{fa_seq}\n'.encode())['upload_id']
        j2 = client_a.post(
            '/api/profile/fasta',
            json={'fasta_id': fa_id, 'database_id': 'test_project.db', 'sample': 'Clinical_Fasta'},
        ).json()['job_id']

        res1 = client_a.get(f'/api/jobs/{j1}').json()['result']
        res2 = client_a.get(f'/api/jobs/{j2}').json()['result']

        # Verify HTML report serves with strict CSP.
        rep1 = client_a.get('/api/report', params={'artifact_id': res1['report_html_path']})
        assert rep1.status_code == 200
        assert "connect-src 'none'" in rep1.headers.get('content-security-policy', '')

        # Compare both samples.
        comp = client_a.post('/api/compare', json={'artifact_ids': [res1['report_json_path'], res2['report_json_path']]})
        assert comp.status_code == 200
        comp_data = comp.json()
        assert comp_data['samples'] == ['Clinical_Vcf', 'Clinical_Fasta']
        gap_col = next(i for i, m in enumerate(comp_data['mutations']) if m['position'] == 21)
        assert comp_data['matrix'][0][gap_col]['coverage_gap'] is True
        assert comp_data['matrix'][1][gap_col]['coverage_gap'] is False and comp_data['matrix'][1][gap_col]['allele_freq'] == 1.0

        # Bundle all artifacts and verify session B isolation.
        all_arts = res1['artifact_ids'] + res2['artifact_ids']
        assert client_b.post('/api/artifact-bundle', json={'artifact_ids': all_arts}).status_code == 404
        zip_resp = client_a.post('/api/artifact-bundle', json={'artifact_ids': all_arts})
        assert zip_resp.status_code == 200
        with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as zf:
            assert len(zf.namelist()) == len(all_arts)

        # Cleanup uploads (including .bam.bai) + artifacts.
        clean = client_a.post('/api/session/cleanup', json={'upload_ids': [vcf_id, ref_id, bam_id, fa_id], 'artifact_ids': all_arts})
        assert clean.status_code == 200
        assert clean.json()['deleted_count'] == 4 + len(all_arts)
        assert list(config.uploads_dir.iterdir()) == []

    def test_t4_scenario2_batch_queue_worker_json_regeneration_roundtrip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Scenario 2: Batch FASTA + VCF/BAM -> worker drains queues -> JSON artifacts -> batch regenerate -> quota."""
        fake_redis, fake_server = _patch_fakeredis(monkeypatch)
        monkeypatch.setattr(rate_limit_module, '_current_window_minute', lambda: 1_000_000)
        monkeypatch.setenv('RESPRO_WEB_MAX_BATCH_SIZE', '5')
        db_uuid = str(uuid.uuid4())
        config = _make_startup_config(tmp_path, db_builders=[('cohort.db', {'db_uuid': db_uuid})])
        app, _, batch_q = _make_app_and_queue(config, is_async=True, redis_server=fake_server)
        client = TestClient(app)

        f1 = _upload_file(client, 'fasta', 'f1.fasta', MUTATED_TINY_FASTA.encode('utf-8'))['upload_id']
        v1 = _upload_file(client, 'vcf', 'v1.vcf', VALID_TINY_VCF.encode('utf-8'))['upload_id']
        ref = _upload_file(client, 'fasta', 'ref.fasta', VALID_REF_FASTA.encode('utf-8'))['upload_id']
        bam = _upload_file(client, 'bam', 'b1.bam', _build_pysam_bam(tmp_path / 'b1.bam').read_bytes())['upload_id']

        b_fa = client.post(
            '/api/profile/batch/fasta',
            json={'fasta_ids': [f1], 'db_path': 'cohort.db', 'sample_names': ['FaSample']},
        ).json()
        b_vcf = client.post(
            '/api/profile/batch/vcf',
            json={'vcf_ids': [v1], 'reference_id': ref, 'db_path': 'cohort.db', 'sample_names': ['VcfSample'], 'bam_ids': [bam]},
        ).json()

        worker_module.run_worker(queue_names=(batch_q.name,), connection=fake_redis, burst=True, worker_class=SimpleWorker)

        json_uploads: list[str] = []
        for entry in b_fa['samples'] + b_vcf['samples']:
            st = client.get(f"/api/jobs/{entry['job_id']}").json()
            assert st['status'] == 'succeeded'
            raw_json = client.get('/api/artifact', params={'artifact_id': st['result']['report_json_path']}).content
            json_uploads.append(_upload_file(client, 'json', f"{entry['sample_name']}.results.json", raw_json)['upload_id'])

        regen = client.post(
            '/api/regenerate/batch',
            json={'json_ids': json_uploads, 'sample_names': ['FaSample', 'VcfSample']},
        )
        assert regen.status_code == 200
        worker_module.run_worker(queue_names=(batch_q.name,), connection=fake_redis, burst=True, worker_class=SimpleWorker)

        for entry in regen.json()['samples']:
            st = client.get(f"/api/jobs/{entry['job_id']}").json()
            assert st['status'] == 'succeeded'
            assert st['result']['sample_name'] == entry['sample_name']

        # Quota consumed = 1 + 1 + 2 = 4 out of 5; requesting 2 more must return 429 + Retry-After.
        over = client.post(
            '/api/regenerate/batch',
            json={'json_ids': json_uploads, 'sample_names': ['FaSample', 'VcfSample']},
        )
        assert over.status_code == 429
        assert int(over.headers['retry-after']) >= 1

    def test_t4_scenario3_multidb_curation_corrupt_isolation_and_cross_ref_compare_tsv(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Scenario 3: 3 curated DBs + 1 corrupt .db -> isolation, exact ref, Venn compare, TSV neutralization."""
        _patch_fakeredis(monkeypatch)
        config = _make_startup_config(
            tmp_path,
            db_builders=[
                (
                    'db_alpha.db',
                    {
                        'references': [
                            {'name': 'REF1', 'accession': 'ACC_SHARED', 'organism': 'Herpes'},
                            {'name': 'REF10', 'accession': 'ACC_EXTRA', 'organism': 'Herpes'},
                        ],
                        'rules': [
                            {'ref_name': 'REF1', 'feature': 'gag', 'drug': 'ACV', 'position': 1, 'reference': 'K', 'mutation': 'E', 'phenotype': '=HYPERLINK("x")'},
                            {'ref_name': 'REF1', 'feature': 'gag', 'drug': 'FOS', 'position': 2, 'reference': 'A', 'mutation': 'T', 'phenotype': 'resistant'},
                        ],
                        'include_formula_component': True,
                    },
                ),
                (
                    'db_beta.db',
                    {
                        'references': [{'name': 'REF1', 'accession': 'ACC_SHARED', 'organism': 'Herpes'}],
                        'rules': [
                            {'ref_name': 'REF1', 'feature': 'gag', 'drug': 'ACV', 'position': 1, 'reference': 'K', 'mutation': 'E', 'phenotype': 'resistant'},
                        ],
                    },
                ),
                (
                    'db_gamma.db',
                    {
                        'references': [{'name': 'REF1', 'accession': 'ACC_SHARED', 'organism': 'Herpes'}],
                        'rules': [
                            {'ref_name': 'REF1', 'feature': 'gag', 'drug': 'ACV', 'position': 1, 'reference': 'K', 'mutation': 'E', 'phenotype': 'resistant'},
                            {'ref_name': 'REF1', 'feature': 'gag', 'drug': 'CDV', 'position': 3, 'reference': 'F', 'mutation': 'L', 'phenotype': 'intermediate'},
                        ],
                    },
                ),
            ],
        )
        (config.project_databases_dir / 'corrupt_dropin.db').write_bytes(b'corrupt!')
        client = TestClient(_make_app_and_queue(config)[0])

        catalog = client.get('/api/databases').json()['data']
        assert catalog['count'] == 3
        alpha_entry = next(i for i in catalog['items'] if i['id'] == 'db_alpha.db')
        assert alpha_entry['mutation_count'] == 2  # excludes __formula_component__

        assert client.get('/api/mutations', params={'database_id': 'db_alpha.db', 'reference': 'REF1'}).json()['data']['count'] == 2
        shared = client.get('/api/databases/shared-references', params={'ids': 'db_alpha.db,db_beta.db,db_gamma.db'}).json()['data']
        assert [s['accession'] for s in shared['items']] == ['ACC_SHARED']

        comp = client.post(
            '/api/databases/compare',
            json={'database_ids': ['db_alpha.db', 'db_beta.db', 'db_gamma.db'], 'accession': 'ACC_SHARED'},
        ).json()['data']
        assert comp['counts']['intersection'] == 1
        assert comp['counts']['total_unique'] == 3
        formula_cell = next(r['per_db']['db_alpha.db']['phenotype'] for r in comp['rows'] if r['drug'] == 'ACV')

        node_res = _run_node(
            "import { createRequire } from 'module';\n"
            "const req = createRequire(import.meta.url);\n"
            "const { JSDOM } = req('./web/frontend/node_modules/jsdom');\n"
            "const dom = new JSDOM('<!doctype html><html><body></body></html>');\n"
            "globalThis.window = dom.window; globalThis.document = dom.window.document;\n"
            "let tsv = ''; globalThis.Blob = class { constructor(p){ tsv = p.join(''); } };\n"
            "globalThis.URL = { createObjectURL: () => 'blob:x', revokeObjectURL: () => {} };\n"
            "dom.window.HTMLAnchorElement.prototype.click = () => {};\n"
            "const u = await import('./web/frontend/src/utils.js');\n"
            f"u.downloadTsv(['Drug', 'Phenotype'], [['ACV', {json.dumps(formula_cell)}]], 'cmp.tsv');\n"
            "console.log(tsv);\n"
        )
        assert node_res.returncode == 0
        assert "'=HYPERLINK" in node_res.stdout

    def test_t4_scenario4_maintained_db_bootstrap_and_online_policy_lifecycle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Scenario 4: S3/S4 manifest confinement + online startup policy + CSRF + Redis 503 vs health 200."""
        from respro.io import maintained_db
        from tests.conftest import write_genbank

        srv_dir = tmp_path / 'manifest_srv'
        (srv_dir / 'db_ok').mkdir(parents=True)
        write_genbank(
            srv_dir / 'ACC_SC4.gb',
            [{'id': 'ACC_SC4', 'accession': 'ACC_SC4', 'organism': 'V', 'sequence': TINY_REF_SEQ, 'features': [{'feature': 'gag', 'start': 1, 'end': len(TINY_REF_SEQ), 'strand': '+'}]}],
        )
        (srv_dir / 'db_ok' / 'rules.tsv').write_text(
            'feature\tposition\treference\tmutation\tantiviral\tphenotype\treference_identifier\n'
            'gag\t2\tK\tE\tTestDrug\tresistant\tACC_SC4\n',
            encoding='utf-8',
        )
        (srv_dir / 'db_ok' / 'metadata.json').write_text(json.dumps({'tsv_checksum': 'v1'}), encoding='utf-8')
        (srv_dir / 'manifest.json').write_text(
            json.dumps({
                'databases': [
                    {
                        'source_name': 'sc4_db',
                        'rules_path': 'db_ok/rules.tsv',
                        'metadata_path': 'db_ok/metadata.json',
                        'formula_rules_path': '',
                        'metadata': {'tsv_checksum': 'v1'},
                    }
                ]
            }),
            encoding='utf-8',
        )
        httpd, port = _start_local_http_server(srv_dir)
        try:
            base_url = f'http://127.0.0.1:{port}'
            monkeypatch.setattr(
                maintained_db,
                'CLI_CONFIG',
                replace(
                    maintained_db.CLI_CONFIG,
                    urls=replace(
                        maintained_db.CLI_CONFIG.urls,
                        github_respro_db_raw=base_url,
                        ncbi_nuccore_efetch=f'{base_url}/{{accession}}.gb',
                    ),
                ),
            )

            data_dir = tmp_path / 'data'
            monkeypatch.setenv('RESPRO_WEB_DATA_DIR', str(data_dir))
            monkeypatch.setenv('RESPRO_WEB_MAINTAINED_BOOTSTRAP', 'true')
            monkeypatch.setenv('RESPRO_WEB_DEPLOYMENT_MODE', 'online')
            monkeypatch.setenv('RESPRO_WEB_TRUSTED_PROXIES', '127.0.0.1')
            monkeypatch.setenv('RESPRO_WEB_CORS_ORIGINS', 'https://respro.example.org')

            _fake_redis, fake_server = _patch_fakeredis(monkeypatch)
            cfg = load_startup_config()
            assert (cfg.project_databases_dir / 'sc4_db.db').is_file()
            client = TestClient(
                _make_app_and_queue(cfg)[0],
                base_url='https://respro.example.org',
                raise_server_exceptions=False,
            )

            # Online CSRF protection blocks cross-site POST.
            assert client.post(
                '/api/session/cleanup',
                json={'upload_ids': [], 'artifact_ids': []},
                headers={'Sec-Fetch-Site': 'cross-site'},
            ).status_code == 403

            # Same-origin mutation succeeds.
            assert client.post(
                '/api/session/cleanup',
                json={'upload_ids': [], 'artifact_ids': []},
                headers={
                    'Host': 'respro.example.org',
                    'X-Forwarded-Proto': 'https',
                    'Origin': 'https://respro.example.org',
                    'Sec-Fetch-Site': 'same-origin',
                },
            ).status_code == 200

            # Redis outage -> health stays 200, readiness & stateful routes return 503.
            fake_server.connected = False
            assert client.get('/api/health').status_code == 200
            assert client.get('/api/readiness').status_code == 503
            assert client.get('/api/databases').status_code == 503
        finally:
            httpd.shutdown()

    def test_t4_scenario5_frontend_dist_vite_proxy_and_sandboxed_report_iframe_postmessage(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Scenario 5: Built SPA + strict index CSP + Vite proxy + real HTML report + report.js null-origin postMessage."""
        _patch_fakeredis(monkeypatch)
        out_dist = tmp_path / 'dist'
        build = subprocess.run(
            ['npx', 'vite', 'build', '--outDir', str(out_dist)],
            cwd=str(FRONTEND_DIR),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert build.returncode == 0, build.stderr
        assert 'larger than 500' not in f'{build.stdout}\n{build.stderr}'

        monkeypatch.setattr(main_module, '_frontend_dist_dir', lambda: out_dist)
        config = _make_startup_config(tmp_path)
        client = TestClient(_make_app_and_queue(config)[0])

        spa = client.get('/analysis')
        assert spa.status_code == 200
        soup = BeautifulSoup(spa.text, 'html.parser')
        assert [s for s in soup.find_all('script') if not s.get('src')] == []

        job_id = client.post(
            '/api/profile/fasta',
            json={'use_example': True, 'database_id': 'test_project.db', 'sample': 'IframeScenario'},
        ).json()['job_id']
        html_id = client.get(f'/api/jobs/{job_id}').json()['result']['report_html_path']
        rep = client.get('/api/report', params={'artifact_id': html_id})
        assert rep.status_code == 200
        assert "frame-ancestors 'self'" in rep.headers.get('content-security-policy', '')

        # Extract the embedded inline <script> from the real generated HTML report and exercise it in jsdom with null origin.
        rep_soup = BeautifulSoup(rep.text, 'html.parser')
        inline_scripts = [s.string for s in rep_soup.find_all('script') if not s.get('src') and s.string]
        assert len(inline_scripts) == 1
        js_file = tmp_path / 'embedded_report.js'
        js_file.write_text(inline_scripts[0], encoding='utf-8')

        node_res = _run_node(
            "import { createRequire } from 'module';\n"
            "import fs from 'fs';\n"
            "const req = createRequire(import.meta.url);\n"
            "const { JSDOM } = req('./web/frontend/node_modules/jsdom');\n"
            f"const js = fs.readFileSync({json.dumps(str(js_file))}, 'utf8');\n"
            "const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'about:blank', runScripts: 'outside-only' });\n"
            "const posted = [];\n"
            "const parent = { postMessage(m, t) { posted.push({ m, t }); } };\n"
            "Object.defineProperty(dom.window, 'parent', { value: parent, configurable: true });\n"
            "dom.window.eval(js);\n"
            "dom.window.dispatchEvent(new dom.window.MessageEvent('message', { data: { type: 'respro:report-theme', theme: 'dark' }, origin: 'http://127.0.0.1:5173', source: parent }));\n"
            "console.log(dom.window.document.documentElement.getAttribute('data-theme'));\n"
        )
        assert node_res.returncode == 0, node_res.stderr
        assert node_res.stdout.strip().splitlines()[-1] == 'dark'

    def test_t4_scenario6_active_job_cancellation_process_group_and_resource_recovery(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Scenario 6: Active started job -> DELETE /api/jobs/{id} invokes send_stop_job_command -> cleanup."""
        fake_redis, fake_server = _patch_fakeredis(monkeypatch)
        config = _make_startup_config(tmp_path)
        app, profile_q, _ = _make_app_and_queue(config, is_async=True, redis_server=fake_server)
        client = TestClient(app)

        up_id = _upload_file(client, 'fasta', 'slow.fasta', MUTATED_TINY_FASTA.encode('utf-8'))['upload_id']
        job_id = client.post(
            '/api/profile/fasta',
            json={'fasta_id': up_id, 'database_id': 'test_project.db', 'sample': 'ActiveCancel'},
        ).json()['job_id']

        from rq.job import Job

        rq_job = Job.fetch(job_id, connection=profile_q.connection, serializer=JSONSerializer)
        rq_job.worker_name = 'worker-test-1'
        rq_job.set_status('started')
        rq_job.save()

        stop_calls: list[str] = []
        import rq.command as rq_command_module

        import web.backend.jobs as jobs_exec_module
        import web.backend.routes.jobs as jobs_route_module

        # Verify routes/jobs.py uses RQ send_stop_job_command on started jobs (W6 / WA-020).
        def _spy_stop(connection: Any, jid: str, **_kwargs: Any) -> None:
            del connection
            stop_calls.append(jid)
            j = Job.fetch(jid, connection=profile_q.connection, serializer=JSONSerializer)
            j.set_status('stopped')
            j.save()

        monkeypatch.setattr(rq_command_module, 'send_stop_job_command', _spy_stop)
        if hasattr(jobs_route_module, 'send_stop_job_command'):
            monkeypatch.setattr(jobs_route_module, 'send_stop_job_command', _spy_stop)

        # Behaviorally verify web/backend/jobs.py runs CLI in a new session and killpgs the whole process group on timeout (W6/WA-020).
        info_path = tmp_path / 'proc_info.txt'
        child_script = (
            'import os, subprocess, sys, time; '
            'gc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"]); '
            f'f = open({str(info_path)!r}, "w", encoding="utf-8"); '
            'f.write(f"{os.getpgid(0)} {gc.pid}"); f.flush(); f.close(); '
            'time.sleep(30)'
        )
        import sys as _sys

        monkeypatch.setattr(jobs_exec_module, 'resolve_subprocess_timeout_seconds', lambda: 0.5)
        grandchild_pid: int | None = None
        child_pgid: int | None = None
        try:
            with pytest.raises(RuntimeError, match='timed out'):
                jobs_exec_module._run_respro_command([_sys.executable, '-c', child_script])
            assert info_path.is_file(), 'Child subprocess did not write process group metadata before timeout'
            pgid_raw, gc_raw = info_path.read_text(encoding='utf-8').strip().split()
            child_pgid = int(pgid_raw)
            grandchild_pid = int(gc_raw)
            assert child_pgid != os.getpgid(0), (
                'CLI subprocess must run in an isolated process group / session (W6/WA-020)'
            )
            dead = False
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                try:
                    os.kill(grandchild_pid, 0)
                except ProcessLookupError:
                    dead = True
                    break
                stat_file = Path(f'/proc/{grandchild_pid}/stat')
                if stat_file.is_file():
                    stat_text = stat_file.read_text(encoding='utf-8', errors='ignore')
                    rparen = stat_text.rfind(')')
                    if rparen != -1 and stat_text[rparen + 2:rparen + 3] == 'Z':
                        dead = True
                        break
                time.sleep(0.02)
            assert dead, f'Grandchild PID {grandchild_pid} survived timeout without killpg (W6/WA-020)'
        finally:
            if grandchild_pid is not None:
                with contextlib.suppress(ProcessLookupError, OSError):
                    os.kill(grandchild_pid, signal.SIGKILL)
            if child_pgid is not None and child_pgid != os.getpgid(0):
                with contextlib.suppress(ProcessLookupError, OSError):
                    os.killpg(child_pgid, signal.SIGKILL)

        cancel = client.delete(f'/api/jobs/{job_id}')
        assert cancel.status_code == 204
        assert stop_calls == [job_id], 'DELETE /api/jobs/{id} on started job must invoke send_stop_job_command (W6/WA-020)'

        status = client.get(f'/api/jobs/{job_id}').json()
        assert status['status'] == 'failed'
        assert status['error'] == 'Job canceled by user.'
        assert list(config.results_dir.iterdir()) == []

        clean = client.post('/api/session/cleanup', json={'upload_ids': [up_id], 'artifact_ids': []})
        assert clean.status_code == 200
        assert clean.json()['deleted_count'] == 1
        assert list(config.uploads_dir.iterdir()) == []
