# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Visão geral

ÁGORA é a assistente corporativa experimental da Latitudes (Viagens de Conhecimento): um chat em Streamlit com agente Google ADK (modelos via OpenRouter/LiteLLM), pesquisa web via Tavily e Supabase (Auth, Postgres com RLS, Storage). Apoia pesquisas, análise de anexos e criação de roteiros de viagem; conversas são privadas e só roteiros publicados voluntariamente entram na memória coletiva.

Textos de interface, prompts, mensagens de erro e comentários são em português do Brasil — mantenha assim.

Arquitetura, decisões, problemas conhecidos e plano de migração: [docs/arquitetura.md](docs/arquitetura.md). Consulte-o em vez de duplicar o conteúdo aqui.

## Fase atual

Migração para ferramentas pagas na branch `migracao-ferramentas-pagas`: Supabase pago (mantido, sem migrar dados), OpenRouter no lugar da chamada direta ao Gemini, e VM Linux no Azure no lugar da Vercel. Prioridades e critérios de aceite estão nas seções 6, 7 e 9 de docs/arquitetura.md.

## Mapa do código

Camadas: `streamlit_app.py` / `main.py` → `services/chat_service.py` → `services/agent_runner.py` → `latitudes_agent/agent.py` (+ `agent_tools/`). Os módulos de `database/` recebem um `client` Supabase autenticado. Código novo fica fora do `streamlit_app.py`: painel do TI em `admin/`, partes da tela da conversa em `ui/`.

