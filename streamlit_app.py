# -*- coding: utf-8 -*-
"""
Atlas — app Streamlit (versão standalone p/ Streamlit Community Cloud).

Local:  streamlit run streamlit_app.py
Cloud:  main file = streamlit_app.py; credenciais em st.secrets (ver README).

Diferença p/ a versão do monorepo: o botão "Atualizar dados" recarrega só
BigQuery + Sheets — o passo GLPI (MySQL interno → BQ) roda por dentro da rede,
pelo pipeline gpli ou pelo app interno.
"""
import html as _html
import sys
from pathlib import Path

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent))
from atlas_data import build_atlas, dados_de, email_key
from atlas_er import DIAGRAMS_DIR, dot_dbml, dot_erd, parse_dbml, parse_erd

st.set_page_config(page_title="Atlas - TAS Grupo OM", page_icon="🧭", layout="wide")

# paleta categórica validada (ordem fixa) + status (reservado p/ alertas)
PAL = ["#2a78d6", "#1baf7a", "#eda100", "#008300", "#4a3aa7", "#e34948", "#e87ba4", "#eb6834"]
ST_GOOD, ST_WARN, ST_CRIT = "#0ca30c", "#fab219", "#d03b3b"

# cards de texto das fichas (o st.metric corta o texto sem tooltip; aqui o title
# nativo do navegador mostra o valor completo ao passar o mouse)
st.markdown("""<style>
.fi-lab{font-size:0.82rem;color:#898781;margin-bottom:2px}
.fi-val{font-size:1.45rem;font-weight:600;line-height:1.25;
        white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
</style>""", unsafe_allow_html=True)


def filtro_tabela(df, busca_col, escolha_cols, key):
    """Filtros estilo planilha (Google Sheets) acima de uma tabela: busca por texto na
    coluna principal + um popover por coluna com PESQUISA de valores, botões Tudo/Limpar
    e checklist (desmarcou, escondeu).

    As desmarcações vivem em st.session_state (fora dos widgets): um checkbox oculto
    pela pesquisa perde o estado de widget, mas a exclusão persiste."""
    n_orig = len(df)
    barra = st.columns([3] + [1] * len(escolha_cols))
    termo = barra[0].text_input("Busca", placeholder=f"Buscar {busca_col}...",
                                key=f"{key}_busca", label_visibility="collapsed")
    if termo:
        df = df[df[busca_col].astype(str).str.contains(termo, case=False, regex=False, na=False)]

    for i, col in enumerate(escolha_cols):
        serie = df[col].fillna("—").astype(str).replace("", "—")
        vals = sorted(serie.unique())
        excl = st.session_state.setdefault(f"{key}_{col}_excl", set())
        # sincroniza com os cliques da última interação ANTES de montar o rótulo
        for v in vals:
            s = st.session_state.get(f"{key}_{col}_cb_{v}")
            if s is True:
                excl.discard(v)
            elif s is False:
                excl.add(v)
        n_ativos = sum(1 for v in vals if v not in excl)
        rotulo = (col.capitalize() if n_ativos == len(vals)
                  else f"{col.capitalize()} · {n_ativos}/{len(vals)}")

        with barra[i + 1].popover(rotulo, width="stretch"):
            pesq = st.text_input("Pesquisar valores", key=f"{key}_{col}_pesq",
                                 placeholder="Pesquisar...", label_visibility="collapsed")
            b1, b2 = st.columns(2)
            if b1.button("Tudo", key=f"{key}_{col}_tudo", width="stretch"):
                excl.clear()
                for v in vals:
                    st.session_state[f"{key}_{col}_cb_{v}"] = True
            if b2.button("Limpar", key=f"{key}_{col}_limpar", width="stretch"):
                excl.update(vals)
                for v in vals:
                    st.session_state[f"{key}_{col}_cb_{v}"] = False
            visiveis = [v for v in vals if pesq.lower() in v.lower()] if pesq else vals
            with st.container(height=max(100, min(280, 42 * len(visiveis) + 16))):
                if not visiveis:
                    st.caption("Nenhum valor encontrado.")
                for v in visiveis:
                    marcado = st.checkbox(v, value=(v not in excl), key=f"{key}_{col}_cb_{v}")
                    (excl.discard if marcado else excl.add)(v)

        if excl:
            df = df[~df[col].fillna("—").astype(str).replace("", "—").isin(excl)]

    if len(df) != n_orig:
        st.caption(f"{len(df)} de {n_orig} itens após os filtros.")
    return df


