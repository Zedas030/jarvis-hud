"""Remote access: only paired devices get in through the tailscale port."""
import http.client
import json
import os
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402  (must come before any Jarvis module)

import devices  # noqa: E402
devices.PATH = TMP / "devices.json"
import server   # noqa: E402

HOST = "jarvis-pc.tail1234.ts.net"


class Remote(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.remote = ThreadingHTTPServer(("127.0.0.1", 0), server.RemoteHandler)
        cls.local = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        server.PORT = cls.local.server_address[1]
        for s in (cls.remote, cls.local):
            threading.Thread(target=s.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        for s in (cls.remote, cls.local):
            s.shutdown()

    def setUp(self):
        devices.PATH.unlink(missing_ok=True)
        devices._CODE.update(code=None, until=0, tries=0)
        server.REMOTE_HOSTS.clear()

    def req(self, method, path, body=None, host=HOST, origin=None, cookie=None, local=False, token=False):
        srv = self.local if local else self.remote
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        h = {"Host": host}
        if body is not None:
            h["Content-Type"] = "application/json"
        if origin:
            h["Origin"] = origin
        if cookie:
            h["Cookie"] = cookie
        if token:
            h["X-Jarvis-Token"] = server.API_TOKEN
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, data

    def code(self):
        local = f"127.0.0.1:{server.PORT}"
        r, data = self.req("POST", "/api/pair/start", {}, host=local, origin="http://" + local, local=True, token=True)
        self.assertEqual(r.status, 200)
        return json.loads(data)["code"]

    def pair(self, code, origin="https://" + HOST):
        return self.req("POST", "/api/pair", {"code": code, "name": "iPhone"}, origin=origin)

    def test_unpaired_is_sent_to_pairing(self):
        r, _ = self.req("GET", "/")
        self.assertEqual((r.status, r.getheader("Location")), (302, "/pair.html"))
        self.assertEqual(self.req("GET", "/pair.html")[0].status, 200)
        for icon in ("/favicon.svg", "/icon-32.png", "/icon-192.png"):   # the pairing page and manifest show them
            self.assertEqual(self.req("GET", icon)[0].status, 200, icon)
        self.assertEqual(self.req("GET", "/api/status")[0].status, 401)
        self.assertEqual(self.req("GET", "/api/dashboard")[0].status, 401)
        self.assertEqual(self.req("POST", "/api/run", {"message": "hi"}, origin="https://" + HOST)[0].status, 401)

    def test_foreign_host_rejected(self):
        self.assertEqual(self.req("GET", "/pair.html", host="evil.example.com")[0].status, 403)
        server.REMOTE_HOSTS.add("other.ts.net")
        self.assertEqual(self.req("GET", "/pair.html")[0].status, 403)

    def test_pc_only_endpoints_closed_remotely(self):
        for p in ("/api/pair/start", "/api/devices", "/api/permission/request", "/api/internal"):
            self.assertEqual(self.req("POST", p, {}, origin="https://" + HOST)[0].status, 403, p)

    def test_refused_post_does_not_spoil_the_connection(self):
        # The iPad's status post was refused unread; the next request on the same
        # kept-alive connection began with that JSON and failed with 400.
        c = http.client.HTTPConnection("127.0.0.1", self.remote.server_address[1], timeout=5)
        h = {"Host": HOST, "Origin": "https://" + HOST, "Content-Type": "application/json"}
        c.request("POST", "/api/hud", body='{"visible": false, "st": "off"}', headers=h)
        r = c.getresponse()
        r.read()
        self.assertEqual(r.status, 403)
        c.request("GET", "/pair.html", headers={"Host": HOST})
        self.assertEqual(c.getresponse().status, 200)
        c.close()

    def test_restart_allowed_from_a_paired_device(self):
        self.assertNotIn("/api/restart", server.LOCAL_ONLY)

    def test_pairing_flow_and_revoke(self):
        code = self.code()
        self.assertEqual(self.pair(code, origin="https://evil.example.com")[0].status, 403)
        self.assertEqual(self.pair("000000" if code != "000000" else "111111")[0].status, 403)
        r, _ = self.pair(code)
        self.assertEqual(r.status, 200)
        cookie = r.getheader("Set-Cookie").split(";")[0]
        self.assertIn("HttpOnly", r.getheader("Set-Cookie"))
        self.assertEqual(self.pair(code)[0].status, 403, "a code works only once")
        r, data = self.req("GET", "/", cookie=cookie)
        self.assertEqual(r.status, 200)
        self.assertIn(server.API_TOKEN.encode(), data)
        r, data = self.req("GET", "/api/status", cookie=cookie)
        self.assertTrue(json.loads(data)["remote"])
        did = devices.listing()[0]["id"]
        self.assertTrue(devices.revoke(did))
        self.assertEqual(self.req("GET", "/", cookie=cookie)[0].status, 302)

    def test_each_device_keeps_its_own_view(self):
        import commands
        commands.update_state(lambda s: (s.pop("dashboard_layouts", None), s.__setitem__("dashboard_layout", "zen")))
        r, _ = self.pair(self.code())
        cookie = r.getheader("Set-Cookie").split(";")[0]
        local = f"127.0.0.1:{server.PORT}"

        def view(remote):
            if remote:
                return json.loads(self.req("GET", "/api/layout", cookie=cookie)[1])["layout"]
            return json.loads(self.req("GET", "/api/layout", host=local, local=True)[1])["layout"]

        self.assertEqual((view(False), view(True)), ("zen", "zen"), "the old shared choice is where both start")
        r, _ = self.req("POST", "/api/layout", {"layout": "tablet"}, cookie=cookie, origin="https://" + HOST, token=True)
        self.assertEqual(r.status, 200)
        r, _ = self.req("POST", "/api/layout", {"layout": "orbit"}, host=local, origin="http://" + local, local=True, token=True)
        self.assertEqual(r.status, 200)
        self.assertEqual((view(False), view(True)), ("orbit", "tablet"), "the PC's pick must not move the iPad")
        r, data = self.req("GET", "/", cookie=cookie)
        self.assertIn(b'name="jarvis-layout" content="tablet"', data)
        r, _ = self.req("POST", "/api/layout", {"layout": "nope"}, host=local, origin="http://" + local, local=True, token=True)
        self.assertEqual(r.status, 400)

    def test_code_burns_after_wrong_guesses(self):
        code = self.code()
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(devices.CODE_TRIES):
            self.pair(wrong)
        self.assertEqual(self.pair(code)[0].status, 403)

    def test_only_hash_is_stored(self):
        devices.start_pairing()
        token = devices.pair(devices._CODE["code"], "iPad")
        self.assertNotIn(token, devices.PATH.read_text(encoding="utf-8"))
        self.assertTrue(devices.check(token))
        self.assertIsNone(devices.check(token + "x"))


if __name__ == "__main__":
    unittest.main()
