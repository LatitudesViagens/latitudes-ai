-- 011 — Exclusão de usuários pelo TI preservando o que é coletivo.
--
-- Aplicar manualmente no SQL Editor do Supabase, depois da 010, somente
-- após aprovação da Isabelle. Pode ser rodada de novo sem erro.
--
-- Ao excluir uma conta (API admin do Supabase), o banco apaga em cascata:
--   - as conversas e mensagens da pessoa (decisão da Isabelle, 07/10/2026);
--   - o papel TI e a obrigação de troca de senha.
-- E PRESERVA, sem o vínculo com a pessoa ou com a conversa:
--   - roteiros publicados na memória coletiva;
--   - respostas da base de conhecimento (sugeridas ou revisadas por ela);
--   - modelos de prompt que ela criou ou editou;
--   - histórico de gastos (tentativas e chamadas avulsas);
--   - registros de auditoria (resets, cadastros).
-- Antes, várias dessas ligações eram "on delete cascade" (apagavam junto) ou
-- sem regra (impediam a exclusão de quem já tinha feito ações de TI).


-- 1. Memória coletiva: o roteiro continua publicado sem a conversa de origem.
alter table public.shared_itineraries
    alter column source_conversation_id drop not null,
    alter column source_message_id drop not null,
    alter column created_by drop not null;

alter table public.shared_itineraries
    drop constraint if exists shared_itineraries_source_conversation_id_fkey,
    drop constraint if exists shared_itineraries_source_message_id_fkey,
    drop constraint if exists shared_itineraries_created_by_fkey;

alter table public.shared_itineraries
    add constraint shared_itineraries_source_conversation_id_fkey
        foreign key (source_conversation_id)
        references public.conversations(id) on delete set null,
    add constraint shared_itineraries_source_message_id_fkey
        foreign key (source_message_id)
        references public.messages(id) on delete set null,
    add constraint shared_itineraries_created_by_fkey
        foreign key (created_by)
        references auth.users(id) on delete set null;


-- 2. Base de conhecimento: a resposta continua na base.
alter table public.knowledge_entries
    alter column suggested_by drop not null;

alter table public.knowledge_entries
    drop constraint if exists knowledge_entries_suggested_by_fkey,
    drop constraint if exists knowledge_entries_reviewed_by_fkey;

alter table public.knowledge_entries
    add constraint knowledge_entries_suggested_by_fkey
        foreign key (suggested_by)
        references auth.users(id) on delete set null,
    add constraint knowledge_entries_reviewed_by_fkey
        foreign key (reviewed_by)
        references auth.users(id) on delete set null;


-- 3. Modelos de prompt: continuam disponíveis.
alter table public.prompt_templates
    alter column created_by drop not null;

alter table public.prompt_templates
    drop constraint if exists prompt_templates_created_by_fkey,
    drop constraint if exists prompt_templates_updated_by_fkey;

alter table public.prompt_templates
    add constraint prompt_templates_created_by_fkey
        foreign key (created_by)
        references auth.users(id) on delete set null,
    add constraint prompt_templates_updated_by_fkey
        foreign key (updated_by)
        references auth.users(id) on delete set null;


-- 4. Gastos: o custo continua no painel, sem a conversa apagada.
alter table public.message_attempts
    alter column message_id drop not null,
    alter column conversation_id drop not null;

alter table public.message_attempts
    drop constraint if exists message_attempts_message_id_fkey,
    drop constraint if exists message_attempts_conversation_id_fkey;

alter table public.message_attempts
    add constraint message_attempts_message_id_fkey
        foreign key (message_id)
        references public.messages(id) on delete set null,
    add constraint message_attempts_conversation_id_fkey
        foreign key (conversation_id)
        references public.conversations(id) on delete set null;

alter table public.ai_usage
    alter column user_id drop not null;

alter table public.ai_usage
    drop constraint if exists ai_usage_user_id_fkey;

alter table public.ai_usage
    add constraint ai_usage_user_id_fkey
        foreign key (user_id)
        references auth.users(id) on delete set null;


-- 5. Auditoria: resets e cadastros continuam no histórico.
alter table public.password_resets
    alter column target_user_id drop not null,
    alter column reset_by drop not null;

alter table public.password_resets
    drop constraint if exists password_resets_target_user_id_fkey,
    drop constraint if exists password_resets_reset_by_fkey;

alter table public.password_resets
    add constraint password_resets_target_user_id_fkey
        foreign key (target_user_id)
        references auth.users(id) on delete set null,
    add constraint password_resets_reset_by_fkey
        foreign key (reset_by)
        references auth.users(id) on delete set null;

alter table public.user_registrations
    alter column user_id drop not null,
    alter column created_by drop not null;

alter table public.user_registrations
    drop constraint if exists user_registrations_user_id_fkey,
    drop constraint if exists user_registrations_created_by_fkey;

alter table public.user_registrations
    add constraint user_registrations_user_id_fkey
        foreign key (user_id)
        references auth.users(id) on delete set null,
    add constraint user_registrations_created_by_fkey
        foreign key (created_by)
        references auth.users(id) on delete set null;


-- 6. Registro das exclusões (quem excluiu quem e quando). Guarda o nome e o
--    e-mail para o histórico continuar legível depois que a conta some.
create table if not exists public.user_deletions (
    id uuid primary key default gen_random_uuid(),
    deleted_user_id uuid not null,
    full_name text,
    email text not null
        check (char_length(trim(email)) > 0),
    deleted_by uuid default auth.uid()
        references auth.users(id) on delete set null,
    created_at timestamptz not null default now()
);

create index if not exists user_deletions_created_idx
    on public.user_deletions (created_at desc);

alter table public.user_deletions enable row level security;

revoke all on table public.user_deletions from anon, authenticated;
grant select, insert on table public.user_deletions to authenticated;

drop policy if exists "TI reads user deletions" on public.user_deletions;

create policy "TI reads user deletions"
    on public.user_deletions
    for select
    to authenticated
    using (public.is_ti());

drop policy if exists "TI logs user deletions" on public.user_deletions;

create policy "TI logs user deletions"
    on public.user_deletions
    for insert
    to authenticated
    with check (
        public.is_ti()
        and deleted_by = (select auth.uid())
    );
