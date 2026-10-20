"""Exercise contract adapters independently of a product's module imports."""

import ast
import json
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.common import ROOT


def contract_functions(subject: Any, *names: str) -> dict[str, Any]:
    path = ROOT / "collection_modules/tests/unit/plugins/modules/test_contract.py.j2"
    source = path.read_text().replace("{{ namespace }}", "example")
    source = source.replace("{{ collection_name }}", "product")
    tree = ast.parse(source)
    tree.body = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    for node in tree.body:
        assert isinstance(node, ast.FunctionDef)
        node.decorator_list = []
    namespace: dict[str, Any] = {
        "Any": Any,
        "RunModule": Any,
        "subject": subject,
        "json": json,
        "pytest": pytest,
        "arguments": lambda **values: values,
    }
    exec(compile(tree, str(path), "exec"), namespace)
    return namespace


@pytest.mark.parametrize("storage", ["identifier", "natural"])
def test_contract_rows_use_composite_identity(storage: str) -> None:
    first = {"name": "Example", "version": "1.0", "published": "yesterday"}
    second = {"name": "Example", "version": "2.0", "published": "today"}
    stored = (
        {101: first, 102: second}
        if storage == "identifier"
        else {("example", "1.0"): first, ("example", "2.0"): second}
    )
    subject = SimpleNamespace(
        RESOURCE=SimpleNamespace(
            keys=(SimpleNamespace(name="name"), SimpleNamespace(name="version"))
        ),
        rows=lambda fake: stored,
    )
    indexed = contract_functions(subject, "rows")["rows"](None)
    indexed[("Example", "2.0")]["unmanaged"] = "kept"
    assert first == {"name": "Example", "version": "1.0", "published": "yesterday"}
    assert second["unmanaged"] == "kept"


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("predicted_assigned", [False, True])
def test_contract_creation_requires_assigned_field_absent(
    explicit: bool, predicted_assigned: bool
) -> None:
    subject = SimpleNamespace(
        RESOURCE=SimpleNamespace(identifier=SimpleNamespace(name="id")),
        RESULT="package",
        VALUE="listed",
        VALUES=(False, True),
        SECRETS=(),
        OLD_SECRET="private",
        main=None,
    )
    if explicit:
        subject.ASSIGNED = "published"
        subject.RESOURCE = SimpleNamespace()
    assigned = "published" if explicit else "id"
    prediction = {"name": "Example", "version": "1.0", "listed": True}
    if predicted_assigned:
        prediction[assigned] = "invented"
    result = {"changed": True, "verified": "predicted", "package": prediction}
    check = contract_functions(subject, "test_check_mode_and_secrets")[
        "test_check_mode_and_secrets"
    ]
    fake = SimpleNamespace(writes=lambda: [])
    if predicted_assigned:
        with pytest.raises(AssertionError):
            check(fake, lambda *args, **kwargs: result, "present", False, False)
    else:
        check(fake, lambda *args, **kwargs: result, "present", False, False)


@pytest.mark.parametrize("adapter", [False, True])
def test_contract_deletion_preparation_is_optional(adapter: bool) -> None:
    fake = SimpleNamespace(deletion="Unlist")
    subject = SimpleNamespace()

    def prepare_instance(product: Any) -> None:
        product.deletion = "HardDelete"

    if adapter:
        subject.prepare_deletion = prepare_instance
    prepare = contract_functions(subject, "prepare_deletion")["prepare_deletion"]
    prepare(fake)
    assert fake.deletion == ("HardDelete" if adapter else "Unlist")
