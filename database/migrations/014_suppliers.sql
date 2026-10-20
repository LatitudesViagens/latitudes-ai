-- Fornecedores na base de conhecimento (20/10/2026).
--
-- Ficha curta, combinada com a Isabelle: nome, cidade, país, estrelas
-- (quando houver), serviço e contato geral da empresa. Sem propostas,
-- preços, documentos ou contatos pessoais: tudo o que é aprovado fica
-- visível para todos que usam a ÁGORA.
--
-- Mesmo ciclo da base de conhecimento (009): qualquer pessoa SUGERE (entra
-- como pendente), só o TI aprova, edita, rejeita ou apaga, e todos leem os
-- aprovados (a ÁGORA consulta antes de sugerir fornecedores da internet).
-- Quem sugeriu ou revisou pode ter a conta excluída sem apagar a ficha
-- (on delete set null, como na 011).

create table if not exists public.suppliers (
    id uuid primary key default gen_random_uuid(),
    name text not null
        check (char_length(trim(name)) between 1 and 200),
    city text
        check (city is null or char_length(city) <= 120),
    country text not null
        check (char_length(trim(country)) between 1 and 80),
    stars smallint
        check (stars is null or stars between 1 and 5),
    service text not null
        check (char_length(trim(service)) between 1 and 300),
    contact text
        check (contact is null or char_length(contact) <= 300),
    status text not null default 'pendente'
        check (status in ('pendente', 'aprovada', 'rejeitada')),
    source_message_id uuid
        references public.messages(id) on delete set null,
    source_file text
        check (source_file is null or char_length(source_file) <= 200),
    suggested_by uuid default auth.uid()
        references auth.users(id) on delete set null,
    reviewed_by uuid
        references auth.users(id) on delete set null,
    reviewed_at timestamptz,
    review_note text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists suppliers_status_idx
    on public.suppliers (status, created_at desc);

create index if not exists suppliers_source_message_idx
    on public.suppliers (source_message_id);

drop trigger if exists suppliers_touch_updated_at
    on public.suppliers;

create trigger suppliers_touch_updated_at
    before update on public.suppliers
    for each row
    execute function public.touch_updated_at();

alter table public.suppliers enable row level security;

revoke all on table public.suppliers from public, anon, authenticated;
grant select, insert, update, delete
    on table public.suppliers
    to authenticated;

-- Todos leem os aprovados; cada pessoa vê as próprias sugestões; o TI vê tudo.
drop policy if exists "Read approved, own or all if TI"
    on public.suppliers;

create policy "Read approved, own or all if TI"
    on public.suppliers
    for select
    to authenticated
    using (
        status = 'aprovada'
        or suggested_by = (select auth.uid())
        or public.is_ti()
    );

-- Qualquer pessoa pode SUGERIR (entra como pendente, sem revisão).
drop policy if exists "Users suggest suppliers" on public.suppliers;

create policy "Users suggest suppliers"
    on public.suppliers
    for insert
    to authenticated
    with check (
        suggested_by = (select auth.uid())
        and (
            public.is_ti()
            or (
                status = 'pendente'
                and reviewed_by is null
                and reviewed_at is null
            )
        )
    );

-- Só o TI aprova, edita, rejeita ou apaga.
drop policy if exists "TI reviews suppliers" on public.suppliers;

create policy "TI reviews suppliers"
    on public.suppliers
    for update
    to authenticated
    using (public.is_ti())
    with check (public.is_ti());

drop policy if exists "TI deletes suppliers" on public.suppliers;

create policy "TI deletes suppliers"
    on public.suppliers
    for delete
    to authenticated
    using (public.is_ti());
