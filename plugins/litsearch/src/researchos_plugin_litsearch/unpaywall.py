"""lit_unpaywall: DOI -> best open-access location (Unpaywall v2). Requires the owner's email (Unpaywall terms).
The license is taken only from Unpaywall's explicit field; unknown -> None (the host then never stores the PDF)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from researchos.contracts.datasources import DataSourceSpec, OaLocation
from researchos.contracts.http import HttpContext

from .normalize import doi as norm_doi
from .normalize import open_license, text


class UnpaywallResolver:
    spec = DataSourceSpec(
        "lit_unpaywall", "0.2.0", "oa_resolver", "Unpaywall open-access locations (opt-in)", ("api.unpaywall.org",)
    )
    api = "https://api.unpaywall.org/v2/"

    def resolve(self, doi: str, http: HttpContext, settings: Mapping[str, Any]) -> OaLocation | None:
        email = text(settings.get("polite_email"))
        if not email:
            raise ValueError("Unpaywall requires the polite_email setting")
        d = norm_doi(doi)
        if not d:
            raise ValueError("invalid DOI")
        r = http.get(self.api + quote(d, safe="/"), params={"email": email})
        if r.status == 404:
            return None
        if r.status != 200:
            raise ValueError(f"Unpaywall HTTP {r.status}")
        loc = json.loads(r.text()).get("best_oa_location") or {}
        pdf = text(loc.get("url_for_pdf"))
        if not pdf:
            return None
        lic = open_license(loc.get("license"))
        return OaLocation(
            pdf,
            lic,
            "unpaywall:best_oa_location.license" if lic else None,
            text(loc.get("version")),
            text(loc.get("host_type")),
        )
