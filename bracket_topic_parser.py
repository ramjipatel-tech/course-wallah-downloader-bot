import os
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple, Set

from academic_parser import (
    AcademicItem,
    AcademicTopic,
    AcademicUnit,
    AcademicCourse,
    normalize_title,
    detect_subject_and_course
)
from utils import MediaRouter, MediaType

# ==============================================================================
# 🎯 DATA MODEL FOR BRACKET TOPIC FORMAT (FORMAT C)
# ==============================================================================

FIRST_TOPIC_RE = re.compile(r"^\s*\[([^\]]+)\]\s*(.*)$")


@dataclass
class TxtResource:
    topic: str
    title: str
    url: str
    authorized_key: Optional[str] = None
    media_type: Optional[str] = None
    sequence: int = 1


# ==============================================================================
# 🧹 HELPERS & PARSERS
# ==============================================================================

def sanitize_topic_name(name: str) -> str:
    """
    Sanitize topic name for safe Telegram Forum Topic creation.
    Normalizes multi-whitespace and limits length to 120 chars without destroying title.
    """
    if not name:
        return ""
    cleaned = re.sub(r"\s+", " ", str(name)).strip()
    return cleaned[:120]


def parse_first_topic(line: str) -> Optional[Tuple[str, str]]:
    """
    Extract only the FIRST [...] block.

    Example:
    [Linear Algebra] Lecture 1A - Why Study Linear Algebra : URL

    Returns:
        topic = "Linear Algebra"
        remainder = "Lecture 1A - Why Study Linear Algebra : URL"
    """
    if not line or not isinstance(line, str):
        return None

    clean_line = line.strip()
    if not clean_line:
        return None

    match = FIRST_TOPIC_RE.match(clean_line)
    if not match:
        return None

    raw_topic = match.group(1).strip()
    remainder = match.group(2).strip()

    # Reject empty or whitespace-only brackets like [] or [   ]
    if not raw_topic:
        return None

    # Normalize whitespace inside topic (e.g. "[ Linear Algebra ]" -> "Linear Algebra")
    topic = re.sub(r"\s+", " ", raw_topic).strip()
    if not topic:
        return None

    return topic, remainder


def is_bracket_topic_format(text: str) -> bool:
    """
    Detects if the text matches the [TOPIC] Title : URL format.
    Ensures legacy and structured batch formats are not misidentified.
    """
    if not text or not text.strip():
        return False

    # Guard: If structured batch markers are present, structured parser takes priority
    if any(k in text for k in ("BATCH DETAILS", "TOPIC SUMMARY", "LINK SUMMARY", "CONTENT:")):
        return False

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    bracket_resource_count = 0
    reserved_headers = {"subject", "course", "home", "batch", "instructor", "thumbnail", "price", "id"}

    for line in lines:
        parsed = parse_first_topic(line)
        if parsed:
            topic, remainder = parsed
            if topic.lower() in reserved_headers:
                continue
            if re.search(r"https?://", remainder):
                bracket_resource_count += 1

    return bracket_resource_count > 0


