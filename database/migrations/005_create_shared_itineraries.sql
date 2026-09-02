alter table public.conversations
    add column if not exists visibility text
    not null default 'private';

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'conversations_visibility_check'
          and conrelid = 'public.conversations'::regclass
    ) then
        alter table public.conversations
            add constraint conversations_visibility_check
            check (visibility in ('private', 'public'));
    end if;
end;
$$;


create table if not exists public.shared_itineraries (
    id uuid primary key default gen_random_uuid(),

    source_conversation_id uuid not null unique
        references public.conversations(id)
        on delete cascade,

    source_message_id uuid not null
        references public.messages(id)
        on delete cascade,

    created_by uuid not null
        references auth.users(id)
        on delete cascade,

    title text not null
        check (char_length(trim(title)) > 0),

    destination text not null
        check (char_length(trim(destination)) > 0),

    duration_days integer
        check (
            duration_days is null
            or duration_days between 1 and 365
        ),

    traveler_profile text,

    interests text[] not null default '{}',

    budget_range text,

    keywords text[] not null default '{}',

    content text not null
        check (char_length(trim(content)) > 0),

    sources jsonb not null default '[]'::jsonb
        check (jsonb_typeof(sources) = 'array'),

    published_at timestamptz not null default now(),

    updated_at timestamptz not null default now()
);


create index if not exists
    shared_itineraries_destination_idx
    on public.shared_itineraries (
        lower(destination)
    );


create index if not exists
    shared_itineraries_keywords_idx
    on public.shared_itineraries
    using gin (keywords);


create index if not exists
    shared_itineraries_published_idx
    on public.shared_itineraries (
        published_at desc
    );


alter table public.shared_itineraries
    enable row level security;


revoke all
    on table public.shared_itineraries
    from anon, authenticated;


grant select, insert, update, delete
    on table public.shared_itineraries
    to authenticated;


drop policy if exists
    "Authenticated users can view shared itineraries"
    on public.shared_itineraries;

create policy
    "Authenticated users can view shared itineraries"
    on public.shared_itineraries
    for select
    to authenticated
    using (true);


drop policy if exists
    "Users can publish their own itineraries"
    on public.shared_itineraries;

create policy
    "Users can publish their own itineraries"
    on public.shared_itineraries
    for insert
    to authenticated
    with check (
        created_by = (select auth.uid())
        and exists (
            select 1
            from public.conversations
            where conversations.id = source_conversation_id
              and conversations.user_id = (select auth.uid())
              and conversations.visibility = 'public'
        )
        and exists (
            select 1
            from public.messages
            where messages.id = source_message_id
              and messages.conversation_id = source_conversation_id
              and messages.role = 'assistant'
        )
    );


drop policy if exists
    "Users can update their own shared itineraries"
    on public.shared_itineraries;

create policy
    "Users can update their own shared itineraries"
    on public.shared_itineraries
    for update
    to authenticated
    using (
        created_by = (select auth.uid())
    )
    with check (
        created_by = (select auth.uid())
        and exists (
            select 1
            from public.conversations
            where conversations.id = source_conversation_id
              and conversations.user_id = (select auth.uid())
              and conversations.visibility = 'public'
        )
        and exists (
            select 1
            from public.messages
            where messages.id = source_message_id
              and messages.conversation_id = source_conversation_id
              and messages.role = 'assistant'
        )
    );


drop policy if exists
    "Users can remove their own shared itineraries"
    on public.shared_itineraries;

create policy
    "Users can remove their own shared itineraries"
    on public.shared_itineraries
    for delete
    to authenticated
    using (
        created_by = (select auth.uid())
    );


create or replace function
    public.touch_shared_itinerary_updated_at()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;


revoke all
    on function public.touch_shared_itinerary_updated_at()
    from public;


drop trigger if exists
    shared_itineraries_touch_updated_at
    on public.shared_itineraries;

create trigger shared_itineraries_touch_updated_at
    before update on public.shared_itineraries
    for each row
    execute function
        public.touch_shared_itinerary_updated_at();


create or replace function
    public.remove_shared_itinerary_when_private()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    if new.visibility = 'private'
       and old.visibility is distinct from new.visibility then

        delete from public.shared_itineraries
        where source_conversation_id = new.id;
    end if;

    return new;
end;
$$;


revoke all
    on function public.remove_shared_itinerary_when_private()
    from public;


drop trigger if exists
    conversations_remove_shared_itinerary_when_private
    on public.conversations;

create trigger
    conversations_remove_shared_itinerary_when_private
    after update of visibility
    on public.conversations
    for each row
    execute function
        public.remove_shared_itinerary_when_private();