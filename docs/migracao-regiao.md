# Migração do Supabase: São Paulo → EUA (us-east-1)

**Por quê:** o App Service `app-agora-prd` está em **East US 2** (Virgínia) e o Supabase em São Paulo. Cada consulta ao banco atravessa o continente (~0,12–0,2 s por ida e volta), várias vezes por clique e por resposta. A região do Supabase mais próxima é **`us-east-1` (N. Virginia)**.

**Como:** o Supabase não muda a região de um projeto existente (nem no Pro; "restaurar em novo projeto" também fica na mesma região). O caminho é criar um projeto novo em `us-east-1` e copiar tudo. O projeto de São Paulo fica intacto como plano de volta.

**Kit (`scripts/migracao_regiao/`):**

| Arquivo | Para quê |
|---|---|
| `inventario.sql` | Lista tudo o que existe no banco (tabelas e quantidade de linhas, usuários, arquivos, regras de acesso, funções, gatilhos, índices, restrições, permissões, colunas, extensões). Só leitura; mostra nomes e contagens, nenhum dado. |
| `comparar_inventario.py` | Compara o inventário do antigo com o do novo e mostra só as diferenças. |
| `storage_pos_restauracao.sql` | Recria no novo o bucket `chat-attachments` e as 3 regras de acesso dos arquivos (migrações 006 e 007), que a cópia do banco não leva. |
| `copiar_arquivos.py` | Copia os arquivos (anexos e documentos gerados) do antigo para o novo e confere quantidade e tamanho. |

## O que vai e o que não vai na cópia do banco

| Item | Na cópia? | Tratamento |
|---|---|---|
| Tabelas, dados, funções, gatilhos, índices, restrições e regras de acesso do `public` (migrações 001–012 e qualquer ajuste feito à mão) | Sim | conferido pelo inventário |
| Usuários e senhas (`auth`), com os mesmos IDs | Sim | conferido pelo inventário (`auth.users`, `auth.identities`) |
| Extensões `vector` e `unaccent` | **Não** | ligar no novo antes de restaurar (passo B4) |
| Regras de acesso dos arquivos (`storage.objects`) | **Não** | `storage_pos_restauracao.sql` |
| Arquivos do Storage | **Não** | `copiar_arquivos.py` |
| Configurações de login, API e banco do painel | **Não** | lista do passo B5 |
| Chaves e endereço | Mudam | passo C9 |
| Realtime, Edge Functions, Vault, webhooks | — | a ÁGORA não usa |

**Efeito para quem usa:** as chaves de segurança do projeto novo são outras, então é provável que cada pessoa precise **entrar de novo uma vez**, com a mesma senha.

**Cuidado com os arquivos da cópia (`roles.sql`, `schema.sql`, `data.sql`):** contêm todos os dados, inclusive as senhas criptografadas. Ficam numa pasta **fora do projeto** (ex.: `C:\migracao-agora`), nunca vão para o Git nem para o chat, e são apagados no fim (passo D3).

---

## A. Antes (com a ÁGORA funcionando)

1. **Programador do Azure.** O `app-agora-prd` usa integração de rede virtual (`vnet-spoke-apps-prd`) e ponto de extremidade privado. Perguntar:
   - A saída para a internet tem restrição por destino ou IP (firewall, NSG)? Se tiver, liberar o endereço do projeto novo (`*.supabase.co` e `*.pooler.supabase.com` em `us-east-1`) **antes** do dia da troca.
2. **Supabase antigo.** Ver se há restrição de rede: *Project Settings → Database → Network Restrictions*. Se houver lista de IPs, repetir no novo.
3. **Ferramentas neste computador.** Docker Desktop **aberto** (já instalado) e Node (já instalado). O Supabase CLI roda pelo `npx`, e o `psql` pela imagem oficial do PostgreSQL 17, sem instalar mais nada.

## B. Preparar o projeto novo

4. **Criar o projeto** na organização da Latitudes, plano Pro:
   - Região: **East US (North Virginia) — `us-east-1`**.
   - Tamanho de computação: o mesmo do projeto antigo (*Project Settings → Compute and Disk*).
   - Senha do banco: forte, guardada no cofre de senhas.
   - Depois de criado: *Database → Extensions* → ligar **`vector`** e **`unaccent`** (esquema `extensions`).