def info_card(col, label, value):
    """Substituto do st.metric para valores de TEXTO: trunca com reticências,
    mas o texto completo aparece no hover (atributo title)."""
    v = value
    if v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() in ("", "None", "nan", "NaT"):
        v = "—"
    v = str(v)
    col.markdown(f'<div><div class="fi-lab">{_html.escape(label)}</div>'
                 f'<div class="fi-val" title="{_html.escape(v)}">{_html.escape(v)}</div></div>',
                 unsafe_allow_html=True)


def brl(x):
    try:
        v = float(x)
        if v != v:
            return "—"
        return "R$ " + f"{v:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "—"


@st.cache_data(show_spinner="Carregando dados (cache local)...")
def load_data():
    return build_atlas(refresh=False)


def bar_rank(df, cat, val, color, money=False, height_row=30):
    """Barras horizontais ranqueadas, rótulo no fim da barra (sem eixo de valor)."""
    d = df.copy()
    d["_label"] = d[val].map(brl) if money else d[val].map(lambda v: f"{int(v):,}".replace(",", "."))
    base = alt.Chart(d)
    y = alt.Y(f"{cat}:N", sort="-x", title=None, axis=alt.Axis(labelLimit=170))
    bars = base.mark_bar(size=18, cornerRadiusEnd=4, color=color).encode(
        y=y,
        x=alt.X(f"{val}:Q", title=None,
                axis=alt.Axis(labels=False, ticks=False, domain=False, grid=False),
                scale=alt.Scale(domainMax=float(d[val].max()) * 1.18 if len(d) else 1)),
        tooltip=[alt.Tooltip(f"{cat}:N"), alt.Tooltip(f"{val}:Q", format=",.0f")],
    )
    labels = base.mark_text(align="left", dx=5, color="#898781").encode(
        y=y, x=alt.X(f"{val}:Q"), text="_label:N")
    return (bars + labels).properties(height=max(height_row * len(d), 60))


def bar_grouped(df, cat, val, serie, colors, height_row=44):
    """Barras horizontais agrupadas (2 séries), legenda no topo, rótulo no fim."""
    d = df.copy()
    d["_label"] = d[val].map(lambda v: f"{int(v):,}".replace(",", "."))
    base = alt.Chart(d)
    y = alt.Y(f"{cat}:N", sort=None, title=None, axis=alt.Axis(labelLimit=170))
    off = alt.YOffset(f"{serie}:N")
    color = alt.Color(f"{serie}:N", title=None,
                      scale=alt.Scale(range=colors),
                      legend=alt.Legend(orient="top"))
    bars = base.mark_bar(size=16, cornerRadiusEnd=4).encode(
        y=y, yOffset=off, color=color,
        x=alt.X(f"{val}:Q", title=None,
                axis=alt.Axis(labels=False, ticks=False, domain=False, grid=False),
                scale=alt.Scale(domainMax=float(d[val].max()) * 1.18 if len(d) else 1)),
        tooltip=[alt.Tooltip(f"{cat}:N"), alt.Tooltip(f"{serie}:N"), alt.Tooltip(f"{val}:Q")],
    )
    labels = base.mark_text(align="left", dx=5, color="#898781").encode(
        y=y, yOffset=off, x=alt.X(f"{val}:Q"), text="_label:N")
    return (bars + labels).properties(height=max(height_row * d[cat].nunique(), 60))


# ============================================================== sidebar
st.sidebar.title("Atlas")
pagina = st.sidebar.radio("Página", [
    "Painel Gerencial",
    "Ficha do Colaborador",
    "Ficha da Máquina",
    "Exploração livre",
    "Bancos de dados",
], label_visibility="collapsed")

ts = dados_de()
st.sidebar.caption(f"Dados de {ts.strftime('%d/%m/%Y %H:%M')}" if ts is not None else "Sem cache local ainda.")
if st.sidebar.button("Atualizar dados"):
    # recarrega BQ + Sheets (o passo GLPI->BQ roda na rede interna, fora deste app)
    with st.spinner("Recarregando BigQuery + Sheets..."):
        build_atlas(refresh=True)
    load_data.clear()
    st.rerun()

