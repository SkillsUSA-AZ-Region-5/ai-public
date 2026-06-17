#!/usr/bin/env python3
"""Generate compact SkillsUSA retrieval cards from MinerU Markdown exports."""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
from pathlib import Path


DEFAULT_SECTIONS = [
    "purpose",
    "eligibility",
    "clothing requirement",
    "clothing requirements",
    "equipment and materials",
    "prohibited devices",
    "online submission requirements",
    "scope of the competition",
    "knowledge performance",
    "skill performance",
    "competition guidelines",
]

ALIASES = {
    "Internet of Things (IOT) Smart Home": [
        "IOT",
        "IoT",
        "Internet of Things",
        "Smart Home",
        "home technology integration",
        "residential smart devices",
    ],
    "Information Technology Services": ["IT Services", "computer support", "help desk", "technician support"],
    "Cyber Security": ["cyber", "cybersecurity", "information security", "CTF"],
    "Internetworking": ["networking", "Cisco", "routers", "switches", "network configuration"],
    "Telecommunications Cabling": ["cabling", "telecom cabling", "fiber", "copper", "network cable installation"],
    "Technical Computer Applications": ["computer applications", "office applications", "technical computer apps"],
    "Interactive Application and Video Game Development": [
        "game development",
        "video game",
        "interactive application",
        "app development",
    ],
    "Occupational Health and Safety - Multiple": ["OSHA multiple", "occupational safety multiple", "team safety"],
    "Occupational Health and Safety - Single": ["OSHA single", "occupational safety single", "individual safety"],
    "Job Skill Demonstration A": ["job skill demo A", "demonstration A", "skill demonstration"],
    "Job Skill Demonstration Open": ["job skill demo open", "demonstration open", "open skill demonstration"],
    "Career Pathways Showcase - Business Management and Technology": [
        "career pathways",
        "business management",
        "business technology",
        "showcase",
    ],
    "Career Pathways Showcase - Industrial and Engineering Technology": [
        "career pathways",
        "industrial technology",
        "engineering technology",
        "showcase",
    ],
}

QUESTION_FORMS = {
    "equipment and materials": [
        "What equipment or tools do I need to bring for the {title} competition?",
        "{title} equipment",
        "{title} tools",
        "{title} supplies",
        "what to bring for {title}",
        "competitor-supplied materials for {title}",
    ],
    "clothing requirement": ["What should I wear for the {title} competition?", "{title} clothing requirement"],
    "clothing requirements": ["What should I wear for the {title} competition?", "{title} clothing requirements"],
    "eligibility": ["Who is eligible for the {title} competition?", "{title} eligibility"],
    "knowledge performance": ["What is the knowledge performance for {title}?", "{title} knowledge test"],
    "skill performance": ["What is the skill performance for {title}?", "{title} skill performance"],
    "competition guidelines": ["What are the competition guidelines for {title}?", "{title} guidelines"],
}

SECTION_TERMS = {
    "equipment and materials": "equipment tools supplies materials bring competitor-supplied required items",
    "clothing requirement": "clothing uniform wear dress attire",
    "clothing requirements": "clothing uniform wear dress attire",
    "eligibility": "eligibility eligible who can compete team size members",
    "knowledge performance": "knowledge performance knowledge test written test exam",
    "skill performance": "skill performance hands-on skills performance",
    "competition guidelines": "competition guidelines rules scoring procedure format",
    "online submission requirements": "online submission requirements resume documents upload deadline",
    "prohibited devices": "prohibited devices cellphone phone electronics not allowed",
    "scope of the competition": "scope of competition overview contest includes",
    "purpose": "purpose description overview",
}


def loose_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


ALIASES_BY_KEY = {loose_key(key): value for key, value in ALIASES.items()}


