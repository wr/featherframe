-- The admin page's household controls (W-860): a suspended household's page
-- is closed to its owner and its server is no longer woken.
ALTER TABLE households ADD COLUMN suspended_at INTEGER;
