"""
ExamShield AI - Academic Performance Report Exporter
Generates institutional-grade downloadable reports in PDF, Excel (XLSX), and CSV formats.
"""

import csv
import io
from datetime import datetime
from typing import Any, Dict

from fpdf import FPDF
import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


class AcademicReportPDF(FPDF):
    def __init__(self, filter_desc: str = ""):
        super().__init__(orientation="landscape", unit="mm", format="A4")
        self.filter_desc = filter_desc
        self.set_auto_page_break(auto=True, margin=15)

    def header(self):
        # Header banner
        self.set_font("Helvetica", "B", 15)
        self.set_text_color(30, 58, 138)  # Primary Navy
        self.cell(0, 8, "EXAMSHIELD AI - ACADEMIC PERFORMANCE & EVALUATION REPORT", new_x="LMARGIN", new_y="NEXT", align="C")
        self.set_font("Helvetica", "", 8)
        self.set_text_color(100, 116, 139)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cell(0, 5, f"Generated: {now_str} | Filter Scope: {self.filter_desc or 'All Cohorts & Subjects'}", new_x="LMARGIN", new_y="NEXT", align="C")
        self.line(10, 24, 287, 24)
        self.ln(4)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(148, 163, 184)
        self.cell(0, 8, f"Page {self.page_no()}/{{nb}} | Confidential Faculty Assessment Document - ExamShield AI", align="C")


