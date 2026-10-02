-- 009 — Etapa 2: custos em R$, papel TI, troca obrigatória de senha,
-- base de conhecimento com aprovação (pgvector), busca de conversas e
-- modelos de prompt.
--
-- PROPOSTA: aplicar manualmente no SQL Editor do Supabase, depois da 008,
-- somente após aprovação da Isabelle. O script pode ser rodado de novo sem
-- erro (if not exists / drop ... if exists).

-- 0. Extensões ------------------------------------------------------------
create extension if not exists vector with schema extensions;
create extension if not exists unaccent with schema extensions;

-- Função genérica para manter updated_at (tabelas novas desta migração).
create or replace function public.touch_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;


-- 1. Papel TI ----------------------------------------------------------------
-- Quem tem linha aqui com role = 'ti' acessa o painel do TI. Não há política
-- de escrita: o papel só é concedido pelo SQL Editor (ou service_role), nunca
-- pela interface.
create table if not exists public.user_roles (
    user_id uuid primary key
        references auth.users(id) on delete cascade,
    role text not null
        check (role in ('ti')),
    created_at timestamptz not null default now()
);

alter table public.user_roles enable row level security;

revoke all on table public.user_roles from anon, authenticated;
grant select on table public.user_roles to authenticated;

drop policy if exists "Users read their own role" on public.user_roles;

create policy "Users read their own role"
    on public.user_roles
    for select
    to authenticated
    using (user_id = (select auth.uid()));

-- Checagem de papel usada pelas políticas abaixo (roda no banco, não na tela).
create or replace function public.is_ti()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select exists (
        select 1
        from public.user_roles
        where user_id = (select auth.uid())
          and role = 'ti'
    );
$$;

revoke execute on function public.is_ti() from public, anon;
grant execute on function public.is_ti() to authenticated;


-- 2. Reset de senha pelo TI ---------------------------------------------------
-- A senha temporária é definida pela API admin do Supabase (service_role, só
-- no servidor). Aqui ficam o registro do reset e a obrigação de trocar.
create table if not exists public.password_resets (
    id uuid primary key default gen_random_uuid(),
    target_user_id uuid not null
        references auth.users(id) on delete cascade,
    reset_by uuid not null default auth.uid()
        references auth.users(id),
    created_at timestamptz not null default now()
);

create index if not exists password_resets_created_idx
    on public.password_resets (created_at desc);

alter table public.password_resets enable row level security;

revoke all on table public.password_resets from anon, authenticated;
grant select, insert on table public.password_resets to authenticated;

drop policy if exists "TI reads password resets" on public.password_resets;

create policy "TI reads password resets"
    on public.password_resets
    for select
    to authenticated
    using (public.is_ti());

drop policy if exists "TI logs password resets" on public.password_resets;

create policy "TI logs password resets"
    on public.password_resets
    for insert
    to authenticated
    with check (
        public.is_ti()
        and reset_by = (select auth.uid())
    );

-- Enquanto houver linha aqui, a pessoa só vê a tela "criar nova senha".
create table if not exists public.password_change_required (
    user_id uuid primary key
        references auth.users(id) on delete cascade,
    reset_id uuid
        references public.password_resets(id) on delete set null,
    created_at timestamptz not null default now()
);

alter table public.password_change_required enable row level security;

revoke all on table public.password_change_required from anon, authenticated;
grant select, insert, update, delete
    on table public.password_change_required
    to authenticated;

drop policy if exists "Users read their password requirement"
    on public.password_change_required;

create policy "Users read their password requirement"
    on public.password_change_required
    for select
    to authenticated
    using (user_id = (select auth.uid()) or public.is_ti());

drop policy if exists "TI requires password change"
    on public.password_change_required;

create policy "TI requires password change"
    on public.password_change_required
    for insert
    to authenticated
    with check (public.is_ti());

drop policy if exists "TI updates password requirement"
    on public.password_change_required;

create policy "TI updates password requirement"
    on public.password_change_required
    for update
    to authenticated
    using (public.is_ti())
    with check (public.is_ti());

