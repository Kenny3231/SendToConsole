"""OCR d'une capture (core/ocr.py) : regroupement des lignes et erreurs.
Le vrai moteur n'est jamais charge ici (lent) : recognize est remplace."""

from core import ocr


def _box(x, y, w=100, h=20):
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def test_join_lines_groups_fragments_on_same_height():
    """'show running-config' sort de RapidOCR en trois fragments : sans
    regroupement, la frappe validerait chaque mot par ENTREE."""
    result = [
        [_box(200, 52), "running-config", 0.9],    # desordre voulu
        [_box(20, 50), "root@server:~#", 0.9],
        [_box(120, 49), "show", 0.9],
        [_box(20, 110), "Passw0rd 2468", 0.9],
    ]
    assert ocr.join_lines(result) == (
        "root@server:~# show running-config\nPassw0rd 2468")


def test_join_lines_empty():
    assert ocr.join_lines(None) == "" and ocr.join_lines([]) == ""


def _run(monkeypatch, exc=None, text="abc"):
    def fake(_png):
        if exc is not None:
            raise exc
        return text
    monkeypatch.setattr(ocr, "recognize", fake)
    out = []
    job = ocr.OcrJob(b"png")
    job.result_ready.connect(lambda t, e: out.append((t, e)))
    job.run()                      # appel direct : meme thread, signal direct
    return out[0]


def test_job_success(monkeypatch, qcore_app):
    assert _run(monkeypatch) == ("abc", "")


def test_job_missing_module_is_reported(monkeypatch, qcore_app):
    assert _run(monkeypatch, ImportError("x")) == (
        "", "RapidOCR n'est pas installé")


def test_job_error_never_carries_the_text(monkeypatch, qcore_app):
    """Le message d'une exception peut contenir des donnees : seul le nom
    de sa classe est conserve."""
    text, error = _run(monkeypatch, ValueError("MotDePasse-Secret!42"))
    assert text == "" and "ValueError" in error
    assert "MotDePasse-Secret!42" not in error
