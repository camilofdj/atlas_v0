# -*- coding: utf-8 -*-
"""
Camada de dados do Atlas — compartilhada pelo notebook (atlas.ipynb) e pelo app
Streamlit (atlas_app.py).

Monta 5 DataFrames limpos a partir das fontes (BQ + Google Sheets), com cache
parquet local em `.data_cache/atlas` (fora do git):

  colaboradores  1/pessoa      AD ⨝ ativos ⨝ Adobe/Microsoft
  equipamentos   1/ativo       gpli.vw_ativos + specs de gpli.computadores
  softwares      1/(máq×app)   gpli.softwares_instalados (liga por computador_id)
  licencas       1/licença     planilhas Adobe + Microsoft, cruzadas com o AD
  ferramentas    1/contrato    planilha Ferramentas (senhas descartadas)

Gotchas tratados aqui (não reintroduzir):
  * `vw_ativos.ativo_id` NÃO é único entre categorias — specs/contagem de
    software só nas linhas de computador.
  * Software liga à máquina/pessoa por `computador_id` (o usuario_contato do
    softwares_instalados é sempre 'administrator').
  * `data_instalacao` chega como dbdate (db-dtypes) → converter p/ datetime
    (o pygwalker não reconhece dbdate).
"""
import os
import re
import unicodedata

import numpy as np
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv

from helpers_gcp import (bootstrap_credentials, get_gspread_client,
                         google_sheets_to_dataframe, read_from_bq)

_AQUI = Path(__file__).resolve().parent
load_dotenv(_AQUI / ".env")                                # opcional, p/ rodar local
bootstrap_credentials()                                    # Streamlit Cloud: st.secrets -> GCP
CACHE = _AQUI / ".data_cache" / "atlas"                    # no .gitignore

PROJ = os.getenv("GOOGLE_CLOUD_PROJECT_ID")
BQ = "sheetsintegration-451500"

SHEET_ADOBE = ("1VaZ0-h689BcAOY-2MYl2npGgWiHC2-nMA6IZmbQrJG0", "Usuários")
SHEET_MS    = ("10AVhPEqsxHCG6OYQaY-xmLsIfpu_SeGF_HJclDPm1tc", "Lista de Usuários Microsoft")
SHEET_FERR  = ("1ajHXlxXPVOHIV_TLV51L0Yri05qNA22C89mK_7SA6P0", "Nova versão")

EMPRESA_MAP = {"BB": "Brainbox", "DOM": "D'OM", "GOM": "Grupo OM", "HC": "Housecricket",
               "OM": "Opus Multipla", "SE": "Senso", "TM": "Tailor Media"}
MS_LIC_ATIVA = "Microsoft 365 Apps for business"

# O campo `empresa` do AD (attribute company) é digitado livre — tem variações de
# grafia ('Opus Multipla' vs 'OpusMúltipla') e contas em branco. Normalizamos aqui.
EMPRESA_CANON = {"opusmultipla": "Opus Multipla", "grupoom": "Grupo OM", "brainbox": "Brainbox",
                 "housecricket": "Housecricket", "senso": "Senso", "tailormedia": "Tailor Media",
                 "dom": "D'OM"}
# domínio de e-mail → empresa (p/ licenças de contas fora do AD)
DOMINIO_MAP = {"grupoom.com.br": "Grupo OM", "opusmultipla.com.br": "Opus Multipla",
               "brainboxdesign.com.br": "Brainbox", "housecricket.com.br": "Housecricket",
               "sensoperformance.com.br": "Senso", "dom-solucoes.com": "D'OM"}
# prefixo da OU no DN do AD → empresa (fallback p/ contas com company em branco)
OU_EMPRESA = {"OM": "Opus Multipla", "HC": "Housecricket", "GOM": "Grupo OM", "BB": "Brainbox",
              "SE": "Senso", "TM": "Tailor Media", "DOM": "D'OM"}


def _canon_key(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s.lower())


def canon_empresa(s):
    """'OpusMúltipla'/'opus multipla' → 'Opus Multipla'; mantém o valor se não reconhecer."""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return s
    return EMPRESA_CANON.get(_canon_key(s), s)