def parse_bracket_topic_raw(file_content: str) -> List[TxtResource]:
    """
    Parses a bracket topic text file into a sequential list of normalized TxtResource objects.
    Preserves exact TXT file order.
    """
    resources: List[TxtResource] = []
    lines = file_content.splitlines()
    seq = 1
    seen_dedup: Set[Tuple[str, str, str]] = set()

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        parsed = parse_first_topic(line)
        if not parsed:
            continue

        topic, remainder = parsed
        url_match = re.search(r"(https?://\S+)", remainder)
        if not url_match:
            continue

        full_url_token = url_match.group(1).strip()
        title_raw = remainder[:url_match.start()].strip()
        title = re.sub(r"[\s:—\-]+$", "", title_raw).strip()

        # If title starts with the topic in brackets, remove duplicated bracket prefix
        bracket_in_title = re.match(r"^\[(.*?)\]\s*(.*)$", title)
        if bracket_in_title and bracket_in_title.group(1).strip().lower() == topic.lower():
            title = bracket_in_title.group(2).strip()

        if not title:
            title = f"{topic} Item {seq}"

        # URL and authorized key separation
        clean_url = full_url_token
        auth_key = None
        if "*" in full_url_token:
            parts = full_url_token.split("*", 1)
            clean_url = parts[0].strip()
            auth_key = parts[1].strip() if len(parts) > 1 else None

        # Media classification via centralized MediaRouter
        classified_media = MediaRouter.classify_url(full_url_token)
        media_type_str = classified_media.value if hasattr(classified_media, "value") else str(classified_media)

        # De-duplicate identical line entries if repeated accidentally
        dedup_key = (topic, title, full_url_token)
        if dedup_key in seen_dedup:
            continue
        seen_dedup.add(dedup_key)

        res = TxtResource(
            topic=topic,
            title=title,
            url=clean_url,
            authorized_key=auth_key,
            media_type=media_type_str,
            sequence=seq
        )
        resources.append(res)
        seq += 1

    return resources


def parse_bracket_topic_txt(file_content: str, filename: str = "") -> AcademicCourse:
    """
    Parses bracket topic text into AcademicCourse with first [] mapped to Telegram Forum Topics (Units).
    Preserves exact TXT file order and reuses topics when returning to earlier topics.
    """
    resources = parse_bracket_topic_raw(file_content)
    if not resources:
        return AcademicCourse(subject="General", course="Course", format_type="bracket_topic")

    # Determine Subject & Course Name
    subject = resources[0].topic
    course = resources[0].topic
    if filename:
        detected_subj, detected_course = detect_subject_and_course(filename)
        if detected_subj and detected_subj != "General Course":
            subject = detected_subj
        if detected_course and detected_course != "General Course":
            course = detected_course

    course_obj = AcademicCourse(
        subject=subject,
        course=course,
        format_type="bracket_topic"
    )

    # Ordered grouping of topics (OrderedDict preserves appearance order of distinct topics)
    topics_order: OrderedDict[str, List[TxtResource]] = OrderedDict()
    for res in resources:
        topics_order.setdefault(res.topic, []).append(res)

    unit_map: Dict[str, AcademicUnit] = {}
    unit_list: List[AcademicUnit] = []

    for idx, (topic_name, itms) in enumerate(topics_order.items(), start=1):
        norm_key = f"bracket_topic_{normalize_title(topic_name)}"
        sanitized_name = sanitize_topic_name(topic_name)
        display_hdr = f"📌 {sanitized_name}"

        unit = AcademicUnit(
            number=idx,
            title=topic_name,
            normalized_key=norm_key,
            display_header=display_hdr
        )
        unit_map[topic_name] = unit
        unit_list.append(unit)

    all_items: List[AcademicItem] = []

    # Map each resource to an AcademicItem preserving exact sequential file order
    for res in resources:
        target_unit = unit_map[res.topic]
        category = "video"
        if res.media_type in ("DIRECT_PDF", "pdf"):
            category = "pdf"

        # Reconstruct full URL with key if present for downstream handlers
        full_url = f"{res.url}*{res.authorized_key}" if res.authorized_key else res.url
        url_no_scheme = re.sub(r"^https?://", "", full_url)

        item = AcademicItem(
            raw_line=f"[{res.topic}] {res.title} : {full_url}",
            index=res.sequence,
            title=res.title,
            url=full_url,
            url_without_scheme=url_no_scheme,
            category=category,
            unit_number=target_unit.number,
            unit_title=target_unit.title,
            topic_title=res.topic,
            course_name=course,
            subject_name=res.topic,
            parent_folder=res.topic,
            subfolder=""
        )
        setattr(item, "authorized_key", res.authorized_key)
        setattr(item, "media_type", res.media_type)
        setattr(item, "topic", res.topic)

        target_unit.items.append(item)
        all_items.append(item)

    course_obj.units = unit_list
    course_obj.all_items = all_items
    return course_obj
