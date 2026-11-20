"""
ExamShield AI - AI-Assisted Question Extraction & Grounded Generation Service
Integrates with Gemini / OpenAI APIs when configured, and provides an intelligent
deterministic rule-based fallback when offline or no API key is present.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import requests

from backend.services.document_parser import parse_bulk_text

logger = logging.getLogger(__name__)


def get_ai_status() -> Dict[str, Any]:
    """Returns the current AI integration status and available provider."""
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()

    if gemini_key:
        return {
            "configured": True,
            "provider": "Gemini AI",
            "model": "gemini-1.5-flash",
            "description": "Google Gemini High-Fidelity Extraction & Generation",
        }
    elif openai_key:
        return {
            "configured": True,
            "provider": "OpenAI",
            "model": "gpt-4o-mini",
            "description": "OpenAI Multi-Modal Parser & Question Synthesizer",
        }
    return {
        "configured": False,
        "provider": "Rule-Based Heuristic Engine",
        "model": "local-nlp-extractor",
        "description": "Built-in intelligent rule-based extractor (zero cloud dependency)",
    }


# =============================================================================
# 1. AI-ASSISTED DOCUMENT EXTRACTION
# =============================================================================

EXTRACTION_SYSTEM_PROMPT = """You are an academic examination paper parsing assistant.
Extract all examination questions from the provided document into a structured JSON array.
Follow these CRITICAL RULES:
1. NEVER fabricate or guess answers. If an answer key or correct answer is explicitly marked in the source (e.g., 'Ans: B'), include it. Otherwise, leave 'correct_answer_text' empty and set all option 'is_correct' to false.
2. Preserve original mathematical equations, LaTeX symbols, chemical formulas, and question wording verbatim.
3. Identify question type accurately: mcq, multiple_select, true_false, fill_blank, short_answer, long_answer, numerical, descriptive.
4. Detect sections (e.g. 'Section A', 'Part I', 'Section B') and assign each question to its section.
5. Identify marks and negative marks if specified.
6. Extract answer explanations or solutions if explicitly present in the document.
7. Return ONLY a valid JSON array of question objects adhering to this schema:
[
  {
    "question_number": 1,
    "statement": "...",
    "question_type": "mcq",
    "marks": 2.0,
    "negative_marks": 0.0,
    "difficulty": "Medium",
    "topic": "General",
    "section": "Section A",
    "options": [
      {"option_text": "...", "is_correct": false, "label": "A"}
    ],
    "correct_answer_text": "",
    "explanation": "",
    "confidence": "high",
    "requires_review": true,
    "review_reasons": ["Answer key was not specified in the document."]
  }
]
"""


def _call_gemini_api(api_key: str, system_prompt: str, user_prompt: str) -> Optional[str]:
    """Calls the Google Gemini REST API."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": f"{system_prompt}\n\nTask:\n{user_prompt}"}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }
    try:
        resp = requests.post(url, json=payload, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            candidates = data.get("candidates", [])
            if candidates:
                return candidates[0]["content"]["parts"][0]["text"]
        logger.warning("Gemini API call returned status %d: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.error("Gemini API call failed: %s", exc)
    return None


def _call_openai_api(api_key: str, system_prompt: str, user_prompt: str) -> Optional[str]:
    """Calls the OpenAI REST API."""
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            return data["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.error("OpenAI API call failed: %s", exc)
    return None


def extract_questions_with_ai(raw_text: str, default_subject: str = "General") -> List[Dict[str, Any]]:
    """
    Extracts questions using cloud AI if configured, otherwise falls back to rule-based engine.
    """
    status = get_ai_status()
    if status["configured"]:
        gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
        openai_key = os.getenv("OPENAI_API_KEY", "").strip()
        ai_response = None

        user_content = f"Subject: {default_subject}\n\nDOCUMENT TEXT:\n{raw_text[:15000]}"

        if gemini_key:
            ai_response = _call_gemini_api(gemini_key, EXTRACTION_SYSTEM_PROMPT, user_content)
        elif openai_key:
            ai_response = _call_openai_api(openai_key, EXTRACTION_SYSTEM_PROMPT, user_content)

        if ai_response:
            try:
                parsed = json.loads(ai_response)
                if isinstance(parsed, dict) and "questions" in parsed:
                    parsed = parsed["questions"]
                if isinstance(parsed, list) and len(parsed) > 0:
                    for idx, q in enumerate(parsed, 1):
                        q["question_number"] = idx
                        q["source_type"] = "ai_extracted"
                    return parsed
            except Exception as e:
                logger.warning("Failed to parse AI JSON response: %s", e)

    # Fallback to local rule-based extractor
    return parse_bulk_text(raw_text, default_subject)


# =============================================================================
# 2. GENERATE QUESTIONS GROUNDED IN STUDY MATERIAL
# =============================================================================

STUDY_MATERIAL_SYSTEM_PROMPT = """You are an academic test development expert.
Your task is to generate questions STRICTLY GROUNDED in the provided lecture notes and study material.
Follow these RULES:
1. Every question must be directly answerable from the provided text.
2. Provide a 'source_reference' attribute citing the specific section title, heading, or concept from which the question is derived.
3. Do not invent facts or concepts not mentioned in the text.
4. Support the requested question types: MCQ, True/False, Short Answer, Long Answer, Numerical.
5. For MCQs, provide 4 options with exactly one correct option clearly marked, plus a brief explanation quoting the text.
6. Clearly mark all questions with 'is_ai_generated': true, 'requires_review': true.
7. Return a JSON array matching the schema:
[
  {
    "question_number": 1,
    "statement": "...",
    "question_type": "mcq",
    "marks": 2.0,
    "negative_marks": 0.0,
    "difficulty": "Medium",
    "topic": "...",
    "section": "Section A",
    "options": [
      {"option_text": "...", "is_correct": true, "label": "A"},
      {"option_text": "...", "is_correct": false, "label": "B"}
    ],
    "correct_answer_text": "...",
    "explanation": "...",
    "source_reference": "Section 2.1: Key Principles",
    "is_ai_generated": true,
    "requires_review": true
  }
]
"""


def generate_questions_from_study_material(
    material_text: str,
    subject: str = "General",
    topic: str = "Key Concepts",
    num_questions: int = 5,
    difficulty: str = "Medium",
    question_types: Optional[List[str]] = None,
    marks_per_question: float = 2.0,
) -> List[Dict[str, Any]]:
    """
    Generates questions grounded in uploaded study material.
    Uses AI if configured, otherwise applies intelligent semantic chunking and grounded heuristics.
    """
    if not question_types:
        question_types = ["mcq", "short_answer", "true_false"]

    status = get_ai_status()
    if status["configured"]:
        gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
        openai_key = os.getenv("OPENAI_API_KEY", "").strip()
        user_prompt = (
            f"Generate exactly {num_questions} questions for Subject: {subject}, Topic: {topic}.\n"
            f"Difficulty: {difficulty}. Allowed Question Types: {', '.join(question_types)}.\n"
            f"Marks per question: {marks_per_question}.\n\n"
            f"STUDY MATERIAL EXCERPT:\n{material_text[:12000]}"
        )

        ai_response = None
        if gemini_key:
            ai_response = _call_gemini_api(gemini_key, STUDY_MATERIAL_SYSTEM_PROMPT, user_prompt)
        elif openai_key:
            ai_response = _call_openai_api(openai_key, STUDY_MATERIAL_SYSTEM_PROMPT, user_prompt)

        if ai_response:
            try:
                parsed = json.loads(ai_response)
                if isinstance(parsed, dict) and "questions" in parsed:
                    parsed = parsed["questions"]
                if isinstance(parsed, list) and len(parsed) > 0:
                    for idx, q in enumerate(parsed, 1):
                        q["question_number"] = idx
                        q["is_ai_generated"] = True
                        q["requires_review"] = True
                        q["status"] = "needs_review"
                    return parsed[:num_questions]
            except Exception as e:
                logger.warning("Failed to parse AI generated questions: %s", e)

    # Deterministic Grounded Generator Fallback (Zero-Cloud / Rule-based)
    return _generate_grounded_questions_heuristic(
        material_text=material_text,
        subject=subject,
        topic=topic,
        num_questions=num_questions,
        difficulty=difficulty,
        question_types=question_types,
        marks_per_question=marks_per_question,
    )


def _generate_grounded_questions_heuristic(
    material_text: str,
    subject: str,
    topic: str,
    num_questions: int,
    difficulty: str,
    question_types: List[str],
    marks_per_question: float,
) -> List[Dict[str, Any]]:
    """
    Generates grounded questions from text sentences and paragraphs without external API calls.
    Extracts definitions, key principles, facts, and formulas.
    """
    questions: List[Dict[str, Any]] = []
    lines = [line.strip() for line in material_text.split("\n") if len(line.strip()) > 30]

    # Find definitional and factual sentences
    def_sentences = []
    for line in lines:
        if any(marker in line.lower() for marker in ("is defined as", "refers to", "is a", "consists of", "primary function", "principle of", "means that", "characterized by")):
            def_sentences.append(line)

    if not def_sentences:
        def_sentences = lines[:max(10, num_questions * 2)]

    type_cycle = [t.lower() for t in question_types]
    q_num = 1

    for idx, sentence in enumerate(def_sentences):
        if len(questions) >= num_questions:
            break

        current_type = type_cycle[(q_num - 1) % len(type_cycle)]
        clean_stmt = sentence.rstrip(".")

        if current_type == "true_false":
            stmt = f"According to the study material: \"{clean_stmt}\"."
            questions.append({
                "question_number": q_num,
                "statement": stmt,
                "question_text": stmt,
                "question_type": "true_false",
                "type": "true_false",
                "marks": max(1.0, marks_per_question),
                "negative_marks": 0.0,
                "difficulty": difficulty,
                "topic": topic,
                "section": "Section A",
                "options": [
                    {"option_text": "True", "text": "True", "is_correct": True, "label": "A"},
                    {"option_text": "False", "text": "False", "is_correct": False, "label": "B"},
                ],
                "correct_answer": "True",
                "correct_answer_text": "True",
                "explanation": f"Grounded directly in text: \"{sentence}\"",
                "source_reference": f"Excerpt #{idx + 1}",
                "confidence": "high",
                "is_ai_generated": True,
                "requires_review": True,
                "status": "needs_review",
            })
            q_num += 1

        elif current_type in ("mcq", "multiple_select"):
            # Split sentence into subject and predicate
            parts = re.split(r"\b(?:is defined as|refers to|is an?|consists of)\b", sentence, maxsplit=1, flags=re.IGNORECASE)
            if len(parts) == 2:
                subject_part = parts[0].strip()
                predicate_part = parts[1].strip().rstrip(".")
                opt_correct = predicate_part
                # Generate sibling distractors
                distractors = [
                    f"Inversely proportional to {subject_part.lower()}",
                    f"A secondary subset unrelated to {subject_part.lower()}",
                    f"A legacy protocol replaced by modern alternatives",
                ]
                options = [
                    {"option_text": opt_correct, "text": opt_correct, "is_correct": True, "label": "A"},
                    {"option_text": distractors[0], "text": distractors[0], "is_correct": False, "label": "B"},
                    {"option_text": distractors[1], "text": distractors[1], "is_correct": False, "label": "C"},
                    {"option_text": distractors[2], "text": distractors[2], "is_correct": False, "label": "D"},
                ]
                stmt = f"Based on the course material, what is {subject_part}?"
                questions.append({
                    "question_number": q_num,
                    "statement": stmt,
                    "question_text": stmt,
                    "question_type": "mcq",
                    "type": "mcq",
                    "marks": max(1.0, marks_per_question),
                    "negative_marks": 0.0,
                    "difficulty": difficulty,
                    "topic": topic,
                    "section": "Section A",
                    "options": options,
                    "correct_answer": "A",
                    "correct_answer_text": "A",
                    "explanation": f"Source excerpt: \"{sentence}\"",
                    "source_reference": f"Excerpt #{idx + 1}",
                    "confidence": "medium",
                    "is_ai_generated": True,
                    "requires_review": True,
                    "status": "needs_review",
                })
                q_num += 1

        elif current_type in ("short_answer", "descriptive", "long_answer"):
            stmt = f"Explain the core concept and significance of the following principle: \"{clean_stmt}\"."
            questions.append({
                "question_number": q_num,
                "statement": stmt,
                "question_text": stmt,
                "question_type": current_type,
                "type": current_type,
                "marks": max(2.0, marks_per_question * 2 if current_type == "long_answer" else marks_per_question),
                "negative_marks": 0.0,
                "difficulty": difficulty,
                "topic": topic,
                "section": "Section B" if current_type == "short_answer" else "Section C",
                "options": [],
                "correct_answer": sentence,
                "correct_answer_text": sentence,
                "explanation": f"Model answer derived from source material: \"{sentence}\"",
                "source_reference": f"Excerpt #{idx + 1}",
                "confidence": "high",
                "is_ai_generated": True,
                "requires_review": True,
                "status": "needs_review",
            })
            q_num += 1

        elif current_type == "fill_blank":
            words = clean_stmt.split()
            if len(words) > 5:
                # Blank out a key word in the middle
                blank_idx = min(len(words) - 2, max(2, len(words) // 2))
                key_word = words[blank_idx].strip("(),;:. ")
                words[blank_idx] = "__________"
                blanked_stmt = " ".join(words)
                questions.append({
                    "question_number": q_num,
                    "statement": blanked_stmt,
                    "question_text": blanked_stmt,
                    "question_type": "fill_blank",
                    "type": "fill_blank",
                    "marks": max(1.0, marks_per_question),
                    "negative_marks": 0.0,
                    "difficulty": difficulty,
                    "topic": topic,
                    "section": "Section A",
                    "options": [],
                    "correct_answer": key_word,
                    "correct_answer_text": key_word,
                    "explanation": f"Missing word from material: \"{key_word}\"",
                    "source_reference": f"Excerpt #{idx + 1}",
                    "confidence": "high",
                    "is_ai_generated": True,
                    "requires_review": True,
                    "status": "needs_review",
                })
                q_num += 1

    return questions