| Área | Onde |
|---|---|
| Login e sessão | `database/auth.py` (login, reset de senha por código); `streamlit_app.py`: `show_login`, `persist_authentication` / `restore_authentication` (cookie só para o login), `SessionCookieManager` (stub usado na Vercel, sessão não sobrevive a recarga). A conversa aberta fica no endereço (`?c=<id>`, `sync_selected_conversation`), não em cookie: gravar cookie faz o componente forçar uma atualização extra da tela |
| Cliente Supabase | `database/client.py` |
| Conversas e mensagens | `database/conversations.py`, `database/messages.py`; orquestração em `services/chat_service.py` |
| Anexos | `database/attachments.py` (bucket privado `chat-attachments`, limites no topo do arquivo, redimensiona imagens, extrai texto de DOCX/XLSX) |
| Agente ADK | `latitudes_agent/agent.py`: `root_agent` e `fallback_agent` com `LiteLlm` (OpenRouter); modelos em `AGORA_MODEL_PRINCIPAL`/`AGORA_MODEL_FALLBACK`, de empresas diferentes e escolhidos por custo × qualidade; o `instruction` é o prompt de sistema e contém as regras de comportamento |
| Chamada aos modelos | `services/agent_runner.stream_agent`: N tentativas no principal com espera crescente e depois M no fallback (`services/settings.py`, variáveis `AGORA_*`); falha = exceção, timeout ou resposta vazia; 401/402 param na hora (mesma conta do OpenRouter), 400/404 pulam para o fallback. Cada tentativa é avisada ao `attempt_listener` (início/fim, com tokens). Histórico reconstruído em `InMemorySessionService` a cada chamada; data/hora de São Paulo injetada em cada turno; partes `thought=True` (raciocínio) são descartadas |
| Turnos (salvar antes da IA) | `services/chat_service.start_turn` grava a pergunta e reserva a resposta (`status` pendente, `reply_to` = pergunta) ANTES da IA; `run_turn_stream` executa/repete o turno sempre atualizando o MESMO registro (`processando` → `concluida`/`erro`) e registra tentativas em `message_attempts`; `cancel_turn` marca `cancelada` (some da tela e do histórico da IA). `recover_stale_turns` transforma em erro turnos parados há mais que `RetrySettings.stale_turn_seconds`. A IA roda em segundo plano em `services/turn_worker.py` (thread por turno, registro em módulo importado, `cancel_turn_job` para o "Parar"); a tela acompanha com `streamlit_app.running_turn_controls` (fragmento a cada 1 s que olha primeiro a memória do processo, `turn_worker.is_running`, e só consulta o banco se a tarefa não estiver aqui). O botão Parar fica dentro desse fragmento, em `st.bottom` ao lado do campo de mensagem (`show_chat_input(running_reply_id=...)`), e só aparece com a IA pensando. Pendente: o 1º clique em Parar/Tentar novamente ainda se perde no navegador (ver docs/arquitetura.md, "Pendências da interface"). UI: `save_turn`/`answer_turn`, botões Tentar novamente/Cancelar. Requer a migração 008 |
| Títulos das conversas | Provisório por regras em `streamlit_app.create_title_from_message`; ao iniciar o 1º turno, `turn_worker` gera em paralelo um resumo do PEDIDO (`chat_service.update_title_from_first_question` → `services/title_service.py`), pronto antes da resposta |
| Documentos | `services/document_export.py` gera PDF (reportlab), Word (python-docx) e Excel/CSV (só as tabelas) com a identidade visual da Latitudes (cores de `assets/styles.css`, logo no topo e no rodapé). Só são gerados quando a pessoa pede (ferramenta `generate_document`); não há menu de exportar nas respostas |
| Fontes consultadas | `streamlit_app.split_sources_footer` separa a seção do fim da resposta e a mostra como uma linha de rodapé (sem marcadores); o conteúdo salvo não muda |
| Geração de arquivos pelo agente | Ferramenta `agent_tools/document_generator.generate_document` (PDF/docx/xlsx/csv via `document_export`). Os arquivos vão para a `ContextVar` `GENERATED_FILES`, que `agent_runner.stream_agent` recria a cada tentativa (descartados se a tentativa falha); `chat_service` salva no bucket `chat-attachments` em `{user_id}/{conversation_id}/` (mesmas políticas dos anexos) e grava em `attachments` da mensagem do assistente com `generated: True`; `streamlit_app.display_generated_files` mostra o cartão com download no clique |
| Fotos na conversa | Ferramenta `agent_tools/image_search.search_images` (Tavily com `include_images`); só age quando a mensagem pede fotos/imagens (`IMAGES_REQUESTED`). As fotos entram em `sources` com `type: "image"` (via `agent_runner._collect_images`) e `streamlit_app.display_image_gallery` mostra a galeria. Direitos de uso desconhecidos: não entram em documentos para clientes |
| Travas de geração | `generate_document` e `search_images` só agem se a mensagem atual pedir (regex em `user_requested_file`/`user_requested_images`, definidas por `agent_runner.stream_agent`); documentos levam só o roteiro (`document_export.remove_advice_sections` tira recomendações, observações, dicas, notas e fontes) |
| LGPD na memória coletiva | `services/personal_data_check.find_client_data` (padrões fixos + IA) bloqueia a publicação de roteiros com dados de clientes: verifica o conteúdo ao abrir a ficha (botão desativado) e os campos digitados ao publicar. Falha na verificação = bloqueio. Regra de negócio inegociável: dados de clientes nunca entram na memória coletiva |
| Ficha de publicação | `services/itinerary_metadata.py` sugere os campos via IA (uma vez por mensagem, em `st.session_state`); `streamlit_app._get_publication_suggestions` |
| Ferramenta Tavily | `agent_tools/web_search.py` (`search_web`); fontes coletadas em `agent_runner._collect_web_sources` |
| Memória coletiva | `database/shared_itineraries.py`; UI em `streamlit_app.py` (`show_publish_itinerary_dialog`, `create_shared_itinerary_offer`, `show_conversation_visibility_dialog`) |
| Custos e painel do TI | `services/custos.py` (cotação PTAX com cache diário e reserva, conversão para R$, `collect_usage`/`note_usage` para chamadas avulsas); `services/llm_cost_client.py` guarda o `usage.cost` do OpenRouter, que o ADK descarta; custo das respostas em `message_attempts`, das chamadas avulsas (título, LGPD, ficha) em `ai_usage`. Painel em `admin/` (`painel.py` com o botão e as abas; `gastos.py` mostra só o total e o custo por modelo); papel conferido no banco por `database/roles.is_ti` (função `public.is_ti`, migração 009). `admin/supabase_admin.py` é o ÚNICO lugar que lê `SUPABASE_SERVICE_ROLE_KEY`, e só depois de conferir o papel TI |
| Senhas (TI) | `admin/senhas.py`: o TI gera uma senha temporária (API admin, `supabase_admin.set_temporary_password`), o reset vai para `password_resets` e a obrigação para `password_change_required`. Enquanto houver obrigação, a pessoa só vê "Crie sua nova senha" (checagem no fim do `streamlit_app.py`, antes de `show_authenticated_area`). Quem apaga a obrigação é o servidor (`clear_own_password_requirement`, service_role, ID vindo do token), nunca a pessoa. Não há "Esqueci minha senha" na tela de login. Cadastro de contas pelo TI em `admin/usuarios.py` (`supabase_admin.create_user_with_temporary_password`, conta já confirmada, sem e-mail de convite; registro em `user_registrations`, migração 010; a conta nova entra no mesmo fluxo de troca obrigatória). Exclusão de contas também em `admin/usuarios.py` (`supabase_admin.delete_user_account`): apaga conta, conversas e arquivos do Storage; a migração 011 trocou as ligações por `on delete set null` para preservar roteiros publicados, base de conhecimento, modelos, gastos e auditoria; registro em `user_deletions`. Equipe de TI (papel em `user_roles`) também em `admin/usuarios.py` (`supabase_admin.set_ti_role`): ninguém retira o próprio acesso e a ÁGORA nunca fica sem TI; vale no próximo login da pessoa |
| Base de conhecimento | `services/base_conhecimento.py` decide antes da IA (em `chat_service._find_knowledge_match`): similaridade ≥ `AGORA_BASE_LIMIAR_DIRETO` responde direto (só na 1ª pergunta da conversa), ≥ `AGORA_BASE_LIMIAR_CONTEXTO` entra como contexto interno, abaixo segue normal; falha = fluxo normal. Base vazia não calcula embedding (cache de 60 s, limpo quando o TI muda a base). Embeddings em `services/embeddings.py` (OpenRouter, 768 dimensões; custo estimado pelos tokens). Dados em `database/knowledge_entries.py`; aba do TI e botão "Sugerir para a base" (com trava LGPD) em `admin/base_conhecimento.py`. Nada entra sem aprovação do TI |
| Lista de conversas | `ui/lista_conversas.py`: UM componente (`st.components.v2`) no lugar de ~85 elementos por conversa, que eram a maior parte da lentidão (medição de 09/10/2026). O clique vira uma ação pendente aplicada no começo da execução (`streamlit_app.apply_conversation_list_action`), então roda a tela uma vez só; prefira `on_click` a `if st.button(...): ...; st.rerun()` pelo mesmo motivo. Títulos entram só por `textContent` |
| Medição da lentidão (temporária) | `ui/medicao.py`: com `?medir=1` no endereço, contador no canto da tela e vigia `[TRAVA]` no terminal. Remover quando a lentidão estiver resolvida |
| Busca de conversas | `ui/busca.py` (barra lateral) → `database/search.py` → função SQL `search_conversations` (português, sem acento, RLS) |
| Modelos de prompt | TI cria/edita em `admin/modelos_prompt.py`; a pessoa escolhe na conversa vazia (`ui/modelos_prompt.py`) e o texto vai para o campo via `restore_chat_input`. Dados em `database/prompt_templates.py` |
| Integração RD Station CRM + Envision | Regras em `docs/regras-integracao-clientes.md` (não mudar sem a Isabelle); plano em `docs/plano-integracao.md`. Conectores SOMENTE LEITURA em `integracoes/` (`http.py` tem a trava global: só GET, exceto `POST /Records/Query` do Envision, e nada de `/Records` além das 3 leituras); classificações em `integracoes/mapeamentos.json`; contas e filtro de campos permitidos em `services/perfil_cliente.py`; ferramenta `agent_tools/perfil_cliente.py` (anota a consulta em `services/contexto_turno.TURNO_ATUAL`; `chat_service._save_client_lookups` grava em `client_lookups`, migração 012). Testes: `python -m unittest discover -s tests` (só fixtures; nunca chamar as APIs reais no desenvolvimento) |
| Migrações Supabase | `database/migrations/NNN_*.sql`, aplicadas manualmente no SQL Editor, em ordem; incluem as políticas RLS |
| UI / assets | `streamlit_app.py` (arquivo único); `assets/` (CSS e imagens embutidas como data URI); `static/` servido em `/app/static/...` via `enableStaticServing` |

