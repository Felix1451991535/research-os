"""Text catalog of the report exporters (ADR-234): slide titles, kind labels, boilerplate and references headings in
the export's `language` option (en default, zh-CN). Section codes stay English internally (`SLIDE_SECTIONS`)."""

from __future__ import annotations

from researchos.sdk.texts import Catalog

TEXTS = Catalog(
    {
        "en": {
            "kind_report": "Report",
            "kind_answer": "Cited answer",
            "kind_manuscript": "Manuscript",
            "kind_paper_card": "Paper card",
            "untitled": "Untitled",
            "date_format": "%d %B %Y",
            "provenance_note": "Numbers and citations link to verified records (provenance.json)",
            "references": "References",
            "claim": "Claim: {id}",
            "section_background": "Background",
            "section_objectives": "Objectives",
            "section_method": "Method",
            "section_results": "Results",
            "section_discussion": "Discussion",
            "section_conclusion": "Conclusion",
        },
        "zh-CN": {
            "kind_report": "报告",
            "kind_answer": "带引用的回答",
            "kind_manuscript": "论文稿",
            "kind_paper_card": "论文卡片",
            "untitled": "无标题",
            "date_format": "%Y 年 %m 月 %d 日",
            "provenance_note": "数字和引用均链接到已核验的记录（provenance.json）",
            "references": "参考文献",
            "claim": "论断：{id}",
            "section_background": "背景",
            "section_objectives": "目的",
            "section_method": "方法",
            "section_results": "结果",
            "section_discussion": "讨论",
            "section_conclusion": "结论",
        },
    }
)
