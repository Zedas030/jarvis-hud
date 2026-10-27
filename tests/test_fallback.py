"""
Fallback chain (fallback_llm.py) against a fake OpenAI-style provider — no
network, no keys. The Jarvis tools run for real (jarvis_mcp.py as a child) on
the temp vault from isolate.py.

    .venv\\Scripts\\python.exe -m unittest tests.test_fallback -v
"""
import json
import os
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

os.environ["JARVIS_PERM_URL"] = "http://127.0.0.1:9/api/permission/request"   # nobody confirms
os.environ["JARVIS_PORT"] = "9"
import fallback_llm   # noqa: E402

# per provider: list of canned replies, consumed in order; each is (status, body, headers)
SCRIPT = {}
SEEN = []


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        name = self.path.strip("/").split("/")[0]
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        SEEN.append((name, body))
        status, reply, headers = SCRIPT[name].pop(0)
        if body.get("stream") and status == 200:
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            for piece in reply["choices"][0]["message"]["content"].split(" "):
                ev = {"choices": [{"delta": {"content": piece + " "}}]}
                self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return
        data = json.dumps(reply).encode()
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def text(t):
    return (200, {"choices": [{"message": {"content": t}}],
                  "usage": {"prompt_tokens": 100, "completion_tokens": 10}}, None)


