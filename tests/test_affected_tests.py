from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

from conftest import git
from framegate.adapters import get_adapter
from framegate.config import parse_config

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("affected_tests", ROOT / "tools/affected_tests.py")
assert _spec and _spec.loader
tool = importlib.util.module_from_spec(_spec)
sys.modules["affected_tests"] = tool
_spec.loader.exec_module(tool)

FILES = {
    "src/shop/__init__.py": "",
    "src/shop/price.py": "def net(x):\n    return x\n\n\ndef gross(x):\n    return x * 2\n",
    "src/shop/cart.py": "from shop.price import gross\n\n\ndef total(xs):\n    return sum(xs)\n",
    "src/shop/label.py": "def text(x):\n    return str(x)\n",
    "tools/report.py": "def render():\n    return ''\n",
    "tests/test_price.py": (
        "from shop import price\n\n\ndef test_gross():\n    assert price.gross(1) == 2\n\n\n"
        "def test_net():\n    assert price.net(1) == 1\n"
    ),
    "tests/test_import_only.py": "from shop.price import net\n\n\ndef test_net():\n    net(1)\n",
    "tests/test_cart.py": "from shop.cart import total\n\n\ndef test_total():\n    total([1])\n",
    "tests/test_label.py": "from shop.label import text\n\n\ndef test_text():\n    text(1)\n",
    "tests/test_name_only.py": "def test_gross():\n    gross = 1\n    assert gross\n",
    "tests/test_report.py": "TOOL = 'tools/report.py'\n\n\ndef test_render():\n    render = 1\n",
}


@pytest.fixture
def repo(tmp_path: pathlib.Path):
    for rel, text in FILES.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "start")
    return get_adapter(parse_config(tmp_path, {}))


def write(adapter, rel: str, text: str) -> None:
    (adapter.cfg.root / rel).write_text(text, encoding="utf-8")


def test_nothing_changed_selects_nothing(repo) -> None:
    assert tool.select(repo, "main") == ([], [])


def test_a_changed_function_selects_its_tests_in_order(repo) -> None:
    write(repo, "src/shop/price.py", FILES["src/shop/price.py"].replace("x * 2", "x * 3"))
    write(repo, "tests/test_new.py", "def test_nothing():\n    assert True\n")
    write(repo, "notes.md", "a note\n")

    hits, notes = tool.select(repo, "main")

    assert [(h.file, h.why()) for h in hits] == [
        ("tests/test_new.py", "changed"),
        ("tests/test_price.py", "imports a changed module, 1 test(s) name changed code"),
        ("tests/test_import_only.py", "imports a changed module"),
        ("tests/test_name_only.py", "1 test(s) use a changed name, no import found"),
        ("tests/test_cart.py", "one import from a changed module"),
    ]
    assert notes == ["not traced (1 file(s) that are not Python): notes.md"]


def test_a_script_outside_the_source_folders_selects_the_tests_that_name_its_file(repo) -> None:
    write(
        repo, "tools/report.py", "def render():\n    return 'x'\n\n\ndef gross():\n    return 1\n"
    )
    hits, _ = tool.select(repo, "main")
    assert [(h.file, h.why()) for h in hits] == [
        ("tests/test_report.py", "imports a changed module, 1 test(s) name changed code")
    ]


def test_a_changed_conftest_reaches_the_tests_of_its_folder(repo) -> None:
    write(repo, "tests/conftest.py", "import shop.label\n")
    hits, _ = tool.select(repo, "main")
    assert len(hits) == 6 and {h.why() for h in hits} == {"one import from a changed module"}

    git(repo.cfg.root, "add", "-A")
    git(repo.cfg.root, "commit", "-q", "-m", "conftest")
    write(repo, "src/shop/label.py", "def text(x):\n    return repr(x)\n")
    hits, _ = tool.select(repo, "main~0")
    assert hits[0].file == "tests/test_label.py" and len(hits) == 6
    assert hits[1].why() == "imports a changed module"


def test_the_cap_names_what_it_leaves_out() -> None:
    hits = [tool.Hit(f"tests/test_{n}.py", changed=True) for n in "abc"]
    assert tool.cut(hits, 3) == (hits, "")
    kept, message = tool.cut(hits, 1)
    assert kept == hits[:1]
    assert message == "left out 2 file(s) over the cap of 1: tests/test_b.py, tests/test_c.py"


def test_imports_are_read_with_their_package() -> None:
    text = "import a.b\nfrom c import d\nfrom . import e\nfrom .f import g\nfrom .. import h\n"
    assert tool.imported(text, "p.q") == {"a.b", "c", "c.d", "p.q", "p.q.e", "p.q.f", "p.q.f.g"} | {
        "p",
        "p.h",
    }
    assert tool.imported("def broken(:\n") == set()
    assert tool.module_name("src/shop/__init__.py", ["src"]) == "shop"
    assert tool.module_name("src/shop/price.py", ["src"]) == "shop.price"
    assert tool.module_name("tools/report.py", ["src"]) == ""
