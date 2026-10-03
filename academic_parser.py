import os
import re
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple

@dataclass
class AcademicItem:
    raw_line: str
    index: int                  # 1-based sequential or parsed index (e.g. 35)
    title: str                  # Clean title
    url: str                    # Full URL (with https:// or http://)
    url_without_scheme: str     # URL without '://' (for backward compatibility with main.py)
    category: str               # 'video', 'pdf', 'image', 'zip', 'audio', 'html', 'other'
    unit_number: Optional[Any] = None
    unit_title: Optional[str] = None
    topic_title: Optional[str] = None
    course_name: Optional[str] = None
    subject_name: Optional[str] = None
    parent_folder: Optional[str] = None
    subfolder: Optional[str] = None

@dataclass
class AcademicTopic:
    title: str
    items: List[AcademicItem] = field(default_factory=list)

@dataclass
class AcademicUnit:
    number: Any                 # 1, 2, "1.1", "General", etc.
    title: str                  # "Logic and Proof Techniques"
    normalized_key: str         # Stable key for DB mapping: "mathematics_unit_1_logic_and_proof_techniques"
    display_header: str         # "📌 UNIT 1 — LOGIC AND PROOF TECHNIQUES"
    topics: List[AcademicTopic] = field(default_factory=list)
    items: List[AcademicItem] = field(default_factory=list)
    topic_thread_id: Optional[int] = None
    header_message_id: Optional[int] = None
    status_message_id: Optional[int] = None

@dataclass
class AcademicCourse:
    subject: str
    course: str
    units: List[AcademicUnit] = field(default_factory=list)
    all_items: List[AcademicItem] = field(default_factory=list)
    format_type: str = "legacy_appx"
    structured_batch: Optional[Any] = None

# Common subject code/abbreviation mapping
SUBJECT_MAP = {
    "math": "Mathematics",
    "maths": "Mathematics",
    "mathematics": "Mathematics",
    "cs": "Computer Science",
    "cse": "Computer Science",
    "it": "Information Technology",
    "ee": "Electrical Engineering",
    "ece": "Electronics and Communication Engineering",
    "me": "Mechanical Engineering",
    "ce": "Civil Engineering",
    "phy": "Physics",
    "physics": "Physics",
    "chem": "Chemistry",
    "chemistry": "Chemistry",
    "bio": "Biology",
    "biology": "Biology",
    "os": "Operating Systems",
    "cn": "Computer Networks",
    "dbms": "Database Management Systems",
    "toc": "Theory of Computation",
    "cd": "Compiler Design",
    "coa": "Computer Organization and Architecture",
    "cao": "Computer Architecture and Organization",
    "ds": "Discrete Structures",
    "dsa": "Data Structures and Algorithms",
    "ai": "Artificial Intelligence",
    "ml": "Machine Learning",
}

UNIT_REGEX = re.compile(
    r"^(?:[▶✔✓★●▪⭐📌📚📖📁⚡\s\-*#]*)(?:UNIT|Unit|unit|MODULE|Module|module|CHAPTER|Chapter|chapter|BLOCK|Block|block)[\s\-_:]*(\d+(?:\.\d+)?)[\s\-_:.]*(.*?)(?:[▶✔✓★●▪⭐📌📚📖📁⚡\s]*)$"
)

TOPIC_REGEX = re.compile(
    r"^(?:[▶✔✓★●▪⭐📌📚📖📁⚡\s\-*#]*)(?:TOPIC|Topic|topic|SECTION|Section|section)[\s\-_:]*(.*?)(?:[▶✔✓★●▪⭐📌📚📖📁⚡\s]*)$"
)

def normalize_title(title: str) -> str:
    """Normalize title for matching/keys: lowercase, alphanumeric and underscores only."""
    if not title:
        return ""
    cleaned = re.sub(r"[^\w\s]", " ", str(title).lower())
    return "_".join(cleaned.split())

