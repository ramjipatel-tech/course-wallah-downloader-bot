import os
import re
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple, Set

from academic_parser import (
    AcademicItem,
    AcademicTopic,
    AcademicUnit,
    AcademicCourse,
    normalize_title,
    classify_item_url
)
from utils import MediaRouter, MediaType

# ==============================================================================
# 🎯 DATA MODEL FOR STRUCTURED BATCH FORMAT (FORMAT B)
# ==============================================================================

@dataclass
class StructuredContentItem:
    parent_folder: str
    topic: str
    title: str
    url: str
    media_type: str
    sequence: int
    raw_line: str
    unit_number: Optional[Any] = None

@dataclass
class StructuredBatch:
    batch_name: str = ""
    batch_id: str = ""
    instructor: str = ""
    thumbnail: str = ""
    price: str = ""
    start_date: str = ""
    end_date: str = ""
    generated_on: str = ""
    topic_summary: List[Dict[str, Any]] = field(default_factory=list)
    link_summary: Dict[str, Any] = field(default_factory=dict)
    content_items: List[StructuredContentItem] = field(default_factory=list)
    raw_header: str = ""

    def to_academic_course(self) -> AcademicCourse:
        return structured_batch_to_academic_course(self)


# ==============================================================================
# 🔍 FORMAT DETECTION ENGINE
# ==============================================================================

STRUCTURED_SECTION_PATTERNS = [
    re.compile(r"[-=─━\s]*BATCH\s+DETAILS[-=─━\s]*", re.IGNORECASE),
    re.compile(r"[-=─━\s]*TOPIC\s+SUMMARY[-=─━\s]*", re.IGNORECASE),
    re.compile(r"[-=─━\s]*LINK\s+SUMMARY[-=─━\s]*", re.IGNORECASE),
    re.compile(r"^CONTENT\s*:", re.IGNORECASE | re.MULTILINE),
]

STRUCTURED_FIELD_PATTERNS = [
    re.compile(r"(?:🌟|\*|-)?\s*Batch\s*:", re.IGNORECASE),
    re.compile(r"(?:🪪|\*|-)?\s*ID\s*:\s*\d+", re.IGNORECASE),
    re.compile(r"(?:👨🏫|👨‍🏫|\*|-)?\s*Instructor\s*:", re.IGNORECASE),
    re.compile(r"(?:📸|\*|-)?\s*Thumbnail\s*:\s*https?://", re.IGNORECASE),
    re.compile(r"Total\s+Number\s+of\s+Links\s*:", re.IGNORECASE),
    re.compile(r"Total\s+Videos\s*:", re.IGNORECASE),
    re.compile(r"Total\s+PDFs\s*:", re.IGNORECASE),
    re.compile(r"📁\s*[^|\n]+\|\|", re.IGNORECASE),
    re.compile(r"\[[^\]]+\]\s*\([^\)]+\)\s*Class", re.IGNORECASE),
]

