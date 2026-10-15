-- Inventário do banco da ÁGORA (SOMENTE LEITURA).
-- Rodar no SQL Editor do projeto ANTIGO (São Paulo) e do NOVO (EUA) e baixar o
-- resultado como CSV ("Export" → CSV). O comparar_inventario.py compara os dois.
-- Não mostra dados: só nomes de objetos e contagens.

select tipo, nome, valor
from (
    -- Linhas de cada tabela do esquema public
    select
        'linhas' as tipo,
        t.table_schema || '.' || t.table_name as nome,
        (xpath(
            '/row/n/text()',
            query_to_xml(format('select count(*) as n from %I.%I', t.table_schema, t.table_name), false, true, '')
        ))[1]::text as valor
    from information_schema.tables as t
    where t.table_schema = 'public'
      and t.table_type = 'BASE TABLE'

    -- Usuários e logins (contagem)
    union all select 'linhas', 'auth.users', count(*)::text from auth.users
    union all select 'linhas', 'auth.identities', count(*)::text from auth.identities

    -- Arquivos e buckets
    union all
    select 'arquivos', coalesce(bucket_id, '(sem bucket)'), count(*)::text
    from storage.objects
    group by bucket_id
    union all
    select 'bytes', coalesce(bucket_id, '(sem bucket)'), coalesce(sum((metadata ->> 'size')::bigint), 0)::text
    from storage.objects
    group by bucket_id
    union all
    select
        'bucket',
        b.id,
        'publico=' || b.public::text
            || ' limite=' || coalesce(b.file_size_limit::text, '-')
            || ' tipos=' || coalesce(array_to_string(array(select unnest(b.allowed_mime_types) order by 1), ','), '-')
    from storage.buckets as b

    -- Regras de acesso (RLS)
    union all
    select 'rls_ligado', schemaname || '.' || tablename, rowsecurity::text
    from pg_tables
    where schemaname = 'public'
    union all
    select
        'politica',
        schemaname || '.' || tablename || ' :: ' || policyname,
        cmd || ' | para=' || array_to_string(roles, ',')
            || ' | usando=' || coalesce(md5(qual), '-')
            || ' | conferindo=' || coalesce(md5(with_check), '-')
    from pg_policies
    where schemaname in ('public', 'storage')

    -- Funções, gatilhos, índices, restrições, permissões
    union all
    select
        'funcao',
        n.nspname || '.' || p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')',
        md5(pg_get_functiondef(p.oid))
    from pg_proc as p
    join pg_namespace as n on n.oid = p.pronamespace
    where n.nspname = 'public'
    union all
    select 'gatilho', event_object_table || ' :: ' || trigger_name, action_timing || ' ' || event_manipulation
    from information_schema.triggers
    where trigger_schema = 'public'
    union all
    select 'indice', schemaname || '.' || indexname, md5(indexdef)
    from pg_indexes
    where schemaname = 'public'
    union all
    select 'restricao', conrelid::regclass::text || ' :: ' || conname, md5(pg_get_constraintdef(oid))
    from pg_constraint
    where connamespace = 'public'::regnamespace
    union all
    select 'permissao', table_schema || '.' || table_name || ' :: ' || grantee, string_agg(privilege_type, ',' order by privilege_type)
    from information_schema.role_table_grants
    where table_schema = 'public'
      and grantee in ('anon', 'authenticated', 'service_role')
    group by table_schema, table_name, grantee
    union all
    select 'colunas', table_schema || '.' || table_name, md5(string_agg(column_name || ':' || data_type || ':' || coalesce(column_default, ''), ',' order by ordinal_position))
    from information_schema.columns
    where table_schema = 'public'
    group by table_schema, table_name

    -- Extensões
    union all
    select 'extensao', extname, extversion
    from pg_extension
) as inventario
order by tipo, nome;
