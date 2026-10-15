-- Storage no projeto NOVO, depois de restaurar o banco (passo 9 do roteiro).
-- A cópia do banco não leva as regras de acesso do esquema "storage"; aqui
-- elas são recriadas exatamente como nas migrações 006 e 007. Pode rodar mais
-- de uma vez.

-- Bucket privado dos anexos e documentos gerados (006 + tipos da 007).
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
        'text/csv',
        'text/plain',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    ]
)
on conflict (id) do update
set
    public = excluded.public,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

-- Cada pessoa só vê, envia e apaga arquivos da própria pasta ({user_id}/...).
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
