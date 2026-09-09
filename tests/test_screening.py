import unittest

from drdoc import PdfScreeningSystem, RequestQueue, UploadRequest


class PdfScreeningSystemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.queue = RequestQueue()
        self.system = PdfScreeningSystem(self.queue)

    def test_fishy_pdf_gets_added_to_request_queue(self):
        request = UploadRequest(
            request_id="r1",
            filename="fishy.pdf",
            pdf_bytes=b"%PDF-1.7\n1 0 obj\n<< /OpenAction << /S /JavaScript >> >>",
        )

        result = self.system.process_upload(request)

        self.assertTrue(result)
        self.assertEqual(len(self.queue), 1)
        self.assertEqual(self.queue.items()[0], request)

    def test_clean_pdf_does_not_get_added_to_request_queue(self):
        request = UploadRequest(
            request_id="r2",
            filename="clean.pdf",
            pdf_bytes=b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>",
        )

        result = self.system.process_upload(request)

        self.assertFalse(result)
        self.assertEqual(len(self.queue), 0)

    def test_non_pdf_upload_is_rejected(self):
        request = UploadRequest(
            request_id="r3",
            filename="not-pdf.txt",
            pdf_bytes=b"not a pdf",
        )

        with self.assertRaises(ValueError):
            self.system.process_upload(request)


if __name__ == "__main__":
    unittest.main()
