# -*- coding: utf-8 -*-
"""
Acesso GCP do Atlas standalone (substitui o pacote `src` do monorepo).

Credenciais (uma service account para tudo):
  * BigQuery: a SA direto (papéis IAM: BigQuery Job User + Data Viewer).
  * Google Sheets: a SA com DWD (domain-wide delegation) — `with_subject` acessa
    as planilhas COMO o usuário de `gcp_impersonate_user`, sem precisar
    compartilhar planilha com a SA. Requer o client ID da SA autorizado no
    Admin do Workspace com os escopos de spreadsheets + drive.

No Streamlit Cloud tudo vem de st.secrets; local, de GOOGLE_CREDENTIALS_JSON /
GOOGLE_IMPERSONATE_USER (ou OAuth de navegador via GOOGLE_APPLICATION_CREDENTIALS).
"""
import json
import os

import pandas as pd

_BQ_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
_SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets",
                  "https://www.googleapis.com/auth/drive"]


def bootstrap_credentials():
    """No Streamlit Cloud, converte st.secrets em credenciais para BQ e Sheets."""
    try:
        import streamlit as st
        sa = st.secrets.get("gcp_service_account", None)
        subject = st.secrets.get("gcp_impersonate_user", None)
    except Exception:
        sa, subject = None, None
    if subject:
        os.environ.setdefault("GOOGLE_IMPERSONATE_USER", str(subject))
    if not sa:
        return
    info = dict(sa)
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT_ID", info.get("project_id", ""))
    os.environ["GOOGLE_CREDENTIALS_JSON"] = json.dumps(info)
    from google.oauth2.service_account import Credentials
    import pandas_gbq
    pandas_gbq.context.credentials = Credentials.from_service_account_info(info, scopes=_BQ_SCOPES)
    pandas_gbq.context.project = info.get("project_id")


def read_from_bq(query, project_id=None):
    from pandas_gbq import read_gbq
    return read_gbq(query, project_id=project_id or os.getenv("GOOGLE_CLOUD_PROJECT_ID"))


def get_gspread_client(gcp=False):
    """SA via GOOGLE_CREDENTIALS_JSON (+DWD se GOOGLE_IMPERSONATE_USER); senão OAuth local."""
    import gspread
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        from google.oauth2.service_account import Credentials
        info = json.loads(creds_json)
        creds = Credentials.from_service_account_info(info, scopes=_SHEETS_SCOPES)
        subject = os.environ.get("GOOGLE_IMPERSONATE_USER")
        if subject:
            creds = creds.with_subject(subject)   # DWD: lê as planilhas como o usuário
        return gspread.authorize(creds)
    path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not path:
        raise ValueError("Sem credenciais Google: configure st.secrets[gcp_service_account] "
                         "(Streamlit Cloud) ou GOOGLE_APPLICATION_CREDENTIALS (local).")
    return gspread.oauth(credentials_filename=path)


def google_sheets_to_dataframe(sheet_key, worksheet_name, client=None, isgcp=False):
    if client is None:
        client = get_gspread_client()
    ws = client.open_by_key(sheet_key).worksheet(worksheet_name)
    vals = ws.get_all_values()
    return pd.DataFrame(vals[1:], columns=vals[0])
