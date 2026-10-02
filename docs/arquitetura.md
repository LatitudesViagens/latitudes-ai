# Documento técnico da ÁGORA

Arquitetura, decisões, implementação e plano de migração

| **Campo**            | **Definição**                                          |
|----------------------|--------------------------------------------------------|
| Projeto              | ÁGORA — assistente corporativa da Latitudes            |
| Estado               | Piloto funcional em transição para infraestrutura paga |
| Arquitetura-alvo     | Supabase pago, máquina virtual no Azure e OpenRouter   |
| Data de consolidação | 28 de setembro de 2026                                 |

# Resumo executivo

A ÁGORA é uma assistente corporativa experimental para apoiar colaboradores da Latitudes em pesquisas, organização de informações, análise de anexos e criação de roteiros de viagem. O piloto já valida autenticação própria, conversas privadas, memória coletiva controlada, pesquisa na internet e análise de arquivos. A implantação atual na Vercel permitiu testes rápidos, mas expôs incompatibilidades entre aplicações Streamlit persistentes e uma execução orientada a funções efêmeras. Por isso, a arquitetura-alvo passa a usar uma VM Linux no Azure para manter o contêiner da aplicação ativo, Supabase pago para dados, autenticação e arquivos, e OpenRouter como camada de acesso a múltiplos modelos de IA.

A prioridade da próxima etapa é estabilizar sessão, upload e exibição de imagens, substituir a integração direta com modelos Gemini pelo OpenRouter, preparar observabilidade e segurança e executar uma migração controlada para o Azure. O banco continuará no Supabase, evitando uma migração de dados desnecessária neste momento.

# 1. Objetivo do agente

A ÁGORA foi concebida como uma interface corporativa única para tarefas de pesquisa e apoio ao planejamento de viagens. Ela não representa uma fonte oficial da Latitudes e, enquanto não houver integração com bases corporativas homologadas, seus resultados devem ser apresentados como sugestões preliminares geradas por IA.

- Responder em português do Brasil com linguagem clara, cordial e profissional.

- Criar roteiros personalizados após coletar destino, duração, perfil dos viajantes, interesses e orçamento.

- Pesquisar informações atuais quando a resposta depender de vistos, alertas, preços, horários, eventos, transportes ou funcionamento de serviços.

- Exibir as fontes efetivamente consultadas e declarar quando uma informação não puder ser verificada.

- Analisar imagens, PDFs, arquivos Word, planilhas, CSV e texto enviados pelo usuário.

- Preservar a privacidade das conversas e reutilizar apenas conteúdo autorizado, como histórico do próprio usuário e roteiros publicados voluntariamente.

- Usar data e hora fornecidas internamente pela aplicação para interpretar hoje, amanhã e expressões equivalentes sem perguntar isso ao usuário.

# 2. Arquitetura definida

## 2.1 Arquitetura atual do piloto

| **Camada**         | **Tecnologia atual**                                   | **Responsabilidade**                                                                        |
|--------------------|--------------------------------------------------------|---------------------------------------------------------------------------------------------|
| Interface          | Streamlit                                              | Login, conversas, anexos, histórico, publicação de roteiros e renderização das respostas.   |
| Hospedagem         | Vercel com contêiner Docker                            | Ambiente público usado para validar o piloto e coletar problemas reais de uso.              |
| Agente             | Google ADK                                             | Orquestração da chamada ao modelo, ferramentas e contexto de conversa.                      |
| Modelos            | Gemini 3.1 Flash Lite e fallback Gemini 3.5 Flash Lite | Resposta principal e tentativa alternativa diante de timeout ou indisponibilidade.          |
| Pesquisa web       | Tavily                                                 | Busca de informações atuais e retorno de fontes.                                            |
| Dados e identidade | Supabase                                               | Autenticação por e-mail e senha, banco PostgreSQL, políticas RLS e armazenamento de anexos. |
| Empacotamento      | Docker                                                 | Imagem reproduzível da aplicação e dependências Python.                                     |
| Código             | Git e GitHub                                           | Versionamento e integração com o deploy.                                                    |

## 2.2 Arquitetura-alvo paga

A arquitetura-alvo mantém a aplicação monolítica durante a próxima fase para reduzir o custo de aprendizado e o risco de migração. O contêiner Streamlit será executado em uma VM Linux no Azure, com acesso HTTPS por domínio corporativo. O Supabase pago continuará como serviço gerenciado de dados e identidade. O OpenRouter substituirá o acoplamento direto a um único provedor de modelo.