def detect_subject_and_course(filename: str, first_lines: Optional[List[str]] = None) -> Tuple[str, str]:
    """
    Extract Subject and Course name from filename and optional heading lines.
    Example: 240_Discrete_Structures(Math).txt -> (Subject='Mathematics', Course='Discrete Structures')
    """
    base = os.path.splitext(os.path.basename(filename))[0] if filename else ""
    # Strip leading numbers like 240_ or 01_
    base_clean = re.sub(r"^\d+[\s\-_]+", "", base).strip()
    
    subject = ""
    course = ""
    
    # Check for parentheses: Discrete_Structures(Math) -> Course: Discrete Structures, Subject: Math
    paren_match = re.search(r"^(.*?)\((.*?)\)$", base_clean)
    if paren_match:
        c_part = paren_match.group(1).replace("_", " ").strip()
        s_part = paren_match.group(2).replace("_", " ").strip()
        course = c_part
        subject = SUBJECT_MAP.get(s_part.lower(), s_part.title())
    elif " - " in base_clean or " _ " in base_clean:
        parts = [p.strip() for p in re.split(r"[\-_]+", base_clean) if p.strip()]
        if len(parts) >= 2:
            p0_mapped = SUBJECT_MAP.get(parts[0].lower())
            p1_mapped = SUBJECT_MAP.get(parts[-1].lower())
            if p0_mapped:
                subject = p0_mapped
                course = " ".join(parts[1:])
            elif p1_mapped:
                subject = p1_mapped
                course = " ".join(parts[:-1])
            else:
                course = " ".join(parts)
                subject = course
        else:
            course = base_clean.replace("_", " ")
            subject = course
    else:
        course = base_clean.replace("_", " ") if base_clean else "General Course"
        subject = course

    # Fallback to heading lines if present
    if first_lines:
        for line in first_lines[:5]:
            l_str = line.strip()
            if l_str.startswith(("[Subject]", "# Subject:", "Subject:")):
                subject = l_str.split(":", 1)[1].strip() if ":" in l_str else l_str.replace("[Subject]", "").strip()
            elif l_str.startswith(("[Course]", "# Course:", "Course:")):
                course = l_str.split(":", 1)[1].strip() if ":" in l_str else l_str.replace("[Course]", "").strip()

    if not subject:
        subject = course if course else "General Studies"
    if not course:
        course = subject

    # Beautify casing while preserving well known acronyms
    UPPER_ACRONYMS = {"os", "cs", "it", "ee", "ece", "me", "ce", "ds", "dsa", "ai", "ml", "dbms", "cn", "toc", "cd", "coa", "cao", "sql", "html", "css", "js", "ts", "php", "c", "cpp"}
    
    def format_title_words(text: str) -> str:
        words = text.split()
        res = []
        for w in words:
            if w.lower() in UPPER_ACRONYMS:
                res.append(w.upper())
            else:
                res.append(w.capitalize())
        return " ".join(res)

    course = format_title_words(course)
    subject = format_title_words(subject)
    return subject, course

def classify_item_url(url: str) -> str:
    """Classify the content item type based on URL patterns."""
    url_lower = url.lower()
    if ".pdf" in url_lower or "utkarshapp.com/admin_v1/file_manager/pdf" in url_lower:
        return "pdf"
    elif any(url_lower.endswith(ext) or ext in url_lower for ext in [".jpg", ".jpeg", ".png", ".webp"]):
        return "image"
    elif "zip" in url_lower:
        return "zip"
    elif any(url_lower.endswith(ext) or ext in url_lower for ext in [".mp3", ".wav", ".m4a", ".audio"]):
        return "audio"
    elif ".ws" in url_lower or ".html" in url_lower:
        return "html"
    elif any(x in url_lower for x in ["youtu", "m3u8", "mpd", "fetch_video", "v2", "drm", "stream", "video", ".mp4", ".mkv"]):
        return "video"
    return "video"

