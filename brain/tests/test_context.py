"""The shared context, built by hand: feed lines read into rows, the processed marks, and the
minute-blocks the prompt cache depends on."""

from brain.context import Context, Row, parse, show

from conftest import NOW, rows


def test_feed_lines_become_rows_and_unknown_types_stay_text():
    line = parse("LINE#812 18:22:31 Steve voice action_oriented 0.93 -8dB | build me a castle", now=NOW)
    assert (line.seq, line.at, line.who, line.source, line.cls, line.text) == \
        (812, "2026-10-10T18:22:31.000Z", "Steve", "voice", "action_oriented", "build me a castle")
    assert line.show() == "18:22:31 Steve (action_oriented): build me a castle"
    typed = parse("LINE#813 18:22:33 Alex chat context 0.71 - was=not_relevant | that cave | by spawn", now=NOW)
    assert typed.text == "that cave | by spawn"
    assert typed.show() == "18:22:33 Alex (typed, context): that cave | by spawn"
    game = parse("GAME#814 18:22:40 death Steve | Steve was slain by Zombie", now=NOW)
    assert (game.kind, game.who, game.show()) == ("death", "Steve", "18:22:40 game, death: Steve was slain by Zombie")
    assert parse("SYS#816 18:25:00 | jev unavailable", now=NOW).show() == "18:25:00 system: jev unavailable"
    ai = parse("AI#817 18:25:02 task galick_gun_styrk | new: A Galick Gun for Styrkaeopteryx", now=NOW)
    assert ai.processed and ai.task == "galick_gun_styrk"  # the brain's own rows are read as they are written
    assert ai.show() == "18:25:02 AI task [galick_gun_styrk]: new: A Galick Gun for Styrkaeopteryx"
    assert parse("AI#818 18:25:03 result castle_steve | castle_steve done: castle up", now=NOW).show() == \
        "18:25:03 AI result: castle_steve done: castle up"
    action = parse("AI#819 18:25:03 action - | command: give Steve diamond_pickaxe — ok: Gave 1", now=NOW)
    assert (action.task, action.show()) == ("", "18:25:03 AI action: command: give Steve diamond_pickaxe — ok: Gave 1")
    assert parse("AI#820 18:25:04 say - | (to Steve) hey", now=NOW).show() == "18:25:04 AI: (to Steve) hey"
    orch = parse("ORCH#815 18:24:02 castle done | castle built", now=NOW)
    assert (orch.type, orch.seq, orch.show()) == ("", 815, "ORCH#815 18:24:02 castle done | castle built")
    assert parse("SYS#0 18:26:00 | vfg watch: reconnected", now=NOW).seq == 0
    assert parse("not a feed line", now=NOW) is None and parse("garbage", seq=9, at="x").text == "garbage"
    # a line from just before midnight, read just after it, is yesterday's
    assert parse("SYS#1 23:59:58 | late", now=NOW.replace(hour=0, minute=0, second=5)).at == "2026-10-09T23:59:58.000Z"


def test_a_read_takes_what_is_unread_once_and_the_ai_rows_are_read_already():
    ctx = rows("LINE#1 18:20:00 Steve voice context 0.9 - | I'm at the cave",
               "AI#2 18:20:01 say - | nice",
               "GAME#3 18:20:02 join Alex | Alex joined the game",
               "LINE#4 18:20:03 Alex voice action_oriented 0.9 - | give me a sword")
    assert not ctx.add(parse("GAME#3 18:20:02 join Alex | again", now=NOW))  # a replayed seq is not added twice
    assert [r.seq for r in ctx.take_unprocessed()] == [1, 3, 4]
    assert ctx.take_unprocessed() == [] and ctx.unprocessed() == []
    ctx.add(parse("GAME#5 18:29:00 death Alex | Alex fell", now=NOW))
    assert [r.seq for r in ctx.take_before("2026-10-10T18:28:00.000Z")] == []
    assert [r.seq for r in ctx.take_before("2026-10-10T18:29:30.000Z")] == [5]


def minute(m: int, seq: int) -> Row:
    return Row(seq, f"2026-10-10T18:{m:02d}:05.000Z", "SYS", f"row {seq}")


def test_the_conversation_is_minute_blocks_marked_at_stable_checkpoints():
    ctx = Context([minute(m, i) for i, m in enumerate([0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
                                                       16, 17, 18, 19, 20, 21], 1)])
    first = ctx.cached("OPENING", 5)
    assert first[0] == ("OPENING", True) and first[1] == ("18:00:05 system: row 1\n18:00:05 system: row 2\n", False)
    # 22 minute-blocks, the last still open: marks on blocks 5, 10, 15, 20, the last three of them
    assert [i for i, (_, marked) in enumerate(first) if marked] == [0, 10, 15, 20]
    assert sum(m for _, m in first) <= 4 and not first[-1][1]
    # the next rows grow the open block and open new ones: everything up to the last mark is the same
    ctx.add(minute(21, 24))
    ctx.add(minute(22, 25))
    second = ctx.cached("OPENING", 5)
    assert second[:21] == first[:21]
    assert second[22] == ("18:21:05 system: row 23\n18:21:05 system: row 24\n", False)
    # and once 25 blocks are closed the marks move on: 10, 15, 20 kept, 25 not yet closed
    for seq, m in enumerate(range(23, 26), 26):
        ctx.add(minute(m, seq))
    third = ctx.cached("OPENING", 5)
    assert [i for i, (_, marked) in enumerate(third) if marked] == [0, 15, 20, 25]
    assert [text for text, _ in third[:22]] == [text for text, _ in first[:22]]


def test_a_context_line_is_kept_but_never_shown_to_a_model():
    ctx = rows("LINE#1 18:20:00 Alex voice context 0.8 - | I'm by the lava pool",
               "GAME#2 18:20:01 death Alex | Alex tried to swim in lava",
               "LINE#3 18:21:00 Alex voice context 0.8 - | anyway",
               "LINE#4 18:22:00 Steve voice conversational 0.9 - | rip Alex")
    assert [r.seq for r in ctx.rows] == [1, 2, 3, 4] and [r.shown for r in ctx.rows] == [False, True, False, True]
    conversation = "".join(text for text, _ in ctx.cached("OPENING", 5))
    assert "lava pool" not in conversation and "anyway" not in conversation
    assert [[r.seq for r in b] for b in ctx.blocks()] == [[2], [4]]  # 18:21's block was the context line alone
    assert [r.seq for r in ctx.take_quiet()] == [1, 3] and ctx.take_quiet() == []  # marked processed
    assert show(ctx.rows) == "18:20:01 game, death: Alex tried to swim in lava\n18:22:00 Steve (conversational): rip Alex"
    assert [r.seq for r in ctx.take_unprocessed()] == [2, 4]
