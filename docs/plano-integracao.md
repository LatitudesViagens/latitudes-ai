# Plano — Integração RD Station CRM + Envision (V1)

Regras de negócio: [regras-integracao-clientes.md](regras-integracao-clientes.md) (seguidas à risca; nenhuma regra muda sem a Isabelle). Branch `migracao-ferramentas-pagas`, sem commit até a aprovação.

## Checklist

- [ ] 0. Plano aprovado + respostas sobre o Envision (seção "Perguntas em aberto") + migração 012 aprovada e aplicada
- [x] 1. Conectores somente leitura (`integracoes/`), com fixtures e testes — 07/10/2026, 19 testes passando (`python -m unittest discover -s tests`)
- [x] 2. Regras de negócio (`services/perfil_cliente.py`), com fixtures e testes — 07/10/2026, 21 testes (40 no total)
  - Interpretações a confirmar: viagens com data futura ficam fora do ticket e aparecem à parte; destino pelo nome do produto (exceto FIT) é copiado como está (ex.: "ARMENIA E GEORGIA MB"); a busca no Envision filtra o resumo das ordens pelo nome do passageiro (desempenho a conferir no nível 2); produto lido do campo `summary` (conferir no nível 3); valores do RD mostrados em R$; sem o CPF no RD, RD e Envision não são unidos e a consultora escolhe.
- [x] 3. Ferramenta do agente (`perfil_cliente`) + registro das consultas — 07/10/2026, 8 testes (48 no total). A ferramenta só anota a consulta no contexto do turno (`services/contexto_turno.py`); o `chat_service` grava em `client_lookups` no fim do turno, na própria thread, mesmo se o turno falhar
- [x] 4. Script de verificação (`scripts/verificar_integracoes.py`, níveis 1 a 4) — 07/10/2026, 5 testes (53 no total); falta a Isabelle rodar com as credenciais reais. Acrescentado ao RD, só leitura: `GET /custom_fields` (nomes dos campos personalizados, para achar o CPF e o destino)

Regras de trabalho: uma camada por vez; ao fim de cada uma, testes com fixtures, resumo e parada até "ok, próximo". **Nenhuma chamada real às APIs no desenvolvimento.** Erros: hipóteses antes de corrigir; parar depois de 2 tentativas.

---

## Decisões gerais

- **Credenciais**: só em variáveis de ambiente (`RD_CRM_API_TOKEN`, `ENVISION_BASE_URL`, `ENVISION_API_KEY`), lidas uma vez em `integracoes/config.py`. Nenhuma credencial em logs, mensagens de erro, exceções ou respostas: os erros mostram só o sistema, o código HTTP e uma mensagem fixa; URLs nunca são impressas (o token do RD vai na URL).
- **Somente leitura em duas travas**:
  1. Cada conector só monta requisições a partir de uma **lista fechada** de (método, caminho) permitidos; qualquer outra combinação levanta `ChamadaBloqueadaError` antes de sair da máquina.
  2. A camada HTTP comum (`integracoes/http.py`) recusa qualquer método diferente de `GET` e qualquer caminho que contenha `/Records` (comparação sem diferenciar maiúsculas), mesmo que um conector peça.
- **Testes sem rede**: os conectores recebem o transporte HTTP por parâmetro; nos testes é um `httpx.MockTransport` que devolve as fixtures (`tests/fixtures/`). Os testes falham se algo tentar sair para a internet.
- **Erros tratados** (mensagens claras em português, sem detalhes técnicos para a consultora): 401/403 → "sem permissão/credencial inválida no <sistema>"; 404 → "não encontrado"; 429 → uma nova tentativa após a espera indicada (máx. 1) e depois "limite de consultas atingido, tente em instantes"; timeout (10 s) → "o <sistema> não respondeu"; demais → "erro no <sistema>". Falha em um sistema não derruba o outro: o perfil sai com o que houver e avisa o que faltou.
- **Extensível para a V2** (sem implementar): cada sistema é um conector com a mesma interface (`Conector` com `nome`, `endpoints_permitidos`, `testar_conexao()`); o perfil junta "fontes" registradas numa lista; o controle de acesso passa por `pode_consultar(usuario, sistema)`, que na V1 sempre libera RD e Envision (regra 5) e na V2 lerá os grupos do painel do TI.
- **Configuração editável sem mexer no código** (`integracoes/mapeamentos.json`): funis do RD → tipo; primeira palavra do produto Envision → tipo (modais do Private: BARCO, TREM, JATO); meses abreviados.

## 1. Conectores (`integracoes/`)

