import re
import io
from datetime import datetime

import streamlit as st
import pandas as pd

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak

from database import get_supabase


# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title="DRT Weekly Report",
    page_icon="DRT",
    layout="wide"
)


# ============================================================
# SCHOOL HEADER
# ============================================================

st.markdown(
    """
    <div style="
        text-align: center;
        padding: 8px 0 18px 0;
        margin-bottom: 12px;
    ">
        <div style="
            font-size: 32px;
            font-weight: 700;
            letter-spacing: 0.5px;
        ">
            The Spice Valley Public School
        </div>
        <div style="
            font-size: 18px;
            font-weight: 500;
            margin-top: 5px;
            opacity: 0.75;
        ">
            CBSE Senior Secondary
        </div>
    </div>
    """,
    unsafe_allow_html=True
)

st.title("Weekly Report")
st.caption("Weekly DRT performance report for one selected week.")


# ============================================================
# DATABASE CONNECTION
# ============================================================

try:
    supabase = get_supabase()

    if supabase is None:
        st.error("Database connection failed.")
        st.stop()

except Exception as e:
    st.error(f"Database connection failed: {e}")
    st.stop()


# ============================================================
# PAGINATED SUPABASE FETCH
# ============================================================

def fetch_all_rows(table_name, select_columns, page_size=1000):

    rows = []
    start = 0

    while True:
        response = (
            supabase
            .table(table_name)
            .select(select_columns)
            .range(start, start + page_size - 1)
            .execute()
        )

        batch = response.data or []

        if not batch:
            break

        rows.extend(batch)

        if len(batch) < page_size:
            break

        start += page_size

    return rows


# ============================================================
# LOAD REGISTERED WEEKS
# ============================================================

@st.cache_data(ttl=60)
def load_weeks():

    rows = fetch_all_rows(
        "drt_weeks",
        "week_id, week_name, start_date, end_date"
    )

    if not rows:
        return pd.DataFrame()

    result = pd.DataFrame(rows)

    result["week_id"] = pd.to_numeric(
        result["week_id"],
        errors="coerce"
    )

    result["start_date"] = pd.to_datetime(
        result["start_date"],
        errors="coerce"
    ).dt.date

    result["end_date"] = pd.to_datetime(
        result["end_date"],
        errors="coerce"
    ).dt.date

    result["week_name"] = (
        result["week_name"]
        .astype("string")
        .str.strip()
    )

    return result.sort_values("start_date").reset_index(drop=True)


weeks_df = load_weeks()

if weeks_df.empty:
    st.warning("No weeks found in drt_weeks.")
    st.stop()


# ============================================================
# LOAD ALL DRT DATA
# ============================================================

@st.cache_data(ttl=60)
def load_staging_data():

    rows = fetch_all_rows(
        "drt_import_staging",
        """
        source_file,
        class_name,
        section,
        stream,
        roll_no,
        student_name,
        test_date,
        subject_name,
        marks,
        status
        """
    )

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows)


df = load_staging_data()

if df.empty:
    st.warning("No DRT data found in drt_import_staging.")
    st.stop()


# ============================================================
# CLEAN DATA
# ============================================================

for col in [
    "source_file",
    "class_name",
    "section",
    "stream",
    "student_name",
    "subject_name",
    "status"
]:

    if col not in df.columns:
        df[col] = None

    df[col] = (
        df[col]
        .astype("string")
        .str.strip()
    )


df["test_date"] = pd.to_datetime(
    df["test_date"],
    errors="coerce"
)

df["marks"] = pd.to_numeric(
    df["marks"],
    errors="coerce"
)

df["roll_no"] = pd.to_numeric(
    df["roll_no"],
    errors="coerce"
)


# ============================================================
# SUBJECT NORMALIZATION
# Same canonical names used by Analytics.
# ============================================================

SUBJECT_NORMALIZATION = {
    "cs": "Computer Science",
    "computer science": "Computer Science",

    "math": "Maths",
    "maths": "Maths",

    "english": "English",
    "physics": "Physics",
    "chemistry": "Chemistry",
    "biology": "Biology",

    "social": "Social Science",
    "social science": "Social Science",

    "tamil": "Tamil",
    "accountancy": "Accountancy",
    "business studies": "Business Studies",
    "economics": "Economics",

    "applied math": "Applied Maths",
    "applied maths": "Applied Maths",

    "computer science / applied maths": "Computer Science / Applied Maths",
    "computer science/applied maths": "Computer Science / Applied Maths",
    "cs/applied math": "Computer Science / Applied Maths",
    "cs/applied maths": "Computer Science / Applied Maths",
    "cs / applied math": "Computer Science / Applied Maths",
    "cs / applied maths": "Computer Science / Applied Maths",
}


