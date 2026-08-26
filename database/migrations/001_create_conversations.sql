create table public.conversations (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    title text not null default 'Nova conversa',
    status text not null default 'active'
        check (status in ('active', 'archived')),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index conversations_user_updated_idx
    on public.conversations (user_id, updated_at desc);

alter table public.conversations enable row level security;

revoke all on table public.conversations from anon, authenticated;

grant select, insert, update, delete
    on table public.conversations
    to authenticated;

create policy "Users can view their own conversations"
    on public.conversations
    for select
    to authenticated
    using ((select auth.uid()) = user_id);

create policy "Users can create their own conversations"
    on public.conversations
    for insert
    to authenticated
    with check ((select auth.uid()) = user_id);

create policy "Users can update their own conversations"
    on public.conversations
    for update
    to authenticated
    using ((select auth.uid()) = user_id)
    with check ((select auth.uid()) = user_id);

create policy "Users can delete their own conversations"
    on public.conversations
    for delete
    to authenticated
    using ((select auth.uid()) = user_id);