Arquivos: `__init__.py`, `config.py`, `http.py` (requisição GET com timeout, bloqueios e tratamento de erros), `erros.py`, `rd_crm.py`, `envision.py`, `mapeamentos.json`; fixtures em `tests/fixtures/rd_crm/` e `tests/fixtures/envision/`.

**RD Station CRM** (API v1, documentação oficial em developers.rdstation.com):
- Base `https://crm.rdstation.com/api/v1`, token no parâmetro `token` da URL.
- Lista fechada (todos `GET`):
  - `/contacts` com `q` (nome), `email`, `page`, `limit` — busca do cliente; o contato traz `emails`, `contact_custom_fields` e `deals` (id, nome, `win`, `closed_at`).
  - `/contacts/{id}`
  - `/deals/{id}` — funil (`deal_pipeline`), etapa (`deal_stage`), responsável (`user`), `win`, `closed_at`, `amount_total`, `created_at`, `prediction_date`, `deal_custom_fields`.
  - `/deal_pipelines` — nomes dos funis (nível 3 do script).
- Status da negociação: `win = true` ganha, `false` perdida, `null` em aberto (documentação oficial).
- Paginação com `page`/`limit` (máx. 200) e `has_more`.

**Envision** (EnvisionAPI v1, Swagger público em `https://api.travelagent.com.br/swagger/docs/v1`, lido em 07/10/2026 sem credenciais):
- Autenticação: esquema `apiKey` no cabeçalho `Authorization` (há também OAuth2 por senha, que não vamos usar). Formato exato do valor a confirmar (pergunta A).
- **As ordens de serviço (viagens) ficam dentro de `/Records`.** Endpoints de leitura relevantes: `POST /Records/Query` (consulta; o filtro vai num texto `criteria`), `GET /Records/{id}` e `GET /Records/GetServiceOrderSummaries`. Os de escrita na mesma área: `POST /Records` (cria — o usado pelo formulário), `PATCH /Records/{id}`, `/Records/Pay`, `/ProcessPayments`, `/ChangeStatus`, `/SaveMessage`, `/ExecuteActionRequired`. Ver pergunta B.
- Campos da ordem de serviço (`ServiceOrder`): `status` (`code`, `name` — "Emitido"/"Cancelado"), `totalValue` (`value`, `currencyCode`), `startDate`/`endDate`, `createdTime`, `travellers` (lista de viajantes: `fullName`, `documents`, `birthDate`, `address`…), `summary`, `identification`, `companyContext.customer` (`name`), `responsibleName`, `customFields`.
- Não há campo de destino estruturado documentado; o `ServiceOrderSummary` tem `route`/`fullRoute`.
- Status: "Emitido" = viagem realizada; "Cancelado" = fora do ticket e da contagem (confirmado pela Isabelle).
- `/Records` continua bloqueado por padrão; só entram na lista os endpoints de leitura acima, se a Isabelle aprovar (pergunta B).

**Pronto quando**: com fixtures, cada endpoint permitido funciona; qualquer método/caminho fora da lista, e `/Records`, é bloqueado sem tocar a rede; 401, 403, 404, 429 e timeout geram as mensagens previstas; nenhuma credencial aparece em erro ou log (teste procura o token fictício em toda a saída).

## 2. Regras de negócio (`services/perfil_cliente.py`)

Tudo calculado em código, seguindo o documento de regras:
- **Identificação** (regra 1): normaliza CPF (só dígitos) e e-mail (minúsculas, sem espaços); cruza por CPF e, sem CPF, por e-mail. Busca pelo nome em cada sistema; mais de um cliente → devolve a lista de candidatos (nome e e-mail) e a ferramenta pede a escolha; nunca escolhe sozinha. Cliente em um só sistema → avisa. O CPF fica só na memória do cálculo e nunca sai da função.
- **Fontes** (regra 2): viagens, valores e ticket médio só do Envision; negociações, funis, etapas e responsáveis só do RD.
- **Ticket e categoria** (regra 3): ticket = soma do valor total das viagens ÷ número de viagens; canceladas fora; Single (1 pessoa) / Double (mais de 1); período padrão de 12 meses pela data da viagem (ou da ordem de serviço); histórico completo só se pedido; moedas diferentes informadas separadamente; resumo sempre com período e quantidade de viagens.
- **Tipos** (regra 4): Envision pela primeira palavra do produto (maiúsculas, sem acento) com GRP/FIT/modais do Private; o resto é "Não classificado". Mês/ano das duas últimas palavras. Destino do campo estruturado, se existir; senão, do nome do produto (no FIT, sem o nome do cliente — ver pergunta 6). RD pelo nome do funil, com a tabela do documento; funil não listado → "Outros".
- **Lista de campos permitidos** (regra 6): o perfil é montado a partir de um modelo com SÓ os campos permitidos (nome e e-mail do cliente; negociações: funil, etapa, status, responsável, datas, destino, valor; viagens: produto, tipo, destino, datas, valor, número de pessoas, categoria). Nada fora disso chega à IA — inclusive anotações (`notes`), telefones, aniversário, campos personalizados e documentos. O filtro é feito por inclusão (copia só o permitido), não por exclusão.
- **Ausentes e conflitos** (regra 7): campo vazio vira "não informado no <sistema>"; quando RD e Envision discordarem (ex.: nome ou e-mail diferentes), o resumo cita o que cada um diz; nada é estimado.

