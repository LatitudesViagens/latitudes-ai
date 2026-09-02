alter table public.messages
    add column if not exists attachments jsonb
    not null default '[]'::jsonb;

alter table public.messages
    drop constraint if exists messages_attachments_is_array;

alter table public.messages
    add constraint messages_attachments_is_array
    check (jsonb_typeof(attachments) = 'array');

insert into storage.buckets (
    id,
    name,
    public,
    file_size_limit,
    allowed_mime_types
)
values (
    'chat-attachments',
    'chat-attachments',
    false,
    10485760,
    array[
        'image/jpeg',
        'image/png',
        'image/webp',
        'application/pdf',
        'text/plain'
    ]
)
on conflict (id) do update
set
    public = excluded.public,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

drop policy if exists "Users read own chat attachments"
    on storage.objects;

create policy "Users read own chat attachments"
    on storage.objects
    for select
    to authenticated
    using (
        bucket_id = 'chat-attachments'
        and (storage.foldername(name))[1] = auth.uid()::text
    );

drop policy if exists "Users upload own chat attachments"
    on storage.objects;

create policy "Users upload own chat attachments"
    on storage.objects
    for insert
    to authenticated
    with check (
        bucket_id = 'chat-attachments'
        and (storage.foldername(name))[1] = auth.uid()::text
    );

drop policy if exists "Users delete own chat attachments"
    on storage.objects;

create policy "Users delete own chat attachments"
    on storage.objects
    for delete
    to authenticated
    using (
        bucket_id = 'chat-attachments'
        and (storage.foldername(name))[1] = auth.uid()::text
    );
