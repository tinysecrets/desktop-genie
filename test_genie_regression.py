import ast
import json
import os
import pathlib
import subprocess
import sys
import tempfile
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
        for name in ("genie.py", "genie_app.py", "genie_ext.py",
                     "genie_workstation.py", "puter_bridge.py", "puter_provider.py"):
            ast.parse(self.read(name), filename=name)

    def test_import_survives_unavailable_external_downloads(self):
        # ~/Downloads can be a removable-drive symlink. A bad target must not
        # stop the assistant from importing; a private config fallback is used.
        with tempfile.TemporaryDirectory() as home:
            (pathlib.Path(home) / "Downloads").write_text("not a directory")
            env = os.environ.copy()
            env["HOME"] = home
            result = subprocess.run(
                [sys.executable, "-c", "import genie_ext; print(genie_ext.DOWNLOADS)"],
                cwd=str(ROOT), env=env, text=True, capture_output=True, timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                pathlib.Path(result.stdout.strip()),
                pathlib.Path(home) / ".config" / "genie" / "downloads",
            )

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
        for command in ("/help", "/new", "/history", "/save", "/puter"):
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

    def test_team_config_parses(self):
        data = json.loads((ROOT / "config" / "ai-team.json").read_text())
        self.assertIsInstance(data.get("members"), list)
        self.assertTrue(data["members"])

    # --- GENIE 2.3+: Puter.js free bridge ---

    def test_puter_bridge_wiring(self):
        s = self.read("genie.py")
        # The Puter tier must use the free bridge and have no paid fallback.
        self.assertIn("def call_puter", s)
        self.assertIn("import puter_bridge", s)
        self.assertIn("catalog = puter_bridge.list_models()", s)
        self.assertIn("puter_bridge.resolve_model(model, catalog)", s)
        self.assertIn("puter_bridge.chat(messages, model=resolved, tools=TOOLS)", s)
        self.assertNotIn("from puter_provider import chat", s)
        self.assertNotIn("using paid REST fallback", s)
        self.assertIn("Paid REST fallback: DISABLED", s)
        self.assertIn("def puter_command", s)
        self.assertIn("def puter_verify", s)
        bridge = self.read("puter_bridge.py")
        self.assertIn("puter.ai.listModels()", bridge)
        self.assertIn("puter.ai.chat(", bridge)
        self.assertIn("normalize: true", bridge)
        self.assertIn("class PuterBridgeError", bridge)

    def test_single_coherent_mascot_theme(self):
        s = self.read("genie_app.py")
        # Mascot-driven palette: tail teal, hair magenta, lamp gold.
        self.assertIn('MAGENTA = "#E452C6"', s)
        self.assertIn('TEAL = "#3BE8D2"', s)
        self.assertIn('GOLD = "#D4AF37"', s)
        # The mascot is drawn as a clean circular medallion.
        self.assertIn("def _medallion", s)
        self.assertIn("ImageDraw.Draw(mask)", s)
        # The competing legacy gold-only look was removed.
        self.assertNotIn("def build_sidebar", s)
        self.assertNotIn("def build_center", s)
        self.assertNotIn("def build_activity", s)

    def test_puter_login_is_robust(self):
        bridge = self.read("puter_bridge.py")
        # Closed auth window must fail fast with a stable code.
        self.assertIn("PUTER_LOGIN_WINDOW_CLOSED", bridge)
        # Reliable in-page fallback when the SDK popup is blocked.
        self.assertIn("https://puter.com/login", bridge)
        # Post sign-in live-catalog verification exists.
        self.assertIn("def verify(", bridge)
        s = self.read("genie.py")
        self.assertIn("genie puter status | login | verify | models", s)

    def test_resolve_model_prefers_free_variant(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "puter_bridge_under_test", ROOT / "puter_bridge.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        catalog = [
            {"id": "hermes-4-405b", "provider": "nousresearch",
             "name": "Hermes 4 405B", "cost": {"input": 100, "output": 400}},
            {"id": "hermes-4-405b:free", "provider": "nousresearch",
             "name": "Hermes 4 405B (free)", "cost": {"input": 0, "output": 0}},
            {"id": "claude-opus-5-5", "provider": "claude",
             "name": "Claude Opus 5.5", "aliases": ["claude-opus-5.5"],
             "cost": {"input": 500, "output": 2500}},
        ]
        # Free-first: the :free variant of the same base model wins.
        self.assertEqual(
            mod.resolve_model("nousresearch/hermes-4-405b", catalog),
            "hermes-4-405b:free")
        # Paid-only and unknown-price models are rejected, never selected.
        self.assertIsNone(mod.resolve_model("anthropic/claude-opus-5.5", catalog))
        self.assertIsNone(mod.resolve_model("mystery/model-x", catalog))
        self.assertIsNone(mod.resolve_model("auto", catalog))
        self.assertIsNone(mod._model_cost({"id": "mystery"}))
        self.assertIsNone(mod._model_cost({"id": "mystery", "cost": {"input": 0}}))
        self.assertIsNone(mod._model_cost({"id": "invalid", "cost": {"input": 1, "output": -1}}))
        self.assertIsNone(mod.resolve_model("auto", catalog))

    def test_desktop_voice_uses_permanent_g8_policy(self):
        app = self.read("genie_app.py")
        self.assertIn("def capture_g8_wav", app)
        self.assertIn("PRIMARY_SOURCE", app)
        self.assertIn("PRIMARY_POLICY", app)
        self.assertNotIn("/home/justin/bin/s22-mic", app)

if __name__ == "__main__":
    unittest.main(verbosity=2)