-- A pessoa NÃO pode apagar a própria obrigação. Depois que ela cria a nova
-- senha, quem apaga é o servidor (service_role, que ignora o RLS), só após
-- a troca ter dado certo. O TI também pode apagar pelo painel.
drop policy if exists "Users clear their password requirement"
    on public.password_change_required;

drop policy if exists "TI clears password requirement"
    on public.password_change_required;

create policy "TI clears password requirement"
    on public.password_change_required
    for delete
    to authenticated
    using (public.is_ti());


-- 3. Custos -------------------------------------------------------------------
-- 3a. Tentativas de resposta (008): valores em R$ e cotação usada.
alter table public.message_attempts
    add column if not exists cost_brl numeric(12, 4),
    add column if not exists usd_brl_rate numeric(10, 4),
    add column if not exists rate_date date,
    add column if not exists rate_source text
        check (rate_source in ('ptax', 'reserva'));

-- Respostas tiradas da base de conhecimento também viram "tentativa" (custo 0),
-- para o painel mostrar quanto a base economizou.
alter table public.message_attempts
    drop constraint if exists message_attempts_model_role_check;

alter table public.message_attempts
    add constraint message_attempts_model_role_check
    check (model_role in ('principal', 'fallback', 'base_conhecimento'));

-- (coluna knowledge_entry_id é criada no item 4, depois da tabela da base)

drop policy if exists "TI reads all attempts" on public.message_attempts;

create policy "TI reads all attempts"
    on public.message_attempts
    for select
    to authenticated
    using (public.is_ti());

-- 3b. Chamadas de IA fora das respostas (título, verificação LGPD, ficha de
--     publicação, embeddings), para o total do painel ficar completo.
create table if not exists public.ai_usage (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null default auth.uid()
        references auth.users(id) on delete cascade,
    conversation_id uuid
        references public.conversations(id) on delete set null,
    purpose text not null
        check (
            purpose in (
                'titulo',
                'verificacao_lgpd',
                'ficha_publicacao',
                'embedding'
            )
        ),
    model text not null,
    prompt_tokens integer,
    completion_tokens integer,
    cost_usd numeric(12, 6),
    cost_brl numeric(12, 4),
    usd_brl_rate numeric(10, 4),
    rate_date date,
    rate_source text
        check (rate_source in ('ptax', 'reserva')),
    created_at timestamptz not null default now()
);

create index if not exists ai_usage_created_idx
    on public.ai_usage (created_at);

alter table public.ai_usage enable row level security;

revoke all on table public.ai_usage from anon, authenticated;
grant select, insert on table public.ai_usage to authenticated;

drop policy if exists "Users log their AI usage" on public.ai_usage;

create policy "Users log their AI usage"
    on public.ai_usage
    for insert
    to authenticated
    with check (user_id = (select auth.uid()));

drop policy if exists "Users and TI read AI usage" on public.ai_usage;

create policy "Users and TI read AI usage"
    on public.ai_usage
    for select
    to authenticated
    using (user_id = (select auth.uid()) or public.is_ti());


-- 4. Base de conhecimento -----------------------------------------------------
-- Embeddings com 768 dimensões (servem tanto para o modelo da OpenAI com
-- dimensions=768 quanto para o gemini-embedding-001). Trocar de modelo exige
-- recalcular os embeddings.
create table if not exists public.knowledge_entries (
    id uuid primary key default gen_random_uuid(),
    question text not null
        check (char_length(trim(question)) > 0),
    answer text not null
        check (char_length(trim(answer)) > 0),
    status text not null default 'pendente'
        check (status in ('pendente', 'aprovada', 'rejeitada')),
    embedding extensions.vector(768),
    embedding_model text,
    source_message_id uuid
        references public.messages(id) on delete set null,
    suggested_by uuid not null default auth.uid()
        references auth.users(id) on delete cascade,
    reviewed_by uuid
        references auth.users(id),
    reviewed_at timestamptz,
    review_note text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    -- Aprovada sempre tem embedding (é o que permite a busca).
    constraint knowledge_entries_approved_has_embedding
        check (status <> 'aprovada' or embedding is not null)
);

