"""Text catalogs for generated documents (ADR-234; extends ADR-221 to the server). Snapshots and contracts carry data
and codes; every package keeps its fixed template text (headings, table headers, captions, boilerplate) in ONE
catalog `{language: {code: text}}` and renders it in the export's `language` option (en default, zh-CN optional).
Not translated, as in the UI: data, model output, protocol values (ids, hashes, split names), proper nouns.

A catalog is checked when it is built (import time): every language has the same codes, no text is empty and every
translation uses exactly the same `{placeholders}`."""

from __future__ import annotations

import string
from collections.abc import Mapping
from typing import Any

LANGUAGES = ("en", "zh-CN")
DEFAULT_LANGUAGE = "en"
# merge into an exporter's options_schema["properties"]
LANGUAGE_OPTION: Mapping[str, Any] = {"language": {"type": "string", "enum": list(LANGUAGES),
                                                    "default": DEFAULT_LANGUAGE}}


def export_language(options: Mapping[str, Any]) -> str:
    """The export's language; ValueError for an unsupported value."""
    lang = options.get("language", DEFAULT_LANGUAGE)
    if lang not in LANGUAGES:
        raise ValueError(f"language must be one of {', '.join(LANGUAGES)}")
    return str(lang)


def snapshot_language(language: str) -> str:
    """The ReportSnapshot.language value ("en" | "zh") of an export language."""
    return "zh" if language == "zh-CN" else "en"


def placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def problems(texts: Mapping[str, Mapping[str, str]]) -> list[str]:
    """Parity / empty / placeholder problems of a catalog (empty list = valid)."""
    out = [f"missing language {lang}" for lang in LANGUAGES if lang not in texts]
    if out:
        return out
    base = texts[DEFAULT_LANGUAGE]
    for lang in LANGUAGES:
        cat = texts[lang]
        out += [f"{lang}: missing {c}" for c in sorted(set(base) - set(cat))]
        out += [f"{lang}: extra {c}" for c in sorted(set(cat) - set(base))]
        out += [f"{lang}: empty {c}" for c, v in cat.items() if not v.strip()]
        out += [f"{lang}: placeholders of {c} differ" for c in sorted(set(base) & set(cat))
                if placeholders(cat[c]) != placeholders(base[c])]
    return out


class Catalog:
    def __init__(self, texts: Mapping[str, Mapping[str, str]]) -> None:
        bad = problems(texts)
        if bad:
            raise ValueError("invalid text catalog: " + "; ".join(bad))
        self.texts = texts

    def t(self, language: str, code: str, **params: Any) -> str:
        return self.texts[language][code].format(**params)

    def lang(self, language: str) -> Bound:
        return Bound(self, language)


class Bound:
    """A catalog fixed to one language: `T = CATALOG.lang(lang); T("references")`."""

    def __init__(self, catalog: Catalog, language: str) -> None:
        if language not in LANGUAGES:
            raise ValueError(f"language must be one of {', '.join(LANGUAGES)}")
        self.catalog, self.language = catalog, language

    def __call__(self, code: str, **params: Any) -> str:
        return self.catalog.t(self.language, code, **params)