Comportamentos não óbvios:
- Todo texto exibido com `st.markdown`/`st.write_stream` passa por `escape_dollar_signs`: o Markdown do Streamlit trata `$...$` como LaTeX (quebrava "US$ 100 a US$ 200").
- `streamlit_app.py` e `main.py` chamam `truststore.inject_into_ssl()` antes de tudo: o Kaspersky das máquinas da Latitudes intercepta HTTPS (inclusive `openrouter.ai`) com um certificado que só o Windows conhece. Scripts avulsos que chamam o OpenRouter precisam fazer o mesmo.
- O streaming real do provedor está desativado (`StreamingMode.NONE`); a resposta aparece inteira quando o turno termina.
- LiteLLM e agente (~4 s de import) não são importados no topo da tela: `chat_service`, `streamlit_app` e afins os importam dentro das funções, e `turn_worker.warm_up()` os pré-carrega em segundo plano ao abrir a página. Não reintroduza imports desses módulos no topo do caminho da tela.
- As tarefas em segundo plano (resposta e título) nunca usam o cliente Supabase da sessão: `turn_worker` lê o token na thread da tela (`database.client.get_access_token`) e cada thread cria o seu (`get_background_client`, sem renovar token). O cliente usa HTTP/2, que trava/falha quando compartilhado entre threads ("Não foi possível carregar suas conversas").
- "Parar" (`turn_worker.cancel_turn_job`) grava o turno como interrompido na hora e depois cancela a tarefa; as gravações da tarefa usam `only_if_status` (só valem para turnos em andamento), então uma resposta que termina depois do Parar é descartada.
- Não chame a IA dentro da execução do script do Streamlit: enquanto espera, o script não "fala" com a tela (o `st.write_stream` ignora trechos vazios), então o Streamlit não consegue interrompê-lo — o "Parar" não funcionava e trocar de conversa misturava telas. Por isso a IA roda em `services/turn_worker.py`.
- `chat_service` limita o histórico a 30 mensagens, envia à IA só turnos concluídos e só reenvia binários do anexo mais recente quando a pergunta menciona anexos.
- Mensagens de erro ao usuário ficam no conteúdo da própria resposta (`TURN_FAILED_MESSAGE` etc. em `chat_service`); detalhes técnicos só no log e em `message_attempts`.
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

