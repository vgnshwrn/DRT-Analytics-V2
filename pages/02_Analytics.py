import re
import io
from datetime import datetime

import streamlit as st
import pandas as pd

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
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
    page_title="DRT Analytics",
    page_icon="📊",
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


# ============================================================
# TITLE
# ============================================================

st.title("DRT Analytics")

st.caption(
    "Compare student and class performance across all registered DRT weeks."
)


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
# SUPABASE PAGINATION
# IMPORTANT:
# Supabase REST normally returns a maximum of 1000 rows per
# request. The staging table contains 25,000+ records, so a
# normal .select().execute() was only loading the first page.
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
        .astype(str)
        .str.strip()
    )

    return result.sort_values(
        "start_date"
    ).reset_index(drop=True)


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
#
# Different source files may use different capitalization,
# spacing, or abbreviations for the same subject. Normalize
# them once here so every selector, calculation, table, XLSX
# export, and PDF report uses the same canonical subject name.
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
    """Return one canonical subject name for analysis and reporting."""

    if pd.isna(subject):
        return subject

    value = str(subject).strip()

    if not value:
        return None

    # Collapse repeated internal whitespace and compare case-insensitively.
    key = " ".join(value.lower().split())

    return SUBJECT_NORMALIZATION.get(key, value)


# Apply normalization once to the master dataframe.
# All downstream filters/calculations therefore use canonical names.
df["subject_name"] = df["subject_name"].apply(normalize_subject)


# ============================================================
# SOURCE FILE → REGISTERED WEEK
#
# This is the critical rule.
#
# The week is determined by the imported DRT file, NOT by
# test_date. Some of the supplied weekly files contain test
# dates outside their calendar week because tests were entered
# later/earlier.
# ============================================================

def identify_week_name(source_file):

    if pd.isna(source_file):
        return None

    name = str(source_file).lower().strip()

    # Remove file extensions and common punctuation.
    name = re.sub(r"\.(xlsx|xls|numbers|csv)$", "", name)
    name = re.sub(r"[^a-z0-9]+", " ", name)

    # June
    if "june" in name and (
        "3rd" in name
        or "3 rd" in name
        or "third" in name
    ):
        return "June 3rd Week"

    if "june" in name and (
        "4th" in name
        or "4 th" in name
        or "fourth" in name
    ):
        return "June 4th Week"

    # July
    if "july" in name and (
        "1st" in name
        or "1 st" in name
        or "ist" in name
        or "first" in name
    ):
        return "July 1st Week"

    if "july" in name and (
        "2nd" in name
        or "2 nd" in name
        or "iind" in name
        or "second" in name
    ):
        return "July 2nd Week"

    if "july" in name and (
        "3rd" in name
        or "3 rd" in name
        or "iiird" in name
        or "third" in name
    ):
        return "July 3rd Week"

    if "july" in name and (
        "4th" in name
        or "4 th" in name
        or "ivth" in name
        or "fourth" in name
    ):
        return "July 4th Week"

    return None


df["week_name"] = df["source_file"].apply(
    identify_week_name
)


# ============================================================
# VALIDATION
# ============================================================

unknown_week_files = sorted(
    df.loc[
        df["week_name"].isna(),
        "source_file"
    ]
    .dropna()
    .unique()
    .tolist()
)


if unknown_week_files:

    st.warning(
        "Some records could not be assigned to a registered DRT week."
    )

    st.dataframe(
        pd.DataFrame(
            {"Unrecognised Source File": unknown_week_files}
        ),
        hide_index=True,
        width="stretch"
    )


# ============================================================
# WEEK ORDER
# ============================================================

week_options = weeks_df[
    "week_name"
].tolist()


# Keep only weeks that actually have data.
available_week_options = [
    week
    for week in week_options
    if (df["week_name"] == week).any()
]


if not available_week_options:

    st.error(
        "No staging records match the weeks registered in drt_weeks."
    )
    st.stop()


# ============================================================
# ANALYSIS SELECTION
# ============================================================

st.divider()

st.header("🎯 Select Analysis")


selected_week_names = st.multiselect(
    "Select Weeks",
    options=available_week_options,
    default=available_week_options[-1:]
)


if not selected_week_names:

    st.info("Please select at least one DRT week.")
    st.stop()


# Always maintain chronological order.
selected_week_names = [
    week
    for week in available_week_options
    if week in selected_week_names
]


# ============================================================
# CLASS
# ============================================================

class_options = sorted(
    df["class_name"]
    .dropna()
    .unique()
)


if not class_options:
    st.warning("No classes found in the DRT data.")
    st.stop()


selected_class = st.selectbox(
    "Class",
    class_options
)


# ============================================================
# SECTION
# ============================================================

section_df = df[
    df["class_name"] == selected_class
]


section_options = sorted(
    section_df["section"]
    .dropna()
    .unique()
)


if not section_options:
    st.warning("No sections found for the selected class.")
    st.stop()


selected_section = st.selectbox(
    "Section",
    section_options
)


