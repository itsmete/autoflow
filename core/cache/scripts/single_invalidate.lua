-- KEYS[1] = data_key
-- KEYS[2..n] = index sets

redis.call("DEL",KEYS[1])

for i=2,#KEYS do
        redis.call("SREM",KEYS[i],KEYS[1])
end

return 1