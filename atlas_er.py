# -*- coding: utf-8 -*-
"""
Diagramas de relação dos bancos de dados (página "Bancos de dados" do Atlas).

Parseia os modelos versionados em `db_diagrams/` e gera DOT (graphviz) para o
`st.graphviz_chart` — nada é consultado ao vivo nos bancos:

  publi_mysql.dbml   dicionário completo do ERP Publi (MySQL, ~400 tabelas) no
                     formato DBML (dbdiagram.io), com um `DiagramView Default`
                     que define o RECORTE exibido (~21 tabelas centrais).
                     O MySQL do Publi NÃO tem FKs — os `Ref:` são inferidos.
  postgres_sql.erd   ERD do DBeaver (XML) do Postgres/Cloud SQL, com FKs reais.
                     Schemas de sistema (pg_catalog, information_schema) e
                     views (vw_*) ficam de fora.

Os DOIS bancos usam o MESMO estilo visual (build_dot): nó-tabela com cabeçalho
colorido + colunas de junção, aresta rotulada pela coluna. No Postgres o .erd
não traz colunas — as linhas do nó são derivadas dos nomes das constraints FK
(`perfis_cargo_id_fkey` → `cargo_id → cargos`) e o cabeçalho ganha o schema
como subtítulo (cor por schema).

Como atualizar os modelos: ver seção "Bancos de dados" no README.
"""
import re
import xml.etree.ElementTree as ET
from pathlib import Path

DIAGRAMS_DIR = Path(__file__).resolve().parent / "db_diagrams"

_HDR_MYSQL = "#2a78d6"
SCHEMA_COLORS = {"funcionarios": "#2a78d6", "rh": "#1baf7a", "midiativo": "#4a3aa7",
                 "home": "#c98500", "public": "#898781"}


# ============================================================ construtor DOT único
def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _node_html(titulo, cor, subtitulo=None, linhas=(), rodape=None):
    """Nó-tabela padrão: cabeçalho colorido, subtítulo opcional, linhas de coluna, rodapé."""
    sub = (f'<TR><TD ALIGN="LEFT" BGCOLOR="{cor}"><FONT COLOR="white" POINT-SIZE="9">'
           f'{_esc(subtitulo)}</FONT></TD></TR>' if subtitulo else "")
    corpo = "".join(
        f'<TR><TD ALIGN="LEFT" BGCOLOR="#fcfcfb"><FONT COLOR="#0b0b0b">{_esc(texto)}  '
        f'<FONT COLOR="#898781" POINT-SIZE="9">{_esc(sufixo)}</FONT></FONT></TD></TR>'
        for texto, sufixo in linhas)
    pe = (f'<TR><TD ALIGN="LEFT" BGCOLOR="#fcfcfb"><FONT COLOR="#898781" POINT-SIZE="9">'
          f'{_esc(rodape)}</FONT></TD></TR>' if rodape else "")
    return (f'<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="4" COLOR="#c3c2b7">'
            f'<TR><TD BGCOLOR="{cor}"><FONT COLOR="white"><B>{_esc(titulo)}</B></FONT></TD></TR>'
            f'{sub}{corpo}{pe}</TABLE>')


def build_dot(nodes, edges):
    """DOT padrão do Atlas.

    nodes: [{id, titulo, cor, subtitulo?, linhas: [(texto, sufixo)], rodape?}]
    edges: [(origem_id, destino_id, rotulo)]
    """
    linhas = ['digraph {', '  graph [rankdir=LR, bgcolor="transparent", splines=true, '
              'nodesep=0.35, ranksep=0.9, fontname="Segoe UI"];',
              '  node [shape=plain, fontname="Segoe UI", fontsize=11];',
              '  edge [color="#898781", arrowsize=0.6, fontname="Segoe UI", '
              'fontsize=9, fontcolor="#52514e"];']
    for n in nodes:
        html = _node_html(n["titulo"], n["cor"], n.get("subtitulo"),
                          n.get("linhas", ()), n.get("rodape"))
        linhas.append(f'  "{n["id"]}" [label=<{html}>];')
    for origem, destino, rotulo in edges:
        linhas.append(f'  "{origem}" -> "{destino}" [label="{rotulo}"];')
    linhas.append("}")
    return "\n".join(linhas)


