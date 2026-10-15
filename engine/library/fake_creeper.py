"""Fake Creeper: the defused creeper horror (and the old herobrine) plants right behind a player,
that hisses for them alone and is gone before it can do anything (it never primes: horror plays the
hiss itself and removes it)."""

from vfg_engine import Actor, ActorConfig


class FakeCreeper(Actor):
    class Config(ActorConfig):
        summary = "A creeper, defused and harmless, that stands behind a player, hisses, and vanishes."
        entity_type = "minecraft:creeper"
        health = 20.0
        size = 1.0
        damage = 0.0
        nbt = "{NoAI:1b,Invulnerable:1b,Silent:1b,ExplosionRadius:0b}"