| **Componente**          | **Decisão-alvo**                      | **Observação**                                                                                                               |
|-------------------------|---------------------------------------|------------------------------------------------------------------------------------------------------------------------------|
| Computação              | VM Linux no Azure                     | Executar um único contêiner da ÁGORA inicialmente, com reinício automático e armazenamento operacional controlado.           |
| Entrada web             | Domínio, DNS, HTTPS e proxy reverso   | A equipe de infraestrutura deverá configurar o subdomínio, certificado TLS, regras de rede e publicação segura.              |
| Banco, login e arquivos | Supabase pago                         | Manter PostgreSQL, Auth, Storage e RLS; revisar limites, backups, recuperação e região.                                      |
| Hub de modelos          | OpenRouter                            | Centralizar créditos e acesso a modelos; a aplicação definirá modelo principal, fallback, limites e critérios de roteamento. |
| Pesquisa                | Tavily inicialmente                   | Manter a ferramenta até avaliar alternativa especializada em turismo e regras de fontes confiáveis.                          |
| Observabilidade         | Azure Monitor ou solução equivalente  | Registrar disponibilidade, erros, latência, consumo por modelo e falhas de ferramentas sem expor conteúdo sensível.          |
| Segredos                | Azure Key Vault ou mecanismo aprovado | Retirar chaves do código e fornecer segredos ao contêiner por identidade ou variáveis protegidas.                            |

## 2.3 Fluxo funcional previsto

**1.** O usuário acessa o domínio da ÁGORA e autentica-se com e-mail e senha na própria interface.

**2.** A aplicação valida a sessão pelo Supabase Auth e carrega somente as conversas permitidas pelas políticas de acesso.

**3.** Ao receber uma pergunta, a aplicação inclui histórico autorizado, data e hora de São Paulo e metadados necessários dos anexos.

**4.** Antes de consumir um modelo, a aplicação verifica se a resposta pode vir do histórico do próprio usuário, de um roteiro publicado ou de uma base de conhecimento aprovada.

**5.** Quando IA generativa for necessária, o roteador escolhe um modelo disponível no OpenRouter conforme tipo de tarefa, custo, capacidade multimodal e limite de tempo.

**6.** Quando houver informação atual, a ferramenta de pesquisa consulta fontes externas e a resposta apresenta os links realmente utilizados.

**7.** Mensagens, anexos e metadados permitidos são persistidos no Supabase; métricas operacionais seguem para a camada de observabilidade.

# 3. Stack técnica

| **Categoria**          | **Tecnologia**          | **Situação**                                                      |
|------------------------|-------------------------|-------------------------------------------------------------------|
| Linguagem              | Python 3.13             | Implementado no contêiner atual                                   |
| Interface              | Streamlit 1.62          | Implementado                                                      |
| Orquestração de agente | Google ADK 2.7.1        | Implementado; modelos acessados via LiteLLM (`LiteLlm`)            |
| LLM                    | OpenRouter              | Implementado em 01/10/2026 (ver seção 12)                         |
| Pesquisa web           | Tavily                  | Implementado, com instabilidades observadas na Vercel             |
| Persistência           | Supabase PostgreSQL     | Implementado; migração para plano pago prevista                   |
| Autenticação           | Supabase Auth           | Implementado com login e senha dentro da ÁGORA                    |
| Arquivos               | Supabase Storage        | Implementado; fluxo de upload e exibição precisa de estabilização |
| Cloud atual            | Vercel                  | Piloto temporário                                                 |
| Cloud futura           | Azure VM                | Arquitetura-alvo                                                  |
| Entrega                | Docker, GitHub e CLI    | Implementado no piloto                                            |

# 4. Decisões tomadas

| **Tema**         | **Decisão**                                                                                                                                                 |
|------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Identidade       | Manter login e senha dentro da ÁGORA usando Supabase Auth. Não depender, por enquanto, de Microsoft 365 ou login externo administrado por terceiros.        |
| Banco            | Manter o Supabase e contratar o plano pago. Não migrar o PostgreSQL para o Azure nesta fase.                                                                |
| Cloud            | Migrar a aplicação da Vercel para uma VM no Azure após a aprovação das ferramentas pagas.                                                                   |
| Modelos          | Usar OpenRouter como hub de acesso a modelos, com créditos centralizados, fallback e políticas de custo.                                                    |
| Economia         | Consultar primeiro informações reutilizáveis autorizadas no banco antes de chamar um modelo, sem acessar conversas privadas de outros usuários.             |
| Memória coletiva | Somente a versão final publicada voluntariamente pode ser reutilizada. A conversa original e a identidade do autor permanecem privadas.                     |
| Fontes           | Validar informações atuais na internet e priorizar fontes oficiais e confiáveis do setor de viagens. Toda fonte exibida deve ter sido realmente consultada. |
| Implantação      | Manter uma arquitetura simples e reversível nesta fase; evitar uma decomposição prematura em muitos serviços.                                               |
| Sessão na Vercel | Aceitar a solução provisória de sessão/cookies apenas durante o piloto, pois a persistência completa será tratada na arquitetura Azure.                     |

