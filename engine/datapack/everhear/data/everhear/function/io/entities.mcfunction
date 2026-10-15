# McIo.entities: every entity within r of (x, y, z), living or not, players and markers aside, or only
# those of the types listed; into everhear:out ents a compound each (everhear:io/entity). everhear:io en
# = {d, x, y, z, r, t: [{t: type}, ...] (empty: any type)}.
data modify storage everhear:out ents set value []
execute unless data storage everhear:io en.t[0] run return run function everhear:io/ents_any with storage everhear:io en
data modify storage everhear:io eq set from storage everhear:io en.t
function everhear:io/ents_next
