"""
ExamShield AI - Document Parsing & Question Extraction Engine
Supports high-fidelity extraction from PDF, DOCX, TXT, and Images (OCR).
Parses questions, options, answer keys, marks, sections, and explanations.
"""

from __future__ import annotations

import io
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Allowed file extensions
ALLOWED_DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt", ".png", ".jpg", ".jpeg"}
MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB limit


def is_allowed_document(filename: str) -> bool:
    ext = os.path.splitext(filename)[1].lower()
    return ext in ALLOWED_DOCUMENT_EXTENSIONS


# =============================================================================
# 1. RAW TEXT EXTRACTION FROM FILES
# =============================================================================

def extract_text_from_pdf(file_input: bytes | io.BytesIO | str) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Extracts text from a PDF file using pypdf.
    Returns (combined_text, pages_metadata).
    """
    import pypdf

    if isinstance(file_input, bytes):
        stream = io.BytesIO(file_input)
    elif isinstance(file_input, io.BytesIO):
        stream = file_input
    else:
        stream = open(file_input, "rb")

    pages_meta = []
    text_parts = []
    has_images = False

    try:
        reader = pypdf.PdfReader(stream)
        num_pages = len(reader.pages)
        for idx in range(num_pages):
            page = reader.pages[idx]
            page_text = page.extract_text() or ""
            cleaned = page_text.strip()
            
            # Check for embedded page images
            page_images_count = 0
            try:
                page_images_count = len(getattr(page, "images", []))
            except Exception:
                page_images_count = 0

            if page_images_count > 0:
                has_images = True

            # If page has no embedded text but has images, attempt OCR if pytesseract is available
            is_page_ocr = False
            if not cleaned and page_images_count > 0:
                try:
                    import pytesseract
                    from PIL import Image
                    for img_obj in page.images:
                        ocr_img = Image.open(io.BytesIO(img_obj.data))
                        ocr_text = pytesseract.image_to_string(ocr_img).strip()
                        if ocr_text:
                            cleaned = ocr_text
                            is_page_ocr = True
                            break
                except Exception as ocr_err:
                    logger.debug("PDF page image OCR skipped: %s", ocr_err)

            pages_meta.append({
                "page_number": idx + 1,
                "text": cleaned,
                "char_count": len(cleaned),
                "images_count": page_images_count,
                "is_ocr": is_page_ocr,
            })
            if cleaned:
                tag = f"--- [Page {idx + 1}{' (OCR)' if is_page_ocr else ''}] ---"
                text_parts.append(f"{tag}\n{cleaned}")
    finally:
        if not isinstance(file_input, (bytes, io.BytesIO)):
            stream.close()

    combined_text = "\n\n".join(text_parts)
    return combined_text, pages_meta


def extract_text_from_docx(file_input: bytes | io.BytesIO | str) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Extracts structured text from DOCX documents (paragraphs & tables) using python-docx.
    """
    import docx

    if isinstance(file_input, bytes):
        stream = io.BytesIO(file_input)
    elif isinstance(file_input, io.BytesIO):
        stream = file_input
    else:
        stream = file_input

    try:
        doc = docx.Document(stream)
    except Exception as exc:
        logger.warning("DOCX parsing error: %s", exc)
        raise ValueError(
            "Unable to parse document. If this is a legacy .doc file, please open and save it as modern .docx or .pdf, or paste text directly."
        ) from exc

    sections_meta = []
    text_parts = []

    # Paragraphs
    for p_idx, para in enumerate(doc.paragraphs):
        p_text = para.text.strip()
        if p_text:
            text_parts.append(p_text)
            sections_meta.append({
                "type": "paragraph",
                "index": p_idx,
                "style": para.style.name if para.style else "Normal",
                "text": p_text,
            })

    # Tables
    for t_idx, table in enumerate(doc.tables):
        table_lines = []
        for row in table.rows:
            row_cells = [cell.text.strip().replace("\n", " ") for cell in row.cells if cell.text.strip()]
            if row_cells:
                table_lines.append(" | ".join(row_cells))
        if table_lines:
            table_str = "\n".join(table_lines)
            text_parts.append(f"\n[Table {t_idx + 1}]\n{table_str}\n")
            sections_meta.append({
                "type": "table",
                "index": t_idx,
                "text": table_str,
            })

    combined_text = "\n\n".join(text_parts)
    return combined_text, sections_meta