create index if not exists knowledge_entries_status_idx
    on public.knowledge_entries (status, created_at desc);

create index if not exists knowledge_entries_embedding_idx
    on public.knowledge_entries
    using hnsw (embedding extensions.vector_cosine_ops)
    where status = 'aprovada';

drop trigger if exists knowledge_entries_touch_updated_at
    on public.knowledge_entries;

create trigger knowledge_entries_touch_updated_at
    before update on public.knowledge_entries
    for each row
    execute function public.touch_updated_at();

alter table public.knowledge_entries enable row level security;

revoke all on table public.knowledge_entries from anon, authenticated;
grant select, insert, update, delete
    on table public.knowledge_entries
    to authenticated;

-- Todos leem as aprovadas (usadas antes de chamar a IA); cada pessoa vê as
-- próprias sugestões; o TI vê tudo.
drop policy if exists "Read approved, own or all if TI"
    on public.knowledge_entries;

create policy "Read approved, own or all if TI"
    on public.knowledge_entries
    for select
    to authenticated
    using (
        status = 'aprovada'
        or suggested_by = (select auth.uid())
        or public.is_ti()
    );

-- Qualquer pessoa pode SUGERIR (entra como pendente, sem revisão).
drop policy if exists "Users suggest entries" on public.knowledge_entries;

create policy "Users suggest entries"
    on public.knowledge_entries
    for insert
    to authenticated
    with check (
        suggested_by = (select auth.uid())
        and (
            public.is_ti()
            or (
                status = 'pendente'
                and reviewed_by is null
                and reviewed_at is null
            )
        )
    );

-- Só o TI aprova, edita, rejeita ou apaga.
drop policy if exists "TI reviews entries" on public.knowledge_entries;

create policy "TI reviews entries"
    on public.knowledge_entries
    for update
    to authenticated
    using (public.is_ti())
    with check (public.is_ti());

drop policy if exists "TI deletes entries" on public.knowledge_entries;

create policy "TI deletes entries"
    on public.knowledge_entries
    for delete
    to authenticated
    using (public.is_ti());

-- Busca por similaridade (cosseno) só entre as aprovadas. security invoker:
-- respeita o RLS de quem chama.
create or replace function public.match_knowledge_entries(
    query_embedding extensions.vector(768),
    match_count integer default 3
)
returns table (
    id uuid,
    question text,
    answer text,
    similarity double precision
)
language sql
stable
security invoker
set search_path = ''
as $$
    select
        entries.id,
        entries.question,
        entries.answer,
        1 - (entries.embedding operator(extensions.<=>) query_embedding)
            as similarity
    from public.knowledge_entries as entries
    where entries.status = 'aprovada'
    order by entries.embedding operator(extensions.<=>) query_embedding
    limit least(greatest(match_count, 1), 10);
$$;

revoke execute on function public.match_knowledge_entries(extensions.vector, integer)
    from public, anon;
grant execute on function public.match_knowledge_entries(extensions.vector, integer)
    to authenticated;

-- Qual entrada da base foi usada na resposta (direta ou como contexto).
alter table public.message_attempts
    add column if not exists knowledge_entry_id uuid
        references public.knowledge_entries(id) on delete set null;


-- 5. Busca de conversas (português, sem diferenciar acentos) -----------------
do $$
begin
    if not exists (
        select 1
        from pg_catalog.pg_ts_config
        where cfgname = 'pt_unaccent'
          and cfgnamespace = 'public'::regnamespace
    ) then
        create text search configuration public.pt_unaccent
            (copy = pg_catalog.portuguese);
    end if;
end;
$$;

alter text search configuration public.pt_unaccent
    alter mapping for hword, hword_part, word
    with extensions.unaccent, portuguese_stem;

alter table public.messages
    add column if not exists content_search tsvector
        generated always as (
            to_tsvector('public.pt_unaccent'::regconfig, coalesce(content, ''))
        ) stored;

create index if not exists messages_content_search_idx
    on public.messages
    using gin (content_search);

