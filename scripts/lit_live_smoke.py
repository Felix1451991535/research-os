"""Live smoke check of the literature sources against the REAL public APIs (free, read-only, rate-limited by
HostHttp). Uses a throw-away data dir. No keys, no email, no paid calls. Prints a JSON report.

Usage: python scripts/lit_live_smoke.py <tmp_data_dir> <out_json>"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    from researchos.app.factory import init_storage
    from researchos.kernel.config import load_settings
    from researchos.kernel.db import connect
    from researchos.services.literature import candidates, discovery
    from researchos.services.wiring import build_services

    data, out = Path(sys.argv[1]), Path(sys.argv[2])
    settings = load_settings(data)
    svc = build_services(settings, providers={})
    init_storage(svc)
    conn = connect(settings.db_path)
    ctx = discovery.Ctx(svc.extensions, svc.keystore, "script:lit_live_smoke")
    report: dict[str, object] = {}
    for name in ("lit_crossref", "lit_openalex", "lit_pubmed", "lit_arxiv", "lit_semantic"):
        r = discovery.search(conn, ctx, "heart rate variability deep learning", [name], page_size=5)
        report[f"search:{name}"] = {**r["per_source"][name], "titles": [c["record"]["title"][:80]
                                                                         for c in r["candidates"][:3]]}
    for name in ("lit_crossref", "lit_openalex", "lit_pubmed", "lit_arxiv"):
        r = discovery.search(conn, ctx, "Yoshua Bengio", [name], filters={"author": "Yoshua Bengio",
                                                                         "from_year": "2020"}, page_size=5)
        report[f"author:{name}"] = {**r["per_source"][name], "years": [c["record"]["year"] for c in r["candidates"]]}
    seed = discovery.search(conn, ctx, "Attention is all you need", ["lit_openalex"], page_size=1)["candidates"]
    if seed:
        rec = candidates.record_of(seed[0])
        for d in ("references", "cited_by"):
            r = discovery.chain(conn, ctx, rec, d, ["lit_openalex"], 5)
            report[f"chain:{d}"] = {**r["per_source"]["lit_openalex"], "seed": rec.title[:80]}
    src = svc.extensions.sources["lit_openalex"]
    from researchos.services.knowledge.http_fetch import HostHttp

    st = src.retraction_status((("doi", "10.1016/s0140-6736(97)11096-0"), ("doi", "10.1038/nature14539")),
                               HostHttp(svc.extensions.granted_hosts("datasources:lit_openalex", src.spec.hosts)), {})
    report["retraction"] = [{"id": s.identifier[1], "retracted": s.retracted} for s in st]
    out.write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps(report, indent=1, default=str)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