def extract_text_from_txt(file_input: bytes | str) -> str:
    """Extracts text from plain text or markdown files with multi-encoding fallback."""
    if isinstance(file_input, str):
        return file_input
    for enc in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        try:
            return file_input.decode(enc)
        except UnicodeDecodeError:
            continue
    return file_input.decode("utf-8", errors="ignore")


def extract_text_from_image(file_input: bytes | io.BytesIO | str) -> str:
    """
    Extracts text from image files (PNG, JPG) using pytesseract if available.
    """
    from PIL import Image

    if isinstance(file_input, bytes):
        img_stream = io.BytesIO(file_input)
    elif isinstance(file_input, io.BytesIO):
        img_stream = file_input
    else:
        img_stream = file_input

    try:
        import pytesseract
        img = Image.open(img_stream)
        # Attempt OCR
        return pytesseract.image_to_string(img)
    except Exception as exc:
        logger.warning("OCR processing warning: %s", exc)
        return ""


def extract_document_text(file_bytes: bytes, filename: str) -> Tuple[str, Dict[str, Any]]:
    """
    Universal text extractor based on file extension.
    Returns (raw_text, metadata).
    """
    ext = os.path.splitext(filename)[1].lower()
    raw_text = ""
    meta: Dict[str, Any] = {"filename": filename, "extension": ext, "pages": []}

    if ext == ".pdf":
        raw_text, pages = extract_text_from_pdf(file_bytes)
        meta["pages"] = pages
        meta["page_count"] = len(pages)
        if not raw_text.strip() and any(p.get("images_count", 0) > 0 for p in pages):
            meta["is_scanned"] = True
    elif ext in (".docx", ".doc"):
        raw_text, sections = extract_text_from_docx(file_bytes)
        meta["sections"] = sections
    elif ext == ".txt":
        raw_text = extract_text_from_txt(file_bytes)
    elif ext in (".png", ".jpg", ".jpeg"):
        raw_text = extract_text_from_image(file_bytes)
        meta["is_ocr"] = True
    else:
        raw_text = extract_text_from_txt(file_bytes)

    meta["char_length"] = len(raw_text)
    return raw_text, meta


# =============================================================================
# 2. QUESTION & SECTION HEURISTIC PARSING ENGINE
# =============================================================================

# Regex patterns for question detection
RE_QUESTION_START = re.compile(
    r"(?:^|\n)\s*(?:"
    r"(?:Q(?:uestion)?\.?\s*(\d+))|"                                     # Q1, Q. 1, Question 1
    r"(?:\(\s*(\d+)\s*\))|"                                              # (1), ( 1 )
    r"(\d+)[\.\)]|"                                                     # 1. or 1)
    r"\[(\d+)\]|"                                                       # [1]
    r"(?:Q(?:uestion)?\s*([IVXLCDM]+)[\.\)])|"                           # Q I., Question II)
    r"(I{1,3}|IV|VI{0,3}|IX|X{1,3}|XI{1,3}|XIV|XV|XVI{0,3}|XIX|XX)\.\s+" # I. II. III.
    r")\s*[:\-\.]?\s*",
    re.IGNORECASE,
)

RE_SECTION_HEADER = re.compile(
    r"(?:^|\n)\s*(?:"
    r"SECTION\s*[-–:]?\s*([A-Z0-9]+)|"
    r"PART\s*[-–:]?\s*([A-Z0-9]+)|"
    r"UNIT\s*[-–:]?\s*([A-Z0-9]+)|"
    r"GROUP\s*[-–:]?\s*([A-Z0-9]+)"
    r")\s*[-–:]?\s*(.*?)(?=\n|$)",
    re.IGNORECASE,
)