dfs = load_data()
COLAB, EQUIP, SOFT = dfs["colaboradores"], dfs["equipamentos"], dfs["softwares"]
LIC, FERR = dfs["licencas"], dfs["ferramentas"]


# ============================================================== página: painel
def _chart(df, chart):
    """Renderiza o gráfico, ou um aviso quando o filtro zera os dados."""
    if len(df):
        st.altair_chart(chart, width="stretch")
    else:
        st.caption("Sem dados para o filtro atual.")


def pagina_painel():
    st.title("Painel Gerencial - TAS Grupo OM")

    # ---- filtros (uma linha, acima de tudo)
    emp_opts = sorted(set(COLAB["empresa"].dropna()) | set(FERR["empresa_nome"].dropna()))
    f1, f2, f3 = st.columns([2, 2, 1])
    sel_emp = f1.multiselect("Empresa", emp_opts, placeholder="Todas as empresas")
    sel_cls = f2.multiselect("Classificação", ["Contratado", "Freelancer"], placeholder="Todas")
    so_ativos = f3.toggle("Só ativos no AD", value=False,
                          help="Aplica aos indicadores de colaboradores.")

    colab_f, equip_f, lic_f, ferr_f = COLAB, EQUIP, LIC, FERR
    if sel_emp:
        colab_f = colab_f[colab_f["empresa"].isin(sel_emp)]
        equip_f = equip_f[equip_f["empresa_glpi"].isin(sel_emp)]
        lic_f   = lic_f[lic_f["empresa"].isin(sel_emp)]
        ferr_f  = ferr_f[ferr_f["empresa_nome"].isin(sel_emp)]
    if sel_cls:
        colab_f = colab_f[colab_f["classificacao"].isin(sel_cls)]
    colab_base = colab_f          # p/ offboarding (precisa dos inativos mesmo com o toggle)
    if so_ativos:
        colab_f = colab_f[colab_f["ativo"]]
    # softwares seguem as máquinas filtradas (empresa do ativo no GLPI)
    maqs_f = equip_f.loc[equip_f["categoria_ativo"] == "computador", "ativo"]
    soft_f = SOFT[SOFT["computador"].isin(maqs_f)]
    if sel_emp or sel_cls or so_ativos:
        st.caption("Filtros ativos - Classificação e “Só ativos” valem para os indicadores de "
                   "colaboradores; Empresa vale para tudo (equipamentos pela entidade do GLPI).")

    n_colab   = len(colab_f)
    n_ativos  = int(colab_f["ativo"].sum())
    n_free    = int((colab_f["classificacao"] == "Freelancer").sum())
    pct_free  = f"{n_free / n_colab * 100:.0f}% do quadro" if n_colab else "—"
    n_adobe   = int((lic_f["tipo"] == "Adobe").sum())
    n_ms      = int(((lic_f["tipo"] == "Microsoft") & lic_f["licenca_ativa"]).sum())
    n_pc      = int((equip_f["categoria_ativo"] == "computador").sum())
    custo_mes = float(ferr_f["custo_mensal_num"].sum(skipna=True))
    custo_ano = float(ferr_f["custo_anual_num"].sum(skipna=True))
    n_desp    = int(lic_f["desperdicio"].sum())

    c = st.columns(4)
    c[0].metric("Colaboradores", f"{n_colab}", f"{n_ativos} ativos no AD", delta_color="off")
    c[1].metric("Freelancers", f"{n_free}", pct_free, delta_color="off")
    c[2].metric("Licenças Adobe", f"{n_adobe}")
    c[3].metric("Microsoft 365 ativas", f"{n_ms}")
    c = st.columns(4)
    c[0].metric("Computadores", f"{n_pc}", f"{len(equip_f)} ativos no total", delta_color="off")
    c[1].metric("Ferramentas / contratos", f"{len(ferr_f)}")
    c[2].metric("Custo mensal", brl(custo_mes), f"{brl(custo_ano)} / ano", delta_color="off")
    c[3].metric("Licenças desperdiçadas", f"{n_desp}", "ativa fora/inativa no AD",
                delta_color="inverse" if n_desp else "off")

    st.divider()

    # ---- colaboradores & licenças
    e1, e2 = st.columns(2)
    with e1:
        st.subheader("Colaboradores por empresa")
        hc = colab_f.groupby("empresa").size().reset_index(name="n")
        _chart(hc, bar_rank(hc, "empresa", "n", PAL[0]))
        st.subheader("Classificação do quadro")
        clf = colab_f["classificacao"].value_counts().reset_index()
        clf.columns = ["classificacao", "n"]
        _chart(clf, bar_rank(clf, "classificacao", "n", PAL[2]))
    with e2:
        st.subheader("Colaboradores com licença, por empresa")
        lic_emp = (colab_f.groupby("empresa")[["tem_adobe", "tem_microsoft"]].sum().reset_index()
                   .rename(columns={"tem_adobe": "Adobe", "tem_microsoft": "Microsoft"}))
        lic_emp = lic_emp[(lic_emp["Adobe"] > 0) | (lic_emp["Microsoft"] > 0)]
        lic_long = lic_emp.melt("empresa", var_name="licenca", value_name="n")
        _chart(lic_long, bar_grouped(lic_long, "empresa", "n", "licenca", [PAL[4], PAL[1]]))

    st.divider()

    # ---- equipamentos & máquinas
    pc = equip_f[equip_f["categoria_ativo"] == "computador"].copy()
    pc["so_fam"] = pc["sistema_operacional"].map(
        lambda s: "Windows" if "windows" in str(s).lower()
        else ("macOS" if "mac" in str(s).lower() else "Outro/—"))
    mem = pd.to_numeric(pc["memoria_gb"], errors="coerce").dropna()

    c = st.columns(4)
    c[0].metric("Memória média", f"{mem.mean():.0f} GB" if len(mem) else "—")
    c[1].metric("Windows", int((pc["so_fam"] == "Windows").sum()))
    c[2].metric("macOS", int((pc["so_fam"] == "macOS").sum()))
    c[3].metric("Softwares inventariados", len(soft_f), f"em {soft_f['computador'].nunique()} máquina(s)",
                delta_color="off")

    e1, e2, e3 = st.columns(3)
    with e1:
        st.subheader("Ativos por categoria")
        cat = equip_f["categoria_ativo"].value_counts().reset_index()
        cat.columns = ["categoria", "n"]
        _chart(cat, bar_rank(cat, "categoria", "n", PAL[1]))
    with e2:
        st.subheader("Computadores por SO")
        so = pc["so_fam"].value_counts().reset_index()
        so.columns = ["so", "n"]
        _chart(so, bar_rank(so, "so", "n", PAL[0]))
    with e3:
        st.subheader("Por fabricante")
        fab = pc["fabricante"].value_counts().head(6).reset_index()
        fab.columns = ["fabricante", "n"]
        _chart(fab, bar_rank(fab, "fabricante", "n", PAL[7]))

    st.divider()

    # ---- ferramentas & custos
    st.caption("Custo anual (R$) — soma da coluna “Custo Anual” da planilha de Ferramentas.")
    e1, e2 = st.columns(2)
    with e1:
        st.subheader("Custo anual por empresa")
        ce = ferr_f.groupby("empresa_nome")["custo_anual_num"].sum().reset_index()
        ce = ce[ce["custo_anual_num"] > 0]
        _chart(ce, bar_rank(ce, "empresa_nome", "custo_anual_num", PAL[2], money=True))
    with e2:
        st.subheader("Custo anual por categoria")
        cc2 = ferr_f.groupby("categoria")["custo_anual_num"].sum().reset_index()
        cc2 = cc2[cc2["custo_anual_num"] > 0].nlargest(8, "custo_anual_num")
        _chart(cc2, bar_rank(cc2, "categoria", "custo_anual_num", PAL[5], money=True))

    st.subheader("Ferramentas mais caras (custo anual)")
    top = (ferr_f[ferr_f["custo_anual_num"] > 0].groupby("ferramenta")["custo_anual_num"]
           .sum().nlargest(10).reset_index())
    _chart(top, bar_rank(top, "ferramenta", "custo_anual_num", PAL[3], money=True))

    st.divider()

    # ---- alertas
    desp = lic_f[lic_f["desperdicio"]].copy()
    desp["motivo"] = np.where(~desp["casou_ad"], "Fora do AD", "Inativo no AD")
    st.subheader(f"Desperdício de licenças ({len(desp)})")
    st.caption("Licença ativa em conta que não está (ou está inativa) no AD — candidata a cancelamento.")
    if len(desp):
        st.dataframe(desp[["tipo", "nome", "email", "empresa", "motivo"]].sort_values("tipo"),
                     hide_index=True, width="stretch")
    else:
        st.success("Nenhum desperdício encontrado.")

    # ---- offboarding: inativo no AD ainda detendo equipamento ou licença
    inat = colab_base[~colab_base["ativo"]].copy()
    inat["_ek"] = inat["email"].map(email_key)
    eq_por_login = (EQUIP[EQUIP["login"].isin(set(inat["login"]))]
                    .groupby("login")["ativo"].apply(lambda s: ", ".join(sorted(s))))
    lic_por_email = (LIC[LIC["email"].isin(set(inat["_ek"].dropna())) & LIC["licenca_ativa"]]
                     .groupby("email")["tipo"].apply(lambda s: ", ".join(sorted(set(s)))))
    inat["equipamentos"] = inat["login"].map(eq_por_login)
    inat["licencas"] = inat["_ek"].map(lic_por_email)
    off = inat[inat["equipamentos"].notna() | inat["licencas"].notna()]
    st.subheader(f"Offboarding — inativos no AD com equipamento ou licença ({len(off)})")
    st.caption("Contas desativadas no AD que ainda detêm ativos do GLPI ou licenças ativas — "
               "equipamento a recolher, licença a cancelar.")
    if len(off):
        st.dataframe(off[["nome_completo", "empresa", "classificacao", "equipamentos", "licencas"]]
                     .fillna("—").sort_values(["empresa", "nome_completo"]),
                     hide_index=True, width="stretch")
    else:
        st.success("Nenhum inativo retendo equipamento ou licença.")

    venc = ferr_f.copy()
    venc["_dt"] = pd.to_datetime(venc["vencimento"], format="%d/%m/%Y", errors="coerce")
    hoje = pd.Timestamp.today().normalize()
    prox = venc[venc["_dt"].notna() & (venc["_dt"] >= hoje)].sort_values("_dt").head(12).copy()
    prox["faltam (dias)"] = (prox["_dt"] - hoje).dt.days
    prox["vence_em"] = prox["_dt"].dt.strftime("%d/%m/%Y")
    prox["total"] = prox["total_num"].map(brl)
    n_sem_data = int(venc["_dt"].isna().sum())
    st.subheader(f"Vencimentos próximos ({len(prox)})")
    st.caption(f"Ordenado por data. {n_sem_data} contrato(s) sem data reconhecível na planilha (formato misto).")
    if len(prox):
        def _farol(v):
            try:
                d = int(v)
            except (TypeError, ValueError):
                return ""
            cor = ST_CRIT if d <= 30 else (ST_WARN if d <= 90 else ST_GOOD)
            return f"background-color: {cor}40; font-weight: 600"
        sty = (prox[["empresa_nome", "ferramenta", "categoria", "vence_em", "faltam (dias)", "total"]]
               .style.map(_farol, subset=["faltam (dias)"]))
        st.dataframe(sty, hide_index=True, width="stretch")
    else:
        st.info("Sem vencimentos futuros reconhecíveis.")


