from actions.meteor import Meteor


class MeteorShower(Meteor):
    """A meteor shower on the players: eight flaming meteors fall at once round each, up to 40 blocks out, each blasting a crater."""

    takes = ["player", "meteor_size", "explosion_power"]
    fixed = {"sky_count": 8, "sky_radius": 40}
