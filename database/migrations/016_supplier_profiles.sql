-- Perfis do fornecedor trocados (20/10/2026, pedido da Isabelle): VIP,
-- BUDGET e SERVIÇOS SEPARADOS no lugar de Luxo/Superior/Intermediário/
-- Econômico. Os antigos viram: Luxo e Superior → VIP; Intermediário e
-- Econômico → BUDGET.
--
-- O campo estrelas sai da ficha (tela, planilha e ÁGORA), mas a coluna
-- fica no banco, sem uso: apagar perderia o dado sem ganho nenhum.
-- As regras de acesso da 015 não mudam.

alter table public.suppliers
    drop constraint if exists suppliers_profile_check;

update public.suppliers
set profile = case
    when profile in ('Luxo', 'Superior') then 'VIP'
    when profile in ('Intermediário', 'Econômico') then 'BUDGET'
    else profile
end
where profile is not null;

alter table public.suppliers
    add constraint suppliers_profile_check
        check (profile is null or profile in ('VIP', 'BUDGET', 'SERVIÇOS SEPARADOS'));