def parse_academic_txt(file_content: str, filename: str = "") -> AcademicCourse:
    """
    Parse TXT file into AcademicCourse with hierarchical Units, Topics, and Items.
    """
    lines = [line.strip() for line in file_content.splitlines() if line.strip()]
    subject, course = detect_subject_and_course(filename, lines)
    
    course_obj = AcademicCourse(subject=subject, course=course)
    
    # Pre-create a General/Unclassified unit for items found before the first Unit header (e.g. Syllabus)
    general_unit = AcademicUnit(
        number=0,
        title="General",
        normalized_key=f"{normalize_title(subject)}_general",
        display_header=f"📁 GENERAL — {subject.upper()}"
    )
    
    current_unit: Optional[AcademicUnit] = None
    current_topic_title: Optional[str] = None
    current_subject: str = subject
    item_counter = 1

    for line in lines:
        l_str = line.strip()
        # Check for Subject/Course header line
        if l_str.startswith(("[Subject]", "# Subject:", "Subject:", "# SUBJECT:", "SUBJECT:")):
            raw_s = l_str.split(":", 1)[1].strip() if ":" in l_str else l_str.replace("[Subject]", "").strip()
            if raw_s:
                current_subject = SUBJECT_MAP.get(raw_s.lower(), raw_s.title())
                current_unit = None
                continue
        elif l_str.startswith(("[Course]", "# Course:", "Course:", "# COURSE:", "COURSE:")):
            raw_c = l_str.split(":", 1)[1].strip() if ":" in l_str else l_str.replace("[Course]", "").strip()
            if raw_c:
                course = raw_c
                continue

        # Check if line contains a URL
        url_match = re.search(r"(https?://\S+)", line)
        if url_match:
            full_url = url_match.group(1).strip()
            # Title is everything before the URL
            title_part = line[:url_match.start()].strip()
            # Clean trailing colons, dashes, em-dashes
            title_part = re.sub(r"[\s:—\-]+$", "", title_part).strip()

            # Extract item index if present (e.g., '35. DS' -> index=35, title='DS')
            idx_match = re.match(r"^(\d+)[\.\s\-_:]+(.*)$", title_part)
            if idx_match:
                item_idx = int(idx_match.group(1))
                item_title = idx_match.group(2).strip() or title_part
            else:
                item_idx = item_counter
                item_title = title_part

            if not item_title:
                item_title = f"Item {item_idx}"

            # URL without scheme for compatibility with existing main.py logic (e.g. "example.com/...")
            url_no_scheme = re.sub(r"^https?://", "", full_url)
            category = classify_item_url(full_url)

            # Determine active unit
            target_unit = current_unit if current_unit is not None else general_unit

            item = AcademicItem(
                raw_line=line,
                index=item_idx,
                title=item_title,
                url=full_url,
                url_without_scheme=url_no_scheme,
                category=category,
                unit_number=target_unit.number if target_unit.number != 0 else None,
                unit_title=target_unit.title,
                topic_title=current_topic_title,
                course_name=course,
                subject_name=current_subject
            )

            if current_topic_title and target_unit.topics:
                target_unit.topics[-1].items.append(item)

            target_unit.items.append(item)
            course_obj.all_items.append(item)
            item_counter += 1
            continue

        # Check for Unit Header
        unit_match = UNIT_REGEX.match(line)
        if unit_match:
            num_str, u_title = unit_match.groups()
            u_title = u_title.strip()
            # Clean symbols at start/end of title
            u_title = re.sub(r"^[▶✔✓★●▪⭐📌📚📖📁⚡\s\-_:]+", "", u_title)
            u_title = re.sub(r"[▶✔✓★●▪⭐📌📚📖📁⚡\s\-_:]+$", "", u_title).strip()
            
            try:
                num_f = float(num_str)
                u_num = int(num_f) if num_f.is_integer() else num_f
            except ValueError:
                u_num = num_str
                
            if not u_title:
                u_title = f"Unit {u_num}"
                
            norm_key = f"{normalize_title(subject)}_unit_{u_num}_{normalize_title(u_title)}"
            display_hdr = f"📌 UNIT {u_num} — {u_title.upper()}"
            
            current_unit = AcademicUnit(
                number=u_num,
                title=u_title,
                normalized_key=norm_key,
                display_header=display_hdr
            )
            course_obj.units.append(current_unit)
            current_topic_title = None
            continue

        # Check for Topic Header
        topic_match = TOPIC_REGEX.match(line)
        if topic_match:
            t_title = topic_match.group(1).strip()
            t_title = re.sub(r"^[▶✔✓★●▪⭐📌📚📖📁⚡\s\-_:]+", "", t_title)
            t_title = re.sub(r"[▶✔✓★●▪⭐📌📚📖📁⚡\s\-_:]+$", "", t_title).strip()
            if t_title:
                current_topic_title = t_title
                if current_unit is not None:
                    topic_obj = AcademicTopic(title=t_title)
                    current_unit.topics.append(topic_obj)
            continue

    # If general_unit has items, insert it at the beginning of units list
    if general_unit.items:
        course_obj.units.insert(0, general_unit)

    return course_obj

