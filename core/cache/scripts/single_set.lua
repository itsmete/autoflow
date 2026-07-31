local data_key = KEYS[1]
-- KEYS[2...N] -> index keys
local data = ARGV[1]
local ttl = tonumber(ARGV[2])


redis.call("SETEX",data_key,ttl,data)


for i = 2, #KEYS do 
        redis.call("SADD",data_key,data_key)
end