# ============================================================== página: ficha colaborador
def pagina_colaborador():
    st.title("Ficha do Colaborador")
    op = COLAB.sort_values("nome_completo")
    labels = op.apply(lambda x: f"{x['nome_completo']} | {x['empresa'] or '—'}", axis=1)
    idx = st.selectbox("Colaborador", range(len(op)),
                       format_func=lambda i: labels.iloc[i], index=None,
                       placeholder="Digite para buscar...")
    if idx is None:
        st.info("Selecione um colaborador acima.")
        return
    r = op.iloc[idx]
    ek = email_key(r["email"])

    c = st.columns(4)
    info_card(c[0], "Cargo", r["cargo"])
    info_card(c[1], "Empresa", r["empresa"])
    info_card(c[2], "Classificação", r["classificacao"])
    info_card(c[3], "Ativo no AD", "Sim" if r["ativo"] else "Não")
    c = st.columns(4)
    info_card(c[0], "E-mail", r["email"])
    info_card(c[1], "Login", r["login"])
    info_card(c[2], "Adobe", "Sim" if r["tem_adobe"] else "—")
    info_card(c[3], "Microsoft", r["ms_status"] if r["tem_microsoft"] else "—")

    # periféricos ficam de fora (são dispositivos detectados pelo agente, não entrega)
    eqs = EQUIP[(EQUIP["login"] == r["login"]) & (EQUIP["categoria_ativo"] != "periferico")]
    st.subheader(f"Equipamentos ({len(eqs)})")
    if len(eqs):
        st.dataframe(eqs[["categoria_ativo", "ativo", "fabricante", "modelo",
                          "sistema_operacional", "processador", "memoria_gb"]],
                     hide_index=True, width="stretch")
    else:
        st.caption("Nenhum equipamento vinculado no GLPI.")

    lics = LIC[LIC["email"] == ek]
    st.subheader(f"Licenças ({len(lics)})")
    if len(lics):
        st.dataframe(lics[["tipo", "produto", "status", "licenca_ativa", "desperdicio"]],
                     hide_index=True, width="stretch")
    else:
        st.caption("Nenhuma licença Adobe/Microsoft encontrada para este e-mail.")

    sfs = SOFT[SOFT["login"] == r["login"]]
    if len(sfs):
        st.subheader(f"Softwares instalados ({len(sfs)})")
        sfs_f = filtro_tabela(sfs, "software", ["fabricante", "categoria"], key="f_sw_colab")
        st.dataframe(sfs_f[["software", "fabricante", "versao", "computador"]],
                     hide_index=True, width="stretch")