# 5. O que já foi implementado

| **Área**              | **Entrega**                                                                                                                                                  |
|-----------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Interface e marca     | Nome ÁGORA, identidade visual, layout de conversas e avatar com a logomarca.                                                                                 |
| Login                 | Autenticação com e-mail e senha e separação das conversas por usuário.                                                                                       |
| Conversas             | Criação, listagem, seleção, histórico, título automático e controle de processamento para evitar envios concorrentes.                                        |
| Privacidade           | Conversas privadas por padrão e políticas de acesso no Supabase.                                                                                             |
| Memória coletiva      | Publicação de roteiros, consulta de roteiros compartilhados e opção de continuar ou criar do zero.                                                           |
| Anexos                | Banco e Storage para imagens, PDF, texto, CSV, Word e Excel; análise multimodal validada localmente e em testes pontuais na Vercel.                          |
| Pesquisa web          | Ferramenta Tavily integrada ao agente e exibição de fontes consultadas.                                                                                      |
| Resiliência do modelo | Timeout por tentativa, modelo de fallback e mensagens de erro que liberam a conversa para nova tentativa.                                                    |
| Desempenho            | Streaming desativado após testes mostrarem travamento; respostas simples locais chegaram a cerca de 1 a 9 segundos, enquanto fluxos completos variaram mais. |
| Contexto temporal     | Data e hora de São Paulo injetadas internamente para evitar que o agente pergunte a data ao usuário.                                                         |
| Deploy                | Dockerfile, arquivos de exclusão, variáveis de ambiente e deploy produtivo na Vercel.                                                                        |
| Banco                 | Migrações para visibilidade de conversas, roteiros compartilhados, anexos de mensagens, bucket privado e políticas de segurança.                             |

# 6. Problemas conhecidos e riscos

| **Problema**               | **Evidência observada**                                                              | **Tratamento**                                                                                                                                    |
|----------------------------|--------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------|
| Sessão não persistente     | Após recarregar ou enviar anexo, alguns usuários precisam autenticar-se novamente.   | Prioridade alta. Revisar tokens e renovação no Supabase; a VM elimina parte da instabilidade de execução, mas não substitui a correção da sessão. |
| Primeiro envio falha       | Em alguns testes, a pergunta ou o chat só apareceu após o segundo envio.             | Resolvido em 02/10/2026 (seção 12): pergunta salva antes da IA, status por turno e novas tentativas no mesmo registro. Requer a migração 008.      |
| Upload de imagem falha     | Arquivos próximos de 3 MB exibiram erro ainda no seletor da Vercel.                  | Aplicar limite compatível no piloto, compressão/validação no cliente e upload direto ao Supabase Storage.                                         |
| Imagens e avatar quebrados | Logo ou anexos aparecem como imagem indisponível, especialmente em outro navegador.  | Servir a logo por URL estável e usar URLs assinadas do Storage, evitando mídia mantida apenas na memória do processo.                             |
| Latência variável          | Respostas variaram de poucos segundos a mais de 40 segundos; houve 503 e timeouts.   | No OpenRouter, configurar timeout por tarefa, fallback rápido, telemetria e limite de custo.                                                      |
| Pesquisa web irregular     | A Tavily funcionou localmente, mas apresentou falhas ou ausência de busca no deploy. | Separar timeout da pesquisa, registrar execução da ferramenta e testar conectividade/segredos na VM.                                              |
| Vercel e Streamlit         | A aplicação depende de sessão WebSocket e de um processo relativamente persistente.  | Encerrar a Vercel como plataforma principal após a migração para Azure.                                                                           |
| Atualização percebida      | Sessões abertas do chefe nem sempre refletiram imediatamente a nova versão.          | Exibir versão do build na interface e executar teste em janela anônima após cada deploy.                                                          |
| Codificação de caracteres  | Algumas substituições de arquivo causaram caracteres quebrados.                      | Padronizar UTF-8, evitar substituições manuais de arquivos completos e manter validação automatizada.                                             |

