-- Roda no projeto ANTIGO (só leitura) e gera o SQL que deixa as permissões de
-- anon, authenticated e PUBLIC no schema public do projeto NOVO idênticas às do
-- antigo: tabelas, colunas e funções. A cópia do banco (db dump) não as levou.

with tabelas as (
    select c.oid, format('%I.%I', n.nspname, c.relname) as nome, c.relacl
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public' and c.relkind in ('r', 'p', 'v', 'm', 'S')
),
funcoes as (
    select p.oid, format('%I.%I(%s)', n.nspname, p.proname, pg_get_function_identity_arguments(p.oid)) as nome, p.proacl
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public'
),
comandos as (
    select 1 as ordem, nome, format('revoke all on %s from public, anon, authenticated;', nome) as sql
    from tabelas
    union all
    select 2, t.nome, format(
        'grant %s on %s to %s;',
        string_agg(a.privilege_type, ', ' order by a.privilege_type),
        t.nome,
        case when a.grantee = 0 then 'public' else quote_ident(pg_get_userbyid(a.grantee)) end
    )
    from tabelas t
    cross join lateral aclexplode(coalesce(t.relacl, acldefault('r', 'postgres'::regrole))) a
    where a.grantee = 0 or pg_get_userbyid(a.grantee) in ('anon', 'authenticated')
    group by t.nome, a.grantee
    union all
    select 3, t.nome || '.' || at.attname, format(
        'grant %s (%I) on %s to %s;',
        string_agg(a.privilege_type, ', ' order by a.privilege_type),
        at.attname,
        t.nome,
        case when a.grantee = 0 then 'public' else quote_ident(pg_get_userbyid(a.grantee)) end
    )
    from tabelas t
    join pg_attribute at on at.attrelid = t.oid and at.attnum > 0 and not at.attisdropped and at.attacl is not null
    cross join lateral aclexplode(at.attacl) a
    where a.grantee = 0 or pg_get_userbyid(a.grantee) in ('anon', 'authenticated')
    group by t.nome, at.attname, a.grantee
    union all
    select 4, nome, format('revoke all on function %s from public, anon, authenticated;', nome)
    from funcoes
    union all
    select 5, f.nome, format(
        'grant execute on function %s to %s;',
        f.nome,
        case when a.grantee = 0 then 'public' else quote_ident(pg_get_userbyid(a.grantee)) end
    )
    from funcoes f
    cross join lateral aclexplode(coalesce(f.proacl, acldefault('f', 'postgres'::regrole))) a
    where a.grantee = 0 or pg_get_userbyid(a.grantee) in ('anon', 'authenticated')
)
select sql from comandos order by ordem, nome, sql;
