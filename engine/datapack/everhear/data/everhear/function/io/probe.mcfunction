# McIo.probe: many points tested in one call. everhear:io pr = {d: dimension, f: the test (is, not,
# two), b: a block id or #tag, p: [{x,y,z}, ...]}; into everhear:out probe a byte per point, in order:
# is (the block is b), not (it is not b: "solid" is not #everhear:not_solid), two (it and the one above
# are b: "open" is two of #everhear:open). A point in an unloaded chunk is 0b.
data modify storage everhear:out probe set value []
data modify storage everhear:io pq set from storage everhear:io pr.p
execute if data storage everhear:io pq[0] run function everhear:io/probe_next
