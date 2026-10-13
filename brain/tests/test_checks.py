"""The checks of a hand-off and a follow-up (checks.py), which the brain's step and the router's
answer share: every rule, its words exactly as the model is sent them back, the first that fails,
and that they work on anything with the fields (they never import call.py)."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from brain import checks
from brain.call import FollowUp, HandOff

from conftest import catalog, follow_up, hand_off, made


def h(**parts) -> HandOff:
    return HandOff.model_validate(hand_off(**parts))


def f(task: str, brief: str = "", description: str = "", cancel: bool = False) -> FollowUp:
    return FollowUp.model_validate(follow_up(task, brief, description, cancel))


def refused(fn, *args) -> str:
    with pytest.raises(ValueError) as e:
        fn(*args)
    return str(e.value)


def test_good_hand_offs_and_follow_ups_pass():
    checks.hand_offs([], catalog())
    checks.hand_offs([h(), h(name="grappling_hook_alex")], catalog())
    checks.hand_offs([h(name=" grappling_hook_alex ")], catalog())   # the name is read stripped
    checks.follow_ups([])
    checks.follow_ups([f("castle_steve", "a moat round it"), f("hot_fireball_krown", cancel=True)])
    checks.follow_ups([f("castle_steve", "a moat")], {"castle_steve"})
    checks.follow_ups([f(" castle_steve ", "a moat")], ["castle_steve"])  # any collection, the name stripped


@pytest.mark.parametrize("given, words", [
    ([h(name="Hot Fireball")], "hand-off name 'Hot Fireball': lowercase letters, digits and underscores, starting "
                               "with a letter, 2 to 32 characters"),
    ([h(name="x")], "hand-off name 'x': lowercase letters"),
    ([h(name="a" * 33)], "2 to 32 characters"),
    ([h(name="9lives_sam")], "starting with a letter"),
    ([h(name="cast")], "cast is the bridge's task for an incantation's copies, never a task's name: name the "
                       "thing, then whose"),
    ([h(name="railgun")], "railgun is a library action's name: add whose it is, as frost_bow_alex"),
    ([h(assumed=" ", cast="")], "the hand-off hot_fireball_krown's brief has no assumed, cast: write every part"),
    ([h(description="", words="", picture="", assumed="", cast="")],
     "brief has no description, words, picture, assumed, cast"),
    ([h(done_when=[])], "the hand-off hot_fireball_krown has no done_when: the checklist the implementer checks"),
    ([h(done_when=[" ", ""])], "has no done_when"),
    ([h(), h()], "two hand-offs share a name: one name a thing"),
    ([h(), h(name=" hot_fireball_krown")], "two hand-offs share a name"),
])
def test_a_hand_off_is_sent_back_in_the_brains_words(given, words):
    assert words in refused(checks.hand_offs, given, catalog())


def test_a_hand_off_is_checked_in_order_the_first_rule_that_fails_is_the_one_sent_back():
    # a bad name with a brief missing: the name is what goes back, as call.step_type did
    assert "lowercase letters" in refused(checks.hand_offs, [h(name="Bad", picture="")], catalog())
    # two of the same name, the second with no brief: the brief, since each is read before the pair
    assert "brief has no picture" in refused(checks.hand_offs, [h(), h(picture="")], catalog())


def test_a_server_actions_name_is_no_library_name_and_may_be_a_tasks():
    cat = made("lava_moat_sam", '''
        from actions._types import Action, Players


        class LavaMoatSam(Action):
            """Dig a lava moat round a player's base."""

            script = "lava_moat_sam"
            runs = "once"
            takes = ["player"]

            player: Players = "@a"
            """whose base"""
    ''')
    assert cat.source("lava_moat_sam") != "library"
    checks.hand_offs([h(name="lava_moat_sam")], cat)   # code adds _2 when the task opens, not a check


@pytest.mark.parametrize("given, tasks, words", [
    ([f("a_b", cancel=True), f("a_b", cancel=True)], None,
     "a task is followed up twice: one follow-up a task, saying everything"),
    ([f("a_b", "x"), f(" a_b", "y")], None, "followed up twice"),
    ([f(" ", cancel=True)], None, "a follow-up names no task: its name as TASKS shows it"),
    ([f("a_b", description="A bigger thing.")], None,
     "the follow-up of a_b has no brief: what its implementer changes"),
    ([f("a_b", " \n")], None, "has no brief"),
    ([f("castle_sam", "a moat")], {"castle_steve"},
     "task 'castle_sam' is not under TASKS: follow up one that is, or hand it off as new"),
    ([f("castle_sam", cancel=True)], set(), "is not under TASKS"),
])
def test_a_follow_up_is_sent_back_in_the_brains_words(given, tasks, words):
    assert words in refused(checks.follow_ups, given, tasks)


def test_without_tasks_a_follow_up_of_any_name_passes_with_them_only_one_there():
    checks.follow_ups([f("not_a_task_here", "a moat")])           # the tool refuses it: the next step reads why
    checks.follow_ups([f("not_a_task_here", "a moat")], None)
    assert "is not under TASKS" in refused(checks.follow_ups, [f("not_a_task_here", "a moat")], frozenset())


def test_the_brief_is_checked_before_the_task_is_looked_for():
    assert "has no brief" in refused(checks.follow_ups, [f("castle_sam")], {"castle_steve"})


def test_the_checks_read_only_the_fields_so_anything_shaped_like_them_will_do():
    plain = SimpleNamespace(name="grappling_hook_alex", description="A hook.", words="“a hook”", picture="a rod",
                            assumed="flies there", cast="right-click", done_when=["Alex has the rod"])
    checks.hand_offs([plain], catalog())
    plain.cast = ""
    assert "brief has no cast" in refused(checks.hand_offs, [plain], catalog())
    checks.follow_ups([SimpleNamespace(task="castle_steve", brief="", cancel=True)], ("castle_steve",))
    fake = SimpleNamespace(get=lambda name: object(), source=lambda name: "main")  # any catalog shaped like one
    checks.hand_offs([h(name="railgun")], fake)


def test_checks_never_imports_call_which_imports_it():
    tree = ast.parse((Path(checks.__file__)).read_text(encoding="utf-8"))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not {m for m in imported if m and m.split(".")[-1] in ("call", "router", "loop", "act")}
