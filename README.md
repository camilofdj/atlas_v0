# Atlas — TI Grupo OM (Streamlit Community Cloud)

Versão standalone do Atlas para publicação no Streamlit Community Cloud.
O conteúdo desta pasta é o repositório completo — copie-a para a **raiz** de um
repositório GitHub próprio (**privado**: o app mostra dados internos e o modelo
do ERP está versionado em `db_diagrams/`).

> A versão de desenvolvimento (com notebook, pipeline GLPI no botão de atualizar
> e camada `src` do monorepo) vive em `integracoes/projects/dashboard_atlas/`.
> Mudanças de lógica devem ser feitas lá e copiadas para cá.

## Publicar (passo a passo)

1. **Repo**: crie um repositório **privado** no GitHub e suba o conteúdo desta pasta
   na raiz (`streamlit_app.py` precisa ficar na raiz).
2. **Service account** (validado com a `integracoes-dados@sheetsintegration-451500`):
   - **BigQuery**: a SA acessa direto — papéis **BigQuery Job User** + **Data Viewer**
     (datasets `LDAP` e `gpli`). Já ok na SA acima.
   - **Sheets via DWD**: a SA lê as planilhas **como um usuário do domínio**
     (`gcp_impersonate_user` no secrets) — não precisa compartilhar planilha com a SA.
     O DWD da `integracoes-dados` **já está autorizado** no Workspace com os escopos
     `spreadsheets` + `drive`. (Se usar outra SA: Admin do Workspace → Segurança →
     Controles de API → Delegação em todo o domínio → autorizar o client ID com esses
     2 escopos.) O usuário impersonado precisa ter acesso às 3 planilhas.
3. **Deploy**: em [share.streamlit.io](https://share.streamlit.io) → *Create app* →
   aponte o repo/branch, main file `streamlit_app.py`.
4. **Secrets**: em *App settings → Secrets*, cole o conteúdo de
   [.streamlit/secrets.toml.example](.streamlit/secrets.toml.example) preenchido com a
   chave JSON da SA (o `gcp_impersonate_user` vem ANTES da seção, regra do TOML).
5. **Privacidade (obrigatório)**: em *App settings → Sharing*, deixe o app **privado**
   ("Only specific people can view this app") e adicione os e-mails dos viewers.
   O app expõe nomes, e-mails, equipamentos e custos internos — **não** deixar público.

## Diferenças desta versão

- O botão **Atualizar dados** recarrega **BigQuery + Sheets**. O passo GLPI → BigQuery
  (MySQL interno, inacessível do cloud) continua rodando por dentro da rede — pelo
  pipeline `gpli` ou pelo botão do app interno.
- As credenciais vêm de `st.secrets[gcp_service_account]` (ver `helpers_gcp.py`);
  localmente também roda com `GOOGLE_APPLICATION_CREDENTIALS`/`GOOGLE_CREDENTIALS_JSON`
  num `.env` ao lado do app.
- O cache parquet (`.data_cache/`) é efêmero no cloud (se perde a cada reboot do app) —
  a primeira visita após um reboot faz a carga completa (~15 s).

## Rodar local

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```
