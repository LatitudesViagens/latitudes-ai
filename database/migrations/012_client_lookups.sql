-- 012 — Registro das consultas de perfil de cliente (RD Station CRM + Envision).
--
-- Aplicar manualmente no SQL Editor do Supabase, depois da 011. Pode ser
-- rodada de novo sem erro.
--
-- Regra de segurança (docs/regras-integracao-clientes.md): toda consulta é
-- registrada — quem perguntou, sobre qual cliente e quando. Guarda só o que
-- está na lista de dados permitidos (nome e e-mail do cliente); NUNCA o CPF.
-- Só o TI lê. Ninguém altera nem apaga pela interface.

create table if not exists public.client_lookups (
    id uuid primary key default gen_random_uuid(),
    user_id uuid default auth.uid()
        references auth.users(id) on delete set null,
    conversation_id uuid
        references public.conversations(id) on delete set null,
    search_text text not null
        check (char_length(trim(search_text)) > 0),
    client_name text,
    client_email text,
    systems text[] not null default '{}',
    outcome text not null
        check (outcome in ('encontrado', 'candidatos', 'nao_encontrado', 'erro')),
    created_at timestamptz not null default now()
);

create index if not exists client_lookups_created_idx
    on public.client_lookups (created_at desc);

alter table public.client_lookups enable row level security;

revoke all on table public.client_lookups from anon, authenticated;
grant select, insert on table public.client_lookups to authenticated;

drop policy if exists "Users log their client lookups" on public.client_lookups;

create policy "Users log their client lookups"
    on public.client_lookups
    for insert
    to authenticated
    with check (user_id = (select auth.uid()));

drop policy if exists "TI reads client lookups" on public.client_lookups;

create policy "TI reads client lookups"
    on public.client_lookups
    for select
    to authenticated
    using (public.is_ti());
