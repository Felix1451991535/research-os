"""Export language of the report exporters (ADR-234): catalog parity, the `language` option in both option schemas,
and Chinese manuscript headings mapped to the same slide sections."""

from __future__ import annotations

import jsonschema
import pytest
from researchos.sdk.texts import LANGUAGES, problems

from researchos_plugin_report_export.exporters import OPTIONS, validate_options
from researchos_plugin_report_export.resolve import SLIDE_SECTIONS, slide_section
from researchos_plugin_report_export.slides import SLIDE_OPTIONS
from researchos_plugin_report_export.texts import TEXTS


def test_catalog_is_complete_for_every_section() -> None:
    assert problems(TEXTS.texts) == []
    for lang in LANGUAGES:
        assert all(TEXTS.t(lang, f"section_{s.lower()}") for s in SLIDE_SECTIONS)


@pytest.mark.parametrize("schema", [OPTIONS, SLIDE_OPTIONS])
def test_language_option_is_validated(schema: dict[str, object]) -> None:
    for lang in LANGUAGES:
        validate_options({"language": lang}, schema)
    with pytest.raises(jsonschema.ValidationError):
        validate_options({"language": "fr"}, schema)


def test_chinese_headings_map_to_slide_sections() -> None:
    assert slide_section("3. 方法") == "Method" and slide_section("结果与讨论") == "Results"
    assert slide_section("引言：") == "Background" and slide_section("致谢") is None