# 7. Próximos passos

## 7.1 Preparação antes da aprovação

- Congelar e marcar no Git a versão funcional atual, mantendo um rollback conhecido.

- Documentar todas as variáveis de ambiente sem registrar os valores secretos.

- Criar testes de fumaça para login, primeira mensagem, segunda mensagem, pesquisa web, upload de imagem, recarga da página, privacidade e roteiro publicado.

- Exibir no rodapé uma identificação curta da versão implantada.

- Definir limites temporários de tamanho e tipo de anexo e mensagens de erro compreensíveis.

- Confirmar volume esperado de usuários simultâneos, arquivos por mês e orçamento mensal.

## 7.2 Migração para ferramentas pagas

**1.** Contratar ou habilitar Supabase pago e confirmar backups, retenção, região e limites de Storage.

**2.** Criar a conta/projeto OpenRouter, carregar créditos, definir orçamento, alertas e chaves separadas por ambiente.

**3.** Implementar um adaptador de modelos para que a aplicação não dependa diretamente de nomes ou SDKs de um único provedor.

**4.** Selecionar um modelo principal econômico, um modelo multimodal e pelo menos um fallback; registrar preço, limite de contexto e timeout de cada rota.

**5.** Solicitar à empresa de infraestrutura a VM Linux no Azure, domínio, DNS, HTTPS, regras de firewall, identidade de acesso, monitoramento, backup e cofre de segredos.

**6.** Publicar o contêiner em um registro aprovado, instalar o serviço na VM e configurar reinício automático e health check.

**7.** Executar migração de homologação, validar o checklist e somente então apontar o domínio produtivo para o Azure.

## 7.3 Evolução funcional após a migração

- Implementar cache e recuperação de conhecimento autorizada: histórico do próprio usuário, roteiros publicados e documentos corporativos aprovados.

- Classificar perguntas antes da chamada ao modelo para escolher pesquisa, modelo multimodal, modelo econômico ou resposta recuperada.

- Criar uma política de fontes com lista preferencial de órgãos oficiais, fornecedores reconhecidos e fontes especializadas em viagens.

- Medir custo por usuário, conversa, modelo e ferramenta, sem registrar conteúdo sensível nos logs.

- Preparar administração básica para usuários, roteiros publicados, limites e auditoria.

# 8. Solicitações para a empresa de infraestrutura

A solicitação deve informar que se trata de uma aplicação web Python empacotada em Docker, com conexões de saída para Supabase, OpenRouter e Tavily e sem banco de dados instalado na VM. Os itens abaixo podem ser criados e administrados pela empresa terceirizada.

| **Solicitação**                | **Detalhamento mínimo**                                                                                                              |
|--------------------------------|--------------------------------------------------------------------------------------------------------------------------------------|
| Assinatura e grupo de recursos | Definir assinatura, centro de custo, ambiente, região do Azure e responsáveis.                                                       |
| VM Linux                       | Tamanho inicial a confirmar por teste de carga; disco gerenciado; IP público fixo se necessário; atualizações e reinício automático. |
| Rede                           | Liberar somente HTTPS para usuários e acesso administrativo restrito; permitir saídas HTTPS para serviços externos.                  |
| Domínio                        | Criar subdomínio corporativo, por exemplo agora.dominio-da-empresa, e apontar o DNS para a entrada da aplicação.                     |
| HTTPS                          | Emitir e renovar automaticamente certificado TLS; configurar proxy reverso para o Streamlit.                                         |
| Registro de contêiner          | Criar Azure Container Registry ou aprovar outro registro privado e conceder à VM somente permissão de leitura.                       |
| Segredos                       | Criar Key Vault ou solução aprovada para chaves do Supabase, OpenRouter, Tavily e senha de cookies.                                  |
| Monitoramento                  | Coletar uso de CPU, memória, disco, disponibilidade e logs; criar alertas de indisponibilidade e consumo anormal.                    |
| Backup e recuperação           | Definir backup/configuração da VM, procedimento de restauração e responsáveis por incidentes.                                        |
| Acessos da desenvolvedora      | Conceder acesso mínimo para consultar logs, publicar versões e reiniciar o serviço, com MFA e trilha de auditoria.                   |

# 9. Critérios de aceite da próxima versão

