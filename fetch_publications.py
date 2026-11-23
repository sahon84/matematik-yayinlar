#!/usr/bin/env python3
"""Build a complete OpenAlex + ORCID snapshot for the department website.

No secrets are written to output. Manual additions/exclusions remain managed
by the existing university-hosted manual_publications.json file.
"""
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PEOPLE = [
    ("Prof. Dr. İmdat İŞCAN", "0000-0001-6749-0591"),
    ("Prof. Dr. Oğuz OĞUR", "0000-0002-3206-5330"),
    ("Prof. Dr. Haşim ÇAYIR", "0000-0003-0348-8665"),
    ("Prof. Dr. Özkan ÖZTÜRK", "0000-0001-6657-7409"),
    ("Prof. Dr. Kerim BEKAR", "0000-0002-7531-9345"),
    ("Prof. Dr. Sercan TURHAN", "0000-0002-4392-2182"),
    ("Prof. Dr. Onur ŞAHİN", "0000-0001-5389-9855"),
    ("Prof. Dr. Yasemin SOYLU", "0000-0001-9009-1214"),
    ("Doç. Dr. Canan AKIN", "0000-0002-8922-3272"),
    ("Doç. Dr. Hülya GÜLTEKİN ÇİTİL", "0000-0002-3543-033X"),
    ("Doç. Dr. Hande GÜNAY AKDEMİR", "0000-0003-3241-1560"),
    ("Doç. Dr. Mahir DEMİR", "0000-0002-9670-5210"),
    ("Dr. Öğr. Üyesi Selim NUMAN", "0000-0002-5483-6861"),
    ("Dr. Öğr. Üyesi Serdar SOYLU", "0000-0003-4973-4227"),
    ("Dr. Öğr. Üyesi Saime Şule AKSAKAL", "0000-0002-1810-1040"),
    ("Arş. Gör. Selcan AKSOY", "0000-0001-7853-0624"),
]

API_KEY = os.environ.get("OPENALEX_API_KEY", "").strip()
if not API_KEY:
    sys.exit("OPENALEX_API_KEY is missing; refusing to generate an incomplete snapshot")


def get_json(url, *, attempts=4):
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers={
                "Accept": "application/json",
                "User-Agent": "GiresunMatematikPublications/1.0 (academic publication listing)",
            })
            with urllib.request.urlopen(req, timeout=40) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            # A daily-budget failure cannot be fixed by repeated requests.
            if exc.code in (401, 402, 403):
                raise RuntimeError(f"API authentication/budget error (HTTP {exc.code})") from exc
            if exc.code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                raise
            delay = min(2 ** attempt * 3 + random.random(), 30)
            time.sleep(delay)
        except (urllib.error.URLError, TimeoutError):
            if attempt == attempts - 1:
                raise
            time.sleep(min(2 ** attempt * 3, 30))
    raise RuntimeError("Request failed")


def text(v):
    return str(v or "").strip()


def doi_norm(value):
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)", "", text(value).lower())


def title_norm(value):
    return " ".join(re.findall(r"\w+", text(value).casefold()))


def year_of(work):
    return text(work.get("publication_date"))[:4]


def work_key(work):
    doi = doi_norm(work.get("doi"))
    return "doi:" + doi if doi else "title:" + title_norm(work.get("display_name")) + "|" + year_of(work)


def compact(work):
    source = (work.get("primary_location") or {}).get("source") or {}
    return {
        "id": work.get("id"),
        "display_name": work.get("display_name"),
        "publication_date": work.get("publication_date"),
        "type": work.get("type"),
        "doi": work.get("doi"),
        "cited_by_count": work.get("cited_by_count"),
        "issn": work.get("issn", []),
        "primary_location": {"source": {
            "display_name": source.get("display_name"),
            "issn": source.get("issn"),
            "issn_l": source.get("issn_l"),
        }} if source else None,
        "authorships": [{"author": {"display_name": (a.get("author") or {}).get("display_name")}}
                        for a in (work.get("authorships") or [])],
        "openalex_source": True,
    }


