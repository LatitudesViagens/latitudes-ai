# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Visão geral

ÁGORA é a assistente corporativa experimental da Latitudes (Viagens de Conhecimento): um chat em Streamlit com agente Google ADK (Gemini), pesquisa web via Tavily e Supabase (Auth, Postgres com RLS, Storage). Apoia pesquisas, análise de anexos e criação de roteiros de viagem; conversas são privadas e só roteiros publicados voluntariamente entram na memória coletiva.

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
| Agente ADK | `latitudes_agent/agent.py`: `root_agent` e `fallback_agent`; o `instruction` é o prompt de sistema e contém as regras de comportamento |
| Chamada aos modelos Gemini | `services/agent_runner.py`: tentativa principal → fallback com timeout por tentativa; histórico reconstruído em `InMemorySessionService` a cada chamada; data/hora de São Paulo injetada em cada turno |
| Ferramenta Tavily | `agent_tools/web_search.py` (`search_web`); fontes coletadas em `agent_runner._collect_web_sources` |
| Memória coletiva | `database/shared_itineraries.py`; UI em `streamlit_app.py` (`show_publish_itinerary_dialog`, `create_shared_itinerary_offer`, `show_conversation_visibility_dialog`) |
| Migrações Supabase | `database/migrations/NNN_*.sql`, aplicadas manualmente no SQL Editor, em ordem; incluem as políticas RLS |
| UI / assets | `streamlit_app.py` (arquivo único); `assets/` (CSS e imagens embutidas como data URI); `static/` servido em `/app/static/...` via `enableStaticServing` |

Comportamentos não óbvios:
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
| `GOOGLE_API_KEY`, `GOOGLE_GENAI_USE_VERTEXAI` | lidas implicitamente pelo SDK `google-genai` |
| `COOKIES_PASSWORD` | `streamlit_app.py`; obrigatória fora da Vercel |
| `VERCEL` | definida pela plataforma; ativa o stub de cookies |
| `PORT` | porta do contêiner (`Dockerfile.vercel`, padrão 80) |

## Regras de trabalho

- Todo arquivo em UTF-8. Nunca reescreva um arquivo inteiro quando uma edição pontual resolve. Já há texto corrompido (mojibake) em `services/agent_runner.py`; no PowerShell leia arquivos com `Get-Content -Encoding utf8`.
- Nunca leia, imprima ou copie `.env`, `.env.local`, `.streamlit/secrets.toml` ou qualquer segredo. Para saber quais variáveis existem, consulte o código ou esta lista.
- Qualquer mudança em RLS, políticas de Storage ou migrações do Supabase exige confirmação explícita da Isabelle antes de ser feita.
- Não altere a lógica de privacidade (visibilidade de conversas, acesso a anexos) nem da memória coletiva (publicação/reuso de roteiros, instruções do agente sobre contexto compartilhado) sem avisar antes e explicar o impacto.
- Estilo: argumentos nomeados nas chamadas, um argumento por linha em chamadas com vários argumentos, type hints com `X | None`.