alter table public.conversations
    add column if not exists title_search tsvector
        generated always as (
            to_tsvector('public.pt_unaccent'::regconfig, coalesce(title, ''))
        ) stored;

create index if not exists conversations_title_search_idx
    on public.conversations
    using gin (title_search);

-- security invoker: o RLS de conversations/messages garante que cada pessoa
-- só encontra as próprias conversas. Turnos cancelados ficam de fora.
create or replace function public.search_conversations(
    search_text text,
    max_results integer default 20
)
returns table (
    conversation_id uuid,
    title text,
    snippet text,
    rank real,
    last_match_at timestamptz
)
language sql
stable
security invoker
set search_path = ''
as $$
    with query as (
        select websearch_to_tsquery('public.pt_unaccent'::regconfig, search_text) as q
    ),
    hits as (
        select
            conversations.id as conversation_id,
            conversations.title,
            messages.content,
            messages.created_at,
            ts_rank(messages.content_search, query.q)
                + ts_rank(conversations.title_search, query.q) * 2 as rank
        from public.conversations
        join public.messages
            on messages.conversation_id = conversations.id
        cross join query
        where conversations.user_id = (select auth.uid())
          and messages.role in ('user', 'assistant')
          and messages.status <> 'cancelada'
          and (
              messages.content_search @@ query.q
              or conversations.title_search @@ query.q
          )
    ),
    best as (
        select distinct on (hits.conversation_id) hits.*
        from hits
        order by hits.conversation_id, hits.rank desc, hits.created_at desc
    )
    select
        best.conversation_id,
        best.title,
        ts_headline(
            'public.pt_unaccent'::regconfig,
            best.content,
            (select q from query),
            'MaxWords=25, MinWords=10, StartSel=**, StopSel=**'
        ) as snippet,
        best.rank,
        best.created_at as last_match_at
    from best
    order by best.rank desc, best.created_at desc
    limit least(greatest(max_results, 1), 50);
$$;

revoke execute on function public.search_conversations(text, integer)
    from public, anon;
grant execute on function public.search_conversations(text, integer)
    to authenticated;


-- 6. Modelos de prompt --------------------------------------------------------
create table if not exists public.prompt_templates (
    id uuid primary key default gen_random_uuid(),
    title text not null
        check (char_length(trim(title)) > 0),
    description text,
    content text not null
        check (char_length(trim(content)) > 0),
    is_active boolean not null default true,
    sort_order integer not null default 0,
    created_by uuid not null default auth.uid()
        references auth.users(id),
    updated_by uuid
        references auth.users(id),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists prompt_templates_active_idx
    on public.prompt_templates (is_active, sort_order);

drop trigger if exists prompt_templates_touch_updated_at
    on public.prompt_templates;

create trigger prompt_templates_touch_updated_at
    before update on public.prompt_templates
    for each row
    execute function public.touch_updated_at();

alter table public.prompt_templates enable row level security;

revoke all on table public.prompt_templates from anon, authenticated;
grant select, insert, update, delete
    on table public.prompt_templates
    to authenticated;

drop policy if exists "Users read active templates" on public.prompt_templates;

create policy "Users read active templates"
    on public.prompt_templates
    for select
    to authenticated
    using (is_active or public.is_ti());

drop policy if exists "TI creates templates" on public.prompt_templates;

create policy "TI creates templates"
    on public.prompt_templates
    for insert
    to authenticated
    with check (public.is_ti());

drop policy if exists "TI edits templates" on public.prompt_templates;

create policy "TI edits templates"
    on public.prompt_templates
    for update
    to authenticated
    using (public.is_ti())
    with check (public.is_ti());

drop policy if exists "TI deletes templates" on public.prompt_templates;

create policy "TI deletes templates"
    on public.prompt_templates
    for delete
    to authenticated
    using (public.is_ti());


-- 7. Depois de aplicar: conceder o papel TI (trocar pelo e-mail correto) -----
-- insert into public.user_roles (user_id, role)
-- select id, 'ti' from auth.users where email = 'email-do-ti@latitudes...'
-- on conflict (user_id) do nothing;
