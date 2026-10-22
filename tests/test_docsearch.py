"""
Local document search: index, passages with source, incremental updates,
work files skipped, results treated as untrusted.

    .venv\\Scripts\\python.exe -m unittest tests.test_docsearch -v
"""
import os
import sys
import time
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import desktop      # noqa: E402
import docsearch    # noqa: E402
import jarvis_mcp   # noqa: E402
import policy       # noqa: E402

ROOT = desktop.FILE_ROOTS[0]


class Index(unittest.TestCase):
    def setUp(self):
        if docsearch.DB.exists():
            docsearch.DB.unlink()
        (ROOT / "Handbuecher").mkdir(parents=True, exist_ok=True)
        self.manual = ROOT / "Handbuecher" / "Waschmaschine.md"
        self.manual.write_text("# Waschmaschine\n\nDas Flusensieb unten rechts alle vier Wochen reinigen.\n"
                               + "Fülltext. " * 300 + "\nFehlercode E21: Abpumpen gestört.\n", encoding="utf-8")
        with zipfile.ZipFile(ROOT / "Garantie.docx", "w") as z:
            z.writestr("word/document.xml", "<w:document><w:body><w:p><w:r><w:t>Garantie bis 2028 für den "
                                            "Trockner</w:t></w:r></w:p></w:body></w:document>")
        (ROOT / "Kunde Contoso Vertrag.txt").write_text("Vertrag mit Flusensieb-Klausel", encoding="utf-8")

    def test_search_with_sources(self):
        n, _, skipped = docsearch.index()
        self.assertGreaterEqual(n, 2)
        self.assertGreaterEqual(skipped, 1)                       # the work file
        out = docsearch.search("Flusensieb reinigen")
        self.assertIn("Waschmaschine.md (Zeichen 0)", out)
        self.assertIn("»Flusensieb«", out)
        self.assertNotIn("Contoso", out)
        self.assertIn("(Zeichen 2400)", docsearch.search("Fehlercode E21"))   # third chunk, cited with its position
        self.assertIn("Garantie.docx", docsearch.search("Garantie Trockner"))

    def test_incremental(self):
        docsearch.index()
        self.assertEqual(docsearch.index()[0], 0)                 # nothing changed
        time.sleep(0.05)
        self.manual.write_text("Neue Anleitung: Türdichtung trocknen lassen.", encoding="utf-8")
        os.utime(self.manual, (time.time() + 5, time.time() + 5))
        self.assertEqual(docsearch.index()[0], 1)
        self.assertIn("Waschmaschine.md", docsearch.search("Türdichtung"))
        self.assertIn("Nichts", docsearch.search("Flusensieb"))
        self.manual.unlink()
        self.assertGreaterEqual(docsearch.index()[1], 1)          # removed from the index
        self.assertIn("Nichts", docsearch.search("Türdichtung"))

    def test_status_and_empty(self):
        self.assertIn("noch leer", docsearch.search("irgendwas"))
        docsearch.index()
        st = docsearch.status()
        self.assertIn("Dokumente", st)
        if not docsearch.pdf_available():
            self.assertIn("pypdf nicht installiert", st)

    @unittest.skipUnless(docsearch.pdf_available(), "pypdf not installed")
    def test_pdf_with_page(self):
        pages = ["Inhaltsverzeichnis", "Entkalken: alle drei Monate mit Zitronensaeure"]
        objs, kids = [], []
        objs.append("<< /Type /Catalog /Pages 2 0 R >>")
        objs.append(None)                                   # pages, filled below
        for i, txt in enumerate(pages):
            content = f"BT /F1 12 Tf 20 100 Td ({txt}) Tj ET"
            objs.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")
            objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 200] /Contents {len(objs)} 0 R "
                        f"/Resources << /Font << /F1 {2 + 2 * len(pages) + 1} 0 R >> >> >>")
            kids.append(f"{len(objs)} 0 R")
        objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>"
        objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
        out, offsets = b"%PDF-1.4\n", []
        for n, body in enumerate(objs, 1):
            offsets.append(len(out))
            out += f"{n} 0 obj\n{body}\nendobj\n".encode("latin-1")
        xref = len(out)
        out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
        out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
        out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
        (ROOT / "Kaffeemaschine.pdf").write_bytes(out)
        docsearch.index()
        self.assertIn("Kaffeemaschine.pdf (Seite 2)", docsearch.search("entkalken"))
        self.assertIn("PDF wird gelesen", docsearch.status())

    def test_untrusted(self):
        docsearch.index()
        jarvis_mcp.TAINTED.update(on=False, by="")
        out = jarvis_mcp.handle_call("doc_search", {"query": "Flusensieb"})
        self.assertTrue(out.startswith("«FREMDE DATEN aus doc_search"))
        self.assertTrue(jarvis_mcp.TAINTED["on"])
        jarvis_mcp.TAINTED.update(on=False, by="")
        self.assertIn("doc_search", policy.UNTRUSTED)


if __name__ == "__main__":
    unittest.main()