# ============================================================
# SUBJECT
# ============================================================

subject_df = section_df[
    section_df["section"] == selected_section
]


subject_options = sorted(
    subject_df["subject_name"]
    .dropna()
    .unique()
)


if not subject_options:
    st.warning("No subjects found for the selected class/section.")
    st.stop()


selected_subject = st.selectbox(
    "Subject",
    subject_options
)


# ============================================================
# FILTER ANALYSIS DATA
# ============================================================

filtered_df = df[
    (df["class_name"] == selected_class)
    &
    (df["section"] == selected_section)
    &
    (df["subject_name"] == selected_subject)
    &
    (df["week_name"].isin(selected_week_names))
].copy()


if filtered_df.empty:

    st.warning(
        "No data available for the selected analysis."
    )

    st.stop()


# ============================================================
# SELECTED WEEKS
# ============================================================

st.divider()

st.header("📅 Selected Weeks")


selected_week_table = []

for week_name in selected_week_names:

    row = weeks_df[
        weeks_df["week_name"] == week_name
    ]

    if row.empty:
        continue

    row = row.iloc[0]

    selected_week_table.append(
        {
            "Week": week_name,
            "Start": row["start_date"],
            "End": row["end_date"]
        }
    )


st.dataframe(
    pd.DataFrame(selected_week_table),
    hide_index=True,
    width="stretch"
)


# ============================================================
# BUILD WEEKLY STUDENT AVERAGES
#
# A student may have multiple entries for the same subject
# in one weekly import. Valid marks are averaged. Absent/
# blank-mark rows do not contribute to the average.
# ============================================================

student_week_data = {}


for week_name in selected_week_names:

    week_df = filtered_df[
        filtered_df["week_name"] == week_name
    ].copy()

    week_df = week_df[
        week_df["marks"].notna()
    ]

    if week_df.empty:
        continue

    grouped = (
        week_df
        .groupby(
            [
                "roll_no",
                "student_name"
            ],
            dropna=False
        )["marks"]
        .mean()
        .reset_index()
    )

    grouped["roll_no"] = pd.to_numeric(
        grouped["roll_no"],
        errors="coerce"
    )

    grouped = grouped[
        grouped["roll_no"].notna()
    ]

    grouped = grouped.sort_values(
        "roll_no"
    )

    student_week_data[week_name] = grouped


# ============================================================
# CLASS AVERAGES
# ============================================================

class_averages = {}


for week_name in selected_week_names:

    week_students = student_week_data.get(
        week_name
    )

    if week_students is None or week_students.empty:
        continue

    class_averages[week_name] = float(
        week_students["marks"].mean()
    )


# ============================================================
# STUDENT MASTER LIST
# ============================================================

student_master = {}


for week_students in student_week_data.values():

    for _, row in week_students.iterrows():

        roll = row["roll_no"]

        if pd.isna(roll):
            continue

        roll = int(roll)

        student_master[roll] = {
            "Roll No": roll,
            "Student Name": row["student_name"]
        }


student_master_rows = sorted(
    student_master.values(),
    key=lambda x: x["Roll No"]
)


# ============================================================
# HELPER
# ============================================================

def get_student_mark(week_name, roll_no):

    week_students = student_week_data.get(
        week_name
    )

    if week_students is None:
        return None

    matches = week_students[
        week_students["roll_no"] == roll_no
    ]

    if matches.empty:
        return None

    return float(
        matches.iloc[0]["marks"]
    )



def prepare_result_table(dataframe):
    """
    Prepare every school-facing student/result table.

    Roll No is used internally but never displayed.
    Every table containing Student Name receives a fresh S.No.
    """
    result = dataframe.copy()

    if "Roll No" in result.columns:
        result = result.drop(columns=["Roll No"])

    if "S.No" in result.columns:
        result = result.drop(columns=["S.No"])

    if "Student Name" in result.columns:
        result.insert(
            0,
            "S.No",
            range(1, len(result) + 1)
        )

    return result


# ============================================================
# CLASS PERFORMANCE
# ============================================================

st.divider()

st.header("📈 Class Performance")


class_performance_rows = []

for week_name in selected_week_names:

    average = class_averages.get(week_name)

    if average is None:
        continue

    class_performance_rows.append(
        {
            "Week": week_name,
            "Class Average": round(average, 2)
        }
    )


class_performance_df = pd.DataFrame(
    class_performance_rows
)

if not class_performance_df.empty:
    class_performance_df = class_performance_df.sort_values(
        "Class Average",
        ascending=False,
        kind="stable"
    )

st.dataframe(
    class_performance_df,
    hide_index=True,
    width="stretch"
)


# ============================================================
# STUDENT PERFORMANCE
# ============================================================

st.divider()

st.header("🎓 Student Performance")


student_performance_rows = []

for student in student_master_rows:

    row_data = {
        "S.No": 0, "Roll No": student["Roll No"],
        "Student Name": student["Student Name"]
    }

    for week_name in selected_week_names:

        value = get_student_mark(
            week_name,
            student["Roll No"]
        )

        row_data[week_name] = (
            "—"
            if value is None
            else round(value, 2)
        )

    student_performance_rows.append(
        row_data
    )


