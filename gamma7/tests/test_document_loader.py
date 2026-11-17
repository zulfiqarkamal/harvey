"""
Tests for gamma7.engine.document_loader
"""
import pathlib
import sys
import tempfile
import textwrap

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))

from gamma7.engine.document_loader import load, LoadedDocument


def test_load_markdown(tmp_path):
    f = tmp_path / "contract.md"
    f.write_text("# Contract\n\nSome text.", encoding="utf-8")
    doc = load(f)
    assert doc.format == "markdown"
    assert "Some text" in doc.text
    assert str(f) == doc.source_path


def test_load_txt(tmp_path):
    f = tmp_path / "contract.txt"
    f.write_text("Plain text contract.", encoding="utf-8")
    doc = load(f)
    assert doc.format == "text"
    assert "Plain text" in doc.text


def test_load_unknown_extension_treated_as_text(tmp_path):
    f = tmp_path / "contract.contract"
    f.write_text("Some contract text.", encoding="utf-8")
    doc = load(f)
    assert "Some contract text" in doc.text


def test_load_missing_file_raises():
    import pytest
    with pytest.raises(FileNotFoundError):
        load("/nonexistent/path/contract.md")


def test_load_preserves_unicode(tmp_path):
    f = tmp_path / "contract.md"
    f.write_text("Leverandøren skal overholde DORA-kravene.", encoding="utf-8")
    doc = load(f)
    assert "Leverandøren" in doc.text


def test_load_returns_loaded_document_type(tmp_path):
    f = tmp_path / "contract.md"
    f.write_text("Test content.")
    doc = load(f)
    assert isinstance(doc, LoadedDocument)
    assert doc.text
    assert doc.source_path
    assert doc.format