def normalize_subject(subject):

    if pd.isna(subject):
        return subject

    value = str(subject).strip()

    if not value:
        return None

    key = " ".join(value.lower().split())

    return SUBJECT_NORMALIZATION.get(key, value)


df["subject_name"] = df["subject_name"].apply(normalize_subject)


# ============================================================
# SOURCE FILE -> REGISTERED WEEK
# Same week identification rule used by Analytics.
# ============================================================

def identify_week_name(source_file):

    if pd.isna(source_file):
        return None

    name = str(source_file).lower().strip()

    name = re.sub(r"\.(xlsx|xls|numbers|csv)$", "", name)
    name = re.sub(r"[^a-z0-9]+", " ", name)

    if "june" in name and (
        "3rd" in name or "3 rd" in name or "third" in name
    ):
        return "June 3rd Week"

    if "june" in name and (
        "4th" in name or "4 th" in name or "fourth" in name
    ):
        return "June 4th Week"

    if "july" in name and (
        "1st" in name or "1 st" in name or "ist" in name or "first" in name
    ):
        return "July 1st Week"

    if "july" in name and (
        "2nd" in name or "2 nd" in name or "iind" in name or "second" in name
    ):
        return "July 2nd Week"

    if "july" in name and (
        "3rd" in name or "3 rd" in name or "iiird" in name or "third" in name
    ):
        return "July 3rd Week"

    if "july" in name and (
        "4th" in name or "4 th" in name or "ivth" in name or "fourth" in name
    ):
        return "July 4th Week"

    return None


df["week_name"] = df["source_file"].apply(identify_week_name)


# ============================================================
# VALID WEEKS
# ============================================================

week_options = weeks_df["week_name"].tolist()

available_week_options = [
    week
    for week in week_options
    if (df["week_name"] == week).any()
]

if not available_week_options:
    st.error("No staging records match the registered DRT weeks.")
    st.stop()


# ============================================================
# SELECTIONS
# ============================================================

st.divider()
st.header("Report Selection")

selected_week = st.selectbox(
    "Select Week",
    options=available_week_options,
    index=len(available_week_options) - 1
)


class_options = sorted(
    df.loc[
        df["week_name"] == selected_week,
        "class_name"
    ]
    .dropna()
    .unique()
    .tolist()
)

if not class_options:
    st.warning("No classes found for the selected week.")
    st.stop()

selected_class = st.selectbox(
    "Select Class",
    options=class_options
)


# ============================================================
# SECTION
# ============================================================

section_options = sorted(
    df.loc[
        (df["week_name"] == selected_week)
        & (df["class_name"] == selected_class),
        "section"
    ]
    .dropna()
    .astype(str)
    .str.strip()
    .loc[lambda s: s != ""]
    .unique()
    .tolist()
)

if not section_options:
    st.warning("No sections found for the selected week and class.")
    st.stop()

selected_section = st.selectbox(
    "Select Section",
    options=section_options
)


# ============================================================
# SUBJECT
# ============================================================

subject_options = sorted(
    df.loc[
        (df["week_name"] == selected_week)
        & (df["class_name"] == selected_class)
        & (df["section"] == selected_section),
        "subject_name"
    ]
    .dropna()
    .unique()
    .tolist()
)

if not subject_options:
    st.warning(
        "No subjects found for the selected week, class and section."
    )
    st.stop()

selected_subject = st.selectbox(
    "Select Subject",
    options=subject_options
)


# ============================================================
# FILTER SELECTED WEEK / CLASS / SECTION / SUBJECT
# ============================================================

filtered_df = df[
    (df["week_name"] == selected_week)
    & (df["class_name"] == selected_class)
    & (df["section"] == selected_section)
    & (df["subject_name"] == selected_subject)
].copy()


# ============================================================
# VALID MARKS
# Absent / OD / SB / P / NC / Holiday / Not Conducted
# records do not participate in performance calculations.
# A genuine numeric 0 remains a valid mark.
# ============================================================