# ============================================================== página: ficha máquina
def _inventario_glpi():
    """Visão geral do inventário GLPI — aparece quando nenhuma máquina está selecionada."""
    st.subheader("Visão geral do inventário GLPI")
    st.caption("Saúde do cadastro enquanto o inventário é populado. Selecione uma máquina "
               "acima para ver a ficha individual.")

    pc = EQUIP[EQUIP["categoria_ativo"] == "computador"]
    n_cat = EQUIP["categoria_ativo"].value_counts()
    com_resp = int(EQUIP["responsavel"].notna().sum())
    inv_max = pc["ultima_inventariacao"].max()
    inv_recente = int((pc["ultima_inventariacao"]
                       >= pd.Timestamp.today().normalize() - pd.Timedelta(days=30)).sum())
    maqs_com_sw = SOFT["computador"].nunique()

    c = st.columns(4)
    c[0].metric("Ativos no GLPI", len(EQUIP),
                " · ".join(f"{v} {k}" for k, v in n_cat.items()), delta_color="off")
    c[1].metric("Com responsável", f"{com_resp / len(EQUIP) * 100:.0f}%" if len(EQUIP) else "—",
                f"{com_resp} de {len(EQUIP)}", delta_color="off")
    c[2].metric("Agente de inventário (30d)", f"{inv_recente} de {len(pc)}",
                f"último: {inv_max.strftime('%d/%m/%Y')}" if pd.notna(inv_max) else "—",
                delta_color="off")
    c[3].metric("Máquinas c/ software inventariado", f"{maqs_com_sw} de {len(pc)}")

    sem_status = int(pc["status"].isna().sum() + (pc["status"] == "").sum())
    sem_serie  = int(pc["numero_serie"].isna().sum() + (pc["numero_serie"] == "").sum())
    estoque    = int(pc["status"].astype(str).str.strip().str.lower().eq("estoque").sum())
    c = st.columns(3)
    c[0].metric("Sem status", sem_status, delta_color="off")
    c[1].metric("Sem nº de série", sem_serie, delta_color="off")
    c[2].metric("Em estoque", estoque)

    mon_per = EQUIP[EQUIP["categoria_ativo"].isin(["monitor", "periferico"])]
    n_det = int((mon_per["origem"] == "agente").sum())
    st.caption(f"Monitores e periféricos: {n_det} de {len(mon_per)} foram detectados "
               "automaticamente pelo agente (dispositivos conectados no momento do inventário), "
               "não cadastro manual. A coluna `origem` distingue os dois na exploração.")

    e1, e2 = st.columns(2)
    with e1:
        st.subheader("Evolução do cadastro")
        ev = EQUIP.dropna(subset=["data_criacao"]).copy()
        if len(ev):
            ev["mes"] = ev["data_criacao"].dt.to_period("M").dt.to_timestamp()
            serie = ev.groupby("mes").size().sort_index().cumsum().reset_index(name="total")
            base = alt.Chart(serie).encode(
                x=alt.X("mes:T", title=None, axis=alt.Axis(format="%b/%y")),
                y=alt.Y("total:Q", title=None))
            area = base.mark_area(opacity=0.12, color=PAL[0])
            linha = base.mark_line(strokeWidth=2, color=PAL[0])
            ultimo = alt.Chart(serie.tail(1))
            ponto = ultimo.mark_point(filled=True, size=70, color=PAL[0]).encode(x="mes:T", y="total:Q")
            rotulo = ultimo.mark_text(align="left", dx=8, color="#898781").encode(
                x="mes:T", y="total:Q", text="total:Q")
            st.altair_chart((area + linha + ponto + rotulo).properties(height=240), width="stretch")
        else:
            st.caption("Sem datas de cadastro disponíveis.")
    with e2:
        st.subheader("Ativos por empresa (entidade GLPI)")
        emp = EQUIP["empresa_glpi"].fillna("(sem entidade)").value_counts().reset_index()
        emp.columns = ["empresa", "n"]
        _chart(emp, bar_rank(emp, "empresa", "n", PAL[1]))

    st.subheader("Pendências de cadastro (computadores)")
    pend = pc.copy()
    falta = {"status": pend["status"].isna() | pend["status"].astype(str).str.strip().eq(""),
             "nº série": pend["numero_serie"].isna() | pend["numero_serie"].astype(str).str.strip().eq(""),
             "software": ~pend["ativo"].isin(set(SOFT["computador"]))}
    pend["faltando"] = [", ".join(k for k, m in falta.items() if m.iloc[i])
                        for i in range(len(pend))]
    pend = pend[pend["faltando"] != ""]
    st.caption(f"{len(pend)} de {len(pc)} computadores com algum campo pendente no GLPI.")
    if len(pend):
        st.dataframe(pend[["ativo", "responsavel", "empresa_glpi", "faltando"]]
                     .sort_values(["empresa_glpi", "ativo"]),
                     hide_index=True, width="stretch")


