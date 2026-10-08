# Regras de negócio: integração RD Station CRM + Envision (V1)

Documento de referência para o Claude Code. As regras aqui são decisões da Latitudes e não devem ser alteradas sem aprovação.

## Escopo

**V1 (agora):** RD Station CRM e Envision (Travelagent).

**V2 (depois):** Stur (acesso só do financeiro), RD Station Marketing (acesso só do marketing) e grupos de acesso gerenciados no painel do TI. Nada da V2 deve ser implementado agora, mas o código deve permitir adicionar novos sistemas e grupos sem reescrever o que existe.

## Segurança

- Acesso **somente leitura**. Os conectores só fazem consultas, por uma lista fechada de endpoints e métodos permitidos. Qualquer chamada fora da lista é bloqueada no código, mesmo que a credencial permita escrita.
- A chave da API do Envision é a mesma usada pelo formulário de viajantes e **tem permissão de escrita**. Por isso o bloqueio no código é obrigatório, incluindo o endpoint `/Records`. Não fazer testes de escrita no ambiente real.
  - Decisão da Isabelle (07/10/2026): as ordens de serviço só existem dentro de `/Records`, então ficam liberadas **somente** as consultas `POST /Records/Query`, `GET /Records/{id}` e `GET /Records/GetServiceOrderSummaries`. Todo o resto de `/Records` (criar — usado pelo formulário —, alterar, pagar, mudar status, mensagens) continua bloqueado no código.
- Credenciais apenas em variáveis de ambiente (`RD_CRM_API_TOKEN`, `ENVISION_BASE_URL`, `ENVISION_API_KEY`). Nunca imprimir credenciais em logs, erros ou respostas.
  - Também em 08/10/2026: depois do login, `POST /Authorization/GetSession` (abre a sessão no contexto da agência/conta, sem gravar dados), como faz o formulário de viajantes; os demais `/Authorization` (trocar/resetar senha) seguem bloqueados. Contexto em `ENVISION_TRAVEL_AGENCY_ID` e `ENVISION_SYSTEM_ACCOUNT_ID`; usuário e senha também aceitos como `ENVISION_USERNAME`/`ENVISION_PASSWORD`.
  - Acrescentadas em 08/10/2026 (aprovadas pela Isabelle): `ENVISION_USUARIO` e `ENVISION_SENHA`, do perfil da ÁGORA no Envision. A API do Envision exige login OAuth2 (`POST /token`, só autenticação) em todas as consultas.
- Desenvolvimento e testes com **respostas fictícias** no formato das APIs. Nenhum dado real de cliente no desenvolvimento.
- Toda consulta é registrada: quem perguntou, sobre qual cliente e quando.

## 1. Identificação do cliente nos dois sistemas

- Chave principal: **CPF**. Se não houver CPF, usar **e-mail**. O CPF é usado só internamente para o cruzamento e nunca aparece na resposta.
- Normalizar antes de comparar: CPF só com dígitos; e-mail em minúsculas e sem espaços.
- A consultora pergunta pelo nome. Se a busca encontrar mais de um cliente, a ÁGORA lista os candidatos (nome e e-mail) e pede para a consultora escolher. Nunca escolher sozinha.
- Se o cliente existir em um sistema e não no outro, informar isso na resposta.

## 2. Fonte de cada informação

| Informação | Fonte |
| --- | --- |
| Viagens realizadas, valores e ticket médio | Envision |
| Interesses e negociações (abertas, ganhas, perdidas), funis, etapas, responsáveis | RD Station CRM |

Uma viagem é considerada realizada quando está lançada no Envision. Toda negociação fechada vira uma viagem no Envision, por isso valores e contagem de viagens vêm **somente** do Envision.

## 3. Ticket médio e categoria da viagem

- Calculado **em código**, nunca pela IA, somente com dados do Envision.
- **Ticket médio = soma do valor total das viagens ÷ número de viagens** (valor por viagem, não por pessoa).
- Viagens canceladas não entram no cálculo.
- Categoria de cada viagem pelo número de pessoas cadastradas: **Single** (uma pessoa) ou **Double** (mais de uma).
- A resposta mostra a divisão. Exemplo: "João viajou 3 vezes acompanhado e 1 vez sozinho nos últimos 12 meses; ticket médio de R$ X por viagem".
- Período padrão: **últimos 12 meses**, pela data da viagem (ou da ordem de serviço, se não houver data da viagem). Histórico completo apenas se a consultora pedir.
- A resposta sempre informa o período e a quantidade de viagens usadas no cálculo.
- Se houver valores em moedas diferentes, não somar: informar separadamente.