EXCLUDED_STATUSES = {
    "absent",
    "od",
    "sb",
    "p",
    "nc",
    "holiday",
    "not conducted",
    "not_conducted",
}

status_normalized = (
    filtered_df["status"]
    .fillna("")
    .astype(str)
    .str.strip()
    .str.lower()
)

valid_df = filtered_df[
    filtered_df["marks"].notna()
    & ~status_normalized.isin(EXCLUDED_STATUSES)
].copy()

if valid_df.empty:
    st.warning("No valid marks are available for this selection.")
    st.stop()


# ============================================================
# BUILD WEEKLY STUDENT AVERAGES
# Same logic as Analytics:
# multiple valid entries for a student in the selected week
# are averaged. Absent / blank marks are ignored.
# ============================================================

student_weekly = (
    valid_df
    .groupby(
        ["roll_no", "student_name"],
        dropna=False
    )["marks"]
    .mean()
    .reset_index()
)

student_weekly["roll_no"] = pd.to_numeric(
    student_weekly["roll_no"],
    errors="coerce"
)

student_weekly = student_weekly[
    student_weekly["roll_no"].notna()
].copy()

student_weekly["roll_no"] = student_weekly["roll_no"].astype(int)
student_weekly = student_weekly.sort_values("marks", ascending=False)

if student_weekly.empty:
    st.warning("No students with valid marks were found.")
    st.stop()


# ============================================================
# CLASS AVERAGE
# Same Analytics rule: mean of each student's weekly average.
# ============================================================

class_average = float(student_weekly["marks"].mean())


# ============================================================
# REPORT CATEGORIES
# ============================================================

# Rank by highest weekly average using DENSE RANKING.
#
# Example:
#   4.75 -> 1st
#   4.75 -> 1st
#   4.50 -> 2nd
#
# This means a tie for 1st does not consume the 2nd-place rank.
ranked_df = student_weekly.sort_values(
    ["marks", "student_name"],
    ascending=[False, True]
).reset_index(drop=True)

ranked_df["rank"] = (
    ranked_df["marks"]
    .rank(
        method="dense",
        ascending=False
    )
    .astype(int)
)

# Include every student holding the 1st or 2nd rank.
toppers_df = ranked_df[
    ranked_df["rank"] <= 2
].copy()


above_average_df = ranked_df[
    ranked_df["marks"] > class_average
].copy()

below_average_df = ranked_df[
    ranked_df["marks"] < class_average
].copy()

very_poor_df = ranked_df.tail(3).copy()
very_poor_df = very_poor_df.sort_values(
    ["marks", "student_name"],
    ascending=[True, True]
).reset_index(drop=True)


# ============================================================
# DISPLAY HELPERS
# ============================================================

def display_student_table(dataframe, mark_column="Average"):

    if dataframe.empty:
        st.info("No students in this category.")
        return

    result = dataframe[["student_name", "marks"]].copy()

    result.columns = [
        "Student Name",
        mark_column
    ]

    result[mark_column] = result[mark_column].round(2)
    result.insert(0, "S.No", range(1, len(result) + 1))

    st.dataframe(
        result,
        hide_index=True,
        width="stretch"
    )


# ============================================================
# REPORT HEADER
# ============================================================

st.divider()

st.header("Weekly DRT Performance Report")

week_row = weeks_df[
    weeks_df["week_name"] == selected_week
]

if not week_row.empty:
    week_row = week_row.iloc[0]
    start_text = (
        week_row["start_date"].strftime("%d-%m-%Y")
        if pd.notna(week_row["start_date"])
        else ""
    )
    end_text = (
        week_row["end_date"].strftime("%d-%m-%Y")
        if pd.notna(week_row["end_date"])
        else ""
    )
else:
    start_text = ""
    end_text = ""

st.write(f"**Week:** {selected_week}")
st.write(f"**Class:** {selected_class}")
st.write(f"**Section:** {selected_section}")
st.write(f"**Subject:** {selected_subject}")

if start_text and end_text:
    st.write(f"**Period:** {start_text} to {end_text}")

st.metric("Class Average", f"{class_average:.2f} / 5")


# ============================================================
# 1. FIRST AND SECOND PLACE
# ============================================================

