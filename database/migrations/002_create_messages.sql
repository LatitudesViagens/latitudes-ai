create table public.messages (
    id uuid primary key default gen_random_uuid(),
    conversation_id uuid not null
        references public.conversations(id)
        on delete cascade,
    role text not null
        check (role in ('user', 'assistant', 'system', 'tool')),
    content text not null
        check (char_length(trim(content)) > 0),
    sources jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now()
);

create index messages_conversation_created_idx
    on public.messages (conversation_id, created_at);

alter table public.messages enable row level security;

revoke all on table public.messages from anon, authenticated;

grant select, insert
    on table public.messages
    to authenticated;

create policy "Users can view messages from their conversations"
    on public.messages
    for select
    to authenticated
    using (
        exists (
            select 1
            from public.conversations
            where conversations.id = messages.conversation_id
              and conversations.user_id = (select auth.uid())
        )
    );

create policy "Users can add messages to their conversations"
    on public.messages
    for insert
    to authenticated
    with check (
        exists (
            select 1
            from public.conversations
            where conversations.id = messages.conversation_id
              and conversations.user_id = (select auth.uid())
        )
    );