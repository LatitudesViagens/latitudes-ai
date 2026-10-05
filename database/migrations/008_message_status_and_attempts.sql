-- Turnos resistentes a falhas: status no registro de resposta, atualização
-- da mesma resposta em novas tentativas e registro de cada tentativa.

-- 1. Status do turno, guardado no registro de resposta do assistente.
--    Mensagens existentes ficam como 'concluida'.
alter table public.messages
    add column if not exists status text not null default 'concluida',
    add column if not exists reply_to uuid
        references public.messages(id) on delete cascade,
    add column if not exists updated_at timestamptz not null default now();

alter table public.messages
    drop constraint if exists messages_status_check;

alter table public.messages
    add constraint messages_status_check
    check (
        status in (
            'pendente',
            'processando',
            'concluida',
            'erro',
            'cancelada'
        )
    );

-- A resposta reservada nasce vazia e só recebe conteúdo ao concluir.
-- Remove a regra antiga do conteúdo (criada na 002), qualquer que seja o
-- nome gerado pelo Postgres.
do $$
declare
    constraint_record record;
begin
    for constraint_record in
        select conname
        from pg_constraint
        where conrelid = 'public.messages'::regclass
          and contype = 'c'
          and pg_get_constraintdef(oid) ilike '%char_length%content%'
    loop
        execute format(
            'alter table public.messages drop constraint %I',
            constraint_record.conname
        );
    end loop;
end;
$$;

alter table public.messages
    add constraint messages_content_check
    check (
        char_length(trim(content)) > 0
        or (
            role = 'assistant'
            and status in ('pendente', 'processando', 'cancelada')
        )
    );

create index if not exists messages_conversation_status_idx
    on public.messages (conversation_id, status);

create index if not exists messages_reply_to_idx
    on public.messages (reply_to);

create or replace function public.touch_message_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists messages_touch_updated_at
    on public.messages;

create trigger messages_touch_updated_at
    before update on public.messages
    for each row
    execute function public.touch_message_updated_at();

-- 2. Nova permissão: atualizar SOMENTE respostas do assistente, SOMENTE nas
--    próprias conversas e SOMENTE estas colunas (papel e conversa não mudam).
grant update (content, sources, attachments, status)
    on table public.messages
    to authenticated;

drop policy if exists "Users can update assistant replies in their conversations"
    on public.messages;

create policy "Users can update assistant replies in their conversations"
    on public.messages
    for update
    to authenticated
    using (
        role = 'assistant'
        and exists (
            select 1
            from public.conversations
            where conversations.id = messages.conversation_id
              and conversations.user_id = (select auth.uid())
        )
    )
    with check (
        role = 'assistant'
        and exists (
            select 1
            from public.conversations
            where conversations.id = messages.conversation_id
              and conversations.user_id = (select auth.uid())
        )
    );

-- 3. Registro de tentativas: só inserção e leitura, nunca alterado.
--    Os campos de tokens e custo ficam prontos para o controle de custos.
create table if not exists public.message_attempts (
    id uuid primary key default gen_random_uuid(),
    message_id uuid not null
        references public.messages(id) on delete cascade,
    conversation_id uuid not null
        references public.conversations(id) on delete cascade,
    user_id uuid not null default auth.uid(),
    attempt_number smallint not null
        check (attempt_number > 0),
    model_role text not null
        check (model_role in ('principal', 'fallback')),
    model text not null,
    status text not null
        check (
            status in (
                'sucesso',
                'erro',
                'timeout',
                'vazia',
                'interrompida',
                'cancelada'
            )
        ),
    error_type text,
    duration_ms integer not null
        check (duration_ms >= 0),
    simulated boolean not null default false,
    prompt_tokens integer,
    completion_tokens integer,
    reasoning_tokens integer,
    cost_usd numeric(12, 6),
    started_at timestamptz not null,
    created_at timestamptz not null default now()
);

create index if not exists message_attempts_message_idx
    on public.message_attempts (message_id);

create index if not exists message_attempts_user_created_idx
    on public.message_attempts (user_id, created_at);

alter table public.message_attempts enable row level security;

revoke all on table public.message_attempts from anon, authenticated;

grant select, insert
    on table public.message_attempts
    to authenticated;

drop policy if exists "Users read their attempts"
    on public.message_attempts;

create policy "Users read their attempts"
    on public.message_attempts
    for select
    to authenticated
    using (user_id = (select auth.uid()));

drop policy if exists "Users log attempts in their conversations"
    on public.message_attempts;

create policy "Users log attempts in their conversations"
    on public.message_attempts
    for insert
    to authenticated
    with check (
        user_id = (select auth.uid())
        and exists (
            select 1
            from public.conversations
            where conversations.id = message_attempts.conversation_id
              and conversations.user_id = (select auth.uid())
        )
    );
