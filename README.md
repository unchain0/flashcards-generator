# Flashcards Generator

Gera flashcards com **Cloze Deletion** a partir de PDFs usando o NotebookLM do Google.

## Formato Anki

Os flashcards são gerados no formato **Cloze Deletion** compatível com o Anki.

### Formato CSV (Principal)

Arquivos `.csv` para importação direta:

```csv
Front,Back,Tags
"A capital da França é {{c1::Paris}}.","Paris","geografia"
"A fórmula \(E=mc^2\) foi criada por {{c1::Einstein}}.","Einstein","fisica"
```

**Math inline:** `\( ... \)`
**Math display:** `\[ ... \]`

### Importação no Anki

1. Gere os flashcards:
   ```bash
   python main.py --input-dir ./pdfs --output-dir ./decks
   ```

2. Arquivo → Importar → Selecione `{tema}.csv`

3. Configure:
   - **Tipo de nota:** Cloze
   - **Delimitador:** Vírgula
   - **Aspas:** Aspas duplas (")
   - **Tags:** Coluna 3

### Formatos Suportados

| Arquivo | Formato | Uso |
|---------|---------|-----|
| `{tema}.csv` | **CSV** | **Importação Anki (principal)** |
| `{tema}.json` | JSON | Dados completos |
| `{tema}_anki.txt` | TSV | Importação alternativa |
| `{tema}.md` | Markdown | Referência legível |

## Instalação

```bash
uv tool install notebooklm-py --with playwright
playwright install chromium
notebooklm login  # Autenticar no Google
```

## Uso

```bash
python main.py --input-dir ./pdfs --output-dir ./decks
```

## Estrutura de Pastas

```
pdfs/
├── Anatomia/
│   ├── capitulo1.pdf
│   └── capitulo2.pdf
├── Fisiologia/
│   └── aula1.pdf
```

Cada pasta vira um deck separado no Anki.