def build_unit_header_message(subject: str, course: str, unit: AcademicUnit) -> str:
    """Build the header message sent before a Unit starts (for normal channels/groups)."""
    return (
        f"📚 <b>{subject.upper()}</b>\n"
        f"📖 <i>{course.upper()}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{unit.display_header}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )

def build_unit_status_message(
    subject: str,
    course: str,
    unit: AcademicUnit,
    current_item: Optional[AcademicItem] = None,
    current_idx: int = 0,
    total_unit_items: int = 0,
    failed_unit_items: int = 0,
    is_done: bool = False
) -> str:
    """Build the live updating status message for a Unit."""
    if not is_done:
        item_text = f"<code>{current_item.index}. {current_item.title}</code>" if current_item else "Starting..."
        return (
            f"📚 <b>{subject.upper()}</b>\n"
            f"📖 <i>{course.upper()}</i>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{unit.display_header}\n\n"
            f"⏳ <b>Downloading:</b>\n"
            f"{item_text}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Progress:</b> {current_idx} / {total_unit_items}\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
    else:
        success = max(0, total_unit_items - failed_unit_items)
        return (
            f"📚 <b>{subject.upper()}</b>\n"
            f"📖 <i>{course.upper()}</i>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{unit.display_header}\n\n"
            f"✅ <b>Unit Processing Completed</b>\n\n"
            f"📦 <b>Total Items:</b> {total_unit_items}\n"
            f"✅ <b>Successful:</b> {success}\n"
            f"❌ <b>Failed:</b> {failed_unit_items}\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )

def build_enriched_caption(
    subject_name_or_item = None,
    course_name_or_count = None,
    item_or_credit = None,
    item_index: int = 1,
    credit: str = None,
    ext: str = "mp4",
    height: int = 720,
    width: int = 1280,
    **kwargs
) -> str:
    """
    Build rich academic caption with Subject -> Course -> Unit -> Topic hierarchy
    and clean Course Wallah branding.
    """
    # Disambiguate arguments
    if isinstance(subject_name_or_item, AcademicItem):
        item = subject_name_or_item
        count = course_name_or_count or item_index
        cr = item_or_credit or credit or kwargs.get("cr") or "Course Wallah"
        b_name = kwargs.get("b_name") or item.course_name or "Course"
        subject_name = item.subject_name or "Subject"
    else:
        subject_name = str(subject_name_or_item) if subject_name_or_item else ""
        b_name = str(course_name_or_count) if course_name_or_count else "Course"
        item = item_or_credit if isinstance(item_or_credit, AcademicItem) else kwargs.get("item")
        count = item_index or kwargs.get("count", 1)
        cr = credit or kwargs.get("cr") or "Course Wallah"

    unit_title = getattr(item, "unit_title", "") if item else ""
    unit_number = getattr(item, "unit_number", None) if item else None
    topic_title = getattr(item, "topic_title", "") if item else ""
    item_title = getattr(item, "title", "Content") if item else "Content"

    unit_line = f"<blockquote><b>📌 Unit {unit_number} — {unit_title}</b></blockquote>\n\n" if unit_number is not None else ""
    topic_line = f"<blockquote><b>📝 Topic : {topic_title}</b></blockquote>\n\n" if topic_title else ""
    subject_line = f"<blockquote><b>📚 Subject » {subject_name}</b></blockquote>\n" if subject_name else ""
    course_display = b_name

    credit_str = cr or '<a href="https://t.me/course_wallah_official_bot">𝄟⃝⚡️ Course Wallah 🎓 🔥</a>'

    return (
        f"<blockquote><b> ——— ✦ {str(count).zfill(3)} ✦——— </b></blockquote>\n\n"
        f"<blockquote><b> 📚 {subject_name or course_display}</b></blockquote>\n"
        f"<blockquote><b> 📖 {course_display}</b></blockquote>\n"
        f"{unit_line}"
        f"{topic_line}"
        f"<blockquote><b> 🎬 Title : {item_title}</b></blockquote>\n\n"
        f"<blockquote><b> ├── Extention : <a href='https://t.me/course_wallah_official_bot'>𝄟⃝⚡️ Course Wallah 🎓 🔥</a>.{ext}</b></blockquote>\n"
        f"├── Resolution : {height}p ({width} × {height})\n\n"
        f"{subject_line}"
        f"<blockquote><b> 📚 Course » {course_display}</b></blockquote>\n\n"
        f"<blockquote><b> 🌟 Extracted By : {credit_str}</b></blockquote>"
    )


def build_pdf_caption(
    course_name: str = None,
    lecture_title: str = None,
    subject_name: str = None,
    unit_title: str = None,
    item_index: int = None,
    credit: str = None
) -> str:
    """
    Builds clean, high-aesthetic Course Wallah Study Material / PDF caption.
    """
    header = f"<blockquote><b> ——— ✦ {str(item_index).zfill(3)} ✦ ——— </b></blockquote>\n\n" if item_index else ""
    lines = [
        f"{header}📄 <b>STUDY MATERIAL</b>\n"
    ]
    if course_name:
        lines.append(f"<blockquote><b>📚 Course:</b> {course_name}</blockquote>")
    if lecture_title:
        lines.append(f"<blockquote><b>📝 Lecture:</b> {lecture_title}</blockquote>")
    if subject_name:
        lines.append(f"<blockquote><b>🎓 Subject:</b> {subject_name}</blockquote>")
    if unit_title and unit_title != "General":
        lines.append(f"<blockquote><b>📌 Unit:</b> {unit_title}</blockquote>")

    credit_display = credit or CREDIT
    lines.append(f"\n<blockquote><b>🔥 Extracted By : {credit_display}</b></blockquote>")
    return "\n".join(lines)


def parse_course_txt(file_content: str, filename: str = "") -> AcademicCourse:
    """
    Unified router that automatically identifies which TXT format it received
    (Format A / Legacy APPX vs Format B / Structured Batch vs Format C / Bracket Topic)
    and routes to the appropriate parser.
    """
    from structured_batch_parser import detect_txt_format, parse_structured_batch_txt
    from bracket_topic_parser import parse_bracket_topic_txt
    fmt = detect_txt_format(file_content)
    if fmt == "structured_batch":
        return parse_structured_batch_txt(file_content, filename)
    elif fmt == "bracket_topic":
        return parse_bracket_topic_txt(file_content, filename)
    else:
        return parse_academic_txt(file_content, filename)


# Lazy / Convenience re-exports
try:
    from structured_batch_parser import (
        detect_txt_format,
        parse_structured_batch_txt,
        parse_structured_batch_raw,
        StructuredBatch,
        StructuredContentItem,
        sanitize_folder_name
    )
except ImportError:
    pass

try:
    from bracket_topic_parser import (
        parse_bracket_topic_txt,
        parse_bracket_topic_raw,
        parse_first_topic,
        sanitize_topic_name,
        is_bracket_topic_format,
        TxtResource
    )
except ImportError:
    pass


