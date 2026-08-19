import streamlit as st

from database import get_supabase


# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title="DRT Analytics V2",
    page_icon="📊",
    layout="wide"
)


# ============================================================
# SCHOOL HEADER
# ============================================================

st.markdown(
    """
    <div style="text-align:center;">
        <h1>The Spice Valley Public School</h1>
        <p style="font-size:20px;">
            CBSE Senior Secondary
        </p>
    </div>
    """,
    unsafe_allow_html=True
)


st.divider()


# ============================================================
# APPLICATION TITLE
# ============================================================

st.title("DRT Analytics V2")

st.caption(
    "Weekly DRT performance analysis and academic insights."
)


# ============================================================
# DATABASE CONNECTION
# ============================================================

try:

    supabase = get_supabase()

    response = (
        supabase
        .table("students")
        .select(
            "student_id",
            count="exact"
        )
        .limit(1)
        .execute()
    )

    st.success(
        "Database connected successfully."
    )

    st.metric(
        "Students",
        response.count
    )


except Exception as e:

    st.error(
        "Database connection failed."
    )

    st.code(
        str(e)
    )