def export_pdf(analytics: Dict[str, Any], filter_meta: Dict[str, Any]) -> bytes:
    """Generates an institutional multi-page PDF performance report."""
    filter_parts = []
    if filter_meta.get("subject_name"):
        filter_parts.append(f"Subject: {filter_meta['subject_name']}")
    if filter_meta.get("exam_name"):
        filter_parts.append(f"Exam: {filter_meta['exam_name']}")
    if filter_meta.get("department"):
        filter_parts.append(f"Dept: {filter_meta['department']}")
    if filter_meta.get("semester"):
        filter_parts.append(f"Sem: {filter_meta['semester']}")
    filter_desc = " | ".join(filter_parts) if filter_parts else "All Examinations & Cohorts"

    pdf = AcademicReportPDF(filter_desc=filter_desc)
    pdf.alias_nb_pages()
    pdf.add_page()

    overview = analytics.get("overview", {})

    # --- SECTION 1: EXECUTIVE PERFORMANCE SUMMARY ---
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(0, 7, "1. Executive Performance & Cohort Overview", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

    # Summary metric boxes (4 columns)
    headers = [
        ("Total Enrolled Candidates", str(overview.get("total_enrolled", 0))),
        ("Students Evaluated", str(overview.get("students_evaluated", 0))),
        ("Cohort Average Score", f"{overview.get('average_percentage', 0.0)}%"),
        ("Pass Rate", f"{overview.get('pass_percentage', 0.0)}%"),
    ]
    col_w = 69.25
    pdf.set_font("Helvetica", "B", 9)
    for title, val in headers:
        pdf.set_fill_color(241, 245, 249)
        pdf.set_text_color(71, 85, 105)
        pdf.cell(col_w, 6, title, border=1, fill=True, align="C")
    pdf.ln()

    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(30, 58, 138)
    for title, val in headers:
        pdf.cell(col_w, 8, val, border=1, align="C")
    pdf.ln(10)

    # Secondary metric boxes
    sec_headers = [
        ("Highest Marks Scored", str(overview.get("highest_marks", 0.0))),
        ("Lowest Marks Scored", str(overview.get("lowest_marks", 0.0))),
        ("Average Marks", str(overview.get("average_marks", 0.0))),
        ("Failure Rate", f"{overview.get('failure_percentage', 0.0)}%"),
    ]
    pdf.set_font("Helvetica", "B", 9)
    for title, val in sec_headers:
        pdf.set_fill_color(248, 250, 252)
        pdf.set_text_color(71, 85, 105)
        pdf.cell(col_w, 6, title, border=1, fill=True, align="C")
    pdf.ln()

    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(15, 23, 42)
    for title, val in sec_headers:
        pdf.cell(col_w, 7, val, border=1, align="C")
    pdf.ln(12)

    # --- SECTION 2: SUBJECT-WISE PERFORMANCE TABLE ---
    sub_perf = analytics.get("subject_performance", [])
    if sub_perf:
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(0, 7, "2. Subject-Wise Curriculum Proficiency", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)

        pdf.set_font("Helvetica", "B", 8)
        pdf.set_fill_color(37, 99, 235)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(85, 6, "Subject Curriculum", border=1, fill=True)
        pdf.cell(35, 6, "Exams Included", border=1, fill=True, align="C")
        pdf.cell(40, 6, "Candidates Evaluated", border=1, fill=True, align="C")
        pdf.cell(40, 6, "Average Marks", border=1, fill=True, align="C")
        pdf.cell(40, 6, "Average Percentage", border=1, fill=True, align="C")
        pdf.cell(37, 6, "Pass Percentage", border=1, fill=True, align="C")
        pdf.ln()

        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(15, 23, 42)
        for s in sub_perf[:6]:
            pdf.cell(85, 6, str(s.get("subject_name", "")), border=1)
            pdf.cell(35, 6, str(s.get("exam_count", 0)), border=1, align="C")
            pdf.cell(40, 6, str(s.get("candidates_evaluated", 0)), border=1, align="C")
            pdf.cell(40, 6, str(s.get("avg_marks", 0.0)), border=1, align="C")
            pdf.cell(40, 6, f"{s.get('avg_percentage', 0.0)}%", border=1, align="C")
            pdf.cell(37, 6, f"{s.get('pass_percentage', 0.0)}%", border=1, align="C")
            pdf.ln()
        pdf.ln(8)

    # --- SECTION 3: STUDENT LEADERBOARD & PERFORMANCE TABLE ---
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(0, 7, "3. Student Ranking, Evaluation & Academic Standing", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

    rankings = analytics.get("student_rankings", [])
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(15, 23, 42)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(14, 6, "Rank", border=1, fill=True, align="C")
    pdf.cell(60, 6, "Student Name", border=1, fill=True)
    pdf.cell(32, 6, "Registration No.", border=1, fill=True)
    pdf.cell(50, 6, "Department", border=1, fill=True)
    pdf.cell(24, 6, "Semester", border=1, fill=True, align="C")
    pdf.cell(22, 6, "Attempts", border=1, fill=True, align="C")
    pdf.cell(25, 6, "Marks Scored", border=1, fill=True, align="C")
    pdf.cell(25, 6, "Average %", border=1, fill=True, align="C")
    pdf.cell(15, 6, "Grade", border=1, fill=True, align="C")
    pdf.cell(20, 6, "Status", border=1, fill=True, align="C")
    pdf.ln()

    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(15, 23, 42)
    for st in rankings[:25]:
        pdf.cell(14, 6, f"#{st.get('rank', 0)}", border=1, align="C")
        pdf.cell(60, 6, str(st.get("name", ""))[:32], border=1)
        pdf.cell(32, 6, str(st.get("usn", "")), border=1)
        pdf.cell(50, 6, str(st.get("department", ""))[:28], border=1)
        pdf.cell(24, 6, str(st.get("semester", "")), border=1, align="C")
        pdf.cell(22, 6, str(st.get("attempts_count", 0)), border=1, align="C")
        pdf.cell(25, 6, str(st.get("total_marks", 0.0)), border=1, align="C")
        pdf.cell(25, 6, f"{st.get('avg_percentage', 0.0)}%", border=1, align="C")
        pdf.cell(15, 6, str(st.get("grade", "F")), border=1, align="C")
        status_txt = str(st.get("status", "Pass"))
        if status_txt == "Pass":
            pdf.set_text_color(5, 150, 105)
        else:
            pdf.set_text_color(220, 38, 38)
        pdf.cell(20, 6, status_txt, border=1, align="C")
        pdf.set_text_color(15, 23, 42)
        pdf.ln()

    # --- SECTION 4: QUESTION DIFFICULTY & ACCURACY ---
    easiest = analytics.get("easiest_questions", [])
    diff = analytics.get("most_difficult_questions", [])
    if easiest or diff:
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 7, "4. Item Analysis: Most Challenging & Highest Scoring Questions", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)

        if diff:
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_text_color(153, 27, 27)
            pdf.cell(0, 6, "Questions Requiring Instructional Review (Lowest Accuracy):", new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_fill_color(254, 242, 242)
            pdf.set_text_color(153, 27, 27)
            pdf.cell(140, 6, "Question Statement", border=1, fill=True)
            pdf.cell(55, 6, "Topic / Chapter", border=1, fill=True)
            pdf.cell(32, 6, "Difficulty", border=1, fill=True, align="C")
            pdf.cell(25, 6, "Evaluated", border=1, fill=True, align="C")
            pdf.cell(25, 6, "Accuracy %", border=1, fill=True, align="C")
            pdf.ln()

            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(15, 23, 42)
            for q in diff[:5]:
                stmt = str(q.get("statement", "")).replace("\n", " ")[:78]
                pdf.cell(140, 6, stmt, border=1)
                pdf.cell(55, 6, str(q.get("topic", ""))[:28], border=1)
                pdf.cell(32, 6, str(q.get("difficulty", "Medium")), border=1, align="C")
                pdf.cell(25, 6, str(q.get("total_candidates", 0)), border=1, align="C")
                pdf.cell(25, 6, f"{q.get('accuracy', 0.0)}%", border=1, align="C")
                pdf.ln()
            pdf.ln(6)

        if easiest:
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_text_color(6, 95, 70)
            pdf.cell(0, 6, "High Mastery Questions (Highest Accuracy):", new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_fill_color(236, 253, 245)
            pdf.set_text_color(6, 95, 70)
            pdf.cell(140, 6, "Question Statement", border=1, fill=True)
            pdf.cell(55, 6, "Topic / Chapter", border=1, fill=True)
            pdf.cell(32, 6, "Difficulty", border=1, fill=True, align="C")
            pdf.cell(25, 6, "Evaluated", border=1, fill=True, align="C")
            pdf.cell(25, 6, "Accuracy %", border=1, fill=True, align="C")
            pdf.ln()

            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(15, 23, 42)
            for q in easiest[:5]:
                stmt = str(q.get("statement", "")).replace("\n", " ")[:78]
                pdf.cell(140, 6, stmt, border=1)
                pdf.cell(55, 6, str(q.get("topic", ""))[:28], border=1)
                pdf.cell(32, 6, str(q.get("difficulty", "Medium")), border=1, align="C")
                pdf.cell(25, 6, str(q.get("total_candidates", 0)), border=1, align="C")
                pdf.cell(25, 6, f"{q.get('accuracy', 0.0)}%", border=1, align="C")
                pdf.ln()

    return bytes(pdf.output())


def export_xlsx(analytics: Dict[str, Any], filter_meta: Dict[str, Any]) -> bytes:
    """Generates a professional, multi-tab Excel workbook containing detailed performance sheets."""
    wb = openpyxl.Workbook()
    # Default sheet
    ws1 = wb.active
    ws1.title = "Executive Summary & Exams"

    # Styling constants
    navy_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    title_font = Font(name="Calibri", size=14, bold=True, color="1E3A8A")
    bold_font = Font(name="Calibri", size=11, bold=True)
    regular_font = Font(name="Calibri", size=10)
    center_align = Alignment(horizontal="center", vertical="center")
    thin_border = Border(
        left=Side(style="thin", color="E2E8F0"),
        right=Side(style="thin", color="E2E8F0"),
        top=Side(style="thin", color="E2E8F0"),
        bottom=Side(style="thin", color="E2E8F0"),
    )

    # Sheet 1: Executive Overview
    ws1.append(["EXAMSHIELD AI - PERFORMANCE ANALYTICS REPORT"])
    ws1["A1"].font = title_font
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ws1.append([f"Generated: {now_str}"])
    ws1["A2"].font = Font(italic=True, color="64748B", size=9)
    ws1.append([])

    overview = analytics.get("overview", {})
    ws1.append(["METRIC", "VALUE", "METRIC", "VALUE"])
    for cell in ws1[4]:
        cell.font = header_font
        cell.fill = navy_fill
        cell.alignment = center_align

    ws1.append(["Total Enrolled Candidates", overview.get("total_enrolled", 0), "Average Marks", overview.get("average_marks", 0.0)])
    ws1.append(["Students Evaluated", overview.get("students_evaluated", 0), "Average Percentage", f"{overview.get('average_percentage', 0.0)}%"])
    ws1.append(["Students Attempted", overview.get("students_attempted", 0), "Pass Percentage", f"{overview.get('pass_percentage', 0.0)}%"])
    ws1.append(["Unattempted Candidates", overview.get("unattempted_students", 0), "Failure Percentage", f"{overview.get('failure_percentage', 0.0)}%"])
    ws1.append(["Highest Marks Recorded", overview.get("highest_marks", 0.0), "Passed Count", overview.get("pass_count", 0)])
    ws1.append(["Lowest Marks Recorded", overview.get("lowest_marks", 0.0), "Failed Count", overview.get("fail_count", 0)])

    for row in range(5, 11):
        for col in range(1, 5):
            ws1.cell(row=row, column=col).border = thin_border
            ws1.cell(row=row, column=col).font = regular_font

    # Examination comparison subtable
    ws1.append([])
    ws1.append(["EXAMINATION-LEVEL PERFORMANCE BREAKDOWN"])
    ws1.cell(row=ws1.max_row, column=1).font = bold_font
    ws1.append(["Exam ID", "Examination Title", "Subject", "Department", "Semester", "Evaluated", "Avg Marks", "Avg %", "Pass %", "Highest", "Lowest"])
    for cell in ws1[ws1.max_row]:
        cell.font = header_font
        cell.fill = navy_fill
        cell.alignment = center_align

    for ex in analytics.get("exam_analytics", []):
        ws1.append([
            ex.get("exam_id"),
            ex.get("name"),
            ex.get("subject_name"),
            ex.get("department"),
            ex.get("semester"),
            ex.get("submitted_count", 0),
            ex.get("avg_marks", 0.0),
            f"{ex.get('avg_percentage', 0.0)}%",
            f"{ex.get('pass_percentage', 0.0)}%",
            ex.get("highest_marks", 0.0),
            ex.get("lowest_marks", 0.0),
        ])
        for c in range(1, 12):
            cell = ws1.cell(row=ws1.max_row, column=c)
            cell.border = thin_border
            cell.font = regular_font

    # Sheet 2: Student Rankings
    ws2 = wb.create_sheet(title="Student Leaderboard & Marks")
    ws2.append(["Rank", "Student Name", "Registration No. (USN)", "Email", "Department", "Semester", "Exams Attempted", "Total Marks", "Max Marks", "Average %", "Grade", "Status", "Average Time Taken", "Correct", "Incorrect", "Unanswered", "Strongest Topics", "Weakest Topics"])
    for cell in ws2[1]:
        cell.font = header_font
        cell.fill = navy_fill
        cell.alignment = center_align

    for st in analytics.get("student_rankings", []):
        ws2.append([
            st.get("rank"),
            st.get("name"),
            st.get("usn"),
            st.get("email"),
            st.get("department"),
            st.get("semester"),
            st.get("attempts_count", 0),
            st.get("total_marks", 0.0),
            st.get("total_max_marks", 0.0),
            f"{st.get('avg_percentage', 0.0)}%",
            st.get("grade"),
            st.get("status"),
            st.get("avg_time_taken"),
            st.get("correct_answers", 0),
            st.get("incorrect_answers", 0),
            st.get("unanswered_questions", 0),
            ", ".join(st.get("topic_strengths", [])),
            ", ".join(st.get("topic_weaknesses", [])),
        ])
        for c in range(1, 19):
            cell = ws2.cell(row=ws2.max_row, column=c)
            cell.border = thin_border
            cell.font = regular_font

    # Sheet 3: Question Accuracy
    ws3 = wb.create_sheet(title="Question Accuracy & Difficulty")
    ws3.append(["Question ID", "Exam Name", "Statement", "Topic", "Difficulty", "Marks", "Type", "Candidates Evaluated", "Correct Answers", "Incorrect Answers", "Unanswered", "Accuracy %"])
    for cell in ws3[1]:
        cell.font = header_font
        cell.fill = navy_fill
        cell.alignment = center_align

    for q in analytics.get("question_accuracy", []):
        ws3.append([
            q.get("question_id"),
            q.get("exam_name"),
            q.get("statement"),
            q.get("topic"),
            q.get("difficulty"),
            q.get("marks"),
            q.get("question_type"),
            q.get("total_candidates", 0),
            q.get("correct_count", 0),
            q.get("incorrect_count", 0),
            q.get("unanswered_count", 0),
            f"{q.get('accuracy', 0.0)}%",
        ])
        for c in range(1, 13):
            cell = ws3.cell(row=ws3.max_row, column=c)
            cell.border = thin_border
            cell.font = regular_font

    # Auto-adjust column widths for all sheets
    for ws in [ws1, ws2, ws3]:
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                val = str(cell.value or "")
                if len(val) > max_len:
                    max_len = min(len(val), 40)
            ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_csv(analytics: Dict[str, Any], filter_meta: Dict[str, Any]) -> str:
    """Generates a standard comma-separated values (CSV) string containing student rankings and assessment marks."""
    output = io.StringIO()
    writer = csv.writer(output)

    # Header section
    writer.writerow(["EXAMSHIELD AI - PERFORMANCE REPORT (CSV)"])
    writer.writerow(["Generated At", datetime.now().isoformat()])
    writer.writerow([])

    # Cohort Overview
    overview = analytics.get("overview", {})
    writer.writerow(["--- COHORT PERFORMANCE OVERVIEW ---"])
    writer.writerow(["Total Enrolled", overview.get("total_enrolled", 0)])
    writer.writerow(["Students Evaluated", overview.get("students_evaluated", 0)])
    writer.writerow(["Cohort Average Percentage", f"{overview.get('average_percentage', 0.0)}%"])
    writer.writerow(["Pass Percentage", f"{overview.get('pass_percentage', 0.0)}%"])
    writer.writerow(["Failure Percentage", f"{overview.get('failure_percentage', 0.0)}%"])
    writer.writerow(["Highest Marks", overview.get("highest_marks", 0.0)])
    writer.writerow(["Lowest Marks", overview.get("lowest_marks", 0.0)])
    writer.writerow([])

    # Student Rankings Table
    writer.writerow(["--- STUDENT RANKINGS & MARKS ---"])
    writer.writerow([
        "Rank", "Student Name", "USN", "Email", "Department", "Semester",
        "Exams Attempted", "Total Marks Scored", "Total Max Marks",
        "Average Percentage", "Grade", "Status", "Average Time Taken",
        "Correct Answers", "Incorrect Answers", "Unanswered Questions",
        "Topic Strengths", "Topic Weaknesses"
    ])

    for st in analytics.get("student_rankings", []):
        writer.writerow([
            st.get("rank"),
            st.get("name"),
            st.get("usn"),
            st.get("email"),
            st.get("department"),
            st.get("semester"),
            st.get("attempts_count", 0),
            st.get("total_marks", 0.0),
            st.get("total_max_marks", 0.0),
            f"{st.get('avg_percentage', 0.0)}%",
            st.get("grade"),
            st.get("status"),
            st.get("avg_time_taken"),
            st.get("correct_answers", 0),
            st.get("incorrect_answers", 0),
            st.get("unanswered_questions", 0),
            "; ".join(st.get("topic_strengths", [])),
            "; ".join(st.get("topic_weaknesses", [])),
        ])

    writer.writerow([])
    # Examination Analytics
    writer.writerow(["--- EXAMINATION-LEVEL PERFORMANCE ---"])
    writer.writerow([
        "Exam ID", "Examination Name", "Subject", "Department", "Semester",
        "Enrolled", "Attempted", "Evaluated", "Average Marks", "Average Percentage",
        "Pass Percentage", "Highest Marks", "Lowest Marks"
    ])

    for ex in analytics.get("exam_analytics", []):
        writer.writerow([
            ex.get("exam_id"),
            ex.get("name"),
            ex.get("subject_name"),
            ex.get("department"),
            ex.get("semester"),
            ex.get("enrolled_count", 0),
            ex.get("attempted_count", 0),
            ex.get("submitted_count", 0),
            ex.get("avg_marks", 0.0),
            f"{ex.get('avg_percentage', 0.0)}%",
            f"{ex.get('pass_percentage', 0.0)}%",
            ex.get("highest_marks", 0.0),
            ex.get("lowest_marks", 0.0),
        ])

    return output.getvalue()
