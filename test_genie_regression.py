import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent

class GenieRegression(unittest.TestCase):
    def read(self, name):
        return (ROOT / name).read_text()

    def test_re_and_tts(self):
        s = self.read("genie_app.py")
        self.assertIn("import re", s)
        self.assertIn("self.speak_answer(answer)", s)

    def test_tk_images_are_main_thread_created(self):
        s = self.read("genie_app.py")
        self.assertNotIn("photo = ImageTk.PhotoImage(img)", s)
        self.assertIn("ImageTk.PhotoImage(", s)

    def test_local_browser_is_headless(self):
        s = self.read("genie_app.py")
        self.assertIn('"headless": True', s)
        self.assertNotIn('"headless": False', s)

    def test_core_audit_and_safe_git_parsing(self):
        s = self.read("genie.py")
        self.assertIn("shlex.split(command)", s)
        self.assertIn("workstation.audit(", s)

    def test_sources_parse(self):
        for name in ("genie.py", "genie_app.py", "genie_ext.py", "genie_workstation.py"):
            ast.parse(self.read(name), filename=name)

if __name__ == "__main__":
    unittest.main(verbosity=2)