RE_OPTION_LINE = re.compile(
    r"^\s*(?:\(?([A-Ha-h])[\.\)\:]|\[([A-Ha-h])\])\s*(.*)$"
)

RE_INLINE_OPTIONS = re.compile(
    r"(?:\s*\(?([A-Ha-h])[\.\)]\s+)(.*?)(?=(?:\s*\(?[A-Ha-h][\.\)]|\Z))",
    re.DOTALL,
)

RE_ANSWER_KEY = re.compile(
    r"(?:Ans(?:wer)?|Correct\s*(?:Answer|Option)|Key|Answer\s*Key)\s*[:\-.]?\s*\(?([^\r\n\)]+)\)?",
    re.IGNORECASE,
)

RE_MARKS = re.compile(
    r"(?:\[\s*(\d+(?:\.\d+)?)\s*M(?:arks)?\s*\]|\(\s*(\d+(?:\.\d+)?)\s*M(?:arks)?\s*\)|Marks?\s*[:\-]?\s*(\d+(?:\.\d+)?)|\[Marks?\s*[:\-]?\s*(\d+(?:\.\d+)?)\])",
    re.IGNORECASE,
)

RE_EXPLANATION = re.compile(
    r"(?:Explanation|Rationale|Solution|Reason)\s*[:\-]\s*(.*?)(?=(?:\n\s*(?:Ans|Marks?|Difficulty|Topic|Q\d+|\d+[\.\)]|SECTION|PART)|\Z))",
    re.IGNORECASE | re.DOTALL,
)

RE_DIFFICULTY = re.compile(
    r"(?:Difficulty|Level)\s*[:\-]\s*([A-Za-z]+)",
    re.IGNORECASE,
)

RE_TOPIC = re.compile(
    r"(?:Topic|Chapter|Subject Area)\s*[:\-]\s*([^\n]+)",
    re.IGNORECASE,
)


def _split_into_sections(text: str) -> List[Tuple[str, str, str]]:
    """
    Splits document text into sections.
    Returns [(section_name, section_instructions, section_body), ...].
    """
    sections = []
    lines = text.split("\n")
    current_sec = "Default Section"
    current_instructions = ""
    current_lines = []

    for line in lines:
        sec_match = RE_SECTION_HEADER.match(line)
        if sec_match:
            if current_lines:
                sections.append((current_sec, current_instructions, "\n".join(current_lines)))
                current_lines = []
            matched_group = sec_match.group(1) or sec_match.group(2) or sec_match.group(3) or sec_match.group(4) or "A"
            title_extra = (sec_match.group(5) or "").strip()
            current_sec = f"Section {matched_group.upper()}"
            current_instructions = title_extra
        else:
            current_lines.append(line)

    if current_lines:
        sections.append((current_sec, current_instructions, "\n".join(current_lines)))

    return sections if sections else [("Default Section", "", text)]


def _extract_options(block_text: str) -> Tuple[List[Dict[str, Any]], str]:
    """
    Extracts options from a question block.
    Returns (options_list, text_without_options).
    """
    options = []
    opt_line_pattern = re.compile(r"^\s*(?:\(?([A-Da-d])[\.\)\:]|\[([A-Da-d])\])\s*(.*)$")

    lines = block_text.split("\n")
    statement_lines = []
    current_option = None

    for line in lines:
        m = opt_line_pattern.match(line)
        if m:
            label = (m.group(1) or m.group(2) or "").upper()
            text = m.group(3).strip()
            text = re.split(r"\b(?:Ans|Marks?|Explanation|Difficulty|Topic)\b", text, flags=re.IGNORECASE)[0].strip()
            current_option = {
                "option_text": text,
                "text": text,
                "label": label,
                "is_correct": False,
            }
            options.append(current_option)
        elif current_option is not None:
            stripped = line.strip()
            if stripped:
                clean_stripped = re.split(r"\b(?:Ans|Marks?|Explanation|Difficulty|Topic)\b", stripped, flags=re.IGNORECASE)[0].strip()
                if clean_stripped:
                    current_option["option_text"] = (current_option["option_text"] + " " + clean_stripped).strip()
                    current_option["text"] = current_option["option_text"]
        else:
            statement_lines.append(line)

    statement = "\n".join(statement_lines).strip()

    if len(options) < 2:
        inline_matches = list(RE_INLINE_OPTIONS.finditer(block_text))
        if len(inline_matches) >= 2:
            options = []
            statement = block_text[:inline_matches[0].start()].strip()
            for match in inline_matches:
                label = match.group(1).strip().upper()
                opt_body = match.group(2).strip()
                opt_body = re.split(r"\b(?:Ans|Marks?|Explanation|Difficulty|Topic)\b", opt_body, flags=re.IGNORECASE)[0].strip()
                options.append({
                    "option_text": opt_body,
                    "text": opt_body,
                    "label": label,
                    "is_correct": False,
                })

    return options, statement


