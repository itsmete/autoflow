-- Drop one object and every list answer that could have contained it.
--
-- KEYS[1]    -> data key
-- KEYS[2..n] -> index keys the object belongs to
--
-- The index sets are DELeted, not SREMed. An index means "the complete answer to
-- this scope"; pulling one member out leaves a set that still claims completeness
-- while being wrong, and it does nothing at all for a newly CREATED row that was
-- never a member in the first place - which is why new records used to stay
-- invisible in list endpoints until the TTL ran out.
--
-- Deleting is coarse: touching one user drops the cached user list for its tenant
-- and branch. That is the intended trade. Correct-and-cold beats warm-and-wrong.

redis.call("DEL", KEYS[1])

for i = 2, #KEYS do
        redis.call("DEL", KEYS[i])
end

return 1