def pagina_maquina():
    st.title("Ficha da Máquina")
    pcs = EQUIP[EQUIP["categoria_ativo"] == "computador"].sort_values("ativo")
    nome = st.selectbox("Máquina", pcs["ativo"].tolist(), index=None,
                        placeholder="Digite para buscar — ou veja a visão geral do inventário abaixo")
    if nome is None:
        _inventario_glpi()
        return
    r = pcs[pcs["ativo"] == nome].iloc[0]

    c = st.columns(4)
    info_card(c[0], "Nº de série", r["numero_serie"])
    info_card(c[1], "Responsável", r["responsavel"])
    info_card(c[2], "Empresa", r["empresa_responsavel"])
    info_card(c[3], "Status", r["status"])
    c = st.columns(4)
    info_card(c[0], "Fabricante / Modelo",
              " ".join(str(x) for x in (r["fabricante"], r["modelo"]) if x and pd.notna(x)))
    info_card(c[1], "SO",
              " ".join(str(x) for x in (r["sistema_operacional"], r["versao_so"]) if x and pd.notna(x)))
    info_card(c[2], "Processador", r["processador"])
    info_card(c[3], "Memória", f"{r['memoria_gb']:.0f} GB" if pd.notna(r["memoria_gb"]) else "—")
    inv = (r["ultima_inventariacao"].strftime("%d/%m/%Y")
           if pd.notna(r["ultima_inventariacao"]) else "—")
    st.caption(f"Entidade: {r['entidade'] or '—'} · Local: {r['localizacao'] or '—'} · "
               f"Última inventariação: {inv}")

    sfs = SOFT[SOFT["computador"] == nome].sort_values("software")
    st.subheader(f"Softwares instalados ({len(sfs)})")
    if len(sfs):
        sfs_f = filtro_tabela(sfs, "software", ["fabricante", "categoria"], key="f_sw_maq")
        st.dataframe(sfs_f[["software", "fabricante", "categoria", "versao", "data_instalacao"]],
                     hide_index=True, width="stretch")
    else:
        st.caption("Inventário de software não disponível para esta máquina no GLPI.")


