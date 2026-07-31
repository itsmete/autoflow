-- Read one scoped list result back.
--
-- KEYS[1] -> index key '<prefix><scope>'
--
-- Returns the payloads, or nil when the cache cannot answer. The caller MUST treat
-- nil as "go to the database" and anything else as the complete answer.
--
-- The EXISTS check is the important part. Previously a missing index produced an
-- empty table, which redis hands back to python as [], and get_cached_queryset only
-- fell back to the database on None - so a cold cache served an empty list to the
-- client as if it were real data.
--
-- A member whose data key has expired means the answer is no longer complete, so the
-- whole read is abandoned rather than returning a short list.

local index_key = KEYS[1]

if redis.call("EXISTS", index_key) == 0 then
        return nil
end

local members = redis.call("SMEMBERS", index_key)
local results = {}

for _, data_key in ipairs(members) do
        local data = redis.call("GET", data_key)
        if data == false then
                -- Partially expired: drop the stale index instead of leaving it to
                -- rot. Index sets carry a TTL too, but a member can expire first.
                redis.call("DEL", index_key)
                return nil
        end
        table.insert(results, data)
end

return results
