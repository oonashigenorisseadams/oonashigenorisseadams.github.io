#!/usr/bin/env python3
"""Update the Publications section in index.md from Google Scholar profile data.

Falls back to Semantic Scholar API when Google Scholar is unreachable (e.g. in
GitHub Actions where datacenter IPs are blocked).

Usage:
  python3 scripts/update_publications.py
  python3 scripts/update_publications.py --config scripts/scholar_sync_config.json
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import urllib.error
from urllib.parse import urlparse
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class Publication:
    title: str
    authors: str
    venue: str
    year: str
    citation_path: str


def _clean_html_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _parse_rows(page_html: str) -> List[Publication]:
    rows = re.findall(r"<tr[^>]*\bgsc_a_tr\b[^>]*>[\s\S]*?</tr>", page_html, flags=re.IGNORECASE)
    out: List[Publication] = []

    for row in rows:
        anchor_match = re.search(
            r"<a[^>]*\bgsc_a_at\b[^>]*>[\s\S]*?</a>",
            row,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if not anchor_match:
            continue

        anchor_html = anchor_match.group(0)
        href_match = re.search(
            r"\bhref\s*=\s*(?:\"([^\"]+)\"|'([^']+)'|([^'\"\s>]+))",
            anchor_html,
            flags=re.IGNORECASE,
        )
        if not href_match:
            continue

        href = href_match.group(1) or href_match.group(2) or href_match.group(3) or ""
        citation_path = html.unescape(href.strip())
        title = _clean_html_text(anchor_html)

        gray_fields = re.findall(
            r"<div[^>]*\bgs_gray\b[^>]*>(.*?)</div>",
            row,
            flags=re.DOTALL | re.IGNORECASE,
        )
        authors = _clean_html_text(gray_fields[0]) if len(gray_fields) > 0 else ""
        venue = _clean_html_text(gray_fields[1]) if len(gray_fields) > 1 else ""

        year_match = re.search(r">(19|20)\d{2}<", row)
        if year_match:
            year = year_match.group(0).strip("><")
        else:
            year = ""

        out.append(
            Publication(
                title=title,
                authors=authors,
                venue=venue,
                year=year,
                citation_path=citation_path,
            )
        )

    return out


def _looks_blocked(page_html: str) -> Optional[str]:
    checks = {
        "consent.google.com": "Google consent page returned",
        "unusual traffic": "Google blocked automated traffic",
        "detected unusual traffic": "Google blocked automated traffic",
        "recaptcha": "CAPTCHA challenge page returned",
        "/sorry/": "Google \"sorry\" page returned",
    }
    lower = page_html.lower()
    for needle, reason in checks.items():
        if needle in lower:
            return reason
    return None


def fetch_publications(user_id: str, hl: str = "en", page_size: int = 100) -> List[Publication]:
    publications: List[Publication] = []
    seen_paths = set()

    for offset in range(0, 1000, page_size):
        query = urllib.parse.urlencode(
            {
                "user": user_id,
                "hl": hl,
                "cstart": offset,
                "pagesize": page_size,
                "view_op": "list_works",
                "sortby": "pubdate",
            }
        )
        url = f"https://scholar.google.com/citations?{query}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                )
            },
        )

        with urllib.request.urlopen(req, timeout=20) as response:
            final_url = response.geturl()
            page_html = response.read().decode("utf-8", errors="ignore")
        host = (urlparse(final_url).hostname or "").lower()
        if "scholar.google.com" not in host:
            raise RuntimeError(f"Unexpected redirect host ({host or 'unknown'}). URL: {final_url}")

        blocked_reason = _looks_blocked(page_html)
        if blocked_reason:
            raise RuntimeError(f"{blocked_reason}. URL: {url}")

        page_pubs = _parse_rows(page_html)
        if not page_pubs:
            if offset == 0:
                profile_marker = "citations?user=" in page_html.lower()
                if not profile_marker:
                    raise RuntimeError(
                        "Google Scholar page format did not match expected publication list markup. "
                        "Try opening your profile URL in a browser and confirm it is public."
                    )
            break

        new_count = 0
        for pub in page_pubs:
            if pub.citation_path in seen_paths:
                continue
            seen_paths.add(pub.citation_path)
            publications.append(pub)
            new_count += 1

        if new_count == 0 or len(page_pubs) < page_size:
            break

    return publications


def fetch_publications_semantic_scholar(author_ids: List[str]) -> List[Publication]:
    """Fetch publications from the Semantic Scholar API for the given author IDs."""
    seen_paper_ids: set = set()
    publications: List[Publication] = []
    fields = "title,year,venue,authors,externalIds,url"

    for author_id in author_ids:
        api_url = (
            f"https://api.semanticscholar.org/graph/v1/author/{author_id}/papers"
            f"?fields={fields}&limit=100"
        )
        req = urllib.request.Request(api_url, headers={"User-Agent": "scholar-sync/1.0"})
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))

        for item in data.get("data", []):
            paper_id = item.get("paperId", "")
            if not paper_id or paper_id in seen_paper_ids:
                continue
            seen_paper_ids.add(paper_id)

            title = item.get("title", "")
            if not title:
                continue

            year = str(item.get("year", "")) if item.get("year") else ""
            venue = item.get("venue", "") or ""

            author_names = []
            for author in item.get("authors", []):
                name = author.get("name", "")
                if name:
                    author_names.append(name)
            authors = ", ".join(author_names)

            external_ids = item.get("externalIds") or {}
            doi = external_ids.get("DOI", "")
            if doi:
                citation_path = f"https://doi.org/{doi}"
            else:
                citation_path = item.get("url", "")

            publications.append(
                Publication(
                    title=title,
                    authors=authors,
                    venue=venue,
                    year=year,
                    citation_path=citation_path,
                )
            )

        # Respect rate limits between author ID requests.
        if len(author_ids) > 1:
            time.sleep(1)

    # Sort by year descending (newest first), matching Google Scholar's sortby=pubdate.
    publications.sort(key=lambda p: int(p.year) if p.year.isdigit() else 0, reverse=True)
    return publications


def _normalize_key(value: str) -> str:
    value = html.unescape(value).strip().lower()
    value = value.replace("ﬁ", "fi").replace("ﬂ", "fl")
    value = re.sub(r"\s+", " ", value)
    return value


def _to_initial(token: str) -> str:
    """Convert a name token to initial form: 'Paul' -> 'P.', 'OS' -> 'O.S.'."""
    token = token.rstrip(".")
    if not token:
        return ""
    # Concatenated uppercase initials (e.g. "OS", "KM", "CL")
    if token.isupper() and len(token) <= 4:
        return ".".join(token) + "."
    # Already a single initial
    if len(token) == 1:
        return token.upper() + "."
    # Full name — take first letter
    return token[0].upper() + "."


def _reformat_author_name(name: str, overrides: Optional[Dict[str, str]] = None) -> str:
    """Convert 'First Last' to 'Last, F.' format. Names already in 'Last, First' are unchanged."""
    name = name.strip()
    if not name or name in ("...", "\u2026"):
        return "..."
    # Check explicit overrides first (e.g. fix bad Scholar metadata).
    if overrides and name in overrides:
        return overrides[name]
    # Already in "Last, First" format
    if "," in name:
        return name
    tokens = name.split()
    if len(tokens) < 2:
        return name
    last_name = tokens[-1]
    initials = "".join(_to_initial(t) for t in tokens[:-1])
    return f"{last_name}, {initials}"


def _format_authors(
    authors: str,
    target_last_name: Optional[str],
    author_name_overrides: Optional[Dict[str, str]] = None,
) -> str:
    if not authors:
        return ""

    parts = [p.strip() for p in authors.split(",")]
    # Reformat "First Last" -> "Last, Initials." for auto-fetched names.
    # Names already in "Last, First" format pass through unchanged.
    parts = [_reformat_author_name(p, author_name_overrides) for p in parts if p.strip()]

    needle = (target_last_name or "").lower().strip()
    highlighted = []
    for part in parts:
        if needle and needle in part.lower():
            highlighted.append(f"**{part}**")
        else:
            highlighted.append(part)

    return ", ".join(highlighted)


def _ensure_sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    if text.endswith("."):
        return text
    return text + "."


def _normalize_venue(venue: str) -> str:
    venue = venue.strip()
    if not venue:
        return ""

    lower = venue.lower()
    if lower.startswith("medrxiv"):
        return "medRxiv"
    if lower.startswith("biorxiv"):
        return "bioRxiv"

    # Remove trailing ", YYYY" which duplicates the explicit year we already print.
    venue = re.sub(r",\s*(19|20)\d{2}\s*$", "", venue).strip()
    return venue


def _slug_token(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    ascii_value = ascii_value.lower().strip()
    return re.sub(r"[^a-z0-9]+", "", ascii_value)


def _first_author_last_name_slug(authors: str) -> str:
    if not authors:
        return ""
    first_author = authors.split(",")[0].strip().replace("...", "")
    tokens = [t for t in re.split(r"\s+", first_author) if t]
    if not tokens:
        return ""
    last_name = re.sub(r"[^A-Za-z0-9-]", "", tokens[-1])
    return _slug_token(last_name)


def _resolve_pdf_link(pub: Publication, override_map: Dict[str, str], assets_dir: Path) -> Optional[str]:
    override_link = override_map.get(_normalize_key(pub.title))
    if override_link:
        return override_link

    if not pub.year.isdigit():
        return None

    last_name_slug = _first_author_last_name_slug(pub.authors)
    if not last_name_slug:
        return None

    candidate = assets_dir / f"{last_name_slug}_{pub.year}.pdf"
    if candidate.exists():
        return candidate.as_posix()

    return None


def _link_for_publication(
    pub: Publication,
    override_map: Dict[str, str],
    assets_dir: Path,
    include_scholar_fallback: bool,
) -> str:
    pdf_link = _resolve_pdf_link(pub, override_map, assets_dir)
    if pdf_link:
        return f"[[PDF]]({pdf_link})"

    if not include_scholar_fallback:
        return ""

    # Full URLs (e.g. from Semantic Scholar / DOI) are used directly;
    # relative paths are joined with the Google Scholar base.
    if pub.citation_path.startswith("http"):
        return f"[[Link]]({pub.citation_path})"

    scholar_url = urllib.parse.urljoin("https://scholar.google.com", pub.citation_path)
    return f"[[Scholar]]({scholar_url})"


def _build_markdown_list(
    publications: List[Publication],
    target_last_name: Optional[str],
    pdf_overrides: Dict[str, str],
    assets_dir: Path,
    include_scholar_fallback: bool,
    author_name_overrides: Optional[Dict[str, str]] = None,
) -> List[str]:
    override_map = {_normalize_key(k): v for k, v in pdf_overrides.items()}

    lines = ["## Publications"]
    for i, pub in enumerate(publications, start=1):
        text = _publication_entry_text(pub, target_last_name, author_name_overrides)
        venue = _normalize_venue(pub.venue)
        if venue and not text.endswith(_ensure_sentence(venue)):
            text += f" {_ensure_sentence(venue)}"

        link = _link_for_publication(pub, override_map, assets_dir, include_scholar_fallback)
        suffix = f" {link}" if link else ""
        lines.append(f"{i}. {text}{suffix}")

    return lines


def _publication_entry_text(
    pub: Publication,
    target_last_name: Optional[str],
    author_name_overrides: Optional[Dict[str, str]] = None,
) -> str:
    authors = _format_authors(pub.authors, target_last_name, author_name_overrides)
    year_part = f", {pub.year}" if pub.year else ""

    text = ""
    if authors:
        text += f"{authors}{year_part}. "
    elif pub.year:
        text += f"{pub.year}. "

    text += f"{_ensure_sentence(pub.title)}"
    venue = _normalize_venue(pub.venue)
    if venue:
        text += f" {_ensure_sentence(venue)}"
    return text


def _refresh_recent_existing_items(
    existing_items: List[str],
    publications: List[Publication],
    target_last_name: Optional[str],
    min_year: int,
    override_map: Dict[str, str],
    assets_dir: Path,
    include_scholar_fallback: bool,
) -> List[str]:
    recent_pubs = []
    for pub in publications:
        year = int(pub.year) if pub.year.isdigit() else 0
        if year >= min_year:
            recent_pubs.append(pub)

    refreshed: List[str] = []
    for line in existing_items:
        if not re.match(r"^\s*\d+\.\s+", line):
            refreshed.append(line)
            continue

        norm_line = _normalize_key(line)
        matched_pub: Optional[Publication] = None
        for pub in recent_pubs:
            if _normalize_key(pub.title) in norm_line:
                matched_pub = pub
                break

        if not matched_pub:
            refreshed.append(line)
            continue

        text = _publication_entry_text(matched_pub, target_last_name)
        link = _link_for_publication(matched_pub, override_map, assets_dir, include_scholar_fallback)
        suffix = f" {link}" if link else ""
        refreshed.append(f"1. {text}{suffix}")

    return refreshed


def _section_bounds(markdown: str, heading: str) -> Tuple[int, int]:
    pattern = re.compile(rf"(^\s*{re.escape(heading)}\s*$)", flags=re.MULTILINE)
    matches = list(pattern.finditer(markdown))
    if not matches:
        raise ValueError(f"Heading not found: {heading}")
    # Use the last matching heading to avoid accidental stale/duplicate sections earlier in file.
    match = matches[-1]

    section_start = match.start(1)
    post_heading_start = match.end(1)

    next_heading = re.search(r"^\s*##\s+.+$", markdown[post_heading_start:], flags=re.MULTILINE)
    section_end = post_heading_start + next_heading.start() if next_heading else len(markdown)
    return section_start, section_end


def replace_section(markdown: str, heading: str, new_section_lines: List[str]) -> str:
    section_start, section_end = _section_bounds(markdown, heading)

    before = markdown[:section_start].rstrip("\n")
    after = markdown[section_end:].lstrip("\n")
    new_section = "\n".join(new_section_lines).rstrip() + "\n"

    if after:
        return f"{before}\n\n{new_section}\n{after}"
    return f"{before}\n\n{new_section}"


def _get_existing_section_lines(markdown: str, heading: str) -> List[str]:
    section_start, section_end = _section_bounds(markdown, heading)
    section_text = markdown[section_start:section_end].strip("\n")
    lines = section_text.splitlines()
    if not lines:
        return []
    return lines


def _existing_title_index(lines: List[str]) -> str:
    # Keep existing lines untouched; use a simple normalized bag of text for dedup checks.
    return _normalize_key(" ".join(lines))


def _build_incremental_section(
    existing_section_lines: List[str],
    publications: List[Publication],
    target_last_name: Optional[str],
    pdf_overrides: Dict[str, str],
    min_year: int,
    assets_dir: Path,
    include_scholar_fallback: bool,
    author_name_overrides: Optional[Dict[str, str]] = None,
) -> List[str]:
    if existing_section_lines:
        heading = existing_section_lines[0]
        existing_items = existing_section_lines[1:]
    else:
        heading = "## Publications"
        existing_items = []

    existing_text_index = _existing_title_index(existing_items)
    new_items: List[str] = []

    override_map = {_normalize_key(k): v for k, v in pdf_overrides.items()}
    for pub in publications:
        year = int(pub.year) if pub.year.isdigit() else 0
        if year < min_year:
            continue

        if _normalize_key(pub.title) in existing_text_index:
            continue

        text = _publication_entry_text(pub, target_last_name, author_name_overrides)

        link = _link_for_publication(pub, override_map, assets_dir, include_scholar_fallback)
        suffix = f" {link}" if link else ""
        new_items.append(f"1. {text}{suffix}")
        existing_text_index += " " + _normalize_key(pub.title)

    return [heading] + new_items + existing_items


def _renumber_publication_list(section_lines: List[str]) -> List[str]:
    if not section_lines:
        return section_lines

    renumbered = [section_lines[0]]
    n = 1
    for line in section_lines[1:]:
        if re.match(r"^\s*\d+\.\s+", line):
            line = re.sub(r"^\s*\d+\.\s+", f"{n}. ", line, count=1)
            n += 1
        renumbered.append(line)
    return renumbered


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update Publications section from Google Scholar.")
    parser.add_argument("--config", default="scripts/scholar_sync_config.json", help="Path to JSON config file.")
    parser.add_argument(
        "--debug-html",
        default="",
        help="Optional path to save first fetched Scholar HTML page for debugging.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Config file not found: {config_path}", file=sys.stderr)
        return 1

    with config_path.open("r", encoding="utf-8") as f:
        config = json.load(f)

    user_id = config.get("scholar_user_id")
    if not user_id:
        print("Config must include scholar_user_id", file=sys.stderr)
        return 1

    hl = config.get("hl", "en")
    target_last_name = config.get("target_author_last_name")
    index_file = Path(config.get("index_file", "index.md"))
    section_heading = config.get("section_heading", "## Publications")
    pdf_overrides = config.get("pdf_overrides", {})
    min_year = int(config.get("min_year", 0))
    assets_dir = Path(config.get("assets_dir", "assets"))
    include_scholar_fallback = bool(config.get("include_scholar_fallback", False))
    author_name_overrides = config.get("author_name_overrides", {}) or {}

    if args.debug_html:
        query = urllib.parse.urlencode(
            {
                "user": user_id,
                "hl": hl,
                "cstart": 0,
                "pagesize": 100,
                "view_op": "list_works",
                "sortby": "pubdate",
            }
        )
        debug_url = f"https://scholar.google.com/citations?{query}"
        debug_req = urllib.request.Request(
            debug_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                )
            },
        )
        try:
            with urllib.request.urlopen(debug_req, timeout=20) as response:
                raw = response.read().decode("utf-8", errors="ignore")
            Path(args.debug_html).write_text(raw, encoding="utf-8")
            print(f"Saved debug HTML to {args.debug_html}")
        except Exception as exc:
            print(f"Could not save debug HTML: {exc}", file=sys.stderr)

    s2_author_ids = config.get("semantic_scholar_author_ids", [])
    source = "Google Scholar"

    try:
        publications = fetch_publications(user_id=user_id, hl=hl)
    except (urllib.error.URLError, RuntimeError) as exc:
        print(f"Google Scholar fetch failed: {exc}", file=sys.stderr)
        if s2_author_ids:
            print("Falling back to Semantic Scholar API...", file=sys.stderr)
            try:
                publications = fetch_publications_semantic_scholar(s2_author_ids)
                source = "Semantic Scholar"
            except Exception as s2_exc:
                print(f"Semantic Scholar fallback also failed: {s2_exc}", file=sys.stderr)
                return 1
        else:
            return 1

    if not publications:
        print(
            f"No publications found from {source}. "
            "Check config IDs and confirm profile is public.",
            file=sys.stderr,
        )
        return 1

    markdown = index_file.read_text(encoding="utf-8")
    existing_section_lines = _get_existing_section_lines(markdown, section_heading)
    if min_year > 0:
        new_section_lines = _build_incremental_section(
            existing_section_lines=existing_section_lines,
            publications=publications,
            target_last_name=target_last_name,
            pdf_overrides=pdf_overrides,
            min_year=min_year,
            assets_dir=assets_dir,
            include_scholar_fallback=include_scholar_fallback,
            author_name_overrides=author_name_overrides,
        )
    else:
        new_section_lines = _build_markdown_list(
            publications,
            target_last_name,
            pdf_overrides,
            assets_dir,
            include_scholar_fallback,
            author_name_overrides=author_name_overrides,
        )
    new_section_lines = _renumber_publication_list(new_section_lines)
    updated = replace_section(markdown, section_heading, new_section_lines)
    index_file.write_text(updated, encoding="utf-8")

    print(f"Updated {section_heading} in {index_file} with {len(publications)} items (source: {source}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