**Testes com fixtures**: os três nomes de produto reais do documento; produto "Não classificado"; homônimos (dois "João Silva"); cliente só no RD e só no Envision; cruzamento por CPF e por e-mail; CPF/e-mail com formatação diferente; dados ausentes; viagens canceladas fora do ticket; single/double; moedas diferentes; viagens fora dos 12 meses e histórico completo; funil não listado; e uma verificação de que CPF, telefone, endereço, nascimento e `notes` nunca aparecem no resultado.

## 3. Ferramenta do agente

- Função `perfil_cliente(cliente: str, historico_completo: bool = False, email_escolhido: str | None = None)` em `agent_tools/perfil_cliente.py`, registrada no `root_agent` e no `fallback_agent`.
- Devolve um texto-resumo pronto (com fontes e período) ou a lista de candidatos para a consultora escolher. As contas já vêm feitas; o `instruction` do agente ganha uma regra curta: "use os números da ferramenta como estão, não recalcule nem complete dados; cite as fontes e o período; se vierem candidatos, peça para a consultora escolher".
- Quem está perguntando: `agent_runner` publica numa `ContextVar` o usuário, a conversa e a conexão do turno (mesmo padrão de `GENERATED_FILES`), usada pela ferramenta para o registro e, na V2, para as permissões.
- **Registro de cada consulta** (regra de segurança): tabela nova `client_lookups` — quem perguntou (`user_id`), conversa, texto buscado, cliente identificado (nome e e-mail, que estão na lista permitida; nunca CPF), sistemas consultados, resultado (encontrado, candidatos, não encontrado, erro) e quando. Só o TI lê.
- Custo: a ferramenta não usa IA; as chamadas às APIs não têm custo de modelo.

**Pedidos fora da lista permitida (sugestão da Isabelle, 07/10/2026)**: quando a consultora pedir um dado que a ÁGORA não pode mostrar (ex.: formulário do viajante, histórico de saúde, documentos, endereço, telefone, pagamentos, anotações), a ÁGORA responde com um aviso padrão e indica o sistema responsável, sem buscar nem tentar deduzir o dado:

> "Esse nível de informação não está autorizado pela ÁGORA. Para consultar <dado>, acesse o <sistema responsável>."

- O texto e a tabela "tipo de dado → sistema responsável" ficam em `integracoes/mapeamentos.json` (editável sem mexer no código). Confirmado pela Isabelle (07/10/2026): formulário do viajante, saúde, documentos, endereço, telefone, data de nascimento e anotações → RD Station CRM (mais completo); pagamentos → Envision. Informações das viagens concluídas continuam vindo do Envision, como na regra 2.
- Vale também quando o pedido vier junto com uma consulta permitida: a ÁGORA responde a parte permitida e avisa sobre o resto.
- Implementação: a ferramenta `perfil_cliente` devolve, junto do resumo, a lista de dados que não podem ser mostrados com o sistema responsável; o `instruction` do agente ganha a regra de usar o aviso padrão para qualquer pedido fora da lista. Teste com fixtures: pedidos de saúde, formulário, CPF e endereço recebem o aviso e nenhum valor desses campos aparece.

**Migração 012** (arquivo `database/migrations/012_client_lookups.sql`):
```sql
create table if not exists public.client_lookups (
    id uuid primary key default gen_random_uuid(),
    user_id uuid default auth.uid() references auth.users(id) on delete set null,
    conversation_id uuid references public.conversations(id) on delete set null,
    search_text text not null,
    client_name text,
    client_email text,
    systems text[] not null default '{}',
    outcome text not null check (outcome in ('encontrado', 'candidatos', 'nao_encontrado', 'erro')),
    created_at timestamptz not null default now()
);
-- RLS: insert só com user_id = auth.uid(); select só para o TI (is_ti()).
```

