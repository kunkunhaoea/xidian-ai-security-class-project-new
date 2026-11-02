import json
from pathlib import Path

import pytest

from adapters.shared_files import identity


@pytest.mark.parametrize("name", ["../other", "/tmp", "A", "topic/a", ".hidden", "x"*65])
def test_invalid_topics(store, name):
    with pytest.raises(ValueError):
        store.create(name, "不能创建")


@pytest.mark.parametrize("path", ["../topic-b/README.md", "/etc/passwd", "materials/../../topic-b", "materials\\x", ".private"])
def test_path_boundaries(store, path):
    with pytest.raises(ValueError):
        store.path("topic-a", path)


def test_symlinks_are_not_read_or_written(store, tmp_path):
    (store.topic("topic-a") / "materials").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        store.archive("topic-a", {"title": "x", "url": "https://example.org"}, "arxiv")
    with pytest.raises(ValueError):
        store.path("topic-a", "materials/README.md")


def test_dedup_edited_files_and_topic_isolation(store):
    row = {"title": "memory", "abstract": "old abstract", "url": "https://arxiv.org/abs/2401.12345v1", "author": "作者", "access_token": "hidden"}
    result = store.archive("topic-a", row, "arxiv")
    path = store.path("topic-a", result["path"] + "/abstract.md")
    path.write_text("USER_EDIT_SENTINEL", encoding="utf-8")
    again = store.archive("topic-a", {**row, "url": "https://arxiv.org/abs/2401.12345v2"}, "arxiv")
    assert again["path"] == result["path"] and not again["new"]
    assert path.read_text() == "USER_EDIT_SENTINEL"
    assert not store.materials("topic-b")
    assert "hidden" not in store.path("topic-a", result["path"] + "/raw.json").read_text()
    assert "USER_EDIT_SENTINEL" in store.read_inputs("topic-a", [result["path"] + "/abstract.md"])["documents"][0]["text"]
    assert "全文未下载" in store.materials("topic-a")[0]["status"]
    assert any("摘要" in w for w in store.read_inputs("topic-a", [result["path"] + "/abstract.md"])["warnings"])


def test_long_unicode_names_and_preserved_title(store):
    title = "中文😀" * 200
    folder = store.archive("topic-a", {"title": title, "abstract": "test", "id": "id1"}, "arxiv")["path"]
    for _ in range(3):
        store.write_new("topic-a", folder, title + ".meta.json", "{}")
    for p in store.topic("topic-a").rglob("*"):
        assert len(p.name.encode("utf-8")) < 255
    assert title in store.path("topic-a", folder + "/来源.md").read_text()


def test_no_implicit_inputs_and_unsupported_warning(store):
    with pytest.raises(ValueError):
        store.read_inputs("topic-a", [])
    a = store.write_new("topic-a", "notes", "a.md", "valid text")
    b = store.write_new("topic-a", "notes", "unsupported.bin", b"binary")
    snapshot = store.read_inputs("topic-a", [a,b])
    assert len(snapshot["documents"]) == 1
    assert "不支持" in snapshot["warnings"][0]


def test_different_urls_not_merged_by_title(store):
    a = store.archive("topic-a", {"title":"same", "url":"https://example.com/a"}, "web")
    b = store.archive("topic-a", {"title":"same", "url":"https://example.com/b"}, "web")
    assert a["path"] != b["path"]
    assert identity({"url":"https://arxiv.org/pdf/2401.12345v2.pdf"},"arxiv") == identity({"url":"https://arxiv.org/abs/2401.12345"},"arxiv")


def test_current_pdf_and_corrupt_pdf(store):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    from io import BytesIO
    writer = PdfWriter()
    page = writer.add_blank_page(width=300,height=300)
    font = DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
    stream = DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 10 100 Td (PDF_SENTINEL) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    buffer=BytesIO();writer.write(buffer)
    good=store.write_new('topic-a','materials/m001-pdf','paper.pdf',buffer.getvalue())
    bad=store.write_new('topic-a','materials/m001-pdf','bad.pdf',b'invalid pdf')
    snapshot=store.read_inputs('topic-a',[good,bad])
    assert 'PDF_SENTINEL' in snapshot['documents'][0]['text']
    assert any('bad.pdf' in w for w in snapshot['warnings'])


def test_truncation_is_explicit_and_reads_selected_only(store):
    path=store.write_new('topic-a','notes','long.md','x'*61000)
    snapshot=store.read_inputs('topic-a',[path])
    assert snapshot['documents'][0]['original_chars']==61000
    assert snapshot['documents'][0]['used_chars']==60000
    assert '60000' in snapshot['warnings'][0]


def test_manual_attachment_refresh_and_non_overwrite(store):
    folder=store.archive('topic-a',{'id':'manual','title':'manual'},'manual')['path']
    first=store.write_new('topic-a',folder,'README.md','first')
    second=store.write_new('topic-a',folder,'README.md','second')
    assert first!=second and store.path('topic-a',first).read_text()=='first'
    assert store.materials('topic-a')[0]['status']=='README已取得'


def test_github_description_is_not_presented_as_readme(store):
    store.archive('topic-a',{'title':'repo','url':'https://github.com/org/repo','description':'short description'},'github')
    assert 'README及代码未取得' in store.materials('topic-a')[0]['status']