student_performance_df = pd.DataFrame(
    student_performance_rows
)

if not student_performance_df.empty:
    # Display order only: calculations remain chronological.
    latest_week = selected_week_names[-1]

    student_performance_df["_latest_sort"] = pd.to_numeric(
        student_performance_df[latest_week],
        errors="coerce"
    )

    student_performance_df = (
        student_performance_df
        .sort_values(
            "_latest_sort",
            ascending=False,
            na_position="last",
            kind="stable"
        )
        .drop(columns="_latest_sort")
    )

st.dataframe(
    prepare_result_table(student_performance_df),
    hide_index=True,
    width="stretch"
)


# ============================================================
# BELOW CLASS AVERAGE
# ============================================================

st.divider()

st.header("🟡 Students Below Class Average")

st.caption(
    "Students whose weekly subject average is below "
    "the class average for that week."
)


for week_name in selected_week_names:

    class_average = class_averages.get(week_name)
    week_students = student_week_data.get(week_name)

    if class_average is None or week_students is None:
        continue

    below_df = week_students[
        week_students["marks"] < class_average
    ].sort_values(
        "marks",
        ascending=False,
        kind="stable"
    ).copy()

    st.subheader(f"📅 {week_name}")

    if below_df.empty:

        st.success(
            "No students are below the class average."
        )

    else:

        output = below_df[
            ["roll_no", "student_name", "marks"]
        ].copy()

        output.columns = [
            "Roll No",
            "Student Name",
            "Student Average"
        ]

        output["Class Average"] = round(
            class_average, 2
        )

        output["Student Average"] = output[
            "Student Average"
        ].round(2)

        st.dataframe(
            prepare_result_table(output),
            hide_index=True,
            width="stretch"
        )


# ============================================================
# VERY LOW PERFORMANCE
# ============================================================

st.divider()

st.header("🚨 Very Low Performance")

st.caption(
    "Very low performance = below 50% of that week's class average."
)


for week_name in selected_week_names:

    class_average = class_averages.get(week_name)
    week_students = student_week_data.get(week_name)

    if class_average is None or week_students is None:
        continue

    threshold = class_average / 2

    very_low_df = week_students[
        week_students["marks"] < threshold
    ].sort_values(
        "marks",
        ascending=False,
        kind="stable"
    ).copy()

    st.subheader(f"📅 {week_name}")

    st.caption(
        f"Very-low threshold: {threshold:.2f} / 5"
    )

    if very_low_df.empty:

        st.success(
            "No students fall under the very-low threshold."
        )

    else:

        output = very_low_df[
            ["roll_no", "student_name", "marks"]
        ].copy()

        output.columns = [
            "Roll No",
            "Student Name",
            "Student Average"
        ]

        output["Class Average"] = round(
            class_average, 2
        )

        output["Very Low Threshold"] = round(
            threshold, 2
        )

        output["Student Average"] = output[
            "Student Average"
        ].round(2)

        st.dataframe(
            prepare_result_table(output),
            hide_index=True,
            width="stretch"
        )


# ============================================================
# ABOVE 4
# ============================================================

st.divider()

st.header("🟢 Students Scoring Above 4")

st.caption(
    "Students whose weekly subject average is strictly above 4."
)


for week_name in selected_week_names:

    week_students = student_week_data.get(week_name)

    if week_students is None:
        continue

    above_four_df = week_students[
        week_students["marks"] > 4
    ].sort_values(
        "marks",
        ascending=False,
        kind="stable"
    ).copy()

    st.subheader(f"📅 {week_name}")

    if above_four_df.empty:

        st.info("No students scored above 4.")

    else:

        output = above_four_df[
            ["roll_no", "student_name", "marks"]
        ].copy()

        output.columns = [
            "Roll No",
            "Student Name",
            "Average"
        ]

        output["Average"] = output[
            "Average"
        ].round(2)

        st.dataframe(
            prepare_result_table(output),
            hide_index=True,
            width="stretch"
        )


# ============================================================
# MULTI-WEEK ANALYSIS
# ============================================================

