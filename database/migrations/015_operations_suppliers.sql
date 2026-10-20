-- Fornecedores restritos à equipe de Operações (20/10/2026, pedido da
-- Isabelle). Substitui o modelo da 014 ("qualquer pessoa sugere, o TI
-- aprova, todos leem os aprovados"):
--
-- | Quem          | Cadastra/edita/retira | Lê o cadastro | Vê a base (respostas) |
-- | Coordenadores | sim                   | sim           | não                   |
-- | Operadoras    | não                   | sim (só pela conversa com a ÁGORA) | não |
-- | Demais        | não                   | não           | não                   |
-- | TI            | sim                   | sim           | sim                   |
--
-- O que os coordenadores cadastram já entra aprovado (não há mais fila).
-- A equipe de Operações fica numa tabela própria: user_roles aceita um
-- papel por pessoa, e alguém do TI também pode ser coordenador.

-- 1. Equipe de Operações ------------------------------------------------------
create table if not exists public.operations_team (
    user_id uuid primary key
        references auth.users(id) on delete cascade,
    level text not null
        check (level in ('coordenador', 'operadora')),
    created_by uuid
        references auth.users(id) on delete set null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

drop trigger if exists operations_team_touch_updated_at
    on public.operations_team;

create trigger operations_team_touch_updated_at
    before update on public.operations_team
    for each row
    execute function public.touch_updated_at();

alter table public.operations_team enable row level security;

revoke all on table public.operations_team from public, anon, authenticated;
grant select, insert, update, delete
    on table public.operations_team
    to authenticated;

-- Cada pessoa vê o próprio nível; o TI vê e muda a equipe toda.
drop policy if exists "Read own level or all if TI" on public.operations_team;

create policy "Read own level or all if TI"
    on public.operations_team
    for select
    to authenticated
    using (user_id = (select auth.uid()) or public.is_ti());

drop policy if exists "TI manages operations team" on public.operations_team;

create policy "TI manages operations team"
    on public.operations_team
    for all
    to authenticated
    using (public.is_ti())
    with check (public.is_ti());

-- Checagens usadas pelas políticas e pela ÁGORA (rodam no banco).
create or replace function public.is_operations()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select exists (
        select 1
        from public.operations_team
        where user_id = (select auth.uid())
    );
$$;

create or replace function public.is_operations_coordinator()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select exists (
        select 1
        from public.operations_team
        where user_id = (select auth.uid())
          and level = 'coordenador'
    );
$$;

revoke execute on function public.is_operations() from public, anon;
grant execute on function public.is_operations() to authenticated;
revoke execute on function public.is_operations_coordinator() from public, anon;
grant execute on function public.is_operations_coordinator() to authenticated;


-- 2. Perfil do fornecedor -----------------------------------------------------
alter table public.suppliers
    add column if not exists profile text;

alter table public.suppliers
    drop constraint if exists suppliers_profile_check;

alter table public.suppliers
    add constraint suppliers_profile_check
        check (profile is null or profile in ('Luxo', 'Superior', 'Intermediário', 'Econômico'));


-- 3. Regras de acesso dos fornecedores (trocam as da 014) ---------------------
drop policy if exists "Read approved, own or all if TI" on public.suppliers;
drop policy if exists "Users suggest suppliers" on public.suppliers;
drop policy if exists "TI reviews suppliers" on public.suppliers;
drop policy if exists "TI deletes suppliers" on public.suppliers;

-- Ler: só Operações (coordenadores e operadoras) e TI.
create policy "Operations and TI read suppliers"
    on public.suppliers
    for select
    to authenticated
    using (public.is_ti() or public.is_operations());

-- Cadastrar: coordenadores e TI, já aprovado e em nome de quem cadastra.
create policy "Coordinators and TI add suppliers"
    on public.suppliers
    for insert
    to authenticated
    with check (
        (public.is_ti() or public.is_operations_coordinator())
        and suggested_by = (select auth.uid())
    );

-- Editar e retirar: coordenadores e TI.
create policy "Coordinators and TI edit suppliers"
    on public.suppliers
    for update
    to authenticated
    using (public.is_ti() or public.is_operations_coordinator())
    with check (public.is_ti() or public.is_operations_coordinator());

create policy "Coordinators and TI delete suppliers"
    on public.suppliers
    for delete
    to authenticated
    using (public.is_ti() or public.is_operations_coordinator());
