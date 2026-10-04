import ast
import json
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

    # --- GENIE 2.1+: conversational memory, stop control, desktop QoL ---

    def test_conversational_memory_and_stop_control(self):
        s = self.read("genie.py")
        self.assertIn("def run_agent(task, preferred_model=None, history=None)", s)
        self.assertIn("CANCEL_REQUESTED", s)
        self.assertIn("def load_session", s)
        self.assertIn("def save_session", s)
        self.assertIn("def export_session", s)

    def test_cli_session_commands(self):
        s = self.read("genie.py")
        for command in ("/help", "/new", "/history", "/save"):
            self.assertIn(command, s)

    def test_desktop_stop_export_copy(self):
        s = self.read("genie_app.py")
        self.assertIn("def send_or_stop", s)
        self.assertIn("def export_chat", s)
        self.assertIn("def copy_last_answer", s)
        self.assertIn("genie.request_cancel()", s)
        # Deferred tkinter callbacks must not capture the `as exc` variable,
        # which Python deletes at the end of the except block.
        self.assertNotIn("lambda: self.finish(\"\", str(exc)", s)

    # --- GENIE 2.2+: automatic intelligence stack / brain routing ---

    def test_auto_router_architecture(self):
        s = self.read("genie.py")
        for needle in (
            "def classify_task",
            "def load_routing",
            "def build_brain_chain",
            "def provider_available",
            "def call_member",
            "def call_ollama",
            "def run_member_loop",
            "def run_free_pool",
        ):
            self.assertIn(needle, s)

    def test_router_keeps_hard_policy_gate(self):
        s = self.read("genie.py")
        # Every brain tier must flow through the same policy gate.
        self.assertIn("policy.authorize(name, args, approved=POLICY_APPROVED)", s)

    def test_puter_free_selector_prefers_free_variant(self):
        import puter_bridge
        catalog = {"models": [
            {"id": "nousresearch/hermes-4-405b", "cost": {"input": 1, "output": 1}},
            {"id": "nousresearch/hermes-4-405b:free", "cost": {"input": 0, "output": 0}},
        ]}
        self.assertEqual(puter_bridge.resolve_model("nousresearch/hermes-4-405b", catalog), "nousresearch/hermes-4-405b:free")

    def test_team_config_has_routing(self):
        data = json.loads((ROOT / "config" / "ai-team.json").read_text())
        self.assertIn("routing", data)
        chain = data["routing"].get("chain") or {}
        self.assertIn("simple", chain)
        self.assertIn("complex", chain)
        handles = {m.get("handle") for m in data.get("members") or []}
        for task_class, steps in chain.items():
            self.assertTrue(steps, f"empty chain for {task_class}")
            for handle in steps:
                self.assertIn(handle, handles, f"chain handle {handle} missing from roster")

    def test_puter_free_bridge_architecture(self):
        s = self.read("genie.py")
        b = self.read("puter_bridge.py")
        n = self.read("puter_bridge_node.js")
        for needle in ("puter_bridge", "genie puter login", "genie puter models"):
            self.assertIn(needle, s)
        for needle in ("def status", "def models", "def chat", "def login", "PUTER_AUTH_REQUIRED", "_free"):
            self.assertIn(needle, b)
        self.assertIn("puter.ai.chat", n)
        self.assertIn("puter.ai.listModels", n)
        self.assertIn("puter.auth.signIn", n)
        self.assertIn("normalize:true", n)

    def test_team_config_parses(self):
        data = json.loads((ROOT / "config" / "ai-team.json").read_text())
        self.assertIsInstance(data.get("members"), list)
        self.assertTrue(data["members"])

if __name__ == "__main__":
    unittest.main(verbosity=2)
