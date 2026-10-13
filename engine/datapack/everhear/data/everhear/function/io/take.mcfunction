# Takes off the front of the queue every event numbered up to #upto (the queue is in number order),
# then counts what is left.
function everhear:io/take_loop
execute store result score #len everhear.q if data storage everhear:events q[]