5. **Copiar as configurações do painel**, do antigo para o novo, tela por tela:
   - *Authentication → Sign In / Providers → Email*: provedor ligado; "Confirm email"; "Allow new users to sign up" (a ÁGORA cria contas pelo TI); tamanho mínimo e exigências de senha; "Secure password change".
   - *Authentication → Emails (templates)*: copiar **todos**, principalmente **"Reset Password"**, que precisa do código `{{ .Token }}` (a ÁGORA redefine senha por código: `database/auth.py`, `verify_otp`).
   - *Authentication → Emails → SMTP*: se o antigo usa SMTP próprio, repetir servidor, porta, usuário, senha e remetente.
   - *Authentication → URL Configuration*: Site URL `https://agora.latitudes.com.br/agora/` e as Redirect URLs do antigo.
   - *Authentication → Sessions* e *Rate Limits*: copiar os valores do antigo.
   - *Authentication → Attack Protection* e *Hooks*: copiar se houver algo ligado.
   - *Project Settings → Data API*: esquemas expostos e "Max rows" iguais aos do antigo.
   - *Storage → Settings*: limite global de tamanho de arquivo igual ao antigo (mínimo 10 MB).
   - *Database → Backups / Point in Time Recovery*: igual ao antigo.
6. **Ensaio geral (recomendado).** Fazer os passos C3 a C8 num projeto de **teste** (outro projeto novo, apagado depois), com a ÁGORA funcionando normalmente. Conferir o inventário e abrir a ÁGORA local apontando para ele (só no `.env` local). Assim, o dia da troca é só repetir o que já deu certo. O custo do projeto de teste é de algumas horas de uso.

## C. Dia da troca (ÁGORA parada, ~30 a 60 min)

Abrir o PowerShell numa pasta fora do projeto (ex.: `C:\migracao-agora`) e definir as conexões. Pegue cada uma em *Connect → Session pooler* no painel de cada projeto, trocando `[YOUR-PASSWORD]` pela senha do banco. Não cole essas linhas no chat.

```powershell
$ANTIGO = "postgresql://postgres.<ref-antigo>:<senha-antigo>@aws-0-sa-east-1.pooler.supabase.com:5432/postgres"
$NOVO   = "postgresql://postgres.<ref-novo>:<senha-novo>@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
```

1. **Avisar e parar a ÁGORA:** Azure → `app-agora-prd` → **Parar**. A partir daqui ninguém grava nada no banco antigo.
2. **Inventário do antigo:** SQL Editor do projeto antigo → colar `scripts/migracao_regiao/inventario.sql` → Run → Export CSV → salvar como `antigo.csv`.
3. **Cópia do banco antigo** (3 arquivos):
   ```powershell
   npx supabase@latest db dump --db-url $ANTIGO -f roles.sql --role-only
   npx supabase@latest db dump --db-url $ANTIGO -f schema.sql
   npx supabase@latest db dump --db-url $ANTIGO -f data.sql --use-copy --data-only -x "storage.buckets_vectors" -x "storage.vector_indexes" -x "storage.objects" -x "storage.prefixes"
   ```
   O registro dos arquivos (`storage.objects`) fica de fora de propósito: ele é recriado pelo `copiar_arquivos.py` junto com cada arquivo, sem registros órfãos.
4. **Restaurar no novo:**
   ```powershell
   docker run --rm -v "${PWD}:/work" -w /work postgres:17 psql --single-transaction --variable ON_ERROR_STOP=1 --file roles.sql --file schema.sql --command "SET session_replication_role = replica" --file data.sql --dbname $NOVO
   ```
   Se der erro, nada é gravado (`--single-transaction`). Erros conhecidos do guia oficial:
   - `ALTER ... OWNER TO "supabase_admin"` → comentar essas linhas no `schema.sql`.
   - `permission denied to grant role "postgres"` → comentar a linha `GRANT "postgres" TO "cli_login_postgres"` no `roles.sql`.
   - `"supabase_admin" is a reserved role` no `roles.sql` (aconteceu na migração de 14/10/2026) → restaurar sem o `roles.sql`, depois de conferir em `pg_db_role_setting` e `pg_parameter_acl` que `anon`, `authenticated` e `authenticator` já estão iguais nos dois projetos (só o `supabase_admin`, interno da Supabase, difere).
   - **Permissões:** a cópia NÃO leva as permissões restritas das tabelas e funções (no antigo, `anon` não tem acesso às tabelas e `authenticated` só o necessário, inclusive UPDATE só em 4 colunas de `messages`). Gerar a correção no antigo com `scripts/migracao_regiao/gerar_permissoes.sql` (`psql ... -tA -f gerar_permissoes.sql -o permissoes.sql`), revisar e aplicar no novo com `--single-transaction`.