def openalex_works(orcid):
    # Cursor pagination avoids deep offset limitations.
    cursor = "*"
    all_works = []
    while True:
        params = urllib.parse.urlencode({
            "filter": "authorships.author.orcid:https://orcid.org/" + orcid,
            "per-page": "200",
            "cursor": cursor,
            "api_key": API_KEY,
        })
        data = get_json("https://api.openalex.org/works?" + params)
        batch = data.get("results")
        if not isinstance(batch, list):
            raise RuntimeError("OpenAlex returned invalid results for " + orcid)
        all_works.extend(compact(w) for w in batch)
        next_cursor = (data.get("meta") or {}).get("next_cursor")
        if not batch or not next_cursor:
            break
        if next_cursor == cursor:
            raise RuntimeError("OpenAlex cursor did not advance for " + orcid)
        cursor = next_cursor
    return all_works


def orcid_value(value):
    if isinstance(value, dict):
        return text(value.get("value"))
    return text(value)


def orcid_works(orcid, owner_name):
    data = get_json(f"https://pub.orcid.org/v3.0/{orcid}/works")
    works = []
    for group in data.get("group") or []:
        for item in group.get("work-summary") or []:
            title = orcid_value((item.get("title") or {}).get("title"))
            if not title:
                continue
            publication = item.get("publication-date") or {}
            year = orcid_value(publication.get("year"))
            doi = ""
            for ext in (item.get("external-ids") or {}).get("external-id") or []:
                if text(ext.get("external-id-type")).lower() == "doi":
                    doi = doi_norm(ext.get("external-id-value"))
                    break
            works.append({
                "id": "orcid-doi:" + doi if doi else "orcid:" + orcid + ":" + title_norm(title) + ":" + year,
                "display_name": title,
                "publication_date": year + "-01-01" if year else "",
                "type": text(item.get("publication-type") or item.get("type")) or "other",
                "doi": doi,
                "manual_journal": orcid_value(item.get("journal-title")),
                "cited_by_count": None,
                "issn": [],
                "authorships": [{"author": {"display_name": owner_name}}],
                "orcid_source": True,
            })
    return works


def merge(existing, new):
    # Prefer OpenAlex metadata, but preserve ORCID-only records.
    primary = existing if existing.get("openalex_source") else new if new.get("openalex_source") else existing
    secondary = new if primary is existing else existing
    result = dict(secondary)
    result.update(primary)
    result["orcid_source"] = bool(existing.get("orcid_source") or new.get("orcid_source"))
    result["openalex_source"] = bool(existing.get("openalex_source") or new.get("openalex_source"))
    if not result.get("doi"):
        result["doi"] = secondary.get("doi")
    return result


# Journal issue date corrections for records whose source metadata uses an
# earlier online-publication date. Keep this mapping in source control so that
# daily snapshot regeneration does not overwrite the corrected date.
PUBLICATION_DATE_OVERRIDES = {
    "10.1016/j.ins.2025.123003": "2026-04-05",
}


def apply_publication_date_overrides(work):
    doi = doi_norm(work.get("doi"))
    corrected_date = PUBLICATION_DATE_OVERRIDES.get(doi)
    if corrected_date:
        updated = dict(work)
        updated["publication_date"] = corrected_date
        return updated
    return work


def main():
    per_person = {}
    for name, orcid in PEOPLE:
        print(f"Fetching {name} ({orcid})", flush=True)
        oa = openalex_works(orcid)
        orc = orcid_works(orcid, re.sub(r"^(?:Prof\. Dr\.|Doç\. Dr\.|Dr\. Öğr\. Üyesi|Arş\. Gör\.)\s*", "", name))
        unique = {}
        for work in oa + orc:
            key = work_key(work)
            unique[key] = merge(unique[key], work) if key in unique else work
        per_person[orcid] = [apply_publication_date_overrides(w) for w in unique.values()]
        print(f"  OpenAlex={len(oa)}, ORCID={len(orc)}, merged={len(unique)}", flush=True)

    # Do not publish a partial snapshot if a person/API request failed.
    result = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "people": per_person,
    }
    target = Path("publications_snapshot.json")
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(target)
    print("Published", target, "for", len(per_person), "academics", flush=True)


if __name__ == "__main__":
    main()