Testes automatizados só das integrações (`tests/`, unittest com fixtures). Não há linter ou formatter. Os scripts não versionados `diagnostico_*.py`, `testar_modelo_*.py`, `comparar_modelos.py` e `listar_modelos.py` são verificações manuais e ficam fora do deploy (`.vercelignore`, `.dockerignore`).

## Variáveis de ambiente

Carregadas de `latitudes_agent/.env` (não da raiz) por cada módulo via `load_dotenv`.

| Variável | Uso |
|---|---|
| `SUPABASE_URL`, `SUPABASE_KEY` | `database/client.py` |
| `TAVILY_API_KEY` | `agent_tools/web_search.py` |
| `OPENROUTER_API_KEY` | lida implicitamente pelo LiteLLM (agente e títulos) |
| `AGORA_MODEL_PRINCIPAL`, `AGORA_MODEL_FALLBACK` | opcionais; padrão em `latitudes_agent/agent.py` (formato `openrouter/<empresa>/<modelo>`) |
| `AGORA_TENTATIVAS_PRINCIPAL` / `AGORA_TENTATIVAS_FALLBACK` | opcionais; padrão 2 / 1 (`services/settings.py`) |
| `AGORA_TIMEOUT_PRINCIPAL_SEGUNDOS` / `AGORA_TIMEOUT_FALLBACK_SEGUNDOS` | opcionais; padrão 60 / 90 por tentativa |
| `AGORA_ESPERA_INICIAL_SEGUNDOS` | opcional; padrão 2 (dobra a cada nova tentativa) |
| `AGORA_LOG_TEMPOS` | opcional: `1` grava os tempos de cada etapa (login, conversas, mensagens, turno, tentativas da IA) em `logs/agora-tempos.log` (só tempos e IDs; `services/timing_log.py`). Sem ela, os tempos só vão para o terminal |
| `AGORA_SIMULAR_FALHA` | só para testes: `erro`, `timeout` ou `vazia`, com `:todas` (padrão), `:principal` ou `:primeira`; não chama o modelo nas tentativas simuladas. Nunca definir em produção |
| `AGORA_COTACAO_DOLAR_RESERVA` | cotação usada se a PTAX do Banco Central não responder (ex.: `5,40`); sem ela, o custo fica só em US$ |
| `SUPABASE_SERVICE_ROLE_KEY` | só no servidor; lida apenas por `admin/supabase_admin.py` (e-mails no painel, reset de senha). Nunca exibir nem copiar |
| `AGORA_MODELO_EMBEDDING` | opcional; padrão `openrouter/openai/text-embedding-3-small`. Trocar exige recalcular os embeddings aprovados e recalibrar os limiares |
| `AGORA_BASE_LIMIAR_DIRETO` / `AGORA_BASE_LIMIAR_CONTEXTO` | opcionais; padrão 0,92 / 0,55 |
| `RD_CRM_API_TOKEN`, `ENVISION_BASE_URL`, `ENVISION_API_KEY` | integrações (`integracoes/config.py`); nunca imprimir. A chave do Envision tem permissão de escrita: as travas de leitura são obrigatórias |
| `ENVISION_USUARIO`, `ENVISION_SENHA` | perfil da ÁGORA no Envision; login OAuth2 (`/token`) exigido pela API em todas as consultas. Nunca imprimir |
| `ENVISION_AUTH_FORMATO` | opcional: `senha` (padrão com usuário/senha), `senha_com_chave`, `pura` ou `bearer`, conforme o nível 1 do script de verificação |
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
