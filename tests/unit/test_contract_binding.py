"""Exercise contract adapters independently of a product's module imports."""

import ast
import json
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.common import ROOT


def contract_functions(subject: Any, *names: str) -> dict[str, Any]:
    namespace: dict[str, Any] = {
        "Any": Any,
        "Key": Any,
        "RunModule": Any,
        "subject": subject,
        "json": json,
        "pytest": pytest,
        "arguments": lambda **values: values,
    }
    files = (
        ("plugins/module_utils/_engine.py.j2", {"canonical", "key_value"}),
        ("tests/unit/plugins/modules/test_contract.py.j2", {*names, "identity"}),
    )
    for filename, selected in files:
        path = ROOT / "collection_modules" / filename
        source = path.read_text().replace("{{ namespace }}", "example")
        source = source.replace("{{ collection_name }}", "product")
        tree = ast.parse(source)
        tree.body = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in selected
        ]
        for node in tree.body:
            assert isinstance(node, ast.FunctionDef)
            node.decorator_list = []
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
            keys=tuple(
                SimpleNamespace(name=name, normalizer=None, canonical="")
                for name in ("name", "version")
            )
        ),
        rows=lambda fake: stored,
    )
    indexed = contract_functions(subject, "rows")["rows"](None)
    indexed[("Example", "2.0")]["unmanaged"] = "kept"
    assert first == {"name": "Example", "version": "1.0", "published": "yesterday"}
    assert second["unmanaged"] == "kept"


@pytest.mark.parametrize("legacy", [False, True])
def test_contract_preserves_rows_with_different_wire_names(legacy: bool) -> None:
    row = {"id": "Example", "version": "1.0.0", "published": "today"}
    stored = {"today" if legacy else ("example", "1.0.0"): row}
    resource = SimpleNamespace(
        keys=tuple(
            SimpleNamespace(name=name, normalizer=None, canonical="lower")
            for name in ("name", "version")
        )
    )
    if legacy:
        resource.identifier = SimpleNamespace(name="published")
    subject = SimpleNamespace(
        RESOURCE=resource,
        ASSIGNED="published",
        RESULT="package",
        VALUE="listed",
        VALUES=(False, True),
        OLD_SECRET="private",
        rows=lambda fake: stored,
        main=None,
    )
    results = iter(
        [
            {
                "package": {
                    "name": "Example",
                    "version": "1.0.0",
                    "published": "today",
                },
                "changed_fields": [],
                "verified": "observed",
            },
            {"changed": False},
            {
                "changed_fields": ["listed"],
                "diff": {"before": {"listed": False}, "after": {"listed": True}},
            },
        ]
    )
    check = contract_functions(
        subject, "rows", "test_lifecycle_preserves_unmanaged_fields"
    )["test_lifecycle_preserves_unmanaged_fields"]
    check(None, lambda *args, **kwargs: next(results))
    assert row["unmanaged"] == "private"


@pytest.mark.parametrize("canonical", ["lower", "casefold"])
def test_contract_identity_uses_key_normalization(canonical: str) -> None:
    subject = SimpleNamespace(
        RESOURCE=SimpleNamespace(
            keys=(
                SimpleNamespace(
                    name="address",
                    canonical=canonical,
                    normalizer=lambda value: value.replace("-", ":"),
                ),
            )
        )
    )
    identity = contract_functions(subject, "identity")["identity"]
    assert identity({"address": "AA-BB"}) == ("aa:bb",)


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
