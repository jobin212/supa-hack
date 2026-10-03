-- Run once against a Supabase project with Storage enabled, after the app migration.
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES ('songs', 'songs', false, 30000000, ARRAY['audio/mpeg', 'audio/mp3', 'audio/wav', 'audio/x-wav'])
ON CONFLICT (id) DO NOTHING;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM storage.buckets WHERE id = 'songs' AND public = true) THEN
        RAISE EXCEPTION 'The songs bucket is public; use a dedicated private bucket';
    END IF;
END;
$$;
