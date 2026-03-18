# Flashcards Generator

Gera flashcards Anki a partir de PDFs e PPTX usando o NotebookLM do Google.

## Instalação

```bash
uv sync
notebooklm login
```

## Uso

Gerar flashcards:

```bash
uv run main.py generate -i ./input -o ./output
```

Limpar notebooks:

```bash
uv run main.py cleanup --all
```

## Importação no Anki

1. Arquivo → Importar → Selecione `{tema}.csv`
2. Configure:
   - **Tipo:** Cloze
   - **Delimitador:** Vírgula
   - **Tags:** Coluna 3

## Estrutura

```
input/
├── Tema1/
│   └── arquivo.pdf
└── Tema2/
    └── arquivo.pptx
```
