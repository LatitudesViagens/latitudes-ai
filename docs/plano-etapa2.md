# Plano — Etapa 2 da ÁGORA

Branch `migracao-ferramentas-pagas`. Banco: uma única migração, `database/migrations/009_etapa2.sql` (aplicar só após aprovação da Isabelle).

## Checklist

- [x] 0. Migração 009 aprovada e aplicada no Supabase (+ papel TI concedido)
- [x] 1. Tokens e custo em R$ (PTAX) + painel de gastos do TI
- [x] 2. Painel do administrador: reset de senha sem e-mail + troca obrigatória
- [x] 3. Base de conhecimento com aprovação (pgvector)
- [x] 4. Busca de conversas (texto em português, só as próprias)
- [x] 5. Modelos de prompt (TI cria/edita; usuário escolhe ao começar)

**Status (02/10/2026):** os 5 itens foram implementados e testados pela Isabelle, com tudo funcionando. Ajustes feitos depois do teste: o painel de gastos mostra só o total e o custo por modelo; a tela de login diz "Bem-vindo(a)" e não tem mais "Esqueci minha senha"; a busca ganhou texto escuro, lupa marrom, o botão "Limpar busca" e esconde a lista enquanto busca; os modelos ficaram centralizados; as respostas da IA feitas com a base levam o aviso "Baseada em uma resposta da base de conhecimento". Próximo passo: as pendências da interface da Etapa 1 (abaixo) e o deploy no Azure.

Regras: um item por vez, parar ao fim de cada um e esperar "ok, próximo". Código novo em módulos separados (`services/`, `admin/`); `streamlit_app.py` só ganha chamadas. Sem commit.

## Decisões gerais

- **Papel TI**: tabela `user_roles` + função `public.is_ti()` no banco. Toda tela/ação de TI checa o papel no servidor (consulta ao banco com o login da pessoa), e as políticas RLS também exigem `is_ti()`: mesmo que a tela falhe, o banco recusa. O papel só é concedido pelo SQL Editor.
- **Chave service_role**: nova variável `SUPABASE_SERVICE_ROLE_KEY`, lida só em `admin/supabase_admin.py`, usada só para a API admin de senha (item 2). Nunca vai para `st.session_state`, cookies, logs ou para o navegador (o Streamlit roda no servidor). Antes de cada uso, o servidor confere `is_ti()` com o login de quem pediu.
- **Painel do TI**: botão "Painel do TI" na barra lateral, visível só para TI; a tela fica em `admin/painel.py` (abas: Gastos, Senhas, Base de conhecimento, Modelos de prompt).

---

## 1. Tokens e custo em R$

**Como funciona**
- **Feito (02/10):** o custo vem de `usage.cost` do OpenRouter, lido por `services/llm_cost_client.CostTrackingLiteLLMClient` (o ADK o descartava); chamadas avulsas anotam o uso com `services.custos.note_usage` dentro de `collect_usage` e quem chamou grava em `ai_usage`.
- Tokens: já somados por tentativa em `agent_runner._accumulate_usage` (entrada, saída, raciocínio) e gravados em `message_attempts` (Etapa 1).
- Custo em US$: o OpenRouter devolve o custo real de cada chamada no campo `usage.cost`. Verificar no início do item se o LiteLLM/ADK repassa esse valor; se não repassar, consultar `GET /api/v1/generation?id=<id>` do OpenRouter ou, como último recurso, a tabela de preços do LiteLLM (marcado como estimado).
- R$: cotação PTAX de venda do Banco Central (API Olinda, pública, sem chave). Fim de semana/feriado: usa a última cotação disponível (volta até 7 dias). Cache em memória por dia (uma consulta por dia por servidor). Reserva: `AGORA_COTACAO_DOLAR_RESERVA` (ex.: `5.40`), marcada como `reserva` no registro.
- Chamadas fora das respostas (título, verificação LGPD, ficha de publicação, embeddings) vão para a tabela nova `ai_usage`, para o total ficar completo.
- Painel "Gastos" (só TI): período (padrão: mês atual), totais em R$ e US$ e uma tabela de custo por modelo. Simplificado a pedido da Isabelle (02/10): as tabelas por dia e por usuário saíram por dificultarem a leitura.

