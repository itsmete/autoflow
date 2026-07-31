-- Store a COMPLETE scoped list result: the objects plus the index that indexes them.
--
-- KEYS[1]      -> index key '<prefix><scope>'  e.g. 'users/tenant:<uuid>'
-- ARGV[1]      -> ttl in seconds
-- ARGV[2..n]   -> data_key, payload, data_key, payload, ...
--
-- The index is DELeted first so the set is a replacement, never a union with a
-- previous population. Without that, a stale member from an earlier, wider query
-- would survive and be served as part of this scope's answer.
--
-- Runs as one script so the index can never be observed half-built.

local index_key = KEYS[1]
local ttl = tonumber(ARGV[1])

redis.call("DEL", index_key)

local written = 0

for i = 2, #ARGV, 2 do
        local data_key = ARGV[i]
        local payload = ARGV[i + 1]

        redis.call("SETEX", data_key, ttl, payload)
        redis.call("SADD", index_key, data_key)

        written = written + 1
end

-- An empty result writes nothing at all: redis drops a set once its last member is
-- gone, so an "empty complete answer" is indistinguishable from "never cached".
-- Callers must not cache empty lists; they fall through to the database instead.
if written > 0 then
        redis.call("EXPIRE", index_key, ttl)
end

return written
