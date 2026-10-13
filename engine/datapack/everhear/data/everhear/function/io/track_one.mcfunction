# A tracked entity by its UUID; one that does not exist adds nothing (the engine reads it as gone).
$execute as $(u) run function everhear:io/track_entity
