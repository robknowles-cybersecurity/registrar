"""The two-parsers hazard, closed.

cpebot reads bodies.yaml with PyYAML when the interpreter has it and with
cpebot/_minyaml.py when it does not. Two parsers means two chances to read
the file differently, and a rules file that means one thing on the laptop
and another thing on the box is a compliance bug wearing a portability
costume.

So: when PyYAML is importable, parse bodies.yaml both ways and require the
resulting structures to be identical. When it is not importable, the
fallback is the only parser in play and there is nothing to compare, so the
test skips loudly rather than passing quietly.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cpebot import _minyaml
from cpebot import bodies as B

BODIES = B.DEFAULT_PATH

try:
    import yaml
    HAVE_PYYAML = True
except ImportError:
    HAVE_PYYAML = False


class TestParserEquivalence(unittest.TestCase):

    @unittest.skipUnless(HAVE_PYYAML, "PyYAML absent: only one parser in play")
    def test_both_parsers_produce_identical_structures(self):
        with open(BODIES, encoding="utf-8") as fh:
            from_pyyaml = yaml.safe_load(fh)
        from_min = _minyaml.load_path(BODIES)

        # Compare top-level keys first so a failure names the section.
        self.assertEqual(sorted(from_pyyaml), sorted(from_min))
        for key in sorted(from_pyyaml):
            self.assertEqual(from_pyyaml[key], from_min[key],
                             "divergence under top-level key %r" % key)
        self.assertEqual(from_pyyaml, from_min)

    def test_fallback_parser_validates_the_shipped_rules(self):
        rules = B.Rules(_minyaml.load_path(BODIES), path=BODIES, parser="minyaml")
        rules.validate()
        self.assertEqual(len(rules.credentials), 7)


class TestFallbackParserSubset(unittest.TestCase):
    """Direct tests of the subset reader, including what it must refuse."""

    def test_scalars(self):
        d = _minyaml.loads(
            'a: 1\nb: 2.5\nc: "hi"\nd: true\ne: false\nf: null\ng: ~\nh: bare\n'
        )
        self.assertEqual(d, {"a": 1, "b": 2.5, "c": "hi", "d": True,
                             "e": False, "f": None, "g": None, "h": "bare"})

    def test_flow_lists(self):
        d = _minyaml.loads('a: []\nb: ["x", "y"]\nc: [1, 2, 3]\n')
        self.assertEqual(d, {"a": [], "b": ["x", "y"], "c": [1, 2, 3]})

    def test_nested_maps_and_sequences(self):
        d = _minyaml.loads(
            "top:\n"
            "  items:\n"
            "    - id: \"one\"\n"
            "      n: 1\n"
            "    - id: \"two\"\n"
            "      n: 2\n"
            "  flag: true\n"
        )
        self.assertEqual(d, {"top": {"items": [{"id": "one", "n": 1},
                                               {"id": "two", "n": 2}],
                                     "flag": True}})

    def test_sequence_at_the_same_indent_as_its_key(self):
        d = _minyaml.loads("key:\n- 1\n- 2\n")
        self.assertEqual(d, {"key": [1, 2]})

    def test_comments_are_stripped_but_not_inside_strings(self):
        d = _minyaml.loads('# lead\na: 1  # trailing\nb: "has # inside"\n')
        self.assertEqual(d, {"a": 1, "b": "has # inside"})

    def test_colon_inside_a_quoted_value_is_not_a_key_split(self):
        d = _minyaml.loads('url: "https://example.org/a:b"\n')
        self.assertEqual(d, {"url": "https://example.org/a:b"})

    def test_escapes(self):
        d = _minyaml.loads('a: "say \\"hi\\""\nb: "one\\ntwo"\n')
        self.assertEqual(d, {"a": 'say "hi"', "b": "one\ntwo"})

    def test_tab_indentation_is_rejected(self):
        with self.assertRaises(_minyaml.MinYamlError):
            _minyaml.loads("a:\n\tb: 1\n")

    def test_a_line_that_is_not_a_mapping_is_rejected(self):
        with self.assertRaises(_minyaml.MinYamlError):
            _minyaml.loads("a: 1\njust a bare line\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