def clean_text(text: str) -> str:
    text = text.replace("\ufeff", "")
    text = re.sub(r"<details>.*?</details>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def canonical_heading(line: str) -> str | None:
    match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", line)
    if not match:
        return None
    heading = re.sub(r"[*_`]", "", match.group(1)).strip()
    return re.sub(r"\s+", " ", heading)


def normalize_heading(heading: str) -> str:
    heading = heading.lower()
    heading = heading.replace("—", "-").replace("–", "-")
    heading = re.sub(r"[^a-z0-9 &/-]+", "", heading)
    heading = re.sub(r"\s+", " ", heading).strip()
    return heading


def prettify_title(raw: str) -> str:
    raw = re.sub(r"[-_]?2024.*$", "", raw)
    raw = raw.replace("_-_", " - ").replace("_", " ")
    raw = re.sub(r"\s+", " ", raw).strip(" -")
    title = raw.title()
    replacements = {
        "Iot": "IOT",
        "Usa": "USA",
        "It ": "IT ",
        "Rj": "RJ",
        "Usb": "USB",
        "Hdmi": "HDMI",
        " And ": " and ",
        " Of ": " of ",
    }
    for old, new in replacements.items():
        title = title.replace(old, new)
    title = title.replace("Internet of Things IOT Smart Home", "Internet of Things (IOT) Smart Home")
    return title


def infer_title(path: Path, text: str) -> str:
    for part in path.parts:
        if "2024-26" in part and not part.lower().endswith(".md"):
            return prettify_title(part)
    if "2024-26" in path.stem:
        return prettify_title(path.stem)
    for line in text.splitlines():
        heading = canonical_heading(line)
        if heading and len(heading) > 3 and normalize_heading(heading) != "purpose":
            return prettify_title(heading)
    return prettify_title(path.stem)


def split_sections(text: str) -> dict[str, str]:
    lines = text.splitlines()
    sections: list[tuple[str, int, int]] = []
    heading_indexes: list[tuple[str, int]] = []
    for idx, line in enumerate(lines):
        heading = canonical_heading(line)
        if heading:
            heading_indexes.append((heading, idx))
    for pos, (heading, start) in enumerate(heading_indexes):
        end = heading_indexes[pos + 1][1] if pos + 1 < len(heading_indexes) else len(lines)
        sections.append((heading, start, end))

    out: dict[str, str] = {}
    for heading, start, end in sections:
        normalized = normalize_heading(heading)
        for wanted in DEFAULT_SECTIONS:
            if normalized == wanted or normalized.startswith(f"{wanted} "):
                body = "\n".join(lines[start:end]).strip()
                out.setdefault(wanted, body)
    return out


def find_source_markdown(source: Path) -> list[Path]:
    candidates = []
    for path in source.rglob("*.md"):
        if path.name.startswith("SkillsUSA "):
            continue
        if "knowledge-cards" in {part.lower() for part in path.parts}:
            continue
        if "extracted" not in {part.lower() for part in path.parts}:
            continue
        candidates.append(path)

    seen: set[str] = set()
    out: list[Path] = []
    for path in sorted(candidates):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        out.append(path)
    return out


def safe_filename(title: str) -> str:
    name = re.sub(r"[^\w .()&-]+", "", title, flags=re.ASCII).strip()
    name = re.sub(r"\s+", " ", name)
    return f"SkillsUSA {name} Retrieval Card.md"


def safe_section_filename(title: str, section: str) -> str:
    name = re.sub(r"[^\w .()&-]+", "", title, flags=re.ASCII).strip()
    name = re.sub(r"\s+", " ", name)
    section_name = section.title().replace(" And ", " and ")
    return f"SkillsUSA {name} - {section_name} Card.md"


def section_questions(title: str, section: str) -> str:
    forms = QUESTION_FORMS.get(section, [])
    aliases = ALIASES_BY_KEY.get(loose_key(title), [])
    if not forms:
        return ""
    expanded = [forms[0].format(title=title)]
    expanded.extend(forms[0].format(title=alias) for alias in aliases)
    return "; ".join(expanded)


def equipment_summary(body: str) -> str:
    lines = body.splitlines()
    start = None
    for idx, line in enumerate(lines):
        if "supplied by the competitor" in line.lower() or "supplied by competitor" in line.lower():
            start = idx + 1
            break
    if start is None:
        return ""

    items: list[str] = []
    for raw in lines[start:]:
        line = raw.strip()
        if not line:
            continue
        lower = line.lower()
        if lower.startswith("note:") or lower.startswith("online submission") or lower.startswith("prohibited devices"):
            break
        line = re.sub(r"^\s*(?:[a-z]|\d+|[ivx]+)[.)]\s*", "", line, flags=re.IGNORECASE)
        line = re.sub(r"\s+", " ", line).strip(" -")
        if line:
            items.append(line)
    summary = "; ".join(items)
    return summary[:1000].rstrip(" ;")


def render_card(title: str, source_path: Path, sections: dict[str, str]) -> str:
    aliases = ALIASES_BY_KEY.get(loose_key(title), [])
    alias_text = ", ".join(aliases) if aliases else "None"
    parts = [
        f"# SkillsUSA {title} Retrieval Card",
        "",
        f"Contest: {title}",
        f"Aliases: {alias_text}",
        f"Source document: {source_path.name}",
        "",
        "Use this generated card to route questions to the matching SkillsUSA contest before answering from the source text.",
        "Ignore chunks from other contests when this contest title or one of its aliases matches the user question.",
        "",
    ]
    for section in DEFAULT_SECTIONS:
        body = sections.get(section)
        if not body:
            continue
        label = section.title()
        parts.extend([f"## {label}", ""])
        parts.extend([body.strip(), ""])
    return "\n".join(parts).strip() + "\n"


def render_section_card(title: str, source_path: Path, section: str, body: str) -> str:
    aliases = ALIASES_BY_KEY.get(loose_key(title), [])
    alias_text = ", ".join(aliases) if aliases else "None"
    label = section.title().replace(" And ", " and ")
    query_terms = f"{title}; {alias_text}; {label}; {SECTION_TERMS.get(section, section)}"
    forms = QUESTION_FORMS.get(section, [])
    question_titles = [title] + aliases[:3]
    question_text = "; ".join(forms[0].format(title=value) for value in question_titles) if forms else ""
    body_text = re.sub(r"^\s*#{1,6}\s+.+?\n+", "", body.strip(), count=1)
    parts = [
        f"# SkillsUSA {title} - {label} Card",
        "",
    ]
    if section == "equipment and materials":
        summary = equipment_summary(body_text)
        if summary:
            parts.extend([f"Equipment/tools answer for {title}: competitor must bring {summary}.", ""])
    parts.extend([
        f"Aliases: {alias_text}.",
        f"Question match: {question_text}." if question_text else f"Question type: {SECTION_TERMS.get(section, section)}.",
        "",
    ])
    parts.extend([
        body_text,
        "",
        "---",
        "",
        f"Contest: {title}",
        f"Aliases: {alias_text}",
        f"Section: {label}",
        f"Source document: {source_path.name}",
        "",
        "Use this generated section card when the user asks about this exact contest and section.",
        "Ignore same-topic chunks from other contests unless the user explicitly asks for that other contest.",
        "",
        f"Query terms: {query_terms}.",
        "",
    ])
    return "\n".join(parts).strip() + "\n"


def generate(source: Path, output: Path, clean: bool) -> int:
    if clean and output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)

    count = 0
    for path in find_source_markdown(source):
        text = clean_text(path.read_text(encoding="utf-8", errors="replace"))
        title = infer_title(path, text)
        sections = split_sections(text)
        if not sections:
            continue
        for section in DEFAULT_SECTIONS:
            body = sections.get(section)
            if not body:
                continue
            section_card = render_section_card(title, path, section, body)
            (output / safe_section_filename(title, section)).write_text(section_card, encoding="utf-8", newline="\n")
            count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate SkillsUSA retrieval cards from extracted Markdown.")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parent / "output")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / "knowledge-cards-generated")
    parser.add_argument("--clean", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    count = generate(args.source.resolve(), args.output.resolve(), args.clean)
    print(f"Generated {count} SkillsUSA retrieval card(s) in {args.output.resolve()}")
    return 0 if count else 1


if __name__ == "__main__":
    raise SystemExit(main())