if len(selected_week_names) >= 2:

    st.divider()

    st.header("🔄 Week-to-Week Student Comparison")

    st.caption(
        "Every selected week is compared in chronological order."
    )


    # ========================================================
    # INDIVIDUAL PERFORMANCE
    # ========================================================

    st.subheader("📊 Individual Student Performance")

    comparison_rows = []

    for student in student_master_rows:

        row_data = {
            "S.No": 0, "Roll No": student["Roll No"],
            "Student Name": student["Student Name"]
        }

        values = []

        for week_name in selected_week_names:

            value = get_student_mark(
                week_name,
                student["Roll No"]
            )

            if value is None:

                row_data[week_name] = "—"

            else:

                row_data[week_name] = round(
                    value, 2
                )

                values.append(value)

        if len(values) >= 2:

            first_value = values[0]
            latest_value = values[-1]

            change = latest_value - first_value

            row_data["Overall Change"] = round(
                change, 2
            )

            if change > 0.25:
                row_data["Trend"] = "📈 Improving"

            elif change < -0.25:
                row_data["Trend"] = "📉 Declining"

            else:
                row_data["Trend"] = "➡️ Stable"

        else:

            row_data["Overall Change"] = "—"
            row_data["Trend"] = "—"

        comparison_rows.append(row_data)


    comparison_df = pd.DataFrame(comparison_rows)

    if not comparison_df.empty:
        latest_week = selected_week_names[-1]

        comparison_df["_latest_sort"] = pd.to_numeric(
            comparison_df[latest_week],
            errors="coerce"
        )

        comparison_df["_change_sort"] = pd.to_numeric(
            comparison_df["Overall Change"],
            errors="coerce"
        )

        comparison_df = (
            comparison_df
            .sort_values(
                ["_latest_sort", "_change_sort"],
                ascending=[False, False],
                na_position="last",
                kind="stable"
            )
            .drop(
                columns=["_latest_sort", "_change_sort"]
            )
        )

    st.dataframe(
        prepare_result_table(comparison_df),
        hide_index=True,
        width="stretch"
    )


    # ========================================================
    # ABOVE 4 CONTINUOUSLY
    # ========================================================

    st.subheader("🟢 Students Above 4 Continuously")

    st.caption(
        "Students who scored above 4 in every selected week."
    )

    rows = []

    for student in student_master_rows:

        qualifies = True

        for week_name in selected_week_names:

            value = get_student_mark(
                week_name,
                student["Roll No"]
            )

            if value is None or value <= 4:
                qualifies = False
                break

        if qualifies:

            rows.append(
                {
                    "S.No": 0, "Roll No": student["Roll No"],
                    "Student Name": student["Student Name"]
                }
            )


    if rows:

        st.dataframe(
            prepare_result_table(
                pd.DataFrame(rows).sort_values("Roll No")
            ),
            hide_index=True,
            width="stretch"
        )

    else:

        st.info(
            "No student remained above 4 in every selected week."
        )


    # ========================================================
    # BELOW CLASS AVERAGE CONTINUOUSLY
    # ========================================================

    st.subheader("🟡 Students Below Class Average Continuously")

    st.caption(
        "Students who remained below the class average in every selected week."
    )

    rows = []

    for student in student_master_rows:

        qualifies = True

        for week_name in selected_week_names:

            value = get_student_mark(
                week_name,
                student["Roll No"]
            )

            class_average = class_averages.get(
                week_name
            )

            if (
                value is None
                or class_average is None
                or value >= class_average
            ):

                qualifies = False
                break

        if qualifies:

            rows.append(
                {
                    "S.No": 0, "Roll No": student["Roll No"],
                    "Student Name": student["Student Name"]
                }
            )


    if rows:

        st.dataframe(
            prepare_result_table(
                pd.DataFrame(rows).sort_values("Roll No")
            ),
            hide_index=True,
            width="stretch"
        )

    else:

        st.success(
            "No student remained below class average in every selected week."
        )


    # ========================================================
    # VERY LOW CONTINUOUSLY
    # ========================================================

    st.subheader(
        "🚨 Students in Very Low Performance Continuously"
    )

    st.caption(
        "Students who remained below 50% of the class average "
        "in every selected week."
    )

    rows = []

    for student in student_master_rows:

        qualifies = True

        for week_name in selected_week_names:

            value = get_student_mark(
                week_name,
                student["Roll No"]
            )

            class_average = class_averages.get(
                week_name
            )

            if (
                value is None
                or class_average is None
            ):

                qualifies = False
                break

            threshold = class_average / 2

            if value >= threshold:

                qualifies = False
                break

        if qualifies:

            rows.append(
                {
                    "S.No": 0, "Roll No": student["Roll No"],
                    "Student Name": student["Student Name"]
                }
            )


    if rows:

        st.dataframe(
            prepare_result_table(
                pd.DataFrame(rows).sort_values("Roll No")
            ),
            hide_index=True,
            width="stretch"
        )

    else:

        st.success(
            "No student remained in very-low performance in every selected week."
        )


    # ========================================================
    # IMPROVING STUDENTS
    # ========================================================

    st.subheader("📈 Students Showing Improvement")

    improving_rows = []

    for student in student_master_rows:

        values = []

        for week_name in selected_week_names:

            value = get_student_mark(
                week_name,
                student["Roll No"]
            )

            if value is not None:
                values.append(value)

        if len(values) >= 2:

            change = values[-1] - values[0]

            if change > 0:

                improving_rows.append(
                    {
                        "S.No": 0, "Roll No": student["Roll No"],
                        "Student Name": student["Student Name"],
                        "First Week": round(values[0], 2),
                        "Latest Week": round(values[-1], 2),
                        "Improvement": round(change, 2)
                    }
                )


    if improving_rows:

        improving_df = pd.DataFrame(improving_rows).sort_values(
            "Improvement",
            ascending=False,
            kind="stable"
        )

        st.dataframe(
            prepare_result_table(improving_df),
            hide_index=True,
            width="stretch"
        )

    else:

        st.info(
            "No students show an overall improvement."
        )


    # ========================================================
    # DECLINING STUDENTS
    # ========================================================

    st.subheader("📉 Students Showing Decline")

    declining_rows = []

    for student in student_master_rows:

        values = []

        for week_name in selected_week_names:

            value = get_student_mark(
                week_name,
                student["Roll No"]
            )

            if value is not None:
                values.append(value)

        if len(values) >= 2:

            change = values[-1] - values[0]

            if change < 0:

                declining_rows.append(
                    {
                        "S.No": 0, "Roll No": student["Roll No"],
                        "Student Name": student["Student Name"],
                        "First Week": round(values[0], 2),
                        "Latest Week": round(values[-1], 2),
                        "Decline": round(change, 2)
                    }
                )


    if declining_rows:

        declining_df = pd.DataFrame(declining_rows).sort_values(
            "Decline",
            ascending=True,
            kind="stable"
        )

        st.dataframe(
            prepare_result_table(declining_df),
            hide_index=True,
            width="stretch"
        )

    else:

        st.success(
            "No students show an overall decline."
        )


    # ========================================================
    # CLASS WEEK-TO-WEEK PERFORMANCE
    # ========================================================

    st.divider()

    st.header("📊 Class Week-to-Week Performance")

    class_comparison_rows = []

    previous_average = None

    for week_name in selected_week_names:

        current_average = class_averages.get(
            week_name
        )

        if current_average is None:
            continue

        if previous_average is None:

            class_comparison_rows.append(
                {
                    "Week": week_name,
                    "Class Average": round(current_average, 2),
                    "Change": "—",
                    "Status": "Baseline"
                }
            )

        else:

            change = current_average - previous_average

            if change > 0.25:
                status = "📈 Improved"

            elif change < -0.25:
                status = "📉 Declined"

            else:
                status = "➡️ Stable"

            class_comparison_rows.append(
                {
                    "Week": week_name,
                    "Class Average": round(current_average, 2),
                    "Change": round(change, 2),
                    "Status": status
                }
            )

        previous_average = current_average


    class_comparison_df = pd.DataFrame(
        class_comparison_rows
    )

    if not class_comparison_df.empty:
        class_comparison_df = class_comparison_df.sort_values(
            "Class Average",
            ascending=False,
            kind="stable"
        )

    st.dataframe(
        class_comparison_df,
        hide_index=True,
        width="stretch"
    )


    # ========================================================
    # CLASS OVERALL STATUS
    # ========================================================

    st.subheader("📋 Overall Class Status")

    valid_averages = [
        class_averages[w]
        for w in selected_week_names
        if w in class_averages
    ]

    if len(valid_averages) >= 2:

        first_average = valid_averages[0]
        latest_average = valid_averages[-1]

        overall_change = latest_average - first_average

        col1, col2, col3 = st.columns(3)

        with col1:
            st.metric(
                "First Selected Week",
                f"{first_average:.2f}"
            )

        with col2:
            st.metric(
                "Latest Selected Week",
                f"{latest_average:.2f}"
            )

        with col3:
            st.metric(
                "Overall Change",
                f"{overall_change:+.2f}"
            )

        if overall_change > 0.25:

            st.success(
                "📈 Overall class performance has improved."
            )

        elif overall_change < -0.25:

            st.error(
                "📉 Overall class performance has declined."
            )

        else:

            st.info(
                "➡️ Overall class performance is relatively stable."
            )