## 4. Script de verificação (`scripts/verificar_integracoes.py`)

Executado **somente pela Isabelle**, com as credenciais reais (`python scripts/verificar_integracoes.py --nivel N`):
- **Nível 1**: testa a conexão com cada sistema → "conectado" ou o erro (sem credenciais).
- **Nível 2**: busca poucos registros e mostra só os **nomes e tipos** dos campos, nunca valores.
- **Nível 3**: lista os funis do RD e os nomes de produtos do Envision com a classificação de cada um, destacando os "Não classificados".
- **Nível 4** (`--email`): gera o perfil de um cliente para comparar com as telas dos sistemas.
- Usa os mesmos conectores (com as travas de leitura) e o `truststore` (Kaspersky).

## Privacidade (LGPD) — pontos para a Isabelle saber

- O resumo do cliente (só os campos permitidos) é enviado ao modelo de IA pelo OpenRouter para escrever a resposta, e a resposta fica salva na conversa da consultora (privada).
- Respostas com dados de clientes já são bloqueadas na memória coletiva e na sugestão para a base de conhecimento pela verificação existente.

## Situação em 08/10/2026 (parar aqui; continuar amanhã)

- **RD Station CRM**: funcionando (níveis 1 a 3). CPF no campo personalizado `655e4f48af0d72000d50799a` (configurado). Funis restantes classificados pela Isabelle (08/10/2026): Perdas, Vendas | Incoming, Latitudes Preview 2027 e Funil | Novo → Outros; Perdas | Aéreo → Aéreo; Formulários não aparece no perfil (é só quem preencheu o formulário, não uma venda).
- **Envision**: login OAuth2 com o perfil da ÁGORA funcionando (`ENVISION_USUARIO`/`ENVISION_SENHA`, formato `senha`). `/Records/Query` filtra por data (`Created >= '...' && Created <= '...'`). Estrutura descoberta: a venda é o registro `ServiceOrder` (valor total, viajantes, status "Fechada"); o produto ("GRP JAPAO LF MAI 27") está no `ItineraryBook` filho; destino e serviços nos `ServiceBook` filhos (`destination.name`); `Person` = cadastro. `GetServiceOrderSummaries` volta vazio para esse perfil.
- **Bloqueio**: falta o nome do campo do `criteria` para filtrar por passageiro/cliente e por tipo de registro. Testados sem sucesso (0 registros, sem erro): `Type`, `RecordType`, `TypeName`, `RecordTypeName` = 'ServiceOrder'; `PassengerEmail`, `PassengersList.PassengerEmail`, `Passenger`, `TravellerEmail`, `Travellers.Email`, `Email`, `ContactInformations.Email`; `Person.FullName`, `FullName`. `.Contains(...)` dá erro. Até `ServiceTypeName = 'Issue'` (exemplo do Envision) voltou 0. O site do Envision busca com outro serviço (`SiteService.svc/AdvancedSearchServiceItems`, filtro `Person.FullName(Person):nome;`), que não serve para a API.
- **Pergunta enviada ao programador** (que usou a API no formulário de viajantes): exemplo real de `criteria` que filtre pelo cliente/passageiro.
- **08/10/2026 — resposta do programador**: `criteria: externalId = "XXXXXXXXXXX"` (campo em minúsculas, valor entre aspas duplas) com `additionalInfo: {travelAgencyId, systemAccountId}`. Nível 6: o `externalId` com CPF (só números) existe apenas nos registros `Person`; vendas e serviços guardam o CPF em `travellers[].documents` (com ou sem pontuação). Nível 5 com controle positivo (CPF de um `Person` recente, que com certeza existe): **0 registros**, com e sem `additionalInfo` (valores tirados dos próprios registros), com e sem pontuação. Conclusão: o filtro não funciona para o perfil da ÁGORA. Pergunta enviada: com qual usuário ele consulta e quais valores usa em `travelAgencyId`/`systemAccountId`. Resposta (08/10): usuário próprio (ENVISION_USERNAME/PASSWORD), agência 160779, conta 38144, "contexto validado no GetSession", ENVISION_RECORD_TYPE=Person. Implementado: aceitar esses nomes, additionalInfo do .env e `POST /Authorization/GetSession` após o login. Resultado: login e sessão OK; `externalId = "CPF"` de um Person existente continua 0. Nova pergunta: o código completo da busca do formulário (do login ao Records/Query). Depois: o programador corrigiu `envision.py`/`http.py`; incorporados additionalInfo como texto, timeout de 30 s e o motivo das recusas no log (sem CPF/e-mail), e liberada a leitura `POST /Shopping/QueryTravellers` (fora do Swagger, usada por ele). Resultados com a lógica dele: `externalId` 0, `GetServiceOrderSummaries` 0 ordens, `QueryTravellers` 0 viajantes; consulta por data segue com 20 registros. A função `historico_cliente` dele NÃO foi incorporada (mostra pagamentos e acompanhantes, contra a regra 6). Próximo passo: o programador rodar a consulta dele com um CPF/nome real e comparar com o nível 7, de preferência em tela compartilhada.
- **08/10/2026 — caminho que funciona (nível 9 e 10)**: o campo precisa de inicial maiúscula (`ExternalId = "CPF"`; `externalId` é ignorado). O `Person` achado pelo CPF tem `associations` com `recordType: "ServiceOrder"`, e `GET /Records/{id}` traz a venda completa (status, valor, viajantes, data, `ItineraryBook` e `ServiceBook`). Nenhum campo do `criteria` acha vendas pelo viajante (nível 11, 12 candidatos com controle positivo). Decisão da Isabelle: usar só esse caminho, sem contatar o Envision.
- **Implementado na busca** (`BuscadorPerfil` + `Envision.vendas_por_cpf`): a consultora pede pelo nome/e-mail → RD acha o contato e o CPF (só em memória) → Envision acha o `Person` pelo CPF → vendas. Com homônimos, o Envision só é consultado depois da escolha. O conector confere o CPF de volta em cada `Person` (se o filtro fosse ignorado, não traria outra pessoa). Produto pelo `ItineraryBook`; destino do itinerário, da venda ou o mais comum entre os serviços; reserva pelo nome do produto.
- **Limitação aceita**: só tem viagens consultáveis quem preencheu o formulário de viajantes (tem `Person`). Sem CPF no RD, sem cadastro no Envision ou com o Envision fora do ar, a resposta diz que as viagens **não foram consultadas** e o motivo — nunca "não encontrado" nem ticket zerado. Cliente que está só no Envision não é achado (a busca começa pelo RD).
- **Data da viagem**: o Envision registra a data da venda, não a da viagem (Isabelle, 08/10/2026); no nível 10, `startDate`/`endDate` da venda, do `ItineraryBook` e dos serviços vieram todos com o dia da consulta. A data da viagem passa a ser o mês/ano do nome do produto ("MAI 27" → 05/2027, dia 1); sem mês/ano, a data da venda (`createdTime`). `startDate` não é mais usado.
- Primeiro teste real (nível 4, um casal da mesma venda): cruzamento, tipo, destino, valor e Double corretos; a data errada levou à correção acima.
- Nada commitado desta etapa.