st.divider()
st.subheader("1. First and Second Place")

topper_display = toppers_df[
    ["rank", "student_name", "marks"]
].copy()

topper_display["Place"] = topper_display["rank"].map(
    {
        1: "1st Place",
        2: "2nd Place"
    }
)

topper_display = topper_display[
    ["Place", "student_name", "marks"]
]

topper_display.columns = [
    "Place",
    "Student Name",
    "Average"
]

topper_display["Average"] = topper_display["Average"].round(2)

topper_display.insert(
    0,
    "S.No",
    range(1, len(topper_display) + 1)
)

st.dataframe(
    topper_display,
    hide_index=True,
    width="stretch"
)


# ============================================================
# 2. ABOVE AVERAGE
# ============================================================

st.divider()
st.subheader("2. Above Average")
st.caption(f"Students scoring above the class average of {class_average:.2f} / 5.")

display_student_table(above_average_df)


# ============================================================
# 3. BELOW AVERAGE
# ============================================================

st.divider()
st.subheader("3. Below Average")
st.caption(f"Students scoring below the class average of {class_average:.2f} / 5.")

display_student_table(below_average_df)


# ============================================================
# 4. LAST THREE VERY POOR STUDENTS
# ============================================================

st.divider()
st.subheader("4. Last Three Very Poor Students")
st.caption("The three students with the lowest valid weekly averages.")

display_student_table(very_poor_df)


# ============================================================
# PDF HELPERS
# ============================================================

def _pdf_table(dataframe, title=None, columns=None, empty_text="No students in this category."):

    story = []

    if title:
        story.append(
            Paragraph(
                title,
                ParagraphStyle(
                    "SectionTitle",
                    parent=styles["Heading2"],
                    fontSize=12,
                    leading=15,
                    spaceBefore=7,
                    spaceAfter=5,
                )
            )
        )

    if dataframe.empty:
        story.append(
            Paragraph(
                empty_text,
                styles["BodyText"]
            )
        )
        return story

    if columns is None:
        columns = list(dataframe.columns)

    table_data = [columns]

    for _, row in dataframe.iterrows():
        table_data.append([
            str(row[col])
            for col in columns
        ])

    table = Table(
        table_data,
        repeatRows=1,
        hAlign="LEFT"
    )

    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E9ECEF")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("LEADING", (0, 0), (-1, -1), 11),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (-1, 1), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))

    story.append(table)
    story.append(Spacer(1, 6 * mm))

    return story


styles = getSampleStyleSheet()

pdf_school_style = ParagraphStyle(
    "PDFSchool",
    parent=styles["Title"],
    fontName="Helvetica-Bold",
    fontSize=18,
    leading=22,
    alignment=TA_CENTER,
    spaceAfter=3,
)

pdf_subtitle_style = ParagraphStyle(
    "PDFSubtitle",
    parent=styles["Normal"],
    fontName="Helvetica",
    fontSize=11,
    leading=14,
    alignment=TA_CENTER,
    spaceAfter=2,
)

pdf_report_title_style = ParagraphStyle(
    "PDFReportTitle",
    parent=styles["Heading1"],
    fontName="Helvetica-Bold",
    fontSize=15,
    leading=18,
    alignment=TA_CENTER,
    spaceBefore=8,
    spaceAfter=10,
)

pdf_meta_style = ParagraphStyle(
    "PDFMeta",
    parent=styles["Normal"],
    fontName="Helvetica",
    fontSize=10,
    leading=14,
    spaceAfter=2,
)


def add_pdf_footer(canvas, doc):

    canvas.saveState()

    canvas.setFont("Helvetica", 8)
    canvas.drawCentredString(
        A4[0] / 2,
        10 * mm,
        f"The Spice Valley Public School | Weekly DRT Report | Page {doc.page}"
    )

    canvas.restoreState()


# ============================================================
# BUILD PDF
# ============================================================