def _classify_question_type(statement: str, options: List[Dict[str, Any]], marks: float) -> str:
    """Classifies the question into one of the supported 8 types."""
    lower_stmt = statement.lower()

    if options:
        # Check if True/False
        opt_texts = {o["option_text"].strip().lower() for o in options}
        if opt_texts in ({"true", "false"}, {"yes", "no"}):
            return "true_false"
        return "mcq"

    # No options detected
    if any(phrase in lower_stmt for phrase in ("true or false", "true/false", "state true or false")):
        return "true_false"

    if any(blank in statement for blank in ("_____", "....", "[blank]", "fill in the blank")):
        return "fill_blank"

    if re.search(r"\b(calculate|compute|solve for|numerical|value of)\b", lower_stmt) or re.search(r"=\s*\?|\b\d+\s*[\+\-\*/\^]\s*\d+\b", statement):
        return "numerical"

    if marks >= 8 or any(w in lower_stmt for w in ("explain in detail", "elaborate", "discuss", "derive", "critically analyze", "write an essay")):
        return "long_answer"

    if marks > 5:
        return "descriptive"

    return "short_answer"


def parse_question_block(block: str, default_q_num: int = 1, section_name: str = "Section A") -> Dict[str, Any]:
    """
    Parses an individual question text block into a normalized question dictionary.
    Guarantees no answers are fabricated; missing elements are flagged for review.
    """
    block_clean = block.strip()

    # 1. Marks Extraction
    marks = 1.0
    marks_match = RE_MARKS.search(block_clean)
    if marks_match:
        val = marks_match.group(1) or marks_match.group(2) or marks_match.group(3)
        try:
            marks = float(val)
        except (ValueError, TypeError):
            marks = 1.0
        block_clean = RE_MARKS.sub("", block_clean).strip()

    # 2. Explanation Extraction
    explanation = ""
    expl_match = RE_EXPLANATION.search(block_clean)
    if expl_match:
        explanation = expl_match.group(1).strip()
        block_clean = RE_EXPLANATION.sub("", block_clean).strip()

    # 3. Answer Key Extraction
    detected_answer = ""
    ans_match = RE_ANSWER_KEY.search(block_clean)
    if ans_match:
        detected_answer = ans_match.group(1).strip()
        block_clean = RE_ANSWER_KEY.sub("", block_clean).strip()

    # 4. Difficulty Extraction
    difficulty = "Medium"
    diff_match = RE_DIFFICULTY.search(block_clean)
    if diff_match:
        difficulty = diff_match.group(1).strip().capitalize()
        block_clean = RE_DIFFICULTY.sub("", block_clean).strip()

    # 5. Topic Extraction
    topic = "General"
    topic_match = RE_TOPIC.search(block_clean)
    if topic_match:
        topic = topic_match.group(1).strip()
        block_clean = RE_TOPIC.sub("", block_clean).strip()

    # 6. Clean up question statement prefix first before option matching
    q_num = default_q_num
    num_match = RE_QUESTION_START.match(block_clean)
    if num_match:
        matched_num = next((g for g in num_match.groups() if g), None)
        if matched_num and matched_num.isdigit():
            q_num = int(matched_num)
        block_clean = RE_QUESTION_START.sub("", block_clean).strip()

    block_clean = re.sub(r"^[–\-:\.]\s*", "", block_clean).strip()

    # 7. Options Extraction
    options, statement = _extract_options(block_clean)

    # 8. Type Classification
    q_type = _classify_question_type(statement, options, marks)

    # 9. Apply Answer Key to Options if available
    is_multi_select = False
    requires_review = False
    review_reasons = []

    if options:
        if detected_answer:
            ans_upper = detected_answer.upper()
            ans_keys = [k.strip() for k in re.split(r"[,/&]|and", ans_upper) if k.strip()]
            correct_count = 0
            for opt in options:
                opt_lbl = opt.get("label", "").upper()
                opt_txt = opt["option_text"].strip().upper()
                if opt_lbl in ans_keys or opt_txt in ans_keys or opt_txt == ans_upper:
                    opt["is_correct"] = True
                    correct_count += 1

            if correct_count > 1:
                q_type = "multiple_select"
                is_multi_select = True
            elif correct_count == 0:
                requires_review = True
                review_reasons.append(f"Answer key '{detected_answer}' could not be matched to an option.")
        else:
            requires_review = True
            review_reasons.append("No correct answer indicated in source text.")

    elif q_type == "true_false":
        is_true = detected_answer.strip().lower() in ("true", "t", "yes", "1")
        is_false = detected_answer.strip().lower() in ("false", "f", "no", "0")
        options = [
            {"option_text": "True", "text": "True", "is_correct": is_true, "label": "A"},
            {"option_text": "False", "text": "False", "is_correct": is_false, "label": "B"},
        ]
        if not (is_true or is_false):
            requires_review = True
            review_reasons.append("Specify whether True or False is the correct answer.")

    else:
        if not detected_answer:
            requires_review = True
            review_reasons.append("No model answer / correct text provided.")

    confidence = "high"
    if requires_review:
        confidence = "medium" if statement else "low"
    if len(statement) < 10:
        requires_review = True
        confidence = "low"
        review_reasons.append("Question statement is brief or incomplete.")

    return {
        "question_number": q_num,
        "statement": statement,
        "question_text": statement,
        "question_type": q_type,
        "type": q_type,
        "marks": max(1.0, marks),
        "negative_marks": 0.0,
        "difficulty": difficulty,
        "topic": topic,
        "chapter": "",
        "section": section_name,
        "section_name": section_name,
        "options": options,
        "correct_answer": detected_answer,
        "correct_answer_text": detected_answer,
        "explanation": explanation,
        "confidence": confidence,
        "requires_review": requires_review,
        "review_reasons": review_reasons,
        "status": "needs_review" if requires_review else "approved",
    }


def parse_bulk_text(raw_text: str, default_subject: str = "General") -> List[Dict[str, Any]]:
    """
    Parses raw text containing multiple questions into structured question objects.
    Preserves sections, question numbering, and formatting.
    """
    if not raw_text or not raw_text.strip():
        return []

    sections = _split_into_sections(raw_text)
    all_questions = []
    global_q_idx = 1

    for sec_name, sec_instructions, sec_body in sections:
        # Split section body by question start delimiters
        # Find all question start positions
        matches = list(RE_QUESTION_START.finditer(sec_body))
        if not matches:
            # Fallback: check paragraphs separated by double newlines
            paragraphs = [p.strip() for p in sec_body.split("\n\n") if p.strip()]
            for p in paragraphs:
                if len(p) > 15:
                    q_data = parse_question_block(p, default_q_num=global_q_idx, section_name=sec_name)
                    all_questions.append(q_data)
                    global_q_idx += 1
            continue

        for i, m in enumerate(matches):
            start_pos = m.start()
            end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(sec_body)
            block = sec_body[start_pos:end_pos].strip()
            if block:
                q_data = parse_question_block(block, default_q_num=global_q_idx, section_name=sec_name)
                all_questions.append(q_data)
                global_q_idx += 1

    return all_questions
