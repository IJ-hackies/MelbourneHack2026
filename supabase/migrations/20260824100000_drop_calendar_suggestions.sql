-- Removed entirely, not just hidden in the UI -- calendar_suggestions had
-- no calendar integration behind it at all (no OAuth, no event reading,
-- nothing consumed the value anywhere in the app), so the column was only
-- ever storing a toggle that did nothing.
alter table public.profiles drop column if exists calendar_suggestions;
