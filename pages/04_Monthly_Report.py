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
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
)

from database import get_supabase


# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title="DRT Monthly Report",
    page_icon="DRT",
    layout="wide",
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
    unsafe_allow_html=True,
)

st.title("Monthly Report")
st.caption("Monthly DRT performance report across all registered weeks in the selected month.")


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
# LOAD WEEKS
# ============================================================

@st.cache_data(ttl=60)
def load_weeks():

    rows = fetch_all_rows(
        "drt_weeks",
        "week_id, week_name, start_date, end_date",
    )

    if not rows:
        return pd.DataFrame()

    result = pd.DataFrame(rows)

    result["week_id"] = pd.to_numeric(
        result["week_id"],
        errors="coerce",
    )

    result["start_date"] = pd.to_datetime(
        result["start_date"],
        errors="coerce",
    ).dt.date

    result["end_date"] = pd.to_datetime(
        result["end_date"],
        errors="coerce",
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
# LOAD DRT DATA
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
        """,
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
    "status",
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
    errors="coerce",
)

df["marks"] = pd.to_numeric(
    df["marks"],
    errors="coerce",
)

df["roll_no"] = pd.to_numeric(
    df["roll_no"],
    errors="coerce",
)


# ============================================================
# SUBJECT NORMALIZATION
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
# ============================================================

def identify_week_name(source_file):

    if pd.isna(source_file):
        return None

    name = str(source_file).lower().strip()
    name = re.sub(r"\.(xlsx|xls|numbers|csv)$", "", name)
    name = re.sub(r"[^a-z0-9]+", " ", name)

    if "june" in name and ("3rd" in name or "3 rd" in name or "third" in name):
        return "June 3rd Week"

    if "june" in name and ("4th" in name or "4 th" in name or "fourth" in name):
        return "June 4th Week"

    if "july" in name and ("1st" in name or "1 st" in name or "ist" in name or "first" in name):
        return "July 1st Week"

    if "july" in name and ("2nd" in name or "2 nd" in name or "iind" in name or "second" in name):
        return "July 2nd Week"

    if "july" in name and ("3rd" in name or "3 rd" in name or "iiird" in name or "third" in name):
        return "July 3rd Week"

    if "july" in name and ("4th" in name or "4 th" in name or "ivth" in name or "fourth" in name):
        return "July 4th Week"

    return None


df["week_name"] = df["source_file"].apply(identify_week_name)


# ============================================================
# MONTH MAPPING FROM REGISTERED WEEKS
# ============================================================

weeks_df["month_name"] = pd.to_datetime(
    weeks_df["start_date"],
    errors="coerce",
).apply(lambda x: x.strftime("%B") if not pd.isna(x) else None)

week_to_month = dict(
    zip(weeks_df["week_name"], weeks_df["month_name"])
)

df["month_name"] = df["week_name"].map(week_to_month)


# ============================================================
# AVAILABLE MONTHS
# ============================================================

available_months = []

for month in weeks_df["month_name"].dropna().tolist():
    if month not in available_months and (df["month_name"] == month).any():
        available_months.append(month)

if not available_months:
    st.error("No staging records match the registered DRT months.")
    st.stop()


# ============================================================
# SELECTIONS
# ============================================================

st.divider()
st.header("Report Selection")

selected_month = st.selectbox(
    "Select Month",
    options=available_months,
    index=len(available_months) - 1,
)

month_weeks = weeks_df[
    weeks_df["month_name"] == selected_month
].copy()

month_weeks = month_weeks[
    month_weeks["week_name"].isin(
        df.loc[df["month_name"] == selected_month, "week_name"].dropna().unique()
    )
].sort_values("start_date")

if month_weeks.empty:
    st.warning("No DRT weeks are available for the selected month.")
    st.stop()

st.caption(
    f"Weeks included in {selected_month}: "
    + ", ".join(month_weeks["week_name"].tolist())
)


# ============================================================
# CLASS
# ============================================================

month_df = df[df["month_name"] == selected_month]

class_options = sorted(
    month_df["class_name"]
    .dropna()
    .unique()
    .tolist()
)

if not class_options:
    st.warning("No classes found for the selected month.")
    st.stop()

selected_class = st.selectbox(
    "Select Class",
    options=class_options,
)


# ============================================================
# SECTION
# ============================================================

section_options = sorted(
    month_df.loc[
        month_df["class_name"] == selected_class,
        "section",
    ]
    .dropna()
    .astype(str)
    .str.strip()
    .loc[lambda s: s != ""]
    .unique()
    .tolist()
)

if not section_options:
    st.warning("No sections found for the selected month and class.")
    st.stop()

selected_section = st.selectbox(
    "Select Section",
    options=section_options,
)


# ============================================================
# SUBJECT
# ============================================================

subject_options = sorted(
    month_df.loc[
        (month_df["class_name"] == selected_class)
        & (month_df["section"] == selected_section),
        "subject_name",
    ]
    .dropna()
    .unique()
    .tolist()
)

if not subject_options:
    st.warning("No subjects found for the selected month, class and section.")
    st.stop()

selected_subject = st.selectbox(
    "Select Subject",
    options=subject_options,
)


# ============================================================
# FILTER SELECTED MONTH / CLASS / SECTION / SUBJECT
# ============================================================

filtered_df = df[
    (df["month_name"] == selected_month)
    & (df["class_name"] == selected_class)
    & (df["section"] == selected_section)
    & (df["subject_name"] == selected_subject)
].copy()


# ============================================================
# EXCLUDED STATUSES
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
# ============================================================

weekly_data = {}

for week_name in month_weeks["week_name"].tolist():

    week_df = valid_df[
        valid_df["week_name"] == week_name
    ].copy()

    if week_df.empty:
        continue

    grouped = (
        week_df
        .groupby(["roll_no", "student_name"], dropna=False)["marks"]
        .mean()
        .reset_index()
    )

    grouped["roll_no"] = pd.to_numeric(
        grouped["roll_no"],
        errors="coerce",
    )

    grouped = grouped[grouped["roll_no"].notna()].copy()

    if grouped.empty:
        continue

    grouped["roll_no"] = grouped["roll_no"].astype(int)
    grouped = grouped.sort_values("roll_no")

    weekly_data[week_name] = grouped


if not weekly_data:
    st.warning("No weekly student averages could be calculated.")
    st.stop()


# ============================================================
# MONTHLY STUDENT MATRIX
# ============================================================

all_weeks = month_weeks["week_name"].tolist()

student_keys = set()
student_names = {}

for week_name, week_df in weekly_data.items():
    for _, row in week_df.iterrows():
        roll = int(row["roll_no"])
        student_keys.add(roll)
        student_names[roll] = row["student_name"]


monthly_rows = []

for roll in sorted(student_keys):

    row = {
        "roll_no": roll,
        "student_name": student_names.get(roll, ""),
    }

    weekly_values = []

    for week_name in all_weeks:

        week_df = weekly_data.get(week_name)

        value = None

        if week_df is not None:
            matches = week_df[week_df["roll_no"] == roll]
            if not matches.empty:
                value = float(matches.iloc[0]["marks"])

        row[week_name] = value

        if value is not None:
            weekly_values.append(value)

    row["weeks_present"] = len(weekly_values)
    row["monthly_average"] = (
        sum(weekly_values) / len(weekly_values)
        if weekly_values
        else None
    )

    monthly_rows.append(row)


monthly_df = pd.DataFrame(monthly_rows)
monthly_df = monthly_df[monthly_df["monthly_average"].notna()].copy()

if monthly_df.empty:
    st.warning("No monthly performance data is available.")
    st.stop()


# ============================================================
# CONTINUOUS TOPPERS
# A continuous topper must have a valid performance in every
# registered week of the selected month.
# Among those students, rank by the average of their weekly
# averages. Dense ranking is used for ties.
# ============================================================

continuous_df = monthly_df[
    monthly_df["weeks_present"] == len(all_weeks)
].copy()

if not continuous_df.empty:
    continuous_df["rank"] = (
        continuous_df["monthly_average"]
        .rank(method="dense", ascending=False)
        .astype(int)
    )
else:
    continuous_df["rank"] = pd.Series(dtype="int64")

first_place_df = continuous_df[
    continuous_df["rank"] == 1
].copy()

second_place_df = continuous_df[
    continuous_df["rank"] == 2
].copy()


# ============================================================
# IMPROVEMENT
# Improvement is calculated from the first registered week
# to the latest registered week of the selected month.
# Only students with valid marks in both endpoints qualify.
# ============================================================

first_week = all_weeks[0]
latest_week = all_weeks[-1]

improvement_df = monthly_df[
    monthly_df[first_week].notna()
    & monthly_df[latest_week].notna()
].copy()

improvement_df["change"] = (
    improvement_df[latest_week]
    - improvement_df[first_week]
)

very_good_df = improvement_df[
    improvement_df["change"] > 0.70
].copy()

improving_df = improvement_df[
    (improvement_df["change"] > 0)
    & (improvement_df["change"] <= 0.70)
].copy()

very_good_df = very_good_df.sort_values(
    "change", ascending=False
)

improving_df = improving_df.sort_values(
    "change", ascending=False
)


# ============================================================
# DISPLAY HELPERS
# ============================================================

def make_student_table(dataframe, value_column, value_label):

    if dataframe.empty:
        return pd.DataFrame()

    result = dataframe[
        ["student_name", value_column]
    ].copy()

    result.columns = [
        "Student Name",
        value_label,
    ]

    result[value_label] = result[value_label].round(2)
    result.insert(0, "S.No", range(1, len(result) + 1))

    return result


def show_empty(message):
    st.info(message)


# ============================================================
# SELECTED PERIOD
# ============================================================

st.divider()
st.header("Monthly DRT Performance Report")

info_col1, info_col2, info_col3, info_col4 = st.columns(4)

with info_col1:
    st.metric("Month", selected_month)

with info_col2:
    st.metric("Class", selected_class)

with info_col3:
    st.metric("Section", selected_section)

with info_col4:
    st.metric("Subject", selected_subject)

st.caption(
    "Included weeks: " + ", ".join(all_weeks)
)


# ============================================================
# 1. CONTINUOUS TOPPERS
# ============================================================

st.divider()
st.subheader("1. Continuous Topper – 1st Place")
st.caption(
    "Students with valid performance in every week of the selected month, "
    "ranked by their monthly average of weekly averages."
)

if first_place_df.empty:
    show_empty("No student completed every week of the selected month, so a continuous 1st-place result cannot be determined.")
else:
    result = make_student_table(
        first_place_df.sort_values("monthly_average", ascending=False),
        "monthly_average",
        "Monthly Average",
    )
    st.dataframe(result, hide_index=True, width="stretch")


st.subheader("2. Continuous Topper – 2nd Place")

if second_place_df.empty:
    show_empty("No continuous 2nd-place student is available for the selected month.")
else:
    result = make_student_table(
        second_place_df.sort_values("monthly_average", ascending=False),
        "monthly_average",
        "Monthly Average",
    )
    st.dataframe(result, hide_index=True, width="stretch")


# ============================================================
# 2. VERY GOOD IMPROVEMENT
# ============================================================

st.divider()
st.subheader("3. Students Showing Very Good Improvement")
st.caption(
    f"Overall improvement of more than +0.70 from {first_week} to {latest_week}."
)

if very_good_df.empty:
    show_empty("No students show more than +0.70 overall improvement.")
else:
    result = very_good_df[
        ["student_name", first_week, latest_week, "change"]
    ].copy()
    result.columns = [
        "Student Name",
        "First Week Average",
        "Latest Week Average",
        "Improvement",
    ]
    result["First Week Average"] = result["First Week Average"].round(2)
    result["Latest Week Average"] = result["Latest Week Average"].round(2)
    result["Improvement"] = result["Improvement"].round(2)
    result.insert(0, "S.No", range(1, len(result) + 1))
    st.dataframe(result, hide_index=True, width="stretch")


# ============================================================
# 3. IMPROVEMENT
# ============================================================

st.divider()
st.subheader("4. Students Showing Improvement")
st.caption(
    f"Overall improvement greater than 0 and up to +0.70 from {first_week} to {latest_week}."
)

if improving_df.empty:
    show_empty("No students show overall improvement in the selected month.")
else:
    result = improving_df[
        ["student_name", first_week, latest_week, "change"]
    ].copy()
    result.columns = [
        "Student Name",
        "First Week Average",
        "Latest Week Average",
        "Improvement",
    ]
    result["First Week Average"] = result["First Week Average"].round(2)
    result["Latest Week Average"] = result["Latest Week Average"].round(2)
    result["Improvement"] = result["Improvement"].round(2)
    result.insert(0, "S.No", range(1, len(result) + 1))
    st.dataframe(result, hide_index=True, width="stretch")


# ============================================================
# PDF HELPERS
# ============================================================

def pdf_table(dataframe, columns, headers, widths=None):

    if dataframe.empty:
        return Table(
            [["No students in this category."]],
            colWidths=[170 * mm],
            style=TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]),
        )

    data = [headers]

    for _, row in dataframe.iterrows():
        values = []
        for col in columns:
            value = row[col]
            if isinstance(value, float):
                values.append(f"{value:.2f}")
            else:
                values.append(str(value))
        data.append(values)

    table = Table(data, colWidths=widths, repeatRows=1)

    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8E8E8")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (-1, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))

    return table


def build_pdf():

    buffer = io.BytesIO()

    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=14 * mm,
        leftMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title="Monthly DRT Performance Report",
        author="The Spice Valley Public School",
    )

    styles = getSampleStyleSheet()

    school_style = ParagraphStyle(
        "School",
        parent=styles["Heading1"],
        alignment=TA_CENTER,
        fontName="Helvetica-Bold",
        fontSize=17,
        leading=21,
        spaceAfter=3,
    )

    subtitle_style = ParagraphStyle(
        "Subtitle",
        parent=styles["Normal"],
        alignment=TA_CENTER,
        fontName="Helvetica",
        fontSize=10.5,
        leading=13,
        spaceAfter=12,
    )

    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Heading2"],
        alignment=TA_CENTER,
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=18,
        spaceAfter=10,
    )

    section_style = ParagraphStyle(
        "Section",
        parent=styles["Heading3"],
        fontName="Helvetica-Bold",
        fontSize=11.5,
        leading=14,
        spaceBefore=8,
        spaceAfter=6,
    )

    normal_style = ParagraphStyle(
        "NormalCustom",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=13,
        spaceAfter=4,
    )

    story = []

    story.append(Paragraph("The Spice Valley Public School", school_style))
    story.append(Paragraph("CBSE Senior Secondary", subtitle_style))
    story.append(Paragraph("MONTHLY DRT PERFORMANCE REPORT", title_style))

    story.append(Paragraph(
        f"Month: <b>{selected_month}</b><br/>"
        f"Class: <b>{selected_class}</b><br/>"
        f"Section: <b>{selected_section}</b><br/>"
        f"Subject: <b>{selected_subject}</b><br/>"
        f"Weeks Included: <b>{', '.join(all_weeks)}</b>",
        normal_style,
    ))

    # Continuous 1st
    story.append(Paragraph("1. Continuous Topper – 1st Place", section_style))

    if first_place_df.empty:
        story.append(Paragraph(
            "No student completed every week of the selected month.",
            normal_style,
        ))
    else:
        pdf_df = first_place_df.sort_values("monthly_average", ascending=False).copy()
        pdf_df.insert(0, "S.No", range(1, len(pdf_df) + 1))
        story.append(pdf_table(
            pdf_df,
            ["S.No", "student_name", "monthly_average"],
            ["S.No", "Student Name", "Monthly Average"],
            [16 * mm, 105 * mm, 40 * mm],
        ))

    # Continuous 2nd
    story.append(Paragraph("2. Continuous Topper – 2nd Place", section_style))

    if second_place_df.empty:
        story.append(Paragraph(
            "No continuous 2nd-place student is available.",
            normal_style,
        ))
    else:
        pdf_df = second_place_df.sort_values("monthly_average", ascending=False).copy()
        pdf_df.insert(0, "S.No", range(1, len(pdf_df) + 1))
        story.append(pdf_table(
            pdf_df,
            ["S.No", "student_name", "monthly_average"],
            ["S.No", "Student Name", "Monthly Average"],
            [16 * mm, 105 * mm, 40 * mm],
        ))

    story.append(PageBreak())

    # Very good improvement
    story.append(Paragraph("3. Students Showing Very Good Improvement", section_style))

    if very_good_df.empty:
        story.append(Paragraph(
            f"No student improved by more than +0.70 from {first_week} to {latest_week}.",
            normal_style,
        ))
    else:
        pdf_df = very_good_df.copy()
        pdf_df.insert(0, "S.No", range(1, len(pdf_df) + 1))
        story.append(pdf_table(
            pdf_df,
            ["S.No", "student_name", first_week, latest_week, "change"],
            ["S.No", "Student Name", "First Week", "Latest Week", "Improvement"],
            [13 * mm, 70 * mm, 27 * mm, 27 * mm, 27 * mm],
        ))

    # Improvement
    story.append(Paragraph("4. Students Showing Improvement", section_style))

    if improving_df.empty:
        story.append(Paragraph(
            f"No student shows overall improvement from {first_week} to {latest_week}.",
            normal_style,
        ))
    else:
        pdf_df = improving_df.copy()
        pdf_df.insert(0, "S.No", range(1, len(pdf_df) + 1))
        story.append(pdf_table(
            pdf_df,
            ["S.No", "student_name", first_week, latest_week, "change"],
            ["S.No", "Student Name", "First Week", "Latest Week", "Improvement"],
            [13 * mm, 70 * mm, 27 * mm, 27 * mm, 27 * mm],
        ))

    # Method note
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "Calculation note: Monthly performance is based on the average of each student's "
        "valid weekly averages. Absent/non-written records are excluded. Improvement is "
        "the latest-week average minus the first-week average.",
        normal_style,
    ))

    def add_page_number(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.drawCentredString(
            A4[0] / 2,
            8 * mm,
            f"Page {doc.page}",
        )
        canvas.restoreState()

    document.build(
        story,
        onFirstPage=add_page_number,
        onLaterPages=add_page_number,
    )

    buffer.seek(0)
    return buffer.getvalue()


# ============================================================
# DOWNLOAD PDF
# ============================================================

st.divider()
st.subheader("Download Monthly Report")

pdf_bytes = build_pdf()

safe_month = re.sub(r"[^A-Za-z0-9]+", "_", selected_month).strip("_")
safe_class = re.sub(r"[^A-Za-z0-9]+", "_", selected_class).strip("_")
safe_section = re.sub(r"[^A-Za-z0-9]+", "_", selected_section).strip("_")
safe_subject = re.sub(r"[^A-Za-z0-9]+", "_", selected_subject).strip("_")

filename = (
    f"Monthly_DRT_Report_"
    f"{safe_month}_"
    f"{safe_class}_"
    f"{safe_section}_"
    f"{safe_subject}.pdf"
)

st.download_button(
    label="Download Monthly Report PDF",
    data=pdf_bytes,
    file_name=filename,
    mime="application/pdf",
    width="stretch",
)


# ============================================================
# FOOTER
# ============================================================

st.divider()
st.caption("DRT Analytics V2 • Monthly subject performance report")
