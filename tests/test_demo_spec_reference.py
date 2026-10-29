"""The worked example in references/demo-spec.md must be a valid demo spec."""

import json
import re
from pathlib import Path

from demo_spec import ACTION_TYPES, DEFAULT_PHONE, DEVICES, validate_spec

REFERENCE = Path(__file__).resolve().parent.parent / "references" / "demo-spec.md"


def worked_example():
    text = REFERENCE.read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(.*?)```", text, flags=re.DOTALL)
    blocks = [block for block in blocks if '"scenes"' in block]
    assert len(blocks) == 1, "expected one complete worked demo spec"
    return json.loads(blocks[0])


def test_worked_example_validates():
    spec = validate_spec(worked_example())

    assert len(spec["scenes"]) >= 3


def test_worked_example_uses_every_documented_top_level_field():
    spec = worked_example()

    for field in (
        "title",
        "product",
        "audience",
        "viewport",
        "voice",
        "scenes",
        "purpose",
        "outcome",
        "target_duration_s",
    ):
        assert field in spec, field


def test_reference_documents_devices_and_the_phone_settings():
    text = REFERENCE.read_text(encoding="utf-8")

    for name in ("`device`", "`phone`", *(f"`{device}`" for device in DEVICES)):
        assert name in text, name
    for field, value in DEFAULT_PHONE.items():
        assert f"`{field}`" in text and str(value) in text, field


def test_reference_documents_every_action_type():
    text = REFERENCE.read_text(encoding="utf-8")

    for action_type in ACTION_TYPES:
        assert f"`{action_type}`" in text, action_type