LEGACY_PATTERNS = [
    re.compile(r"^(?:[▶✔✓★●▪⭐📌📚📖📁⚡\s\-*#]*)(?:UNIT|MODULE|CHAPTER|BLOCK)[\s\-_:]*(\d+(?:\.\d+)?)", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^(?:\[Subject\]|# Subject:|Subject:)", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^(?:\[Course\]|# Course:|Course:)", re.IGNORECASE | re.MULTILINE),
]


def detect_txt_format(text: str) -> str:
    """
    Detect whether the course TXT is Format B ('structured_batch'),
    Format C ('bracket_topic'), Format A ('legacy_appx'), or 'unknown'.

    Returns:
        "structured_batch" | "bracket_topic" | "legacy_appx" | "unknown"
    """
    if not text or not text.strip():
        return "unknown"

    from bracket_topic_parser import is_bracket_topic_format

    structured_score = 0
    for pat in STRUCTURED_SECTION_PATTERNS:
        if pat.search(text):
            structured_score += 2

    for pat in STRUCTURED_FIELD_PATTERNS:
        if pat.search(text):
            structured_score += 1

    if re.search(r"^CONTENT\s*:", text, re.IGNORECASE | re.MULTILINE):
        structured_score += 3

    if re.search(r"Careerwill", text, re.IGNORECASE):
        structured_score += 1

    # Structured batch requires strong positive markers
    if structured_score >= 3:
        return "structured_batch"

    # Format C: Bracket topic format [TOPIC] Title : URL
    if is_bracket_topic_format(text):
        return "bracket_topic"

    legacy_score = 0
    for pat in LEGACY_PATTERNS:
        if pat.search(text):
            legacy_score += 2

    has_urls = bool(re.search(r"https?://", text))

    if legacy_score >= 2 or has_urls:
        return "legacy_appx"
    else:
        return "unknown"


# ==============================================================================
# 🧹 FOLDER & TITLE SANITIZATION
# ==============================================================================

def sanitize_folder_name(name: str) -> str:
    r"""
    Sanitize folder/topic name for safe filesystem usage.
    Removes < > : " / \ | ? * and prevents path traversal (.., absolute paths, drive letters).
    Preserves Hindi/Unicode text, spaces, brackets, hyphens, parentheses.
    """
    if not name:
        return ""
    
    # Strip leading/trailing whitespaces and common bullet icons
    cleaned = name.strip()
    cleaned = re.sub(r"^[📁📂📌▶✔✓★●▪⭐⚡\s\-_:]+", "", cleaned)
    cleaned = re.sub(r"[📁📂📌▶✔✓★●▪⭐⚡\s\-_:]+$", "", cleaned).strip()
    
    # Remove unsafe characters: < > : " / \ | ? *
    cleaned = re.sub(r'[<>:"/\\|?*]', ' ', cleaned)
    
    # Prevent path traversal
    cleaned = re.sub(r"\.\.+", " ", cleaned)
    
    # Clean up multiple spaces
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    
    # Prevent reserved Windows filenames (CON, PRN, AUX, NUL, COM1, LPT1, etc.)
    reserved = {
        "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"
    }
    if cleaned.upper() in reserved:
        cleaned = f"{cleaned}_folder"
        
    return cleaned


def extract_hierarchy_and_title(left_part: str) -> Tuple[str, str, str]:
    """
    Extract parent_folder, topic, and title from content line prefix.
    
    Supported variations:
      - [Parent Folder] (Subfolder / Topic) Class - 01 | Title
      - [Parent Folder] Class - 01 | Title
      - (Subfolder / Topic) Class - 01 | Title
      - Class - 01 | Title
    """
    cleaned = left_part.strip()
    parent = ""
    topic = ""
    title = cleaned
    
    # 1. Match [Parent] at start
    bracket_match = re.match(r"^\[(.*?)\]\s*(.*)$", cleaned)
    if bracket_match:
        parent = sanitize_folder_name(bracket_match.group(1))
        rem = bracket_match.group(2).strip()
        
        # Check if remainder starts with (Topic)
        paren_match = re.match(r"^\((.*?)\)\s*(.*)$", rem)
        if paren_match:
            topic = sanitize_folder_name(paren_match.group(1))
            title = paren_match.group(2).strip()
        else:
            title = rem
    else:
        # Check if line starts with (Topic)
        paren_match = re.match(r"^\((.*?)\)\s*(.*)$", cleaned)
        if paren_match:
            topic = sanitize_folder_name(paren_match.group(1))
            title = paren_match.group(2).strip()
            parent = topic  # Fallback parent to topic name
            
    # Clean trailing colons, dashes, em-dashes from title
    title = re.sub(r"[\s:—\-]+$", "", title).strip()
    if not title:
        title = left_part.strip()
        
    return parent, topic, title


# ==============================================================================
# 📦 STRUCTURED BATCH TXT PARSER (FORMAT B)
# ==============================================================================

def parse_structured_batch_raw(file_content: str) -> StructuredBatch:
    """
    Parse a Format B structured batch text file into StructuredBatch dataclass.
    """
    batch = StructuredBatch()
    lines = file_content.splitlines()
    
    current_section = "HEADER"
    content_sequence = 1
    seen_content_keys: Set[Tuple[str, str]] = set()

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        # Detect section boundaries
        if re.search(r"[-=─━\s]*BATCH\s+DETAILS[-=─━\s]*", line, re.I):
            current_section = "BATCH_DETAILS"
            continue
        elif re.search(r"[-=─━\s]*TOPIC\s+SUMMARY[-=─━\s]*", line, re.I):
            current_section = "TOPIC_SUMMARY"
            continue
        elif re.search(r"[-=─━\s]*LINK\s+SUMMARY[-=─━\s]*", line, re.I):
            current_section = "LINK_SUMMARY"
            continue
        elif re.match(r"^CONTENT\s*:", line, re.I):
            current_section = "CONTENT"
            continue

        # 1. BATCH DETAILS & HEADER METADATA
        if current_section in ("HEADER", "BATCH_DETAILS"):
            # Batch name
            m_batch = re.search(r"(?:🌟|\*|-)?\s*Batch\s*:\s*(.*)$", line, re.I)
            if m_batch and not batch.batch_name:
                batch.batch_name = m_batch.group(1).strip()
                continue

            # Batch ID
            m_id = re.search(r"(?:🪪|\*|-)?\s*ID\s*:\s*(.*)$", line, re.I)
            if m_id and not batch.batch_id:
                batch.batch_id = m_id.group(1).strip()
                continue

            # Instructor
            m_inst = re.search(r"(?:👨🏫|👨‍🏫|\*|-)?\s*Instructor\s*:\s*(.*)$", line, re.I)
            if m_inst and not batch.instructor:
                batch.instructor = m_inst.group(1).strip()
                continue

            # Thumbnail
            m_thumb = re.search(r"(?:📸|\*|-)?\s*Thumbnail\s*:\s*(https?://\S+)", line, re.I)
            if m_thumb and not batch.thumbnail:
                batch.thumbnail = m_thumb.group(1).strip()
                continue

            # Price
            m_price = re.search(r"(?:💰|\*|-)?\s*Price\s*:\s*(.*)$", line, re.I)
            if m_price and not batch.price:
                batch.price = m_price.group(1).strip()
                continue

            # Start Date
            m_start = re.search(r"(?:📅|\*|-)?\s*Start\s*Date\s*:\s*(.*)$", line, re.I)
            if m_start and not batch.start_date:
                batch.start_date = m_start.group(1).strip()
                continue

            # End Date
            m_end = re.search(r"(?:📅|\*|-)?\s*End\s*Date\s*:\s*(.*)$", line, re.I)
            if m_end and not batch.end_date:
                batch.end_date = m_end.group(1).strip()
                continue

            # Generated On
            m_gen = re.search(r"(?:🕒|\*|-)?\s*Generated\s*(?:On|At)\s*:\s*(.*)$", line, re.I)
            if m_gen and not batch.generated_on:
                batch.generated_on = m_gen.group(1).strip()
                continue

        # 2. TOPIC SUMMARY SECTION
        if current_section == "TOPIC_SUMMARY":
            # Example: 📁 Practice Class||SSC GD Practice : 13 videos
            # Example: 📁 Calculation Capsule|| : 5 videos
            # Example: 📁 Rakesh Sir (Revision Class)||Percentage : 9 videos
            clean_tline = re.sub(r"^[📁📂📌▶✔✓★●▪⭐⚡\s\-_:]+", "", line).strip()
            if "||" in clean_tline:
                p_part, sub_part = clean_tline.split("||", 1)
                parent_name = sanitize_folder_name(p_part)
                
                count = 0
                topic_name = ""
                if ":" in sub_part:
                    t_part, c_part = sub_part.split(":", 1)
                    topic_name = sanitize_folder_name(t_part)
                    c_match = re.search(r"(\d+)", c_part)
                    if c_match:
                        count = int(c_match.group(1))
                else:
                    topic_name = sanitize_folder_name(sub_part)
                    
                batch.topic_summary.append({
                    "parent": parent_name,
                    "topic": topic_name,
                    "count": count,
                    "raw": line
                })
                continue
            elif ":" in clean_tline and not clean_tline.lower().startswith("http"):
                p_part, c_part = clean_tline.split(":", 1)
                parent_name = sanitize_folder_name(p_part)
                c_match = re.search(r"(\d+)", c_part)
                count = int(c_match.group(1)) if c_match else 0
                batch.topic_summary.append({
                    "parent": parent_name,
                    "topic": "",
                    "count": count,
                    "raw": line
                })
                continue

        # 3. LINK SUMMARY SECTION
        if current_section == "LINK_SUMMARY":
            if "Total Number of Links" in line or "Total Links" in line:
                m = re.search(r"(\d+)", line)
                if m:
                    batch.link_summary["total_links"] = int(m.group(1))
            elif "Total Videos" in line:
                m = re.search(r"(\d+)", line)
                if m:
                    batch.link_summary["total_videos"] = int(m.group(1))
            elif ".m3u8" in line:
                m = re.search(r"(\d+)", line)
                if m:
                    batch.link_summary["total_m3u8"] = int(m.group(1))
            elif ".mpd" in line:
                m = re.search(r"(\d+)", line)
                if m:
                    batch.link_summary["total_mpd"] = int(m.group(1))
            elif "Youtube" in line or "YouTube" in line:
                m = re.search(r"(\d+)", line)
                if m:
                    batch.link_summary["total_youtube"] = int(m.group(1))
            elif "Total PDFs" in line or "Total PDF" in line:
                m = re.search(r"(\d+)", line)
                if m:
                    batch.link_summary["total_pdfs"] = int(m.group(1))
            continue

        # 4. CONTENT SECTION (or any content line containing URLs outside summary/header)
        # Note: Do not extract batch thumbnail line as content!
        if line.lower().startswith("📸 thumbnail") or line.lower().startswith("thumbnail :"):
            continue

        url_match = re.search(r"(https?://\S+)", line)
        if url_match:
            full_url = url_match.group(1).strip()
            left_part = line[:url_match.start()].strip()
            
            p_folder, topic_title, item_title = extract_hierarchy_and_title(left_part)
            
            # Apply topic hierarchy matching against TOPIC SUMMARY if parent is empty
            if not p_folder and topic_title:
                for ts in batch.topic_summary:
                    if ts["topic"].lower() == topic_title.lower() and ts["parent"]:
                        p_folder = ts["parent"]
                        break
            
            if not p_folder:
                p_folder = batch.batch_name or "General"

            # Stable deduplication key
            dedup_key = (full_url, item_title)
            if dedup_key in seen_content_keys:
                continue
            seen_content_keys.add(dedup_key)

            # Classify media
            m_type = classify_item_url(full_url)
            
            item = StructuredContentItem(
                parent_folder=p_folder,
                topic=topic_title,
                title=item_title,
                url=full_url,
                media_type=m_type,
                sequence=content_sequence,
                raw_line=line
            )
            batch.content_items.append(item)
            content_sequence += 1

    return batch


def structured_batch_to_academic_course(batch: StructuredBatch) -> AcademicCourse:
    """
    Converts StructuredBatch into normalized AcademicCourse data structure
    to seamlessly execute through the existing download and Telegram pipeline.
    """
    course_name = batch.batch_name or "Structured Batch Course"
    # Use instructor if present, else course_name or "Careerwill"
    subject_name = batch.instructor or "Careerwill"

    course_obj = AcademicCourse(
        subject=subject_name,
        course=course_name
    )
    # Attach format metadata
    setattr(course_obj, "format_type", "structured_batch")
    setattr(course_obj, "structured_batch", batch)

    # Group content items into AcademicUnits while preserving original sequence
    unit_map: Dict[Tuple[str, str], AcademicUnit] = {}
    unit_list: List[AcademicUnit] = []
    unit_counter = 1

    for item in batch.content_items:
        p_folder = item.parent_folder or course_name
        topic = item.topic or ""
        group_key = (p_folder, topic)

        if group_key not in unit_map:
            if topic:
                unit_title = f"{p_folder} — {topic}"
                display_hdr = f"📌 {p_folder.upper()} — {topic.upper()}"
                norm_key = f"{normalize_title(p_folder)}_{normalize_title(topic)}"
            else:
                unit_title = p_folder
                display_hdr = f"📌 {p_folder.upper()}"
                norm_key = normalize_title(p_folder)

            acad_unit = AcademicUnit(
                number=unit_counter,
                title=unit_title,
                normalized_key=norm_key,
                display_header=display_hdr
            )
            unit_counter += 1
            unit_map[group_key] = acad_unit
            unit_list.append(acad_unit)

        target_unit = unit_map[group_key]
        url_no_scheme = re.sub(r"^https?://", "", item.url)

        acad_item = AcademicItem(
            raw_line=item.raw_line,
            index=item.sequence,
            title=item.title,
            url=item.url,
            url_without_scheme=url_no_scheme,
            category=item.media_type,
            unit_number=target_unit.number,
            unit_title=target_unit.title,
            topic_title=topic or None,
            course_name=course_name,
            subject_name=p_folder
        )
        # Attach parent_folder and subfolder attributes
        setattr(acad_item, "parent_folder", p_folder)
        setattr(acad_item, "subfolder", topic)

        target_unit.items.append(acad_item)
        course_obj.all_items.append(acad_item)

    course_obj.units = unit_list
    return course_obj


def parse_structured_batch_txt(file_content: str, filename: str = "") -> AcademicCourse:
    """
    Main entry point for Format B structured batch text parsing.
    Returns standard AcademicCourse instance.
    """
    raw_batch = parse_structured_batch_raw(file_content)
    
    # Fallback to filename if batch_name was not in text
    if not raw_batch.batch_name and filename:
        base = os.path.splitext(os.path.basename(filename))[0]
        base_clean = re.sub(r"^\d+[\s\-_]+", "", base).strip().replace("_", " ")
        raw_batch.batch_name = base_clean

    return raw_batch.to_academic_course()
