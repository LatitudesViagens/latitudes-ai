-- Novos campos da ficha do fornecedor (21/10/2026, pedido da Isabelle):
-- - option: Opção 1, 2 ou 3 (ordem de preferência; a ÁGORA mostra a 1 antes);
-- - product_type: tipo de produto GRP, PVT ou FIT;
-- - notes: observação livre (letras, números, o que a pessoa quiser anotar).
-- Todos opcionais. As regras de acesso da 015 não mudam.

alter table public.suppliers
    add column if not exists option smallint,
    add column if not exists product_type text,
    add column if not exists notes text;

alter table public.suppliers
    drop constraint if exists suppliers_option_check,
    drop constraint if exists suppliers_product_type_check,
    drop constraint if exists suppliers_notes_check;

alter table public.suppliers
    add constraint suppliers_option_check
        check (option is null or option between 1 and 3),
    add constraint suppliers_product_type_check
        check (product_type is null or product_type in ('GRP', 'PVT', 'FIT')),
    add constraint suppliers_notes_check
        check (notes is null or char_length(notes) <= 1000);
