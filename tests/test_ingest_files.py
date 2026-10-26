import pymupdf
import pytest

from src.data_ingestion.files import UnsafeFile, pdf_text, validate_pdf


def make_pdf(text="Rocky Mountain Power\nTotal due: $84.20", pages=1) -> bytes:
    d = pymupdf.open()
    for _ in range(pages):
        d.new_page().insert_text((72, 72), text)
    b = d.tobytes()
    d.close()
    return b


def test_valid_pdf_hashed_and_text_in_memory():
    b = make_pdf()
    assert len(validate_pdf(b)) == 64 and "84.20" in pdf_text(b)


@pytest.mark.parametrize("bad", [b"", b"MZ\x90\x00", b"<html>%PDF-</html>", b"%PDF-1.7\ngarbage"])
def test_rejects_non_pdf_and_malformed(bad):
    with pytest.raises(UnsafeFile):
        validate_pdf(bad)


def test_rejects_active_content_incl_obfuscated_names():
    b = make_pdf()
    for marker in (b"/JavaScript", b"/Java#53cript", b"/Launch", b"/EmbeddedFile"):
        with pytest.raises(UnsafeFile):
            validate_pdf(b + b"\n% " + marker)


def test_rejects_embedded_file_in_object():
    d = pymupdf.open()
    d.new_page()
    d.embfile_add("a.txt", b"x")
    with pytest.raises(UnsafeFile):
        validate_pdf(d.tobytes())


def test_size_and_page_caps():
    with pytest.raises(UnsafeFile):
        validate_pdf(make_pdf(), max_bytes=100)
    with pytest.raises(UnsafeFile):
        validate_pdf(make_pdf(pages=3), max_pages=2)
