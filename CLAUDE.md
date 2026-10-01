# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Visão geral

ÁGORA é a assistente corporativa experimental da Latitudes (Viagens de Conhecimento): um chat em Streamlit com agente Google ADK (modelos via OpenRouter/LiteLLM), pesquisa web via Tavily e Supabase (Auth, Postgres com RLS, Storage). Apoia pesquisas, análise de anexos e criação de roteiros de viagem; conversas são privadas e só roteiros publicados voluntariamente entram na memória coletiva.

Textos de interface, prompts, mensagens de erro e comentários são em português do Brasil — mantenha assim.

Arquitetura, decisões, problemas conhecidos e plano de migração: [docs/arquitetura.md](docs/arquitetura.md). Consulte-o em vez de duplicar o conteúdo aqui.

## Fase atual

Migração para ferramentas pagas na branch `migracao-ferramentas-pagas`: Supabase pago (mantido, sem migrar dados), OpenRouter no lugar da chamada direta ao Gemini, e VM Linux no Azure no lugar da Vercel. Prioridades e critérios de aceite estão nas seções 6, 7 e 9 de docs/arquitetura.md.

## Mapa do código

Camadas: `streamlit_app.py` / `main.py` → `services/chat_service.py` → `services/agent_runner.py` → `latitudes_agent/agent.py` (+ `agent_tools/`). Os módulos de `database/` recebem um `client` Supabase autenticado.

| Área | Onde |
|---|---|
| Login e sessão | `database/auth.py` (login, reset de senha por código); `streamlit_app.py`: `show_login`, `persist_authentication` / `restore_authentication` (cookies), `SessionCookieManager` (stub usado na Vercel, sessão não sobrevive a recarga) |
| Cliente Supabase | `database/client.py` |
| Conversas e mensagens | `database/conversations.py`, `database/messages.py`; orquestração em `services/chat_service.py` |
| Anexos | `database/attachments.py` (bucket privado `chat-attachments`, limites no topo do arquivo, redimensiona imagens, extrai texto de DOCX/XLSX) |
| Agente ADK | `latitudes_agent/agent.py`: `root_agent` e `fallback_agent` com `LiteLlm` (OpenRouter); modelos em `AGORA_MODEL_PRINCIPAL`/`AGORA_MODEL_FALLBACK`, de empresas diferentes e escolhidos por custo × qualidade; o `instruction` é o prompt de sistema e contém as regras de comportamento |
| Chamada aos modelos | `services/agent_runner.py`: principal → fallback → fallback de novo (só após 429/5xx), timeout por tentativa; histórico reconstruído em `InMemorySessionService` a cada chamada; data/hora de São Paulo injetada em cada turno; partes `thought=True` (raciocínio) são descartadas |
| Títulos das conversas | Provisório por regras em `streamlit_app.create_title_from_message`; na primeira resposta válida, `chat_service.update_title_after_first_answer` gera um resumo com `services/title_service.py` (em thread, sem bloquear a UI) |
| Exportar respostas | `services/document_export.py` converte o Markdown da resposta em PDF (reportlab), Word (python-docx), Excel/CSV (só as tabelas), com a identidade visual da Latitudes (cores de `assets/styles.css`, logo no topo, rodapé só com logo + "Latitudes"). PDF e Word passam antes por `services/document_cleanup.py`, que tira o tom de conversa via IA (cache por conteúdo; descarta a versão da IA se algum número sumir e cai numa limpeza por regras). Menu "Exportar" em `streamlit_app.show_export_menu`, arquivo gerado só no clique |
| Geração de arquivos pelo agente | Ferramenta `agent_tools/document_generator.generate_document` (PDF/docx/xlsx/csv via `document_export`). Os arquivos vão para a `ContextVar` `GENERATED_FILES`, que `agent_runner.stream_agent` recria a cada tentativa (descartados se a tentativa falha); `chat_service` salva no bucket `chat-attachments` em `{user_id}/{conversation_id}/` (mesmas políticas dos anexos) e grava em `attachments` da mensagem do assistente com `generated: True`; `streamlit_app.display_generated_files` mostra o cartão com download no clique |
| Fotos na conversa | Ferramenta `agent_tools/image_search.search_images` (Tavily com `include_images`); só age quando a mensagem pede fotos/imagens (`IMAGES_REQUESTED`). As fotos entram em `sources` com `type: "image"` (via `agent_runner._collect_images`) e `streamlit_app.display_image_gallery` mostra a galeria. Direitos de uso desconhecidos: não entram em documentos para clientes |
| Travas de geração | `generate_document` e `search_images` só agem se a mensagem atual pedir (regex em `user_requested_file`/`user_requested_images`, definidas por `agent_runner.stream_agent`); documentos levam só o roteiro (`document_export.remove_advice_sections` tira recomendações, observações, dicas, notas e fontes) |
| LGPD na memória coletiva | `services/personal_data_check.find_client_data` (padrões fixos + IA) bloqueia a publicação de roteiros com dados de clientes: verifica o conteúdo ao abrir a ficha (botão desativado) e os campos digitados ao publicar. Falha na verificação = bloqueio. Regra de negócio inegociável: dados de clientes nunca entram na memória coletiva |
| Ficha de publicação | `services/itinerary_metadata.py` sugere os campos via IA (uma vez por mensagem, em `st.session_state`); `streamlit_app._get_publication_suggestions` |
| Ferramenta Tavily | `agent_tools/web_search.py` (`search_web`); fontes coletadas em `agent_runner._collect_web_sources` |
| Memória coletiva | `database/shared_itineraries.py`; UI em `streamlit_app.py` (`show_publish_itinerary_dialog`, `create_shared_itinerary_offer`, `show_conversation_visibility_dialog`) |
| Migrações Supabase | `database/migrations/NNN_*.sql`, aplicadas manualmente no SQL Editor, em ordem; incluem as políticas RLS |
| UI / assets | `streamlit_app.py` (arquivo único); `assets/` (CSS e imagens embutidas como data URI); `static/` servido em `/app/static/...` via `enableStaticServing` |