# ============================================================== página: exploração
@st.cache_data(show_spinner="Montando o explorador...")
def pyg_html(nome_df: str) -> str:
    import pygwalker as pyg
    return pyg.to_html(load_data()[nome_df])


def pagina_exploracao():
    st.title("Exploração livre (pygwalker)")
    escolha = st.selectbox("Conjunto de dados", ["colaboradores", "equipamentos", "softwares",
                                                 "licencas", "ferramentas"])
    df = dfs[escolha]
    st.caption(f"{len(df)} linhas × {len(df.columns)} colunas. Arraste os campos para montar "
               "gráficos e tabelas (estilo Tableau).")
    st.download_button("Baixar CSV", df.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"atlas_{escolha}.csv", mime="text/csv")
    st.iframe(pyg_html(escolha), height=950)


# ============================================================== página: bancos de dados
# o parâmetro _mtime só existe p/ invalidar o cache quando o arquivo do modelo muda
@st.cache_data(show_spinner=False)
def er_publi(_mtime):
    tables, refs, views = parse_dbml(DIAGRAMS_DIR / "publi_mysql.dbml")
    dot, n_tab, n_rel = dot_dbml(tables, refs, views["Default"])
    dicionario = {t: cols for t, cols in tables.items()}
    return dot, n_tab, n_rel, dicionario