## 4. Classificação do tipo de viagem

### Envision: pelo nome do produto

Padrão: `TIPO DESTINO MÊS ANO`, em maiúsculas. Exemplos reais:

- `GRP ARMENIA E GEORGIA MB SET 27`
- `BARCO MEDITERRANEO MAI 27`
- `FIT ADRIANA PEDRANZINI JAPAO SET 25`

| Primeira palavra | Tipo |
| --- | --- |
| `GRP` | Grupo |
| `FIT` | FIT |
| `BARCO`, `TREM` ou `JATO` | Private |
| Qualquer outra | Não classificado (informar, nunca adivinhar) |

- Normalizar antes de comparar: maiúsculas e sem acentos. Lista de modais do Private em configuração.
- Decisão da Isabelle (07/10/2026): BARCO, TREM e JATO são as **Private Expeditions**; o tipo aparece como "Private Expeditions" (também para os funis Private do RD, para os nomes baterem).
- Mês e ano: as duas últimas palavras do nome (meses abreviados em português: JAN, FEV, MAR, ABR, MAI, JUN, JUL, AGO, SET, OUT, NOV, DEZ; ano com dois dígitos).
- Destino: preferir o campo estruturado do Envision, se existir. O nome do produto só deve ser usado como reserva, lembrando que no FIT ele inclui o nome do cliente.

### RD Station CRM: pelo funil

| Funil | Tipo |
| --- | --- |
| Vendas \| Grupo · Pós-Venda (Grupo) | Grupo |
| Vendas \| Private · Pós-Venda (Private) | Private |
| Vendas \| FIT · Pós-Venda (FIT) | FIT |
| Vendas \| Aéreo | Aéreo |
| Entrada de leads (newsletter) · Atendimento - Telefone & E-mail · Negociações 2025 | Outros (não é tipo de viagem) |
| Funil não listado | Outros |

Mapeamento mantido em configuração, para incluir novos funis sem alterar código.

## 5. Permissões (V1)

- Todos os usuários da ÁGORA podem consultar RD CRM e Envision.
- Grupos de acesso por departamento ficam para a V2.

## 6. Dados que podem aparecer na resposta

Lista **permitida** (tudo o que não estiver aqui fica fora da resposta):

- Nome do cliente e e-mail.
- Negociações: funil, etapa, status, responsável, datas, destino e valor.
- Viagens: produto, tipo, destino, datas, valor, número de pessoas e categoria (single ou double).

Nunca aparecem na V1: CPF, documentos, endereço, telefone, data de nascimento, dados de pagamento, demais dados da ficha cadastral e anotações livres das negociações (podem conter dados pessoais).

Pedidos fora desta lista (decisão da Isabelle, 07/10/2026): a ÁGORA não busca o dado e responde com um aviso padrão — "Esse nível de informação não está autorizado pela ÁGORA. Para consultar <dado>, acesse o <sistema responsável>" —, com o sistema responsável de cada tipo de dado em configuração.

## 7. Dados ausentes ou conflitantes

- A ÁGORA informa claramente quando faltar dado ou quando os sistemas discordarem, citando o que cada sistema diz.
- Nunca completar ou estimar informação que não veio dos sistemas.
- Cadastro duplicado no RD (decisão da Isabelle, 08/10/2026): o e-mail é a única chave que não se repete (é a chave do formulário de viajantes); o telefone pode se repetir. Contatos com exatamente o mesmo nome e o mesmo telefone geram só um aviso de "possível cadastro duplicado", com o e-mail do outro contato; a ÁGORA nunca junta os dois perfis. O telefone é usado só nessa comparação e nunca aparece.
- Viagens não consultáveis: sem CPF no RD, sem cadastro do formulário de viajantes no Envision ou com o Envision fora do ar, a ÁGORA diz que as viagens não foram consultadas e o motivo, nunca "não encontrado" (08/10/2026).
- Data da viagem: o Envision registra a data da venda, não a da viagem; a data da viagem é o mês/ano do nome do produto e, sem ele, vale a data da venda (08/10/2026).
- Funil Formulários do RD não aparece no perfil: não é negociação de venda (08/10/2026).

## Pendências

- [ ] Avaliar com o Envision uma chave de API separada para a ÁGORA, somente leitura
- [x] Conferir se há outros funis no RD além dos mapeados (todos classificados em 08/10/2026)
- [ ] Formulário de viajantes cria um contato novo no RD em vez de atualizar o existente (gera duplicados; corrigir na origem)
