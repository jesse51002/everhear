# The engine's one read a tick (vfg_engine.mc.McIo.events): the queue's next page (everhear:io/events)
# and the snapshot (everhear:io/snap), both into everhear:out, which one `data get` then reads.
$function everhear:io/events {upto:$(upto)}
function everhear:io/snap