# ============================================================ DBML (Publi MySQL)
def parse_dbml(path):
    """DBML → (tables {nome: [(col, tipo, pk)]}, refs [(t1,c1,t2,c2)], views {nome: [tabelas]})."""
    tables, refs, views = {}, [], {}
    cur, in_idx, in_view, in_view_tables = None, False, None, False
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        m = re.match(r'^Table "([^"]+)"', line)
        if m:
            cur, in_idx = m.group(1), False
            tables[cur] = []
            continue
        m = re.match(r"^DiagramView (\w+)", line)
        if m:
            in_view, in_view_tables = m.group(1), False
            views[in_view] = []
            continue
        if in_view:
            if line.startswith("Tables"):
                in_view_tables = True
            elif line.startswith("}"):
                if in_view_tables:
                    in_view_tables = False
                else:
                    in_view = None
            elif in_view_tables and line:
                views[in_view].append(line.strip('" '))
            continue
        m = re.match(r'^Ref[^:]*:\s*"([^"]+)"\."([^"]+)"\s*[<>~-]+\s*"([^"]+)"\."([^"]+)"', line)
        if m:
            refs.append(m.groups())
            continue
        if cur is not None:
            if line.startswith("Indexes"):
                in_idx = True
                continue
            if line.startswith("}"):
                if in_idx:
                    in_idx = False
                else:
                    cur = None
                continue
            if not in_idx:
                m = re.match(r'^"([^"]+)"\s+([^\s\[]+)(?:\s+\[([^\]]*)\])?', line)
                if m:
                    tables[cur].append((m.group(1), m.group(2), "pk" in (m.group(3) or "")))
    return tables, refs, views


def dot_dbml(tables, refs, view_tables):
    """Diagrama do recorte: PK + colunas de junção (+N outras), aresta col=col."""
    vset = set(view_tables)
    refs_v = [r for r in refs if r[0] in vset and r[2] in vset]
    rel_cols = {}
    for t1, c1, t2, c2 in refs_v:
        rel_cols.setdefault(t1, set()).add(c1)
        rel_cols.setdefault(t2, set()).add(c2)

    nodes = []
    for t in sorted(vset):
        cols = tables.get(t, [])
        pks = [(c, f"{ty} · PK") for c, ty, pk in cols if pk]
        rels = [(c, ty) for c, ty, pk in cols if not pk and c in rel_cols.get(t, ())]
        mostrar = pks + rels
        outras = len(cols) - len(mostrar)
        nodes.append({"id": t, "titulo": t, "cor": _HDR_MYSQL, "linhas": mostrar,
                      "rodape": f"+ {outras} colunas" if outras > 0 else None})
    edges = [(t2, t1, c1 if c1 == c2 else f"{c1} = {c2}") for t1, c1, t2, c2 in refs_v]
    return build_dot(nodes, edges), len(vset), len(edges)


# ============================================================ ERD DBeaver (Postgres)
def parse_erd(path, schemas=("funcionarios", "home", "midiativo", "rh", "public")):
    """ERD XML → (entities {id: (schema, tabela)}, relations [(fk_ent, pk_ent, nome_constraint)])."""
    root = ET.parse(path).getroot()
    ents = {}
    for e in root.iter("entity"):
        schema = e.get("fq-name", "").split(".")[0]
        nome = e.get("name", "")
        if schema in schemas and not nome.startswith(("vw_", "_pg")):
            ents[e.get("id")] = (schema, nome)
    rels = []
    for r in root.iter("relation"):
        pk, fk = r.get("pk-ref"), r.get("fk-ref")
        if pk in ents and fk in ents:
            rels.append((ents[fk], ents[pk], r.get("name", "")))
    return ents, rels


def _col_da_constraint(nome, tabela_fk):
    """'perfis_cargo_id_fkey' (tabela perfis) → 'cargo_id'."""
    s = nome
    if s.startswith(tabela_fk + "_"):
        s = s[len(tabela_fk) + 1:]
    return s[:-5] if s.endswith("_fkey") else s


def dot_erd(ents, rels):
    """Diagrama no MESMO estilo do MySQL: nó-tabela (subtítulo = schema, cor por schema),
    linhas = colunas FK derivadas das constraints, aresta rotulada pela coluna."""
    fk_linhas = {}      # (schema, tabela) -> [(col, "→ tabela_pk")]
    for (fs, fn), (ps, pn), nome in rels:
        fk_linhas.setdefault((fs, fn), []).append((_col_da_constraint(nome, fn), f"→ {pn}"))

    nodes = []
    for schema, nome in sorted(ents.values()):
        nodes.append({"id": f"{schema}.{nome}", "titulo": nome,
                      "subtitulo": schema, "cor": SCHEMA_COLORS.get(schema, "#898781"),
                      "linhas": fk_linhas.get((schema, nome), ())})
    edges = [(f"{ps}.{pn}", f"{fs}.{fn}", _col_da_constraint(nome, fn))
             for (fs, fn), (ps, pn), nome in rels]
    return build_dot(nodes, edges), len(ents), len(edges)