**Arquivos**: novo `services/custos.py` (cotação + conversão), novo `database/ai_usage.py`, novo `admin/painel.py` e `admin/gastos.py`; ajustes em `services/agent_runner.py` (capturar `cost`), `database/message_attempts.py` (gravar R$), `services/title_service.py`, `services/personal_data_check.py`, `services/itinerary_metadata.py` (registrar uso), `streamlit_app.py` (botão do painel).

**Pronto quando**: cada tentativa nova tem tokens, US$, R$, cotação e origem da cotação; o painel mostra os totais por dia/usuário/modelo e só aparece para TI; sem internet para o BC, usa a reserva.

**Teste**
1. Mandar 2 mensagens; no painel, ver o gasto do dia com R$ e US$.
2. Conferir no Supabase (`message_attempts`) os campos `cost_usd`, `cost_brl`, `rate_source = ptax`.
3. Entrar com usuário comum: o botão do painel não aparece.

## 2. Painel do administrador — senhas

**Como funciona**
- Aba "Senhas": lista de usuários (API admin), botão "Definir senha temporária". O servidor confere `is_ti()`, gera uma senha temporária forte (ou aceita uma digitada), chama `auth.admin.update_user_by_id` com a service_role, grava `password_resets` (quem, para quem, quando) e `password_change_required`.
- A senha temporária aparece uma única vez na tela do TI, para ele repassar à pessoa. Nada de e-mail.
- No login (e ao restaurar a sessão), se existir `password_change_required` para a pessoa, ela só vê a tela "Crie sua nova senha" (regras mínimas: 8+ caracteres, diferente da temporária). Ao salvar: `auth.update_user(password)`; só se a troca der certo, o servidor apaga a obrigação com a service_role. A pessoa não tem permissão para apagar a obrigação (decisão da Isabelle: ela só cria a senha nova).
- Histórico de resets na mesma aba.

**Arquivos**: novos `admin/supabase_admin.py` (cliente service_role + checagem de papel), `admin/senhas.py`, `database/roles.py`, `database/password_resets.py`; ajustes em `database/auth.py` e `streamlit_app.py` (tela de troca obrigatória após o login).

**Pronto quando**: TI redefine a senha de alguém sem e-mail; a pessoa entra com a temporária e é obrigada a criar outra; o reset aparece no histórico; usuário comum não vê nem consegue chamar nada disso.

**Teste**
1. Como TI, definir senha temporária para um usuário de teste.
2. Entrar com esse usuário + senha temporária: aparece só a tela de nova senha.
3. Criar a nova senha, sair e entrar com ela.
4. Ver o reset no histórico (quem, para quem, quando).

## 3. Base de conhecimento com aprovação

**Modelo de embeddings (proposta)**
- Recomendado: `openai/text-embedding-3-small` pelo OpenRouter (mesma chave e mesma fatura), com `dimensions=768`. Custo: US$ 0,02 por 1 milhão de tokens. Estimativa: 1.000 perguntas/dia × ~60 tokens ≈ 1,8 milhão de tokens/mês ≈ **US$ 0,04/mês**.
- Reserva: `gemini-embedding-001` (API do Google, 768 dimensões), US$ 0,15 por 1 milhão de tokens (≈ US$ 0,27/mês na mesma estimativa).
- **Confirmado (02/10):** o OpenRouter atende embeddings (0,9 s por pergunta). Ele não devolve o custo; o custo é calculado pelos tokens. O modelo fica em `AGORA_MODELO_EMBEDDING`; trocar exige recalcular os embeddings das entradas aprovadas.

**Como funciona**
- Entrada na base: qualquer pessoa pode **sugerir** uma resposta da própria conversa (botão discreto na resposta concluída). A sugestão passa pela mesma verificação de dados de clientes da memória coletiva (LGPD): com dados de clientes, não é enviada. Entra como `pendente`.
- O TI aprova, edita (pergunta e resposta) ou rejeita na aba "Base de conhecimento". Ao aprovar ou editar, o embedding da pergunta é calculado.
- Antes de chamar a IA (dentro do turno, em `chat_service`): embedding da pergunta → `match_knowledge_entries`.
  - Similaridade ≥ `AGORA_BASE_LIMIAR_DIRETO` (padrão 0,92): responde direto com a resposta aprovada (custo zero de modelo), com o aviso "Resposta da base de conhecimento aprovada pelo TI".
  - Entre `AGORA_BASE_LIMIAR_CONTEXTO` (padrão 0,55; calibrado: mesma dúvida com outras palavras ≈ 0,57, assunto sem relação ≈ 0,30) e o limiar direto: chama a IA com a resposta aprovada como contexto interno.
  - Abaixo: fluxo normal.
  - Falha no embedding ou na busca: segue o fluxo normal (nunca bloqueia a resposta).