@st.cache_data(show_spinner=False)
def er_postgres(_mtime):
    ents, rels = parse_erd(DIAGRAMS_DIR / "postgres_sql.erd")
    dot, n_tab, n_rel = dot_erd(ents, rels)
    rel_rows = [{"schema (FK)": fs, "tabela (FK)": fn, "referencia (PK)": f"{ps}.{pn}",
                 "constraint": nome} for (fs, fn), (ps, pn), nome in rels]
    return dot, n_tab, n_rel, rel_rows


def pagina_bancos():
    st.title("Bancos de dados — relações entre tabelas")
    banco = st.selectbox("Banco de dados", ["MySQL Publi (ERP)", "Postgres (Cloud SQL)"])

    if banco == "MySQL Publi (ERP)":
        dot, n_tab, n_rel, dicionario = er_publi((DIAGRAMS_DIR / "publi_mysql.dbml").stat().st_mtime)
        st.caption(f"{n_tab} tabelas centrais · {n_rel} relações")
        st.graphviz_chart(dot, width="stretch")
        with st.expander(f"Dicionário do ERP — todas as {len(dicionario)} tabelas"):
            t = st.selectbox("Tabela", sorted(dicionario), index=None,
                             placeholder="Digite para buscar (ex.: pt01, ts01, cli01...)")
            if t:
                cols = pd.DataFrame(dicionario[t], columns=["coluna", "tipo", "pk"])
                st.caption(f"`{t}` — {len(cols)} colunas")
                st.dataframe(cols, hide_index=True, width="stretch")
    else:
        dot, n_tab, n_rel, rel_rows = er_postgres((DIAGRAMS_DIR / "postgres_sql.erd").stat().st_mtime)
        st.caption(f"{n_tab} tabelas em 5 schemas · {n_rel} foreign keys")
        st.graphviz_chart(dot, width="stretch")
        with st.expander(f"Lista das {n_rel} foreign keys"):
            st.dataframe(pd.DataFrame(rel_rows), hide_index=True, width="stretch")


PAGINAS = {
    "Painel Gerencial": pagina_painel,
    "Ficha do Colaborador": pagina_colaborador,
    "Ficha da Máquina": pagina_maquina,
    "Exploração livre": pagina_exploracao,
    "Bancos de dados": pagina_bancos,
}
PAGINAS[pagina]()
