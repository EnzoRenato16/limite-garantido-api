"""Guarda de vocabulário: expressões que a auditoria regulatória proíbe nos textos
públicos e nomes de empresas que não podem aparecer em nenhum arquivo versionado.

Os nomes proibidos não estão escritos aqui: o teste guarda só o SHA-256 de cada palavra
(ou par de palavras seguidas) em minúsculas e compara com o texto tokenizado.
"""

from __future__ import annotations

import hashlib
import itertools
import re
import subprocess
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]

EXPRESSOES = [
    r"\bguardad[oa]s?\b",
    r"\bguardar\b",
    r"\bperda\s+zero\b",
    r"\brisco\s+zero\b",
    r"\belimina\s+o\s+risco\b",
    r"\bqualquer\s+investimento\b",
    r"\bdrex\s+em\s+2026\b",
    r"\bbloqueio\s+no\s+app\b",
    r"\ba\s+b3\s+registra\b",
    r"\bnenhuma\s+norma\b",
]

# Só os hashes: palavras e pares de palavras proibidos, em minúsculas.
HASHES_PROIBIDOS = {
    "ef0c9930fdbe965132c55d5be98f0a51ace24809b15458361319d3872a9c0b94",
    "af634b7a937a36fd2f842cbad156981b7df926bc65630aa9e49dd6c4de6d2325",
    "4946469c1604ec9159a7479f6d29cab8949971832b7553cb8aa109f79d71e623",
    "6774021d89fd4fd1f95833e8fedf662fb66923b98db11b389fc2e15f47a09d54",
}


def _arquivos_versionados() -> list[Path]:
    saida = subprocess.run(
        ["git", "ls-files"],  # noqa: S607
        cwd=RAIZ,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [RAIZ / linha for linha in saida.splitlines() if (RAIZ / linha).is_file()]


def _texto(caminho: Path) -> str | None:
    try:
        return caminho.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def _sem_vocabulario(caminho: Path, texto: str) -> str:
    """docs/regulacao.md traz um bloco que lista os termos a evitar; ele fica de fora."""
    if caminho.relative_to(RAIZ).as_posix() != "docs/regulacao.md":
        return texto
    return re.sub(r"<!-- vocabulario:inicio -->.*?<!-- vocabulario:fim -->", "", texto, flags=re.S)


def _arquivos_do_escopo() -> list[Path]:
    escolhidos = []
    for caminho in _arquivos_versionados():
        rel = caminho.relative_to(RAIZ).as_posix()
        if rel == "docs/DECISOES.md":
            continue
        if (
            rel.startswith("app/api/estatico/")
            or rel == "README.md"
            or (rel.startswith("docs/") and rel.endswith(".md"))
        ):
            escolhidos.append(caminho)
    return escolhidos


def test_nenhuma_expressao_proibida_nos_textos_publicos() -> None:
    padroes = [re.compile(p, re.IGNORECASE) for p in EXPRESSOES]
    achados = []
    for caminho in _arquivos_do_escopo():
        texto = _texto(caminho)
        if texto is None:
            continue
        texto = _sem_vocabulario(caminho, texto)
        for numero, linha in enumerate(texto.splitlines(), start=1):
            for padrao in padroes:
                if padrao.search(linha):
                    achados.append(f"{caminho.relative_to(RAIZ)}:{numero}: {padrao.pattern}")
    assert not achados, "expressões proibidas:\n" + "\n".join(achados)


def test_nenhum_arquivo_versionado_traz_nomes_proibidos() -> None:
    achados = []
    for caminho in _arquivos_versionados():
        texto = _texto(caminho)
        if texto is None:
            continue
        palavras = re.findall(r"[a-z0-9áàâãéêíóôõúç]+", texto.lower())
        candidatos = set(palavras) | {f"{a} {b}" for a, b in itertools.pairwise(palavras)}
        for candidato in candidatos:
            if hashlib.sha256(candidato.encode()).hexdigest() in HASHES_PROIBIDOS:
                achados.append(str(caminho.relative_to(RAIZ)))
                break
    assert not achados, "nomes proibidos em: " + ", ".join(achados)