# ============================================================
# OVERALL PDF REPORT
# ============================================================

def _pdf_safe(value):
    """Convert a value to clean PDF text."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    return str(value)


def _pdf_table(dataframe, title=None, max_rows=None):
    """Create a styled reportlab table from a DataFrame."""
    if dataframe is None or dataframe.empty:
        return []

    data = dataframe.copy()

    if max_rows is not None:
        data = data.head(max_rows)

    data = data.fillna("—")

    # Convert everything to strings for predictable PDF rendering.
    values = [[str(c) for c in data.columns]]
    values.extend(
        [[_pdf_safe(v) for v in row] for row in data.itertuples(index=False, name=None)]
    )

    # Keep wide reports readable in landscape A4.
    page_width = landscape(A4)[0] - 24 * mm
    col_count = max(1, len(values[0]))
    col_width = page_width / col_count

    # Give the name column more room.
    widths = [col_width] * col_count
    for idx, col in enumerate(data.columns):
        if str(col) == "Student Name":
            widths[idx] = min(page_width * 0.28, col_width * 1.8)

    # Rebalance widths so the total remains within the page.
    total = sum(widths)
    if total > page_width:
        factor = page_width / total
        widths = [w * factor for w in widths]

    table = Table(values, colWidths=widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E9ECEF")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#111111")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                ("LEADING", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B8B8B8")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [
                    colors.white,
                    colors.HexColor("#F7F7F7")
                ]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )

    elements = []
    if title:
        elements.append(
            Paragraph(
                title,
                ParagraphStyle(
                    "TableTitle",
                    parent=getSampleStyleSheet()["Heading3"],
                    fontName="Helvetica-Bold",
                    fontSize=11,
                    spaceBefore=8,
                    spaceAfter=5,
                ),
            )
        )
    elements.append(table)
    elements.append(Spacer(1, 6))
    return elements


def build_overall_pdf():
    """Build a professional PDF for the current Analytics selections."""
    buffer = io.BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title="DRT Overall Analytics Report",
        author="The Spice Valley Public School",
    )

    styles = getSampleStyleSheet()

    school_style = ParagraphStyle(
        "School",
        parent=styles["Title"],
        alignment=TA_CENTER,
        fontName="Helvetica-Bold",
        fontSize=19,
        leading=22,
        spaceAfter=2,
    )

    school_sub_style = ParagraphStyle(
        "SchoolSub",
        parent=styles["Normal"],
        alignment=TA_CENTER,
        fontName="Helvetica",
        fontSize=10.5,
        leading=13,
        spaceAfter=8,
    )

    report_title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Heading1"],
        alignment=TA_CENTER,
        fontName="Helvetica-Bold",
        fontSize=16,
        leading=19,
        spaceBefore=4,
        spaceAfter=10,
    )

    section_style = ParagraphStyle(
        "Section",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12.5,
        leading=15,
        spaceBefore=9,
        spaceAfter=5,
    )

    normal_style = ParagraphStyle(
        "NormalReport",
        parent=styles["Normal"],
        fontSize=9,
        leading=12,
        spaceAfter=3,
    )

    story = []

    # Header
    story.append(Paragraph("The Spice Valley Public School", school_style))
    story.append(Paragraph("CBSE Senior Secondary", school_sub_style))
    story.append(Paragraph("DRT OVERALL ANALYTICS REPORT", report_title_style))

    # Selection summary
    week_text = ", ".join(selected_week_names)
    summary_data = [
        ["Class", _pdf_safe(selected_class)],
        ["Section", _pdf_safe(selected_section)],
        ["Subject", _pdf_safe(selected_subject)],
        ["Selected Weeks", _pdf_safe(week_text)],
        ["Generated", datetime.now().strftime("%d %B %Y, %I:%M %p")],
    ]

    summary_table = Table(summary_data, colWidths=[38 * mm, 230 * mm])
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#E9ECEF")),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B8B8B8")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(summary_table)
    story.append(Spacer(1, 8))

    # Class performance
    story.append(Paragraph("1. Class Performance", section_style))
    if not class_performance_df.empty:
        cp = class_performance_df.copy()
        cp["Class Average"] = pd.to_numeric(cp["Class Average"], errors="coerce")
        cp = cp.sort_values("Class Average", ascending=False, kind="stable")
        cp["Class Average"] = cp["Class Average"].round(2)
        story.extend(_pdf_table(cp))
    else:
        story.append(Paragraph("No class performance data available.", normal_style))

    # Student performance
    story.append(Paragraph("2. Student Performance", section_style))
    sp = student_performance_df.copy()
    if not sp.empty:
        # The web table is already sorted by latest selected week.
        story.extend(_pdf_table(prepare_result_table(sp)))

    # Below class average
    story.append(Paragraph("3. Students Below Class Average", section_style))
    below_any = False
    for week_name in selected_week_names:
        week_students = student_week_data.get(week_name)
        class_average = class_averages.get(week_name)

        if week_students is None or class_average is None or week_students.empty:
            continue

        below = week_students[
            week_students["marks"] < class_average
        ].copy()

        if below.empty:
            continue

        below_any = True
        below = below.sort_values("marks", ascending=False, kind="stable")
        output = below[["roll_no", "student_name", "marks"]].copy()
        output.columns = ["Roll No", "Student Name", "Student Average"]
        output["Class Average"] = round(class_average, 2)
        output["Student Average"] = output["Student Average"].round(2)
        output = prepare_result_table(output)

        story.append(
            Paragraph(
                f"{week_name} — Class Average: {class_average:.2f}",
                ParagraphStyle(
                    "WeekLabel",
                    parent=normal_style,
                    fontName="Helvetica-Bold",
                    spaceBefore=4,
                ),
            )
        )
        story.extend(_pdf_table(output))

    if not below_any:
        story.append(Paragraph("No students were below class average in the selected weeks.", normal_style))

    # Very low performance
    story.append(Paragraph("4. Very Low Performance", section_style))
    very_low_any = False
    for week_name in selected_week_names:
        week_students = student_week_data.get(week_name)
        class_average = class_averages.get(week_name)

        if week_students is None or class_average is None or week_students.empty:
            continue

        threshold = class_average / 2
        very_low = week_students[
            week_students["marks"] < threshold
        ].copy()

        if very_low.empty:
            continue

        very_low_any = True
        very_low = very_low.sort_values("marks", ascending=False, kind="stable")
        output = very_low[["roll_no", "student_name", "marks"]].copy()
        output.columns = ["Roll No", "Student Name", "Student Average"]
        output["Class Average"] = round(class_average, 2)
        output["Very Low Threshold"] = round(threshold, 2)
        output["Student Average"] = output["Student Average"].round(2)
        output = prepare_result_table(output)

        story.append(
            Paragraph(
                f"{week_name} — Threshold: {threshold:.2f}",
                ParagraphStyle(
                    "WeekLabel2",
                    parent=normal_style,
                    fontName="Helvetica-Bold",
                    spaceBefore=4,
                ),
            )
        )
        story.extend(_pdf_table(output))

    if not very_low_any:
        story.append(Paragraph("No students were in very low performance in the selected weeks.", normal_style))

    # Above 4
    story.append(Paragraph("5. Students Scoring Above 4", section_style))
    above_any = False
    for week_name in selected_week_names:
        week_students = student_week_data.get(week_name)

        if week_students is None or week_students.empty:
            continue

        above = week_students[
            week_students["marks"] > 4
        ].copy()

        if above.empty:
            continue

        above_any = True
        above = above.sort_values("marks", ascending=False, kind="stable")
        output = above[["roll_no", "student_name", "marks"]].copy()
        output.columns = ["Roll No", "Student Name", "Average"]
        output["Average"] = output["Average"].round(2)
        output = prepare_result_table(output)

        story.append(
            Paragraph(
                week_name,
                ParagraphStyle(
                    "WeekLabel3",
                    parent=normal_style,
                    fontName="Helvetica-Bold",
                    spaceBefore=4,
                ),
            )
        )
        story.extend(_pdf_table(output))

    if not above_any:
        story.append(Paragraph("No students scored above 4 in the selected weeks.", normal_style))

    # Multi-week analysis
    if len(selected_week_names) >= 2:
        story.append(PageBreak())
        story.append(Paragraph("6. Week-to-Week Student Comparison", section_style))

        comparison_rows = []
        for student in student_master_rows:
            row_data = {
                "Roll No": student["Roll No"],
                "Student Name": student["Student Name"],
            }
            values = []

            for week_name in selected_week_names:
                value = get_student_mark(week_name, student["Roll No"])
                row_data[week_name] = "—" if value is None else round(value, 2)
                if value is not None:
                    values.append(value)

            if len(values) >= 2:
                change = values[-1] - values[0]
                row_data["Overall Change"] = round(change, 2)
                if change > 0.25:
                    row_data["Trend"] = "Improving"
                elif change < -0.25:
                    row_data["Trend"] = "Declining"
                else:
                    row_data["Trend"] = "Stable"
            else:
                row_data["Overall Change"] = "—"
                row_data["Trend"] = "—"

            comparison_rows.append(row_data)

        comparison_df = pd.DataFrame(comparison_rows)

        if not comparison_df.empty:
            latest_week = selected_week_names[-1]
            comparison_df["_sort"] = pd.to_numeric(
                comparison_df[latest_week], errors="coerce"
            )
            comparison_df = comparison_df.sort_values(
                "_sort", ascending=False, na_position="last", kind="stable"
            ).drop(columns="_sort")
            story.extend(_pdf_table(prepare_result_table(comparison_df)))

        # Class week-to-week
        story.append(Paragraph("7. Class Week-to-Week Performance", section_style))
        class_rows = []
        previous_average = None

        for week_name in selected_week_names:
            current = class_averages.get(week_name)
            if current is None:
                continue

            if previous_average is None:
                change = "—"
                status = "Baseline"
            else:
                delta = current - previous_average
                change = round(delta, 2)
                if delta > 0.25:
                    status = "Improved"
                elif delta < -0.25:
                    status = "Declined"
                else:
                    status = "Stable"

            class_rows.append({
                "Week": week_name,
                "Class Average": round(current, 2),
                "Change": change,
                "Status": status,
            })
            previous_average = current

        class_week_df = pd.DataFrame(class_rows)
        if not class_week_df.empty:
            # Preserve chronological order for a true week-to-week report.
            story.extend(_pdf_table(class_week_df))

        # Continuous categories
        def continuous_students(condition):
            rows = []
            for student in student_master_rows:
                qualifies = True
                for week_name in selected_week_names:
                    value = get_student_mark(week_name, student["Roll No"])
                    class_average = class_averages.get(week_name)
                    if value is None or class_average is None or not condition(value, class_average):
                        qualifies = False
                        break
                if qualifies:
                    rows.append({
                        "Roll No": student["Roll No"],
                        "Student Name": student["Student Name"],
                    })
            return pd.DataFrame(rows)

        story.append(Paragraph("8. Students Above 4 Continuously", section_style))
        continuous_above = continuous_students(lambda value, avg: value > 4)
        if not continuous_above.empty:
            story.extend(_pdf_table(prepare_result_table(continuous_above)))
        else:
            story.append(Paragraph("No student remained above 4 in every selected week.", normal_style))

        story.append(Paragraph("9. Students Below Class Average Continuously", section_style))
        continuous_below = continuous_students(lambda value, avg: value < avg)
        if not continuous_below.empty:
            story.extend(_pdf_table(prepare_result_table(continuous_below)))
        else:
            story.append(Paragraph("No student remained below class average in every selected week.", normal_style))

        story.append(Paragraph("10. Students in Very Low Performance Continuously", section_style))
        continuous_low = continuous_students(lambda value, avg: value < (avg / 2))
        if not continuous_low.empty:
            story.extend(_pdf_table(prepare_result_table(continuous_low)))
        else:
            story.append(Paragraph("No student remained in very low performance in every selected week.", normal_style))

        # Improvement / decline
        improvement_rows = []
        decline_rows = []

        for student in student_master_rows:
            values = []
            for week_name in selected_week_names:
                value = get_student_mark(week_name, student["Roll No"])
                if value is not None:
                    values.append(value)

            if len(values) >= 2:
                change = values[-1] - values[0]
                item = {
                    "Roll No": student["Roll No"],
                    "Student Name": student["Student Name"],
                    "First Week": round(values[0], 2),
                    "Latest Week": round(values[-1], 2),
                    "Change": round(change, 2),
                }
                if change > 0:
                    improvement_rows.append(item)
                elif change < 0:
                    decline_rows.append(item)

        story.append(Paragraph("11. Students Showing Improvement", section_style))
        improvement_df = pd.DataFrame(improvement_rows)
        if not improvement_df.empty:
            improvement_df = improvement_df.sort_values(
                "Change", ascending=False, kind="stable"
            )
            story.extend(_pdf_table(prepare_result_table(improvement_df)))
        else:
            story.append(Paragraph("No students show an overall improvement.", normal_style))

        story.append(Paragraph("12. Students Showing Decline", section_style))
        decline_df = pd.DataFrame(decline_rows)
        if not decline_df.empty:
            decline_df = decline_df.sort_values(
                "Change", ascending=True, kind="stable"
            )
            story.extend(_pdf_table(prepare_result_table(decline_df)))
        else:
            story.append(Paragraph("No students show an overall decline.", normal_style))

    # Final status
    story.append(Paragraph("Overall Class Status", section_style))

    valid_averages = [
        class_averages[w]
        for w in selected_week_names
        if w in class_averages
    ]

    if len(valid_averages) >= 2:
        first_average = valid_averages[0]
        latest_average = valid_averages[-1]
        overall_change = latest_average - first_average

        status = (
            "Overall class performance has improved."
            if overall_change > 0.25
            else "Overall class performance has declined."
            if overall_change < -0.25
            else "Overall class performance is relatively stable."
        )

        status_df = pd.DataFrame([
            {
                "First Selected Week": round(first_average, 2),
                "Latest Selected Week": round(latest_average, 2),
                "Overall Change": round(overall_change, 2),
                "Status": status,
            }
        ])
        story.extend(_pdf_table(status_df))
    elif valid_averages:
        story.append(
            Paragraph(
                f"Selected week class average: {valid_averages[0]:.2f}",
                normal_style,
            )
        )
    else:
        story.append(Paragraph("No class average is available for the selected weeks.", normal_style))

    # Footer on every page
    def draw_footer(canvas, doc_obj):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.drawString(
            12 * mm,
            7 * mm,
            "The Spice Valley Public School • DRT Analytics"
        )
        canvas.drawRightString(
            landscape(A4)[0] - 12 * mm,
            7 * mm,
            f"Page {doc_obj.page}"
        )
        canvas.restoreState()

    doc.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    buffer.seek(0)
    return buffer.getvalue()


st.divider()
st.header("Download Overall Report")
st.caption(
    "Generate a printable PDF report using the currently selected weeks, "
    "class, section and subject."
)

try:
    pdf_bytes = build_overall_pdf()

    safe_class = re.sub(r"[^A-Za-z0-9]+", "_", str(selected_class)).strip("_")
    safe_section = re.sub(r"[^A-Za-z0-9]+", "_", str(selected_section)).strip("_")
    safe_subject = re.sub(r"[^A-Za-z0-9]+", "_", str(selected_subject)).strip("_")

    pdf_filename = (
        f"DRT_Report_{safe_class}_{safe_section}_{safe_subject}_"
        f"{len(selected_week_names)}_Weeks.pdf"
    )

    st.download_button(
        label="Download Overall Report (PDF)",
        data=pdf_bytes,
        file_name=pdf_filename,
        mime="application/pdf",
        use_container_width=True,
    )

except Exception as e:
    st.error(f"Unable to generate the PDF report: {e}")


# ============================================================
# FOOTER
# ============================================================

st.divider()

st.caption(
    f"DRT Analytics • {len(df):,} staging records loaded • "
    f"{len(available_week_options)} weeks available"
)
