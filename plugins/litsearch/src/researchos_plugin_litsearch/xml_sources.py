"""PubMed ESearch/EFetch and arXiv Atom, with hostile XML rejected."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from typing import Any

from researchos.contracts.datasources import BibRecord, DataSourceSpec, SearchPage, SearchQuery
from researchos.contracts.http import HttpContext

from . import filters
from .json_sources import validate
from .normalize import arxiv, collect, doi, text, year

_EXTERNAL_DOCTYPE = re.compile(
    r"<!DOCTYPE\s+[\w:.-]+(\s+(PUBLIC|SYSTEM)\s+(\"[^\"]*\"|'[^']*')(\s+(\"[^\"]*\"|'[^']*'))?)?\s*>", re.IGNORECASE
)


def xml(data: str) -> ET.Element:
    """A plain external DOCTYPE reference (as PubMed EFetch sends) is removed: ElementTree never loads external
    DTDs. Internal subsets and any ENTITY declaration are refused (no entity expansion, no XXE)."""
    data = _EXTERNAL_DOCTYPE.sub("", data, count=1)
    if "<!DOCTYPE" in data.upper() or "<!ENTITY" in data.upper():
        raise ValueError("DTD/entity declarations forbidden")
    return ET.fromstring(data)


def content(node: ET.Element | None) -> str | None:
    return text("".join(node.itertext())) if node is not None else None


def pubmed(node: ET.Element) -> BibRecord:
    ident = content(node.find(".//PMID"))
    title = content(node.find(".//ArticleTitle"))
    if not ident or not title:
        raise ValueError("PubMed record lacks PMID or title")
    authors = tuple(
        " ".join(
            filter(None, (content(a.find("ForeName")), content(a.find("LastName")), content(a.find("CollectiveName"))))
        )
        for a in node.findall(".//Author")
    )
    d = doi(next((a.text for a in node.findall(".//ArticleId") if a.get("IdType") == "doi"), None))
    return BibRecord(
        "lit_pubmed",
        ident,
        title,
        tuple(a for a in authors if a),
        year(content(node.find(".//JournalIssue/PubDate/Year"))),
        content(node.find(".//Journal/Title")),
        text("\n".join(filter(None, (content(a) for a in node.findall(".//AbstractText"))))),
        (("pmid", ident),) + ((("doi", d),) if d else ()),
        f"https://pubmed.ncbi.nlm.nih.gov/{ident}/",
    )


class PubMedSource:
    spec = DataSourceSpec(
        "lit_pubmed", "0.1.0", "bibliographic", "PubMed ESearch and EFetch", ("eutils.ncbi.nlm.nih.gov",)
    )
    api = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"

    def search(self, query: SearchQuery, http: HttpContext, settings: Mapping[str, Any]) -> SearchPage:
        validate(query)
        offset = int(query.cursor or 0)
        if offset < 0:
            raise ValueError("Negative offset")
        params = {
            "db": "pubmed",
            "term": f"{query.text} AND {who}[Author]"
            if (who := filters.author(query)) and who != query.text
            else (f"{who}[Author]" if who else query.text),
            "retmode": "json",
            "retstart": str(offset),
            "retmax": str(query.page_size),
        }
        if fy := filters.from_year(query):
            params.update({"datetype": "pdat", "mindate": str(fy), "maxdate": "3000"})
        if settings.get("polite_email"):
            params["email"] = str(settings["polite_email"])
        response = http.get(self.api + "esearch.fcgi", params=params)
        if response.status != 200:
            return SearchPage((), warnings=(f"PubMed search HTTP {response.status}",))
        data = json.loads(response.text())["esearchresult"]
        ids = data.get("idlist", [])[: query.page_size]
        total = int(data["count"]) if "count" in data else None
        if not ids:
            return SearchPage((), total=total)
        response = http.get(self.api + "efetch.fcgi", params={"db": "pubmed", "id": ",".join(ids), "retmode": "xml"})
        if response.status != 200:
            return SearchPage((), warnings=(f"PubMed fetch HTTP {response.status}",))
        records, warnings = collect(xml(response.text()).findall("PubmedArticle"), pubmed, "PubMed")
        nxt = offset + len(ids)
        return SearchPage(records, str(nxt) if total is not None and nxt < total else None, total, warnings)


ATOM = "{http://www.w3.org/2005/Atom}"
OS = "{http://a9.com/-/spec/opensearch/1.1/}"


def atom(node: ET.Element) -> BibRecord:
    url, title = content(node.find(ATOM + "id")), content(node.find(ATOM + "title"))
    if not url or not title:
        raise ValueError("arXiv entry lacks id or title")
    ident = arxiv(url)
    d = doi(content(node.find("{http://arxiv.org/schemas/atom}doi")))
    pdf = next((n.get("href") for n in node.findall(ATOM + "link") if n.get("title") == "pdf"), None)
    return BibRecord(
        "lit_arxiv",
        ident,
        title,
        tuple(a for n in node.findall(ATOM + "author") if (a := content(n.find(ATOM + "name")))),
        year(content(node.find(ATOM + "published"))),
        abstract=content(node.find(ATOM + "summary")),
        identifiers=(("arxiv", ident),) + ((("doi", d),) if d else ()),
        url=url,
        oa_pdf_url=pdf,
        kind="preprint",
    )


class ArxivSource:
    spec = DataSourceSpec("lit_arxiv", "0.1.0", "bibliographic", "arXiv Atom search", ("export.arxiv.org",))
    api = "https://export.arxiv.org/api/query"

    def search(self, query: SearchQuery, http: HttpContext, settings: Mapping[str, Any]) -> SearchPage:
        validate(query)
        offset = int(query.cursor or 0)
        if offset < 0:
            raise ValueError("Negative offset")
        response = http.get(
            self.api,
            params={
                "search_query": f'au:"{who}"' if (who := filters.author(query)) else query.text,
                "start": str(offset),
                "max_results": str(query.page_size),
            },
        )
        if response.status != 200:
            return SearchPage((), warnings=(f"arXiv HTTP {response.status}",))
        root = xml(response.text())
        entries = root.findall(ATOM + "entry")[: query.page_size]
        records, warnings = collect(entries, atom, "arXiv")
        if fy := filters.from_year(query):  # arXiv API has no date filter: applied to the returned page
            records = tuple(r for r in records if r.year is None or r.year >= fy)
        total_text = content(root.find(OS + "totalResults"))
        total = int(total_text) if total_text else None
        nxt = offset + len(entries)
        return SearchPage(records, str(nxt) if entries and total is not None and nxt < total else None, total, warnings)
