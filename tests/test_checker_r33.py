import unittest, os, sys, tempfile, json, hashlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import checker as c

NOW = 10000
RULE_R33 = {"id": "R33", "check": "dm_topic_notes", "threshold": 0, "severity": "medium", "owner": "all"}


def E(ident, ts=0, author="agent", topic="group_topic", kind="msg", text=""):
    return {"id": ident, "ts": ts, "author": author, "topic": topic, "kind": kind, "text": text}


class R33Test(unittest.TestCase):
    def test_dm_topic_check_a_flags_agent_msg_and_card(self):
        dm_topics = ["DM_TEST_TOPIC_1"]
        ev_msg = [E("e1", author="agent", topic="DM_TEST_TOPIC_1", kind="msg", text="hello")]
        flags_msg = c.dm_topic_notes(RULE_R33, ev_msg, NOW, dm_topics=dm_topics)
        self.assertEqual(len(flags_msg), 1)
        self.assertEqual(flags_msg[0]["id"], "e1")
        self.assertEqual(flags_msg[0]["rule"], "R33")
        self.assertEqual(flags_msg[0]["owner"], "all")

        ev_card = [E("e2", author="agent", topic="DM_TEST_TOPIC_1", kind="card", text="card content")]
        flags_card = c.dm_topic_notes(RULE_R33, ev_card, NOW, dm_topics=dm_topics)
        self.assertEqual(len(flags_card), 1)
        self.assertEqual(flags_card[0]["id"], "e2")

        # user or auto author on DM topic does not trigger Check A
        ev_user = [E("e3", author="user", topic="DM_TEST_TOPIC_1", kind="msg", text="user msg")]
        flags_user = c.dm_topic_notes(RULE_R33, ev_user, NOW, dm_topics=dm_topics)
        self.assertEqual(flags_user, [])

    def test_group_message_without_banned_phrase_clean(self):
        dm_topics = ["DM_TEST_TOPIC_1"]
        ev = [E("e1", author="agent", topic="group_topic_1", kind="msg", text="Everything is running smoothly.")]
        flags = c.dm_topic_notes(RULE_R33, ev, NOW, dm_topics=dm_topics)
        self.assertEqual(flags, [])

    def test_banned_phrases_check_b_flags_any_topic(self):
        dm_topics = ["DM_TEST_TOPIC_1"]
        phrases_to_test = [
            "execution isn't mine",
            "Execution is not mine",
            "execution isnt mine",
            "التنفيذ مش بتاعي",
            "التنفيذ ليس بتاعي",
        ]
        for i, phrase in enumerate(phrases_to_test):
            ev = [E(f"eb_{i}", author="user", topic="public_topic", kind="msg", text=f"Note: {phrase} here.")]
            flags = c.dm_topic_notes(RULE_R33, ev, NOW, dm_topics=dm_topics)
            self.assertEqual(len(flags), 1, f"Failed to flag banned phrase: {phrase}")
            self.assertEqual(flags[0]["id"], f"eb_{i}")
            # Ensure no secret or text is stored in flag
            self.assertEqual(set(flags[0].keys()), {"rule", "owner", "id", "age_min", "count"})

    def test_missing_config_degrades_r33(self):
        rules = [RULE_R33]
        ev = [E("e1")]
        out = c.run(rules, ev, NOW, dm_topics=None)
        self.assertIn("R33", out["unproven_or_failed"])
        self.assertEqual(out["flags"], [])

    def test_empty_dm_topics_config_runs_r33(self):
        rules = [RULE_R33]
        ev = [E("e1", topic="group_topic", text="Normal text")]
        out = c.run(rules, ev, NOW, dm_topics=[])
        self.assertNotIn("R33", out["unproven_or_failed"])
        self.assertEqual(out["flags"], [])


if __name__ == "__main__":
    unittest.main()
