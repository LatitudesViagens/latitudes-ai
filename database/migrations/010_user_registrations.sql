-- 010 — Registro dos cadastros de usuários feitos pelo TI.
--
-- Aplicar manualmente no SQL Editor do Supabase, depois da 009 (aprovada
-- pela Isabelle em 07/10/2026). Pode ser rodada de novo sem erro.
--
-- A conta é criada pela API admin do Supabase (service_role, só no
-- servidor). Aqui fica só o registro de quem cadastrou quem e quando. A
-- obrigação de trocar a senha usa a tabela password_change_required (009),
-- com reset_id vazio.

create table if not exists public.user_registrations (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null
        references auth.users(id) on delete cascade,
    full_name text not null
        check (char_length(trim(full_name)) > 0),
    email text not null
        check (char_length(trim(email)) > 0),
    created_by uuid not null default auth.uid()
        references auth.users(id),
    created_at timestamptz not null default now()
);

create index if not exists user_registrations_created_idx
    on public.user_registrations (created_at desc);

alter table public.user_registrations enable row level security;

revoke all on table public.user_registrations from anon, authenticated;
grant select, insert on table public.user_registrations to authenticated;

drop policy if exists "TI reads user registrations"
    on public.user_registrations;

create policy "TI reads user registrations"
    on public.user_registrations
    for select
    to authenticated
    using (public.is_ti());

drop policy if exists "TI logs user registrations"
    on public.user_registrations;

create policy "TI logs user registrations"
    on public.user_registrations
    for insert
    to authenticated
    with check (
        public.is_ti()
        and created_by = (select auth.uid())
    );
