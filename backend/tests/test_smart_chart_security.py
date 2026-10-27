import ast
import os
import re
import subprocess

import pytest

from app.smart_chart import config, runner, store, upstox_ext
from tests import smart_chart_fakes as fk

PKG = config.HERE
PY_FILES = [os.path.join(PKG, f) for f in sorted(os.listdir(PKG)) if f.endswith(".py")]


def imports_of(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            out.append(("." * n.level) + (n.module or "") + "::" + ",".join(a.name for a in n.names))
    return out


def test_no_order_or_broker_path_exists_in_the_package():
    banned = ("execution", "angelone_orders", "dhan_client", "dhan_routes", "live_order", "order_manager", "broker", "angelone")
    for f in PY_FILES:
        for imp in imports_of(f):
            low = imp.lower()
            assert not any(b in low for b in banned if b != "angelone") and "angelone" not in low, f"{os.path.basename(f)} imports {imp}"
        src = open(f, encoding="utf-8").read()
        assert not re.search(r"place_?order|submit_?order|/orders|quote_stream", src), os.path.basename(f)


def test_only_the_upstox_data_connector_is_reused_for_market_data():
    ups = [imp for f in PY_FILES for imp in imports_of(f) if "connectors" in imp]
    assert ups and all("upstox" in i for i in ups), ups


def test_no_dynamic_code_execution_or_shell_in_python_sources():
    for f in PY_FILES:
        tree = ast.parse(open(f, encoding="utf-8").read())
        for n in ast.walk(tree):                                    # real code only, not docstrings/comments
            if isinstance(n, ast.Call):
                fn = n.func
                bare = fn.id if isinstance(fn, ast.Name) else ""                    # builtins: eval(), exec(), compile(), __import__()
                attr = fn.attr if isinstance(fn, ast.Attribute) else ""             # os.system(), x.exec(...)
                assert bare not in ("eval", "exec", "compile", "__import__") and attr not in ("system", "popen", "eval", "exec"), (os.path.basename(f), bare or attr)
                for kw in n.keywords:
                    assert not (kw.arg == "shell" and getattr(kw.value, "value", False) is True), os.path.basename(f)
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                assert "pickle" not in ast.dump(n), os.path.basename(f)


def test_worker_has_no_eval_and_strips_network_globals():
    js = open(os.path.join(PKG, "pine_runner", "worker.mjs"), encoding="utf-8").read()
    code = "\n".join(re.sub(r"//.*$", "", line) for line in js.splitlines())
    assert not re.search(r"\beval\(|new Function|child_process|require\(|node:fs|readFileSync|writeFileSync", code)
    for g in ("fetch", "WebSocket", "XMLHttpRequest"):
        assert f"'{g}'" in code.split("for (const k of")[1].split("]")[0]


@pytest.mark.skipif(not runner.available()["ok"], reason="node not installed")
def test_node_permission_model_really_denies_writes_children_and_workers(tmp_path):
    node = runner.node_binary()
    for label, code in (("write", f"require('fs').writeFileSync({str(tmp_path / 'x')!r}, 'y')"),
                        ("child", "require('child_process').execSync('id')"),
                        ("worker", "new (require('worker_threads').Worker)('1', {eval:true})"),
                        ("read-outside", "require('fs').readFileSync('/etc/passwd')")):
        p = subprocess.run([node, "--permission", f"--allow-fs-read={PKG}", "-e", code], capture_output=True, text=True, env={}, cwd=PKG, timeout=20)
        assert p.returncode != 0 and ("ERR_ACCESS_DENIED" in p.stderr or "Access to this API has been restricted" in p.stderr), (label, p.stderr[:300])
    assert not (tmp_path / "x").exists()
    env = subprocess.run([node, "--permission", f"--allow-fs-read={PKG}", "-e", "console.log(Object.keys(process.env).length)"], capture_output=True, text=True, env={}, cwd=PKG)
    assert env.stdout.strip() == "0"


@pytest.mark.skipif(not runner.available()["ok"], reason="node not installed")
def test_pine_source_cannot_reach_js_globals():
    bars = fk.candles_for_pine(60, 5)
    runner._rate.clear()
    for src in ('plot(process.exit(1))', 'x = require("fs")\nplot(1)', 'plot(globalThis.fetch("http://x"))', 'plot(constructor.constructor("return process")())',
                'x = import("fs")\nplot(1)'):
        r = runner.execute({"op": "run", "source": f'//@version=5\nindicator("x")\n{src}', "candles": bars, "tf": "5", "symbol": "N"}, "sec", )
        assert r["ok"] is False, src


@pytest.mark.skipif(not runner.available()["ok"], reason="node not installed")
def test_concurrency_cap_returns_busy_instead_of_queueing_forever(monkeypatch):
    sem = runner.threading.BoundedSemaphore(1)
    monkeypatch.setattr(runner, "_slots", sem)
    sem.acquire()
    runner._rate.clear()
    with pytest.raises(runner.RunnerError) as e:
        runner.execute({"op": "compile", "source": "//@version=5\nindicator('x')\nplot(close)"}, "sec")
    assert e.value.kind == "busy"
    sem.release()


def test_oversize_request_is_refused_before_spawning(monkeypatch):
    monkeypatch.setattr(runner.config, "MAX_RUNNER_OUTPUT_BYTES", 100)
    runner._rate.clear()
    with pytest.raises(runner.RunnerError) as e:
        runner.execute({"op": "run", "source": "x" * 500}, "sec")
    assert e.value.kind == "too_large"


def test_sql_injection_and_odd_strings_roundtrip_as_data(tmp_path, monkeypatch):
    monkeypatch.setenv("SMART_CHART_DB", str(tmp_path / "sc.db"))
    store.close()
    nasty = "x'); DROP TABLE scripts;-- <script>alert(1)</script> \u202e"
    s = store.create_script("o'wner", nasty, nasty, "indicator", "//@version=5\nindicator('" + nasty.replace("'", "") + "')\nplot(close)")
    assert store.get_script("o'wner", s["id"])["name"] == nasty
    assert store.list_scripts("o'wner", q="DROP")["total"] == 1
    assert store.count_scripts("o'wner") == 1
    assert store.get_script("o'wner' OR '1'='1", s["id"]) is None
    store.close()


def test_upstox_errors_and_health_never_carry_credentials(monkeypatch):
    class R:
        status_code = 401
        headers = {}
        text = ""

        def json(self):
            return {"status": "error", "errors": [{"errorCode": "UDAPI100050", "message": "Invalid token"}]}
    with pytest.raises(upstox_ext.UpstoxError) as e:
        upstox_ext._raise_for(R())
    assert "Bearer" not in str(e.value) and e.value.code == "UDAPI100050" and "renew" in str(e.value)
    monkeypatch.setattr(upstox_ext._u, "auth_health", lambda: {"historical_data_api": "OK", "credentials_configured": True, "api_reachable": True,
                                                                "note": "n", "access_token": "SECRET-TOKEN-VALUE", "api_secret": "SECRET2"})
    out = str(upstox_ext.auth_summary())
    assert "SECRET" not in out and "access_token" not in out


def test_limits_are_configured_and_bounded():
    assert 0 < config.MAX_SCRIPT_BYTES <= 200_000 and config.MAX_CANDLES <= 20_000 and config.EXEC_TIMEOUT_SEC <= 60
    assert config.NODE_HEAP_MB <= 1024 and config.MAX_CONCURRENT_RUNS <= 4 and config.CACHE_MAX_ENTRIES * config.CACHE_MAX_BARS_PER_ENTRY <= 3_000_000
