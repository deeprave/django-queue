import ast
from pathlib import Path


def test_package_contains_no_asyncio_to_thread_calls():
    package = Path(__file__).parents[1] / "django_queue"

    offenders = []
    for source in package.rglob("*.py"):
        tree = ast.parse(source.read_text(), filename=source)
        if any(
            isinstance(node, ast.Attribute)
            and node.attr == "to_thread"
            and isinstance(node.value, ast.Name)
            and node.value.id == "asyncio"
            for node in ast.walk(tree)
        ):
            offenders.append(source)

    assert not offenders, offenders
