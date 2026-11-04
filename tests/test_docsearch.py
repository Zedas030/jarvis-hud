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
        (ROOT / "Manuals").mkdir(parents=True, exist_ok=True)
        self.manual = ROOT / "Manuals" / "Washing machine.md"
        self.manual.write_text("# Washing machine\n\nClean the lint filter at the bottom right every four weeks.\n"
                               + "Filler text. " * 240 + "\nError code E21: pumping out disturbed.\n", encoding="utf-8")
        with zipfile.ZipFile(ROOT / "Warranty.docx", "w") as z:
            z.writestr("word/document.xml", "<w:document><w:body><w:p><w:r><w:t>Warranty until 2028 for the "
                                            "dryer</w:t></w:r></w:p></w:body></w:document>")
        (ROOT / "Customer Contoso contract.txt").write_text("Contract with lint filter clause", encoding="utf-8")

    def test_search_with_sources(self):
        n, _, skipped = docsearch.index()
        self.assertGreaterEqual(n, 2)
        self.assertGreaterEqual(skipped, 1)                       # the work file
        out = docsearch.search("clean lint filter")
        self.assertIn("Washing machine.md (character 0)", out)
        self.assertIn("»lint«", out)
        self.assertNotIn("Contoso", out)
        self.assertIn("(character 2400)", docsearch.search("error code E21"))   # third chunk, cited with its position
        self.assertIn("Warranty.docx", docsearch.search("warranty dryer"))

    def test_incremental(self):
        docsearch.index()
        self.assertEqual(docsearch.index()[0], 0)                 # nothing changed
        time.sleep(0.05)
        self.manual.write_text("New instructions: let the door seal dry.", encoding="utf-8")
        os.utime(self.manual, (time.time() + 5, time.time() + 5))
        self.assertEqual(docsearch.index()[0], 1)
        self.assertIn("Washing machine.md", docsearch.search("door seal"))
        self.assertIn("Nothing", docsearch.search("lint filter"))
        self.manual.unlink()
        self.assertGreaterEqual(docsearch.index()[1], 1)          # removed from the index
        self.assertIn("Nothing", docsearch.search("door seal"))

    def test_status_and_empty(self):
        self.assertIn("still empty", docsearch.search("anything"))
        docsearch.index()
        st = docsearch.status()
        self.assertIn("documents", st)
        if not docsearch.pdf_available():
            self.assertIn("pypdf not installed", st)

    @unittest.skipUnless(docsearch.pdf_available(), "pypdf not installed")
    def test_pdf_with_page(self):
        pages = ["Table of contents", "Descaling: every three months with citric acid"]
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
        (ROOT / "Coffee machine.pdf").write_bytes(out)
        docsearch.index()
        self.assertIn("Coffee machine.pdf (page 2)", docsearch.search("descaling"))
        self.assertIn("PDFs are read", docsearch.status())

    def test_untrusted(self):
        docsearch.index()
        jarvis_mcp.TAINTED.update(on=False, by="")
        out = jarvis_mcp.handle_call("doc_search", {"query": "lint filter"})
        self.assertTrue(out.startswith("«THIRD-PARTY DATA from doc_search"))
        self.assertTrue(jarvis_mcp.TAINTED["on"])
        jarvis_mcp.TAINTED.update(on=False, by="")
        self.assertIn("doc_search", policy.UNTRUSTED)


if __name__ == "__main__":
    unittest.main()