## Perguntas em aberto

Respondidas: `/Records` de cadastro não é usado; OS "Emitido" = realizada, "Cancelado" = fora da conta; todos os funis do RD são lidos (Vendas e Pós-venda de Grupo, Private, FIT e Aéreo, além dos demais, que viram "Outros").

- ~~A~~ (resolvida: o nível 1 do script testa a chave pura e com `Bearer`; a forma que conectar vai para `ENVISION_AUTH_FORMATO`). **A. Formato da chave do Envision** no cabeçalho `Authorization`: a chave pura, `Bearer <chave>` ou outro prefixo? (quem montou o formulário de viajantes sabe; o nível 1 do script também pode testar as duas formas sem mostrar a chave).
- ~~B~~ (aprovada em 07/10/2026). **B. Liberar a leitura dentro de `/Records`** (regra de segurança do documento de regras): as viagens só existem ali. Proposta: liberar SOMENTE `POST /Records/Query`, `GET /Records/{id}` e `GET /Records/GetServiceOrderSummaries`, e manter bloqueado todo o resto de `/Records` (criar, alterar, pagar, mudar status, mensagens). Para isso, a trava "só GET" passa a aceitar o `POST /Records/Query`, que é uma consulta.
- **C. Sintaxe do `criteria`** do `/Records/Query` (o Swagger não documenta): um exemplo de consulta por nome do viajante ou CPF. Alternativa: o nível 2 do script testa formatos sem mostrar valores.
- **D. Pós-venda Aéreo** → Aéreo? (o documento de regras só lista Vendas | Aéreo)
- **E. Destino no FIT**: sem campo estruturado, mostrar "destino não informado" em vez de extrair do nome do produto?
- **F. RD**: CPF e destino da negociação ficam em campos personalizados? (o nível 2 mostra só os nomes dos campos)