- Registro: a resposta direta vira uma tentativa com `model_role = base_conhecimento`, custo 0 e `knowledge_entry_id`; o uso como contexto também guarda o `knowledge_entry_id`.
- Não mexe na memória coletiva de roteiros (são coisas separadas).

**Arquivos**: novos `services/base_conhecimento.py` (embedding, busca, decisão por limiar), `services/embeddings.py`, `database/knowledge_entries.py`, `admin/base_conhecimento.py`; ajustes em `services/chat_service.py` (antes da IA), `services/agent_runner.py` (contexto extra), `streamlit_app.py` (botão "Sugerir para a base").

**Pronto quando**: sugestão só entra após aprovação; pergunta quase igual a uma aprovada é respondida direto; pergunta parecida usa a base como contexto; pergunta diferente segue normal; os limites vêm das variáveis.

**Teste**
1. Sugerir uma resposta; ver como pendente no painel; aprovar.
2. Fazer a mesma pergunta: resposta direta da base, rápida, com o aviso.
3. Fazer uma pergunta parecida, com outras palavras: resposta da IA usando a base.
4. Pergunta sem relação: fluxo normal. Rejeitar uma sugestão: ela nunca é usada.

## 4. Busca de conversas

**Como funciona**
- Colunas `tsvector` geradas automaticamente em `messages.content` e `conversations.title`, configuração `pt_unaccent` (português, sem diferenciar acentos: "sao paulo" acha "São Paulo"), índices GIN.
- Função `search_conversations` (security invoker: o RLS garante que cada pessoa só busca nas próprias conversas; turnos cancelados ficam de fora). Aceita a sintaxe de busca web (`"frase exata"`, `-palavra`).
- Campo "Buscar conversas" na barra lateral; resultados com título e trecho destacado; clique abre a conversa.

**Arquivos**: novo `database/search.py`, novo `services/busca.py` (se precisar de tratamento do texto) e ajuste pequeno em `streamlit_app.py` (barra lateral).

**Pronto quando**: busca acha por palavra no título e no conteúdo, com ou sem acento, só nas conversas da própria pessoa.

**Teste**
1. Buscar uma palavra de uma conversa antiga: ela aparece com o trecho.
2. Buscar sem acento: encontra o texto acentuado.
3. Com outro usuário, buscar a mesma palavra: não aparece nada da primeira pessoa.

## 5. Modelos de prompt

**Como funciona**
- Aba "Modelos de prompt" no painel do TI: criar, editar, ativar/desativar, ordenar (título, descrição, texto).
- Na tela de conversa vazia, a pessoa vê os modelos ativos (cartões ou lista) e, ao escolher, o texto vai para o campo de mensagem para ela completar e enviar (usa o mesmo preenchimento de "Cancelar").

**Arquivos**: novos `database/prompt_templates.py`, `admin/modelos_prompt.py`; ajuste em `streamlit_app.show_empty_conversation`.

**Pronto quando**: o TI cria um modelo e ele aparece para todos; desativado some; usuário comum não consegue criar/editar.

**Teste**
1. Como TI, criar um modelo.
2. Como usuário comum, abrir "Nova conversa", escolher o modelo e ver o texto no campo.
3. Desativar o modelo: some da lista.

---

## Variáveis de ambiente novas

| Variável | Uso |
|---|---|
| `SUPABASE_SERVICE_ROLE_KEY` | só servidor, só `admin/supabase_admin.py` (reset de senha) |
| `AGORA_COTACAO_DOLAR_RESERVA` | cotação usada se a PTAX não responder (ex.: `5.40`) |
| `AGORA_MODELO_EMBEDDING` | padrão `openrouter/openai/text-embedding-3-small` |
| `AGORA_BASE_LIMIAR_DIRETO` / `AGORA_BASE_LIMIAR_CONTEXTO` | padrão 0,92 / 0,55 |

## Pendências da Etapa 1 (voltar antes de liberar para a empresa)

Ver docs/arquitetura.md, "Pendências da interface".
