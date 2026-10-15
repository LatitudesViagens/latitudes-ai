-- Slides gerados pela ÁGORA (generate_document com formato "pptx") são
-- salvos no mesmo bucket dos anexos. Só acrescenta o tipo à lista da 007;
-- as políticas de acesso (cada pessoa só vê a própria pasta) não mudam.
update storage.buckets
set allowed_mime_types = array[
    'image/jpeg',
    'image/png',
    'image/webp',
    'application/pdf',
    'text/csv',
    'text/plain',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation'
]
where id = 'chat-attachments';