| **Área**         | **Critério**                                                                                         |
|------------------|------------------------------------------------------------------------------------------------------|
| Login            | Sessão permanece válida após recarregar a página e durante upload, conforme a política definida.     |
| Mensagens        | A primeira mensagem aparece e é persistida no primeiro envio; falhas liberam o campo sem duplicação. |
| Imagens          | Upload, visualização histórica e análise funcionam em navegadores e usuários diferentes.             |
| Privacidade      | Um usuário não acessa conversas ou anexos privados de outro usuário.                                 |
| Memória coletiva | Apenas roteiros explicitamente publicados são recuperados, sem identidade ou conversa original.      |
| Pesquisa         | Perguntas atuais acionam pesquisa e apresentam fontes reais; falhas são informadas sem invenção.     |
| Modelos          | Modelo principal e fallback funcionam pelo OpenRouter, com limites de tempo e custo.                 |
| Desempenho       | Metas de latência serão definidas após teste de carga; a aplicação deve registrar tempo por etapa.   |
| Operação         | Health check, logs, alertas, rollback e recuperação são testados antes da abertura ao grupo piloto.  |

# 10. Pontos ainda a confirmar

- Aprovação final do orçamento e das ferramentas pagas.

- Região, tamanho e sistema operacional exatos da VM Azure.

- Domínio ou subdomínio corporativo definitivo.

- Quantidade de usuários simultâneos e volume mensal de anexos.

- Modelos específicos e regras de roteamento no OpenRouter.

- Orçamento mensal, teto por usuário e política de recarga de créditos.

- Lista oficial de fontes preferenciais de turismo e viagens.

- Responsáveis por suporte, incidentes, acessos, backup e aprovação de novas versões.

# 11. Conclusão

O piloto cumpriu o objetivo de validar o produto e revelou os pontos que precisam de uma infraestrutura persistente. A decisão de manter Supabase, adotar OpenRouter e executar a ÁGORA em uma VM Azure reduz mudanças simultâneas e preserva o que já funciona. A migração deve ser feita por etapas, com homologação, métricas, segurança e rollback. A versão paga estará pronta para o piloto ampliado quando os critérios de aceite de sessão, mensagens, anexos, pesquisa, privacidade e operação forem atendidos.

# 12. Registro de evolução

## 28/09 a 01/10/2026 — ambiente local, branch `migracao-ferramentas-pagas`

| **Tema**              | **O que mudou**                                                                                                                                                                                                                           |
|-----------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Repositórios          | Código em `LatitudesViagens/latitudes-ai` e em `isacontieri/latitudes-ai` (ambos privados); um `git push` atualiza os dois. Tag `piloto-vercel-v1` marca a versão do piloto na Vercel.                                                    |
| Vercel                | Abandonada como plataforma; o deploy será no Azure.                                                                                                                                                                                       |
| Modelos               | OpenRouter via LiteLLM. Principal `google/gemini-3.1-flash-lite`; fallback `openai/gpt-6-luna` (outra empresa, raciocínio baixo). Escolha por custo real × tempo: num roteiro completo, custo equivalente (~US$ 0,001) e Gemini 5–10× mais rápido. Configuráveis por `AGORA_MODEL_PRINCIPAL` e `AGORA_MODEL_FALLBACK`. |
| Diagnóstico Gemini    | A instabilidade de 29–30/09 (503 contínuo) vinha da chave do Gemini no plano gratuito, não do código. Resolvido com o OpenRouter.                                                                                                         |
| Kaspersky             | Intercepta HTTPS de `openrouter.ai` nas máquinas da Latitudes; o app usa `truststore` para confiar nos certificados do sistema. Não afeta o servidor no Azure.                                                                             |
| Resiliência           | Nova tentativa após 429/5xx, mais tempo com anexos, descarte do raciocínio interno dos modelos.                                                                                                                                           |
| Conversa              | Títulos gerados por IA; correção de valores em dólar exibidos como fórmula; ficha de publicação pré-preenchida pela IA; botões Exportar/Publicar só em respostas adequadas (Publicar só em roteiros por dias).                           |
| Arquivos              | A ÁGORA gera PDF, Word, Excel e CSV com identidade visual (logo no topo e no rodapé), salvos na pasta privada do usuário no Storage, sem migração nem mudança de políticas. Documentos levam só o roteiro, sem tom de conversa, recomendações, observações ou fontes. |
| Fotos                 | Busca de fotos na internet (Tavily) exibidas em galeria na conversa, respeitando a quantidade pedida. Direitos desconhecidos: não entram em documentos.                                                                                   |
| Travas                | Arquivos e fotos só são gerados quando a mensagem atual pede; a resposta não pode anunciar arquivo não gerado.                                                                                                                           |
| Prompt                | Não inventar dados pessoais (usa marcadores como [Nome do cliente]); perguntar só o essencial.                                                                                                                                             |
| LGPD                  | Roteiros com dados de clientes (nomes, contatos, documentos, reservas, saúde ou restrições ligadas a uma pessoa) não podem ser publicados na memória coletiva: a ficha bloqueia a publicação e, se a verificação falhar, bloqueia também. Aprovado por Isabelle em 01/10/2026. |

