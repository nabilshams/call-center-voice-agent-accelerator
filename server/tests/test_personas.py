"""Tier-3 tests for ``personas.py``.

Guards two behaviours:
1. ``get_random_persona`` picks from the right pool per context (mmh vs travel).
2. ``build_persona_prompt`` injects the persona identity into the system
   prompt in a stable, predictable location (after ``# Role & Objective``
   when that heading exists, else prepended).
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.handler import personas as personas_module
from app.handler.personas import (
    MMH_PERSONAS,
    TRAVEL_PERSONAS,
    build_persona_prompt,
    get_random_persona,
)


# =========================================================================
# Registry sanity
# =========================================================================


class PersonaRegistryTests(unittest.TestCase):
    """Every persona in both pools must satisfy the ``Persona`` TypedDict shape."""

    REQUIRED_KEYS = {"name", "voice", "greeting_style", "personality_notes"}

    def _assert_pool_shape(self, pool: list[dict]) -> None:
        self.assertGreater(len(pool), 0)
        for p in pool:
            self.assertEqual(set(p.keys()), self.REQUIRED_KEYS)
            for key in self.REQUIRED_KEYS:
                self.assertIsInstance(p[key], str)
                self.assertGreater(len(p[key]), 0, f"{key!r} is empty for {p['name']}")

    def test_mmh_pool_shape(self):
        self._assert_pool_shape(MMH_PERSONAS)

    def test_travel_pool_shape(self):
        self._assert_pool_shape(TRAVEL_PERSONAS)

    def test_persona_names_are_unique_per_pool(self):
        mmh_names = [p["name"] for p in MMH_PERSONAS]
        travel_names = [p["name"] for p in TRAVEL_PERSONAS]
        self.assertEqual(len(mmh_names), len(set(mmh_names)))
        self.assertEqual(len(travel_names), len(set(travel_names)))


# =========================================================================
# get_random_persona -- pool dispatch
# =========================================================================


class RandomPersonaTests(unittest.TestCase):
    def test_default_context_returns_mmh_persona(self):
        with patch.object(personas_module.random, "choice", side_effect=lambda pool: pool[0]):
            persona = get_random_persona()
        self.assertEqual(persona, MMH_PERSONAS[0])

    def test_mmh_context_returns_mmh_persona(self):
        with patch.object(personas_module.random, "choice", side_effect=lambda pool: pool[0]):
            persona = get_random_persona("mmh")
        self.assertEqual(persona, MMH_PERSONAS[0])

    def test_travel_context_returns_travel_persona(self):
        with patch.object(personas_module.random, "choice", side_effect=lambda pool: pool[0]):
            persona = get_random_persona("travel")
        self.assertEqual(persona, TRAVEL_PERSONAS[0])

    def test_unknown_context_falls_back_to_mmh(self):
        with patch.object(personas_module.random, "choice", side_effect=lambda pool: pool[0]):
            persona = get_random_persona("unknown-context")
        # Any context that isn't 'travel' picks from MMH.
        self.assertEqual(persona, MMH_PERSONAS[0])

    def test_random_choice_is_called_with_correct_pool(self):
        with patch.object(personas_module.random, "choice") as mock_choice:
            mock_choice.return_value = TRAVEL_PERSONAS[0]
            get_random_persona("travel")
        mock_choice.assert_called_once_with(TRAVEL_PERSONAS)

    def test_returned_persona_is_actually_from_the_pool(self):
        # With the real (unmocked) random, the returned persona must be a
        # member of the correct pool.
        persona = get_random_persona("mmh")
        self.assertIn(persona, MMH_PERSONAS)
        persona = get_random_persona("travel")
        self.assertIn(persona, TRAVEL_PERSONAS)


# =========================================================================
# build_persona_prompt -- injection placement
# =========================================================================


class BuildPromptTests(unittest.TestCase):
    SAMPLE_PERSONA = {
        "name": "Sarah",
        "voice": "en-NZ-MollyNeural",
        "greeting_style": "Kia ora, this is Sarah.",
        "personality_notes": "Warm and reassuring.",
    }

    def test_prompt_contains_persona_name_and_greeting(self):
        base = "# Role & Objective\nYou are a support agent.\nBe helpful."
        result = build_persona_prompt(self.SAMPLE_PERSONA, base)
        self.assertIn("Sarah", result)
        self.assertIn("Kia ora, this is Sarah.", result)
        self.assertIn("Warm and reassuring.", result)

    def test_mmh_context_uses_mmh_support_wording(self):
        base = "# Role & Objective\nline."
        result = build_persona_prompt(self.SAMPLE_PERSONA, base, "mmh")
        self.assertIn("MMH cybersecurity incident helpline", result)
        self.assertNotIn("travel booking", result)

    def test_travel_context_uses_travel_wording(self):
        base = "# Role & Objective\nline."
        result = build_persona_prompt(self.SAMPLE_PERSONA, base, "travel")
        self.assertIn("travel booking assistant team", result)
        self.assertNotIn("MMH", result)

    def test_injection_happens_after_role_objective_heading(self):
        # Persona section must sit between the heading and the rest of the
        # prompt -- not somewhere further down.
        base = (
            "Preamble.\n"
            "# Role & Objective\n"
            "You are the agent.\n"
            "Detailed instructions follow."
        )
        result = build_persona_prompt(self.SAMPLE_PERSONA, base)
        heading_idx = result.index("# Role & Objective")
        persona_idx = result.index("# Your Identity This Session")
        instructions_idx = result.index("You are the agent.")
        self.assertLess(heading_idx, persona_idx)
        # Injection happens before the rest of the instructions body.
        self.assertLess(persona_idx, instructions_idx)

    def test_no_heading_falls_back_to_prepend(self):
        base = "Just plain instructions, no heading."
        result = build_persona_prompt(self.SAMPLE_PERSONA, base)
        # Persona section is prepended; base prompt appears afterwards.
        self.assertTrue(result.strip().startswith("# Your Identity This Session"))
        self.assertIn("Just plain instructions", result)

    def test_original_base_prompt_content_is_preserved(self):
        base = "# Role & Objective\nYou are the agent.\nAdditional rule XYZ."
        result = build_persona_prompt(self.SAMPLE_PERSONA, base)
        # Every original line must survive intact.
        for original_line in ("You are the agent.", "Additional rule XYZ."):
            self.assertIn(original_line, result)


if __name__ == "__main__":
    unittest.main()
