local results = {}

for i = 1 , #KEYS do
        local members = redis.call("SMEMBERS",KEYS[i])

        for _ , data_key in ipairs(members) do
                local data = redis.call("GET", data_key)
                if data == false then
                        return nil
                end
                table.insert(results,data)
        end
end

return results