update storage.buckets
set allowed_mime_types = array[
    'image/jpeg',
    'image/png',
    'image/webp',
    'application/pdf',
    'text/csv',
    'text/plain',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
]
where id = 'chat-attachments';