## 02/10/2026 — envio resistente a falhas

| **Tema**              | **O que mudou**                                                                                                                                                                                                                           |
|-----------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Causa da 1ª mensagem  | A pergunta só era gravada quando a chamada ao modelo já tinha começado; qualquer interrupção antes disso (clique, reconexão do WebSocket, erro no Supabase) a perdia, e numa conversa nova deixava a conversa vazia.                       |
| Salvar antes da IA    | Pergunta e resposta reservada são gravadas antes de qualquer outra etapa. Cada turno tem status (pendente, processando, concluída, erro, cancelada) no registro de resposta.                                                               |
| Novas tentativas      | 2 no modelo principal (espera de 2 s e 4 s) e 1 no fallback, sempre no mesmo registro de resposta. Erros de conta (401/402) param na hora; erros do modelo (400/404) pulam para o fallback. Timeouts: 60 s (principal) e 90 s (fallback). |
| Interrupções          | "Parar" ou recarregar marca o turno como interrompido na hora; turnos parados por mais de ~2 min viram erro ao abrir a conversa. Botões Tentar novamente e Cancelar (o texto volta ao campo).                                              |
| Registro de tentativas| Tabela `message_attempts` (modelo, nº, duração, status, tipo de erro, tokens; custo reservado para o controle de custos).                                                                                                                 |
| Banco                 | Migração 008: colunas `status`, `reply_to`, `updated_at` em `messages`; permissão de atualizar só respostas do assistente nas próprias conversas; tabela `message_attempts` com RLS.                                                       |
| Segundo plano         | A IA passou a rodar fora da execução da tela: o Streamlit não conseguia interromper o script enquanto ele esperava a IA ("Parar" não funcionava; trocar de conversa misturava telas). Agora a tela só acompanha a tarefa e o botão Parar fica ao lado do campo de mensagem. Cada tarefa usa a própria conexão com o Supabase (o cliente usa HTTP/2 e travava quando compartilhado entre threads). |
| Tempos                | `AGORA_LOG_TEMPOS=1` grava os tempos de cada etapa em `logs/agora-tempos.log`. Medição local: tela montada em ~3–4 s (2 s conferindo o login no Supabase, 1 s na 1ª lista de conversas); a abertura total de 8–17 s inclui partida a frio do servidor e o Kaspersky. Medir de novo no Azure. |
| Ajustes de interface  | Sem menu "Exportar" (documentos só quando pedidos); fontes consultadas como rodapé; título resume o pedido e é gerado em paralelo à resposta.                                                                                              |

**Pendências para a próxima sessão**

- **Pendências da interface (resolver antes de liberar para a empresa):**
  - O 1º clique em "Tentar novamente" (e às vezes em "Parar") se perde: o registro de tempos mostra só um clique chegando ao servidor, e esse funciona na hora. Já tentado sem sucesso: botão Parar dentro do fragmento que atualiza a cada 1 s; trocar o fragmento por um laço de espera no fim do script (desfeito). Suspeita: limitação do Streamlit com fragmentos `run_every` (pedidos de atualização juntados com o clique) ou elemento recriado entre execuções.
  - Ao enviar, a tela não fica no fim da conversa: para na mensagem enviada em vez de acompanhar "Preparando a resposta" e a resposta.
  - "Preparando a resposta" aparece duplicado por um instante logo após o envio.
  - Abertura lenta no servidor local (8–17 s); conferir no Azure, com o servidor na mesma região do Supabase.

- Confirmar com o programador/Dedalus: serviço do Azure (Container Apps, App Service ou VM), branch de deploy e cadastro de `OPENROUTER_API_KEY`.
- Plano de rollback recomendado: tags de versão (`v1.0`…), branch dedicada à produção e, no Azure, revisões/slots ou imagem anterior guardada.
- Fotos com licença (Pexels) para documentos, quando a API do Pexels voltar.
- Renomear `Dockerfile.vercel` e remover restos da Vercel (stub de cookies, `.vercelignore`).