5. **Storage no novo:** SQL Editor do projeto novo → colar `scripts/migracao_regiao/storage_pos_restauracao.sql` → Run.
6. **Copiar os arquivos** (no terminal da pasta do projeto, com o `.venv`):
   ```powershell
   $env:ORIGEM_SUPABASE_URL = "https://<ref-antigo>.supabase.co"
   $env:ORIGEM_SERVICE_ROLE_KEY = "<service_role do antigo>"
   $env:DESTINO_SUPABASE_URL = "https://<ref-novo>.supabase.co"
   $env:DESTINO_SERVICE_ROLE_KEY = "<service_role do novo>"
   python scripts/migracao_regiao/copiar_arquivos.py --contar
   python scripts/migracao_regiao/copiar_arquivos.py
   ```
   Se não terminar em "ARQUIVOS IGUAIS ✔", rodar `python scripts/migracao_regiao/copiar_arquivos.py --falhas`.
7. **Inventário do novo:** mesma consulta do passo 2 no projeto novo → salvar como `novo.csv` →
   ```powershell
   python scripts/migracao_regiao/comparar_inventario.py antigo.csv novo.csv
   ```
   Só seguir se o resultado for **"TUDO IGUAL ✔"**. Diferença de versão de extensão é normal.
8. **Conferência rápida no novo** (SQL Editor): `select count(*) from auth.users;` deve bater com o antigo.
9. **Trocar as chaves.** Azure → `app-agora-prd` → *Configurações → Variáveis de ambiente*:
   - `SUPABASE_URL` = `https://<ref-novo>.supabase.co`
   - `SUPABASE_KEY` = chave **anon/publishable** do novo
   - `SUPABASE_SERVICE_ROLE_KEY` = chave **service_role/secret** do novo

   Salvar. Fazer a mesma troca no `latitudes_agent/.env` local.
10. **Iniciar a ÁGORA** (Azure → Iniciar) e fazer os testes de aceite:
    - [ ] Entrar com uma conta existente, a mesma senha
    - [ ] Conversa antiga abre com as mensagens
    - [ ] Imagem anexada numa conversa antiga aparece
    - [ ] Documento gerado antigo baixa
    - [ ] Pergunta nova é respondida (e aparece em Gastos)
    - [ ] Pergunta parecida com uma resposta aprovada usa a base de conhecimento
    - [ ] Pedido de roteiro encontra a memória coletiva
    - [ ] Busca de conversas acha uma conversa antiga
    - [ ] Painel de controle: Gastos, Senhas, Usuários (lista de e-mails), Base de conhecimento, Modelos
    - [ ] Enviar um anexo novo
    - [ ] Perfil de cliente (RD e Envision)

## Se algo der errado (plano de volta)

Parar a ÁGORA, repor as **3 variáveis antigas** no Azure e no `.env`, e iniciar de novo. O projeto de São Paulo não foi alterado em nada. Só se perde o que foi gravado no projeto novo depois da troca.

## D. Depois

1. Acompanhar uma semana, com o contador `?medir=1` e os logs `[TURN]`, para confirmar o ganho de velocidade.
2. Manter o projeto de São Paulo por **7 dias** sem usar. Antes de apagar, baixar o último backup (*Database → Backups*).
3. Apagar os arquivos da cópia (`roles.sql`, `schema.sql`, `data.sql`, `antigo.csv`, `novo.csv`) da pasta `C:\migracao-agora`.
4. Atualizar o CLAUDE.md e o `docs/arquitetura.md` com a nova região.