def empresa_do_dn(dn):
    """Deriva a empresa da 1ª OU do DN (ex.: 'OU=HC-MIDIAWEB,...' → Housecricket)."""
    m = re.search(r"OU=([A-Za-z]+)", str(dn))
    return OU_EMPRESA.get(m.group(1).upper()) if m else None


def empresa_da_entidade(e):
    """Entidade do GLPI → empresa ('Grupo OM > OpusMúltipla Comunicação...' → 'Opus Multipla')."""
    if e is None or (isinstance(e, float) and pd.isna(e)):
        return None
    key = _canon_key(str(e).split(">")[-1])
    for k in sorted(EMPRESA_CANON, key=len, reverse=True):
        if k in key:
            return EMPRESA_CANON[k]
    return None


def login_key(s):
    """login canônico: parte antes do @, minúsculo (serve p/ 'rafaelgr@OPUSMULTIPLA' e e-mails)."""
    if s is None:
        return None
    s = str(s).strip().lower()
    if not s or s in ("none", "nan", "-"):
        return None
    return (s.split("@")[0].strip() or None)


def email_key(s):
    if s is None:
        return None
    s = str(s).strip().lower()
    return s if "@" in s else None


def parse_brl(x):
    """'R$ 1.234,56' -> 1234.56 ; vazio/'-' -> NaN.

    A planilha é pt-BR (validado: nenhuma célula em formato US), mas por
    segurança também aceita '999.99' e '1,234.56' sem corromper o valor.
    """
    if x is None:
        return np.nan
    s = str(x).strip()
    if not s or s == "-":
        return np.nan
    s = re.sub(r"[R$\s]", "", s)
    if re.fullmatch(r"\d+\.\d{1,2}", s):                 # '999.99' (decimal com ponto)
        return float(s)
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d{1,2})?", s):  # '1,234.56' (formato US)
        return float(s.replace(",", ""))
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return np.nan


def parse_int(x):
    try:
        return int(float(str(x).strip()))
    except Exception:
        return np.nan


def clean_cols(df):
    """Tira espaços do nome das colunas, remove colunas sem nome e duplicadas."""
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    df = df.loc[:, [c for c in df.columns if c != ""]]
    return df.loc[:, ~pd.Index(df.columns).duplicated()]


def _fontes(refresh=False):
    """Lê as 7 fontes cruas (BQ + Sheets) com cache parquet local."""
    CACHE.mkdir(parents=True, exist_ok=True)
    nomes = ["pessoas", "ativos", "specs", "softwares", "adobe", "ms", "ferr"]
    if not refresh and all((CACHE / f"{n}.parquet").exists() for n in nomes):
        fontes = {n: pd.read_parquet(CACHE / f"{n}.parquet") for n in nomes}
        # cache de versão antiga (sem as colunas novas) força recarga
        if ("dn" in fontes["pessoas"].columns
                and "data_criacao" in fontes["ativos"].columns
                and "is_dynamic" in fontes["ativos"].columns
                and "ultima_inventariacao" in fontes["specs"].columns):
            return fontes

    pessoas = read_from_bq(f"""SELECT f.chave_join, f.chave_email, f.usuario, f.nome_completo,
        f.cargo, f.email, f.empresa, f.ativo, f.freela, f.qtd_computadores, f.qtd_monitores,
        f.qtd_perifericos, f.computadores, u.dn
        FROM `{BQ}.LDAP.vw_funcionario_360` f
        LEFT JOIN (
          SELECT LOWER(TRIM(usuario)) AS chave_join, dn
          FROM `{BQ}.LDAP.usuarios`
          QUALIFY ROW_NUMBER() OVER (PARTITION BY LOWER(TRIM(usuario)) ORDER BY nome_completo) = 1
        ) u USING (chave_join)""", project_id=PROJ)
    ativos = read_from_bq(f"""SELECT categoria_ativo, ativo_id, nome, numero_serie, patrimonio, entidade,
        tipo, modelo, fabricante, status, localizacao, usuario_login, usuario_contato,
        sistema_operacional, polegadas, is_dynamic, data_criacao
        FROM `{BQ}.gpli.vw_ativos`""", project_id=PROJ)
    specs = read_from_bq(f"""SELECT computador_id, usuario_contato, versao_so, arquitetura_so,
        processador, total_nucleos, memoria_total_mb, ultima_inventariacao
        FROM `{BQ}.gpli.computadores`""", project_id=PROJ)
    softwares = read_from_bq(f"""SELECT computador_id, computador, numero_serie, entidade, localizacao,
        software, versao, fabricante, categoria, so_da_versao, data_instalacao
        FROM `{BQ}.gpli.softwares_instalados`""", project_id=PROJ)

    client = get_gspread_client()
    adobe = clean_cols(google_sheets_to_dataframe(SHEET_ADOBE[0], SHEET_ADOBE[1], client=client))
    ms    = clean_cols(google_sheets_to_dataframe(SHEET_MS[0],    SHEET_MS[1],    client=client))
    ferr  = clean_cols(google_sheets_to_dataframe(SHEET_FERR[0],  SHEET_FERR[1],  client=client))

    fontes = dict(pessoas=pessoas, ativos=ativos, specs=specs, softwares=softwares,
                  adobe=adobe, ms=ms, ferr=ferr)
    for n, df in fontes.items():
        df.astype(object).where(df.notna(), None).to_parquet(CACHE / f"{n}.parquet", index=False)
    return fontes


