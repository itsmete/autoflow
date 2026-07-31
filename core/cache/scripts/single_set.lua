-- Cache ONE object under its data key.
--
-- KEYS[1] -> data key   '<prefix><pk>'
-- ARGV[1] -> serialized payload
-- ARGV[2] -> ttl in seconds
--
-- Deliberately does NOT touch any index set. A detail fetch knows about exactly one
-- object, so it cannot say anything about the completeness of a list. Adding itself
-- to 'users/all' would fabricate a one-member "complete" answer that bulk_get would
-- then serve instead of querying the database. Index sets are written by bulk_set only.
--
-- (The previous version ran `SADD data_key data_key` in a loop over the index keys:
--  it never wrote the index, and SADD onto the string just written by SETEX raised
--  WRONGTYPE, which handle_script_error then swallowed.)

local data_key = KEYS[1]
local data = ARGV[1]
local ttl = tonumber(ARGV[2])

redis.call("SETEX", data_key, ttl, data)

return 1
