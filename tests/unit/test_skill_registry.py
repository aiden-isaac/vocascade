import unittest
import os
import sys
import tempfile
import shutil
from vocascade.skills import skill, registry, SkillContext

class TestSkillRegistry(unittest.TestCase):
    def setUp(self):
        # Clear registry before each test
        registry.clear()

    def tearDown(self):
        registry.clear()

    def test_decorator_registration(self):
        @skill(
            name="test_skill",
            examples=["test example 1", "test example 2"],
            keywords=["test_kw"],
        )
        async def dummy_handler(intent, entities, ctx):
            return "hello from dummy"

        registered = registry.get_skill("test_skill")
        self.assertIsNotNone(registered)
        self.assertEqual(registered.name, "test_skill")
        self.assertEqual(registered.examples, ["test example 1", "test example 2"])
        self.assertEqual(registered.keywords, ["test_kw"])
        self.assertEqual(registered.handler, dummy_handler)

    def test_duplicate_name_guard(self):
        @skill(name="duplicate_skill")
        async def handler1(intent, entities, ctx):
            return "1"

        with self.assertRaises(ValueError):
            @skill(name="duplicate_skill")
            async def handler2(intent, entities, ctx):
                return "2"

    def test_available_defaults_to_true(self):
        @skill(name="no_gate")
        async def handler(intent, entities, ctx):
            return "ok"

        self.assertTrue(registry.is_available("no_gate"))

    def test_falsy_gate_is_unavailable(self):
        @skill(name="gated_off", available=lambda: False)
        async def handler(intent, entities, ctx):
            return "ok"

        self.assertFalse(registry.is_available("gated_off"))

    def test_raising_gate_is_unavailable_not_fatal(self):
        def boom():
            raise RuntimeError("broken gate")

        @skill(name="gate_raises", available=boom)
        async def handler(intent, entities, ctx):
            return "ok"

        with self.assertLogs("vocascade.skills.registry", level="WARNING"):
            self.assertFalse(registry.is_available("gate_raises"))

    def test_unregistered_skill_is_unavailable(self):
        self.assertFalse(registry.is_available("never_registered"))

    def test_user_skill_import_isolation(self):
        # Create a temporary directory for user skills
        temp_dir = tempfile.mkdtemp()
        try:
            # 1. Create a valid skill file
            valid_content = """from vocascade.skills import skill
@skill(name="valid_user_skill")
async def valid_handler(intent, entities, ctx):
    return "valid"
"""
            with open(os.path.join(temp_dir, "valid_skill.py"), "w") as f:
                f.write(valid_content)

            # 2. Create a broken skill file (raises an exception on import)
            broken_content = """raise RuntimeError("Simulation of broken import")"""
            with open(os.path.join(temp_dir, "broken_skill.py"), "w") as f:
                f.write(broken_content)

            # Discover user skills from the temp directory
            registry.discover_user_skills(temp_dir)

            # The valid skill should be successfully loaded
            valid_skill = registry.get_skill("valid_user_skill")
            self.assertIsNotNone(valid_skill)
            self.assertEqual(valid_skill.source, "user")

            # The broken skill should not have caused discover_user_skills to crash/abort
            # and other files are imported successfully.
        finally:
            shutil.rmtree(temp_dir)
            # Clean up sys.path and sys.modules
            if temp_dir in sys.path:
                sys.path.remove(temp_dir)
            sys.modules.pop("user_skills.valid_skill", None)
            sys.modules.pop("user_skills.broken_skill", None)

if __name__ == "__main__":
    unittest.main()