def build_weekly_pdf():

    buffer = io.BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=14 * mm,
        leftMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=16 * mm,
        title="Weekly DRT Performance Report",
        author="The Spice Valley Public School",
    )

    story = []

    story.append(
        Paragraph(
            "The Spice Valley Public School",
            pdf_school_style
        )
    )

    story.append(
        Paragraph(
            "CBSE Senior Secondary",
            pdf_subtitle_style
        )
    )

    story.append(
        Paragraph(
            "WEEKLY DRT PERFORMANCE REPORT",
            pdf_report_title_style
        )
    )

    story.append(
        Paragraph(
            f"<b>Week:</b> {selected_week}",
            pdf_meta_style
        )
    )

    story.append(
        Paragraph(
            f"<b>Class:</b> {selected_class}",
            pdf_meta_style
        )
    )

    story.append(
        Paragraph(
            f"<b>Section:</b> {selected_section}",
            pdf_meta_style
        )
    )

    story.append(
        Paragraph(
            f"<b>Subject:</b> {selected_subject}",
            pdf_meta_style
        )
    )

    if start_text and end_text:
        story.append(
            Paragraph(
                f"<b>Period:</b> {start_text} to {end_text}",
                pdf_meta_style
            )
        )

    story.append(
        Paragraph(
            f"<b>Class Average:</b> {class_average:.2f} / 5",
            pdf_meta_style
        )
    )

    story.append(Spacer(1, 5 * mm))

    # 1. Toppers
    topper_pdf = toppers_df[
        ["rank", "student_name", "marks"]
    ].copy()

    topper_pdf["Place"] = topper_pdf["rank"].map(
        {
            1: "1st Place",
            2: "2nd Place"
        }
    )

    topper_pdf = topper_pdf[
        ["Place", "student_name", "marks"]
    ]

    topper_pdf.insert(
        0,
        "S.No",
        range(1, len(topper_pdf) + 1)
    )

    topper_pdf.columns = [
        "S.No",
        "Place",
        "Student Name",
        "Average"
    ]

    topper_pdf["Average"] = topper_pdf["Average"].round(2)

    story.extend(
        _pdf_table(
            topper_pdf,
            "1. First and Second Place",
            ["S.No", "Place", "Student Name", "Average"]
        )
    )

    # 2. Above average
    above_pdf = above_average_df[["student_name", "marks"]].copy()
    above_pdf.columns = ["Student Name", "Average"]
    above_pdf.insert(0, "S.No", range(1, len(above_pdf) + 1))
    above_pdf["Average"] = above_pdf["Average"].round(2)

    story.extend(
        _pdf_table(
            above_pdf,
            "2. Above Average",
            ["S.No", "Student Name", "Average"]
        )
    )

    # 3. Below average
    below_pdf = below_average_df[["student_name", "marks"]].copy()
    below_pdf.columns = ["Student Name", "Average"]
    below_pdf.insert(0, "S.No", range(1, len(below_pdf) + 1))
    below_pdf["Average"] = below_pdf["Average"].round(2)

    story.extend(
        _pdf_table(
            below_pdf,
            "3. Below Average",
            ["S.No", "Student Name", "Average"]
        )
    )

    # 4. Very poor
    poor_pdf = very_poor_df[["student_name", "marks"]].copy()
    poor_pdf.columns = ["Student Name", "Average"]
    poor_pdf.insert(0, "S.No", range(1, len(poor_pdf) + 1))
    poor_pdf["Average"] = poor_pdf["Average"].round(2)

    story.extend(
        _pdf_table(
            poor_pdf,
            "4. Last Three Very Poor Students",
            ["S.No", "Student Name", "Average"]
        )
    )

    doc.build(
        story,
        onFirstPage=add_pdf_footer,
        onLaterPages=add_pdf_footer
    )

    buffer.seek(0)
    return buffer.getvalue()


# ============================================================
# DOWNLOAD PDF
# ============================================================

st.divider()

pdf_bytes = build_weekly_pdf()

safe_class = re.sub(r"[^A-Za-z0-9]+", "_", str(selected_class)).strip("_")
safe_subject = re.sub(r"[^A-Za-z0-9]+", "_", str(selected_subject)).strip("_")
safe_week = re.sub(r"[^A-Za-z0-9]+", "_", str(selected_week)).strip("_")

pdf_filename = (
    f"DRT_Weekly_Report_{safe_week}_{safe_class}_{safe_subject}.pdf"
)

st.download_button(
    label="Download Weekly Report PDF",
    data=pdf_bytes,
    file_name=pdf_filename,
    mime="application/pdf",
    type="primary"
)

st.caption(
    f"Generated on {datetime.now().strftime('%d-%m-%Y %I:%M %p')}"
)