def dados_de():
    """Timestamp (local) da última carga das fontes, ou None se não há cache."""
    p = CACHE / "pessoas.parquet"
    if not p.exists():
        return None
    return pd.Timestamp(p.stat().st_mtime, unit="s").tz_localize("UTC").tz_convert("America/Sao_Paulo")


def build_atlas(refresh=False):
    """Devolve dict com os 5 DataFrames limpos: colaboradores, equipamentos, softwares, licencas, ferramentas."""
    F = _fontes(refresh)
    pessoas, ativos, specs, softwares = F["pessoas"], F["ativos"], F["specs"], F["softwares"]
    adobe, ms, ferr = F["adobe"], F["ms"], F["ferr"]

    # --- normaliza empresa ANTES de tudo (dim/joins herdam o valor limpo):
    #     grafias variantes ('OpusMúltipla') e, p/ contas com company em branco no AD,
    #     deriva da OU do DN (ex.: OU=HC-MIDIAWEB → Housecricket).
    pessoas = pessoas.copy()
    pessoas["empresa"] = pessoas["empresa"].map(canon_empresa)
    if "dn" in pessoas.columns:
        sem = pessoas["empresa"].isna() | pessoas["empresa"].eq("(Sem empresa)")
        pessoas.loc[sem, "empresa"] = pessoas.loc[sem, "dn"].map(empresa_do_dn).fillna("(Sem empresa)")

    # --- chaves do AD
    ad_emails   = set(pessoas["chave_email"].map(email_key).dropna())
    ad_by_email = (pessoas.assign(_k=pessoas["chave_email"].map(email_key)).dropna(subset=["_k"])
                   .drop_duplicates("_k").set_index("_k")[["nome_completo", "empresa", "ativo"]])
    dim = pessoas[["chave_join", "nome_completo", "empresa", "cargo"]].drop_duplicates("chave_join")

    # --- login por máquina (computador_id -> login)  [software liga à pessoa por aqui]
    id2login = (specs.assign(login=specs["usuario_contato"].map(login_key))
                .drop_duplicates("computador_id").set_index("computador_id")["login"])

    # --- Adobe / Microsoft
    adobe = adobe.assign(email=adobe["Email"].map(email_key))
    adobe_emails = set(adobe["email"].dropna())
    ms = ms.assign(email=ms["Conta Corporativa"].map(email_key))
    ms = ms[ms["email"].notna()].copy()
    ms["licenca_ativa"]    = ms["Status"].astype(str).str.strip().eq(MS_LIC_ATIVA)
    ms["empresa_planilha"] = ms["Empresa"].astype(str).str.strip().map(EMPRESA_MAP).fillna(ms["Empresa"])
    ms_status = (ms.sort_values("licenca_ativa", ascending=False).drop_duplicates("email")
                 .set_index("email")[["Status", "licenca_ativa"]]
                 .rename(columns={"Status": "ms_status", "licenca_ativa": "ms_licenca_ativa"}))

    # --- softwares -> pessoa/máquina via computador_id
    sw = softwares.copy()
    sw["login"] = sw["computador_id"].map(id2login)
    qtd_sw_pessoa = sw.dropna(subset=["login"]).groupby("login")["software"].nunique().rename("qtd_softwares")
    sw_por_maq    = sw.groupby("computador_id")["software"].nunique()

    # ============================================================ COLAB (1/pessoa)
    colab = pessoas.copy()
    colab["classificacao"] = np.where(colab["freela"], "Freelancer", "Contratado")
    colab["_ek"] = colab["chave_email"].map(email_key)
    colab["tem_adobe"] = colab["_ek"].isin(adobe_emails)
    colab = colab.merge(ms_status, left_on="_ek", right_index=True, how="left")
    colab["tem_microsoft"] = colab["ms_licenca_ativa"].eq(True)   # NaN -> False, sem FutureWarning
    colab = colab.merge(qtd_sw_pessoa, left_on="chave_join", right_index=True, how="left")
    colab["qtd_softwares"] = colab["qtd_softwares"].fillna(0).astype(int)
    colab = colab.rename(columns={"usuario": "login"})
    DF_COLAB = colab[["login", "nome_completo", "cargo", "empresa", "email", "ativo", "classificacao",
        "tem_adobe", "tem_microsoft", "ms_status", "qtd_computadores", "qtd_monitores",
        "qtd_perifericos", "qtd_softwares", "computadores"]]

    # ============================================================ EQUIP (1/ativo)
    # ativo_id NÃO é único entre categorias -> specs/qtd_softwares só nas linhas de computador.
    specs_cols = ["versao_so", "arquitetura_so", "processador", "total_nucleos", "memoria_total_mb",
                  "ultima_inventariacao"]
    specs_pc = specs.drop_duplicates("computador_id")[["computador_id"] + specs_cols]
    is_pc = ativos["categoria_ativo"].eq("computador")
    pc  = (ativos[is_pc].merge(specs_pc, left_on="ativo_id", right_on="computador_id", how="left")
           .drop(columns=["computador_id"]))
    out = ativos[~is_pc].copy()   # sem as colunas de specs; o concat alinha com NaN
    eq = pd.concat([pc, out], ignore_index=True)
    eq["memoria_gb"] = (pd.to_numeric(eq["memoria_total_mb"], errors="coerce") / 1024).round(1)
    eq["data_criacao"] = pd.to_datetime(eq["data_criacao"], errors="coerce")
    eq["ultima_inventariacao"] = pd.to_datetime(eq["ultima_inventariacao"], errors="coerce")
    eq["origem"] = np.where(pd.to_numeric(eq["is_dynamic"], errors="coerce").eq(1),
                            "agente", "manual")   # 1 = criado pelo agente de inventário
    eq["login"] = eq["usuario_contato"].map(login_key)
    eq = eq.merge(dim, left_on="login", right_on="chave_join", how="left")
    eq = eq.rename(columns={"patrimonio": "tag", "nome_completo": "responsavel",
                            "empresa": "empresa_responsavel", "cargo": "cargo_responsavel", "nome": "ativo"})
    eq["empresa_glpi"] = eq["entidade"].map(empresa_da_entidade)   # entidade GLPI → empresa
    eq["qtd_softwares"] = 0
    mpc = eq["categoria_ativo"].eq("computador")
    eq.loc[mpc, "qtd_softwares"] = eq.loc[mpc, "ativo_id"].map(sw_por_maq).fillna(0).astype(int)
    DF_EQUIP = eq[["categoria_ativo", "ativo", "tag", "numero_serie", "fabricante", "modelo", "tipo",
        "status", "entidade", "empresa_glpi", "localizacao", "login", "responsavel", "empresa_responsavel",
        "cargo_responsavel", "sistema_operacional", "versao_so", "arquitetura_so", "processador",
        "total_nucleos", "memoria_gb", "polegadas", "qtd_softwares", "origem", "data_criacao",
        "ultima_inventariacao"]]

    # ============================================================ SOFT (1/máquina×app)
    swx = sw.merge(dim, left_on="login", right_on="chave_join", how="left")
    swx = swx.rename(columns={"nome_completo": "responsavel", "empresa": "empresa_responsavel"})
    swx["data_instalacao"] = pd.to_datetime(swx["data_instalacao"], errors="coerce")  # dbdate -> datetime
    DF_SOFT = swx[["software", "fabricante", "categoria", "versao", "computador", "login", "responsavel",
        "empresa_responsavel", "entidade", "localizacao", "data_instalacao"]]

    # ============================================================ LIC (Adobe + Microsoft)
    ad = pd.DataFrame({"tipo": "Adobe", "email": adobe["email"],
        "nome_planilha": (adobe.get("Nome", "").astype(str).str.strip() + " " +
                          adobe.get("Sobrenome", "").astype(str).str.strip()).str.strip(),
        "produto": adobe.get("Tipo de identidade"), "status": "Ativo (Adobe ID)",
        "licenca_ativa": True, "empresa_planilha": adobe.get("Domínio")})
    msl = pd.DataFrame({"tipo": "Microsoft", "email": ms["email"],
        "nome_planilha": ms.get("Usuário", pd.Series(index=ms.index, dtype=str)).astype(str).str.strip(),
        "produto": ms.get("Conta Microsoft"), "status": ms["Status"],
        "licenca_ativa": ms["licenca_ativa"], "empresa_planilha": ms["empresa_planilha"]})
    lic = pd.concat([ad, msl], ignore_index=True)
    lic = lic[lic["email"].notna()].copy()
    lic["casou_ad"] = lic["email"].isin(ad_emails)
    lic = lic.merge(ad_by_email.rename(columns={"nome_completo": "nome_ad", "empresa": "empresa_ad",
                    "ativo": "ativo_no_ad"}), left_on="email", right_index=True, how="left")
    lic["nome"]    = lic["nome_ad"].fillna(lic["nome_planilha"])
    lic["empresa"] = (lic["empresa_ad"].fillna(lic["empresa_planilha"])
                      .map(lambda s: DOMINIO_MAP.get(str(s).strip().lower(), s))  # domínio → empresa
                      .map(canon_empresa))
    lic["desperdicio"] = lic["licenca_ativa"] & ((~lic["casou_ad"]) | (lic["ativo_no_ad"].eq(False)))
    DF_LIC = lic[["tipo", "email", "nome", "empresa", "produto", "status", "licenca_ativa",
        "casou_ad", "ativo_no_ad", "desperdicio"]]

    # ============================================================ FERR (1/contrato)
    f = ferr.copy()
    for c in ["LoginAdmin", "Pwd", "Senha"]:          # senhas: nunca no Atlas
        if c in f.columns:
            f = f.drop(columns=c)
    f["empresa_nome"] = f["Empresa"].astype(str).str.strip().map(EMPRESA_MAP).fillna(f["Empresa"])
    if "Categoria" in f.columns:
        f["Categoria"] = f["Categoria"].astype(str).str.strip().replace({"": "(Sem categoria)"})
    for s_, d_ in [("Valor", "valor_num"), ("Custo Mensal", "custo_mensal_num"),
                   ("Custo Anual", "custo_anual_num"), ("Total", "total_num")]:
        if s_ in f.columns:
            f[d_] = f[s_].map(parse_brl)
    if "Quantidade" in f.columns:
        f["quantidade_num"] = f["Quantidade"].map(parse_int)
    f = f.rename(columns={"Ferramenta": "ferramenta", "Categoria": "categoria", "Notas": "notas",
        "Renovação / Vencimento": "vencimento", "Licença": "licenca", "Moeda": "moeda", "Tipo": "tipo",
        "Autenticação": "autenticacao", "Forma Pagamento": "forma_pagamento", "Obs gerais": "obs"})
    keep = ["empresa_nome", "ferramenta", "categoria", "notas", "vencimento", "licenca", "tipo", "moeda",
        "valor_num", "quantidade_num", "custo_mensal_num", "custo_anual_num", "total_num",
        "autenticacao", "forma_pagamento", "obs"]
    DF_FERR = f[[c for c in keep if c in f.columns]]

    return {"colaboradores": DF_COLAB, "equipamentos": DF_EQUIP, "softwares": DF_SOFT,
            "licencas": DF_LIC, "ferramentas": DF_FERR}
