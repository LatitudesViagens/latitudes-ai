create or replace function public.touch_conversation_updated_at()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    update public.conversations
    set updated_at = now()
    where id = new.conversation_id;

    return new;
end;
$$;

revoke all
    on function public.touch_conversation_updated_at()
    from public;

drop trigger if exists messages_touch_conversation_updated_at
    on public.messages;

create trigger messages_touch_conversation_updated_at
    after insert on public.messages
    for each row
    execute function public.touch_conversation_updated_at();

update public.conversations as conversation
set updated_at = latest_message.created_at
from (
    select
        conversation_id,
        max(created_at) as created_at
    from public.messages
    group by conversation_id
) as latest_message
where conversation.id = latest_message.conversation_id;