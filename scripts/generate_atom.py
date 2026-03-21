#!/usr/bin/env python3
"""Generate an Atom 1.0 feed from the events table in index.markdown."""

import argparse
import re
import subprocess
import sys
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

SITE_URL = "https://southbaysystems.xyz"
FEED_URL = f"{SITE_URL}/atom.xml"
FEED_TITLE = "South Bay Systems"
FEED_SUBTITLE = "A community tech talk series on systems programming"
SOURCE_FILE = "index.markdown"


def get_blame_timestamps(path):
    """Return a dict of 1-based line number -> unix timestamp from git blame."""
    result = subprocess.run(
        ["git", "blame", "--line-porcelain", path],
        capture_output=True, text=True, check=True
    )
    timestamps = {}
    current_line = None
    for line in result.stdout.splitlines():
        # Header line: <sha> <orig-line> <final-line> [<num-lines>]
        header = re.match(r'^[0-9a-f]{40} \d+ (\d+)', line)
        if header:
            current_line = int(header.group(1))
        elif line.startswith("author-time ") and current_line is not None:
            timestamps[current_line] = int(line.split()[1])
    return timestamps


def parse_links(text):
    """Return list of (display_text, url_or_None) for all markdown links in text.

    Splits only on <br> or ' & ' that appear *between* links (not inside them).
    Uses regex to find all [text](url) patterns, so & inside link text is preserved.
    """
    results = []
    # Find all markdown links; collect their spans
    link_pattern = re.compile(r'\[([^\]]*)\]\(([^)]*)\)')
    last_end = 0
    for m in link_pattern.finditer(text):
        # Check for plain-text between previous match and this one
        gap = text[last_end:m.start()].strip().strip('&').strip('<br />').strip('<br>').strip()
        if gap:
            results.append((gap, None))
        results.append((m.group(1), m.group(2)))
        last_end = m.end()
    # Trailing plain text
    tail = text[last_end:].strip().strip('&').strip('<br />').strip('<br>').strip()
    if tail:
        results.append((tail, None))
    return results


def make_absolute(url):
    if url and url.startswith("/"):
        return SITE_URL + url
    return url


def to_rfc3339(unix_ts):
    dt = datetime.fromtimestamp(unix_ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_table(source_file, blame_map):
    entries = []
    with open(source_file, encoding="utf-8") as f:
        lines = f.readlines()

    in_table = False
    for lineno, raw in enumerate(lines, start=1):
        line = raw.rstrip("\n")
        if not line.startswith("|"):
            in_table = False
            continue
        # Detect header separator row
        if re.match(r'^\|\s*[-:]+\s*\|', line):
            in_table = True
            continue
        # Skip the header row (contains "Date", "Event", etc.)
        if not in_table:
            continue

        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cols) < 4:
            continue

        date_col, event_col, speaker_col, artifacts_col = cols[0], cols[1], cols[2], cols[3]

        # Parse event: may have multiple links separated by <br>
        event_links = parse_links(event_col)
        # Use the first event link as the canonical entry link/id
        if event_links:
            event_title, event_url = event_links[0]
        else:
            event_title, event_url = event_col, None

        # Speakers
        speaker_links = parse_links(speaker_col)
        speaker_names = " & ".join(name for name, _ in speaker_links)

        # Build HTML content
        content_parts = []
        # Date
        content_parts.append(f"<p><strong>{date_col}</strong></p>")
        # All event links
        for etitle, eurl in event_links:
            if eurl:
                eurl_abs = make_absolute(eurl)
                content_parts.append(f'<p>Event: <a href="{eurl_abs}">{etitle}</a></p>')
            else:
                content_parts.append(f"<p>Event: {etitle}</p>")
        # Speakers
        sp_html_parts = []
        for sname, surl in speaker_links:
            if surl:
                surl_abs = make_absolute(surl)
                sp_html_parts.append(f'<a href="{surl_abs}">{sname}</a>')
            else:
                sp_html_parts.append(sname)
        content_parts.append("<p>Speakers: " + " &amp; ".join(sp_html_parts) + "</p>")
        # Artifacts
        artifact_links = parse_links(artifacts_col)
        if any(url for _, url in artifact_links):
            art_html_parts = []
            for aname, aurl in artifact_links:
                if aurl:
                    aurl_abs = make_absolute(aurl)
                    art_html_parts.append(f'<a href="{aurl_abs}">{aname}</a>')
                else:
                    art_html_parts.append(aname)
            content_parts.append("<p>Artifacts: " + " &amp; ".join(art_html_parts) + "</p>")

        content_html = "\n".join(content_parts)

        ts = blame_map.get(lineno, 0)

        entries.append({
            "title": event_title,
            "url": make_absolute(event_url) if event_url else SITE_URL,
            "updated_ts": ts,
            "summary": speaker_names,
            "content": content_html,
        })

    return entries


def build_feed(entries):
    ET.register_namespace("", "http://www.w3.org/2005/Atom")
    feed = ET.Element("{http://www.w3.org/2005/Atom}feed")

    def sub(parent, tag, text=None, **attrib):
        el = ET.SubElement(parent, f"{{http://www.w3.org/2005/Atom}}{tag}", **attrib)
        if text is not None:
            el.text = text
        return el

    sub(feed, "title", FEED_TITLE)
    sub(feed, "subtitle", FEED_SUBTITLE)
    sub(feed, "link", href=SITE_URL + "/")
    sub(feed, "link", rel="self", href=FEED_URL)
    sub(feed, "id", FEED_URL)
    author = sub(feed, "author")
    sub(author, "name", "South Bay Systems")

    sorted_entries = sorted(entries, key=lambda e: e["updated_ts"], reverse=True)
    feed_updated = to_rfc3339(sorted_entries[0]["updated_ts"]) if sorted_entries else to_rfc3339(0)
    sub(feed, "updated", feed_updated)

    for e in sorted_entries:
        entry = sub(feed, "entry")
        sub(entry, "title", e["title"])
        sub(entry, "link", href=e["url"])
        sub(entry, "id", e["url"])
        sub(entry, "updated", to_rfc3339(e["updated_ts"]))
        sub(entry, "summary", e["summary"])
        content_el = sub(entry, "content", type="html")
        content_el.text = e["content"]

    return feed


def main():
    parser = argparse.ArgumentParser(description="Generate Atom feed from index.markdown")
    parser.add_argument("--output", default="_site/atom.xml")
    args = parser.parse_args()

    blame_map = get_blame_timestamps(SOURCE_FILE)
    entries = parse_table(SOURCE_FILE, blame_map)

    if not entries:
        print("No entries found in table.", file=sys.stderr)
        sys.exit(1)

    feed = build_feed(entries)
    tree = ET.ElementTree(feed)
    ET.indent(tree, space="  ")

    with open(args.output, "wb") as f:
        f.write(b'<?xml version="1.0" encoding="utf-8"?>\n')
        tree.write(f, encoding="utf-8", xml_declaration=False)

    print(f"Written {len(entries)} entries to {args.output}")


if __name__ == "__main__":
    main()
