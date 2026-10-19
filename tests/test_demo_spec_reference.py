"""The worked example in references/demo-spec.md must be a valid demo spec."""

import json
import re
from pathlib import Path

from demo_spec import ACTION_TYPES, validate_spec

REFERENCE = Path(__file__).resolve().parent.parent / "references" / "demo-spec.md"


def worked_example():
    text = REFERENCE.read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(.*?)```", text, flags=re.DOTALL)
    assert len(blocks) == 1, "references/demo-spec.md should hold exactly one ```json block"
    return json.loads(blocks[0])


def test_worked_example_validates():
    spec = validate_spec(worked_example())

    assert len(spec["scenes"]) >= 3


def test_worked_example_uses_every_documented_top_level_field():
    spec = worked_example()

    for field in ("title", "product", "audience", "viewport", "voice", "scenes"):
        assert field in spec, field


def test_reference_documents_every_action_type():
    text = REFERENCE.read_text(encoding="utf-8")

    for action_type in ACTION_TYPES:
        assert f"`{action_type}`" in text, action_type
