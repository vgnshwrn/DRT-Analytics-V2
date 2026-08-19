import streamlit as st
from supabase import create_client, Client


@st.cache_resource
def get_supabase() -> Client:

    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_PUBLISHABLE_KEY"]

    return create_client(url, key)