def tool_call(name, args):
    return (200, {"choices": [{"message": {"content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}}]}, None)


class Chain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.saved = {k: dict(v) for k, v in fallback_llm.PROVIDERS.items()}
        for name in ("groq", "gemini"):
            fallback_llm.PROVIDERS[name].update(url=f"{base}/{name}/chat", key="test")
        cls.order = list(fallback_llm.ORDER)
        fallback_llm.ORDER[:] = ["groq", "gemini"]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        fallback_llm.PROVIDERS.update(cls.saved)
        fallback_llm.ORDER[:] = cls.order

    def setUp(self):
        SCRIPT.clear()
        SEEN.clear()
        fallback_llm._COOL.clear()

    def run_chain(self, groups=(), private=False, message="Hallo"):
        evs = list(fallback_llm.run("Du bist Jarvis.", "", message, groups, private))
        return evs, "".join(e.get("text", "") for e in evs if e["t"] == "delta")

    def test_plain_answer_streams(self):
        SCRIPT["groq"] = [text("Guten Tag, Sir.")]
        evs, reply = self.run_chain()
        self.assertEqual(reply.strip(), "Guten Tag, Sir.")
        self.assertEqual(evs[0]["name"], "groq")
        self.assertNotIn("tools", SEEN[0][1])

    def test_limit_skips_to_next_and_cools_down(self):
        SCRIPT["groq"] = [(429, {"error": {"message": "Rate limit. Please try again in 2m30s."}}, None)]
        SCRIPT["gemini"] = [text("Hier Gemini.")]
        evs, reply = self.run_chain()
        self.assertEqual(reply.strip(), "Hier Gemini.")
        self.assertIn("groq", fallback_llm._COOL)
        self.assertAlmostEqual(fallback_llm._COOL["groq"]["until"] - time.time(), 150, delta=5)
        self.assertEqual(fallback_llm.chain(), ["gemini"])     # groq skipped until its retry time

    def test_retry_after_header_and_gemini_delay(self):
        class E:
            headers = {"retry-after": "42"}
        self.assertEqual(fallback_llm._retry_seconds(E(), ""), 42)
        E.headers = {}
        self.assertEqual(fallback_llm._retry_seconds(E(), '{"retryDelay": "17s"}'), 17)
        self.assertEqual(fallback_llm._retry_seconds(E(), "kaputt"), 600)

    def test_bad_key_cools_for_hours(self):
        SCRIPT["groq"] = [(401, {"error": "invalid key"}, None)]
        SCRIPT["gemini"] = [text("ok")]
        self.run_chain()
        self.assertGreater(fallback_llm._COOL["groq"]["until"] - time.time(), 3 * 3600)

    def test_private_turn_never_reaches_gemini(self):
        self.assertEqual(fallback_llm.chain(["mail"]), ["groq"])
        self.assertEqual(fallback_llm.chain([], private=True), ["groq"])
        self.assertEqual(fallback_llm.chain(["weather"]), ["groq", "gemini"])
        SCRIPT["groq"] = [(429, {}, {"retry-after": "60"})]
        evs, _ = self.run_chain(["calendar"])
        self.assertEqual(evs[-1]["t"], "error")
        self.assertEqual([n for n, _ in SEEN], ["groq"])

    def test_tool_loop_runs_real_jarvis_tool(self):
        SCRIPT["groq"] = [tool_call("memory_search", {"query": "Anna"}), text("Nichts über Anna gemerkt.")]
        evs, reply = self.run_chain(["memory"], message="Was weißt du über Anna?")
        self.assertEqual(reply, "Nichts über Anna gemerkt.")
        names = {t["function"]["name"] for t in SEEN[0][1]["tools"]}
        self.assertEqual(names, {"memory_search", "memory_read", "memory_save"})
        tool_msg = SEEN[1][1]["messages"][-1]
        self.assertEqual(tool_msg["role"], "tool")
        self.assertTrue(tool_msg["content"])
        self.assertIn("mcp__jarvis__memory_search", [e.get("name") for e in evs if e["t"] == "tool"])
        usage = next(e for e in evs if e["t"] == "usage")
        self.assertEqual(usage["api_calls"], 2)

    def test_confirmation_needed_tool_is_denied_without_hud(self):
        import datetime as dt   # a day to come: a past one is refused before any card is asked for
        soon = f"{dt.date.today() + dt.timedelta(days=30)} 10:00"
        SCRIPT["groq"] = [tool_call("calendar_add", {"title": "Zahnarzt", "start": soon}),
                          text("Das wurde nicht bestätigt.")]
        self.run_chain(["calendar"], message="Trag Zahnarzt ein")
        result = SEEN[1][1]["messages"][-1]["content"]
        self.assertTrue("Abgelehnt" in result or "nicht bestätigt" in result, result)

    def test_no_handover_after_an_acting_tool(self):
        SCRIPT["groq"] = [tool_call("memory_save", {"note": "Test", "text": "x"}), (503, {"error": "down"}, None)]
        SCRIPT["gemini"] = [text("doppelt")]
        evs, reply = self.run_chain(["memory"])
        self.assertEqual(reply, "")
        self.assertEqual(evs[-1]["t"], "error")
        self.assertNotIn("gemini", [n for n, _ in SEEN])

    def test_handover_after_read_only_tool(self):
        SCRIPT["groq"] = [tool_call("memory_search", {"query": "x"}), (429, {}, {"retry-after": "60"})]
        SCRIPT["gemini"] = [text("Hier Gemini.")]
        evs, reply = self.run_chain(["memory"])
        self.assertEqual(reply, "Hier Gemini.")

    def test_typographic_spaces_are_plain(self):
        SCRIPT["groq"] = [text("6 Wochen, CPU‑Temp")]
        _, reply = self.run_chain()
        self.assertEqual(reply.strip(), "6 Wochen, CPU-Temp")

    def test_profile_never_reaches_gemini(self):
        system = ("Du bist JARVIS.\nDer User heißt Max.\n\nWas du über den User weißt:\nProfil:\n- Mail: max@example.org"
                  "\n\nWeitere Notizen (memory_read): Wissen\n\nJede Nachricht beginnt mit einem <kontext>-Block.")
        prefix = "<kontext>\nJetzt: x\n\nPassende Notizen:\n- Max mag Golf\n</kontext>"
        SCRIPT["groq"] = [(429, {}, {"retry-after": "60"})]
        SCRIPT["gemini"] = [text("ok")]
        list(fallback_llm.run(system, prefix, "Wie wird das Wetter?", ["weather"]))
        gemini = [b for n, b in SEEN if n == "gemini"][0]
        sent = json.dumps(gemini["messages"], ensure_ascii=False)
        self.assertNotIn("Max", sent)
        self.assertNotIn("max@example.org", sent)
        self.assertIn("<kontext>-Block", sent)
        groq = [b for n, b in SEEN if n == "groq"][0]
        self.assertIn("max@example.org", json.dumps(groq["messages"], ensure_ascii=False))

    def test_gemini_safe_schemas(self):
        for spec in fallback_llm.tool_specs(["systems", "home"]):
            params = spec["function"].get("parameters")
            self.assertTrue(params is None or params.get("properties"), spec)

    def test_unsupported_groups_are_dropped(self):
        SCRIPT["groq"] = [text("ok")]
        self.run_chain(["pc"])
        self.assertNotIn("tools", SEEN[0][1])


if __name__ == "__main__":
    unittest.main()
