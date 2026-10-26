"""Scripts that tests import by path must have NO import-time side effects on the process environment (the real .env holds live API / Telegram keys)."""
import ast, importlib.util, os, re, sys
import pytest

HERE = os.path.dirname(os.path.abspath(__file__)); SCRIPTS = os.path.join(HERE, "..", "scripts")


def _scripts_used_by_tests() -> list[str]:
    names = set()
    for f in os.listdir(HERE):
        if f.endswith(".py") and f != os.path.basename(__file__):
            text = open(os.path.join(HERE, f), encoding="utf-8").read()
            if not re.search(r"exec_module|spec_from_file_location|from scripts|import scripts", text): continue           # only tests that really LOAD scripts (not ones that just read their source text)
            for m in re.findall(r'["\']([a-z_0-9]+)\.py["\']', text):
                if os.path.exists(os.path.join(SCRIPTS, m + ".py")): names.add(m)
    return sorted(names)


def test_scripts_imported_by_tests_do_not_read_dotenv_at_import_time():
    bad = []
    for name in _scripts_used_by_tests():
        tree = ast.parse(open(os.path.join(SCRIPTS, name + ".py"), encoding="utf-8").read())
        for n in tree.body:                                                           # top-level statements only: functions, classes, imports and `if __name__` blocks are fine
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)): continue
            if isinstance(n, ast.If) and "__name__" in ast.unparse(n.test): continue
            src = ast.unparse(n)
            if re.search(r"""["']\.env["']""", src) or "load_dotenv" in src: bad.append(f"{name}: {src[:80]!r}")        # a reference to the .env FILE (not "os.environ")
    assert bad == [], "module-level environment loading in scripts that tests import: " + str(bad)


@pytest.mark.parametrize("name", ["priority_capture_watchdog", "arena_ingest_watchdog"])
def test_importing_the_cron_watchdogs_leaves_os_environ_untouched(name):
    before = dict(os.environ)
    sp = importlib.util.spec_from_file_location("hyg_" + name, os.path.join(SCRIPTS, name + ".py")); m = importlib.util.module_from_spec(sp); sys.modules["hyg_" + name] = m
    sp.loader.exec_module(m)
    assert dict(os.environ) == before and hasattr(m, "main") and hasattr(m, "_load_env")


def test_environment_changes_do_not_leak_between_tests_part1():
    os.environ["PHASE12_LEAK_PROBE"] = "1"                                            # deliberately NOT via monkeypatch


def test_environment_changes_do_not_leak_between_tests_part2():
    assert "PHASE12_LEAK_PROBE" not in os.environ                                     # ordering inside the file is definition order
