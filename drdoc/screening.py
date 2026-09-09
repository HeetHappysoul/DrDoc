from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class UploadRequest:
    request_id: str
    filename: str
    pdf_bytes: bytes


class RequestQueue:
    def __init__(self) -> None:
        self._queue = deque()

    def enqueue(self, request: UploadRequest) -> None:
        self._queue.append(request)

    def __len__(self) -> int:
        return len(self._queue)

    def items(self):
        return tuple(self._queue)


class PdfScreeningSystem:
    _fishy_tokens = (
        b"/JavaScript",
        b"/JS",
        b"/Launch",
        b"/OpenAction",
        b"/EmbeddedFile",
        b"/URI",
        b"powershell",
        b"cmd.exe",
    )

    def __init__(self, request_queue: RequestQueue) -> None:
        self.request_queue = request_queue

    def process_upload(self, request: UploadRequest) -> bool:
        if not request.pdf_bytes.startswith(b"%PDF"):
            raise ValueError("Uploaded file is not a PDF")

        is_fishy = any(token in request.pdf_bytes for token in self._fishy_tokens)
        if is_fishy:
            self.request_queue.enqueue(request)

        return is_fishy