Comportamentos não óbvios:
- Todo texto exibido com `st.markdown`/`st.write_stream` passa por `escape_dollar_signs`: o Markdown do Streamlit trata `$...$` como LaTeX (quebrava "US$ 100 a US$ 200").
- `streamlit_app.py` e `main.py` chamam `truststore.inject_into_ssl()` antes de tudo: o Kaspersky das máquinas da Latitudes intercepta HTTPS (inclusive `openrouter.ai`) com um certificado que só o Windows conhece. Scripts avulsos que chamam o OpenRouter precisam fazer o mesmo.
- O streaming real do provedor está desativado (`StreamingMode.NONE`); a resposta completa é fatiada por `_split_for_display` para simular streaming. `streamlit_app.stream_assistant_response` faz a ponte async ↔ Streamlit com `asyncio.Queue`.
- `chat_service` persiste a mensagem do usuário antes de chamar o modelo, reaproveita uma mensagem de usuário pendente idêntica, limita o histórico a 30 mensagens e só reenvia binários do anexo mais recente quando a pergunta menciona anexos. Sempre grava uma resposta do assistente (inclusive "interrompida") para não travar a conversa.
- Mensagens com `role="system"` (ex.: roteiro compartilhado) viram blocos "CONTEXTO INTERNO" de papel `user` no histórico do agente.

## Como rodar

Python 3.13 com venv em `.venv/`.

```bash
pip install -r requirements.txt -c constraints-3.13.txt
streamlit run streamlit_app.py          # interface web
python main.py                          # cliente de terminal (mesma camada de serviços)
```

Docker (mesma imagem usada no deploy da Vercel):

```bash
docker build -f Dockerfile.vercel -t agora .
docker run --rm -p 8501:80 --env-file latitudes_agent/.env agora
```

Não há suíte de testes, linter ou formatter. Os scripts não versionados `diagnostico_*.py`, `testar_modelo_*.py`, `comparar_modelos.py` e `listar_modelos.py` são verificações manuais e ficam fora do deploy (`.vercelignore`, `.dockerignore`).

## Variáveis de ambiente

Carregadas de `latitudes_agent/.env` (não da raiz) por cada módulo via `load_dotenv`.

| Variável | Uso |
|---|---|
| `SUPABASE_URL`, `SUPABASE_KEY` | `database/client.py` |
| `TAVILY_API_KEY` | `agent_tools/web_search.py` |
| `OPENROUTER_API_KEY` | lida implicitamente pelo LiteLLM (agente e títulos) |
| `AGORA_MODEL_PRINCIPAL`, `AGORA_MODEL_FALLBACK` | opcionais; padrão em `latitudes_agent/agent.py` (formato `openrouter/<empresa>/<modelo>`) |
| `GOOGLE_API_KEY`, `GOOGLE_GENAI_USE_VERTEXAI` | não usadas pelo app desde a troca para o OpenRouter; só pelos scripts de diagnóstico antigos |
| `COOKIES_PASSWORD` | `streamlit_app.py`; obrigatória fora da Vercel |
| `VERCEL` | definida pela plataforma; ativa o stub de cookies |
| `PORT` | porta do contêiner (`Dockerfile.vercel`, padrão 80) |

## Regras de trabalho

- Todo arquivo em UTF-8. Nunca reescreva um arquivo inteiro quando uma edição pontual resolve — reescritas completas já causaram mojibake (`Ã§` no lugar de `ç`). No PowerShell leia arquivos com `Get-Content -Encoding utf8`; alguns arquivos têm BOM UTF-8, preserve-o.
- Nunca leia, imprima ou copie `.env`, `.env.local`, `.streamlit/secrets.toml` ou qualquer segredo. Para saber quais variáveis existem, consulte o código ou esta lista.
- Qualquer mudança em RLS, políticas de Storage ou migrações do Supabase exige confirmação explícita da Isabelle antes de ser feita.
- Não altere a lógica de privacidade (visibilidade de conversas, acesso a anexos) nem da memória coletiva (publicação/reuso de roteiros, instruções do agente sobre contexto compartilhado) sem avisar antes e explicar o impacto.
- Estilo: argumentos nomeados nas chamadas, um argumento por linha em chamadas com vários argumentos, type hints com `X | None`.
