"""The worked example in references/demo-spec.md must be a valid demo spec."""

import json
import re
from pathlib import Path

from demo_spec import ACTION_TYPES, DEFAULT_PHONE, DEVICES, validate_spec

REFERENCE = Path(__file__).resolve().parent.parent / "references" / "demo-spec.md"


def worked_example(heading="## Worked example"):
    text = REFERENCE.read_text(encoding="utf-8")
    section = re.search(rf"^{heading}\n(.*?)(?=^## |\Z)", text, flags=re.DOTALL | re.MULTILINE)
    assert section, heading
    blocks = re.findall(r"```json\n(.*?)```", section.group(1), flags=re.DOTALL)
    blocks = [block for block in blocks if '"scenes"' in block]
    assert len(blocks) == 1, f"expected one complete demo spec under {heading}"
    return json.loads(blocks[0])


def test_worked_example_validates():
    spec = validate_spec(worked_example())

    assert len(spec["scenes"]) >= 3


def test_phone_worked_example_hands_off_from_a_desktop_qr_code_to_a_phone_upload():
    spec = validate_spec(worked_example("## Phone worked example"))

    assert [scene["device"] for scene in spec["scenes"]] == ["desktop", "phone", "desktop"]
    phone_actions = [action["type"] for action in spec["scenes"][1]["actions"]]
    assert "upload" in phone_actions


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
