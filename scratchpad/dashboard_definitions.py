"""Turn the dashboard file's tab definitions into a knowledge document the model builder can read.

Reads every `D.add({...})` tab in the dashboard HTML and writes its name, question, widget titles, notes (`sub`),
query descriptions (`q`), KPI labels, band definitions, lens tabs and journey-leg labels as markdown. Old column names
(`current_state`, `on_hold_date`, ...) are translated to the Dataverse names with the glossary's "Was" column.

Usage: python dashboard_definitions.py ni-pmo-dashboards.html TelaGlossary2.md out.md
"""

from __future__ import annotations

import re
import sys

STR = r"'((?:[^'\\]|\\.)*)'"


def unq(s: str) -> str:
    return s.replace("\\'", "'").replace("\\n", " ")


def glossary_map(path: str) -> dict[str, dict[str, str]]:
    """old table → {old column → new column}, from glossary sections '(was `old_table`)' and rows
    `| new | meaning | holds | old |`."""
    out: dict[str, dict[str, str]] = {}
    table = ""
    for line in open(path, encoding="utf-8"):
        if line.startswith("### "):
            m = re.search(r"was `([a-z_]+)`", line)
            table = m.group(1) if m else line
        cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
        if len(cells) == 4 and re.match(r"^[a-z_0-9]+$", cells[0]) and re.match(r"^[a-z_0-9]+$", cells[3]):
            out.setdefault(table, {}).setdefault(cells[3], cells[0])
    return out


def translate(text: str, names: dict[str, dict[str, str]]) -> str:
    """Old column names in a line, translated with the table the line talks about first."""
    first = ["project_requests", "projects"] if "request" in text.lower() else ["projects", "project_requests"]
    order = first + [t for t in names if t not in first]

    def one(m: re.Match[str]) -> str:
        for t in order:
            if m.group(0) in names.get(t, {}):
                return names[t][m.group(0)]
        return m.group(0)
    return re.sub(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b", one, text)


def buckets(src: str, var: str) -> str | None:
    m = re.search(rf"var {re.escape(var)} = (\[[^\]]*\])", src)
    if not m:
        return None
    labels = re.findall(r"label: " + STR + r"(?:, lo: (\d+), hi: (\d+))?", m.group(1))
    parts = []
    for lab, lo, hi in labels:
        parts.append(f"{unq(lab)} ({lo} to {hi} days)" if lo else unq(lab))
    return "; ".join(parts)


def main(html_path: str, glossary_path: str, out_path: str) -> None:
    src = open(html_path, encoding="utf-8").read()
    names = glossary_map(glossary_path)
    starts = [m.start() for m in re.finditer(r"\n  D\.add\(\{", src)]
    md = ["# PMO dashboard tab definitions",
          "",
          "Each section is one dashboard tab: what it shows, how each widget counts, and its bands. Column names are "
          "the Dataverse names. 'Open projects' and 'open requests' follow the rules document.", ""]
    for i, st in enumerate(starts):
        block = src[st: starts[i + 1] if i + 1 < len(starts) else st + 40000]
        name = re.search(r"name: " + STR, block)
        if not name:
            continue
        md.append(f"## {unq(name.group(1))}")
        for key, label in (("question", "Projects view asks"), ("questionR", "Requests view asks")):
            m = re.search(rf"\b{key}: " + STR, block)
            if m:
                md.append(f"- {label}: {unq(m.group(1))}")
        seen: set[str] = set()
        for m in re.finditer(r"title: " + STR + r"(.{0,900})", block):
            title, rest = unq(m.group(1)), m.group(2)
            sub = re.search(r"sub: " + STR, rest)
            q = re.search(r"\bq: " + STR, rest)
            bk = re.search(r"buckets: ([A-Za-z]+)\b", rest)
            line = f"- Widget '{title}'"
            if sub:
                line += f": {unq(sub.group(1))}"
            if q:
                line += f" Query: {unq(q.group(1))}"
            if bk and (b := buckets(src, bk.group(1))):
                line += f" Bands: {b}."
            inline = re.search(r"buckets: (\[\{.*?\}\])", rest)
            if inline:
                labs = [unq(x) for x in re.findall(r"label: " + STR, inline.group(1))]
                line += f" Bands: {'; '.join(labs)}."
            if line not in seen:
                seen.add(line)
                md.append(translate(line, names))
        for m in re.finditer(r"kv\(ctx, " + STR + r", " + STR + r"(.{0,600}?)\bq: " + STR, block):
            md.append(translate(f"- KPI '{unq(m.group(2))}': {unq(m.group(4))}", names))
        for m in re.finditer(r"countKpi\(" + STR + r", " + STR + r", [^,]+, [^,]+, " + STR, block):
            md.append(translate(f"- KPI '{unq(m.group(2))}': open projects that {unq(m.group(3))}", names))
        legs = re.findall(r"leg\(" + STR, block)
        if legs:
            md.append("- Journey legs: " + "; ".join(dict.fromkeys(unq(x) for x in legs)))
        if "lensTabsP" in block or "lensTabsR" in block:
            tabs = re.search(r"var lensTabsP = \[(.*?)\];", src, re.S)
            if tabs:
                for lab, q in re.findall(r"label: " + STR + r".*?\bq: " + STR, tabs.group(1), re.S):
                    md.append(translate(f"- Lens tab '{unq(lab)}': {unq(q)}", names))
        md.append("")
    open(out_path, "w", encoding="utf-8").write("\n".join(md))
    print(f"{len(starts)} tabs, {sum(1 for x in md if x.startswith('- '))} lines → {out_path}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
