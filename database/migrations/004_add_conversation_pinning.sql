alter table public.conversations
    add column if not exists is_pinned boolean
    not null default false;

create index if not exists conversations_user_pinned_updated_idx
    on public.conversations (
        user_id,
        is_pinned desc,
        updated_at desc
    );