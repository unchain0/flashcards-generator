# Flashcards Generator

Gera flashcards Anki em formato Cloze Deletion a partir de PDFs e PPTX usando o NotebookLM do Google.

## Características

- **Formato Cloze Deletion 100%**: Todos os flashcards usam o formato `{{c1::resposta}}` para estudo ativo
- **Suporte a PDFs grandes**: Divide automaticamente PDFs com mais de 50 páginas em chunks otimizados
- **Filtragem de qualidade**: Remove automaticamente flashcards triviais, duplicados e com baixo valor educacional
- **Detecção de capítulos**: Identifica capítulos no PDF e filtra seções irrelevantes (Copyright, Índice, etc.)
- **Suporte a PPTX**: Converte apresentações PowerPoint para flashcards
- **Merge de CSV**: Combine múltiplos arquivos CSV gerados em um único arquivo

## Instalação

```bash
# Clone o repositório
git clone <repo-url>
cd flashcards-generator

# Instale a aplicação no ambiente Python 3.10
uv sync

# Instale o Chromium do Playwright
uv run playwright install chromium

# Faça login no NotebookLM
uv run notebooklm login
```

## Uso

### Interface interativa

```bash
# Abrir a TUI principal
uv run flashcards

# Também é possível iniciar a TUI pelo módulo
uv run python -m flashcards_generator
```

### Gerar flashcards

```bash
# Gerar de todos os PDFs na pasta input
uv run flashcards-cli generate -i ./input -o ./output

# Gerar sem aguardar processamento (modo rápido)
uv run flashcards-cli generate -i ./input -o ./output --no-wait

# Especificar timeout (padrão: 15 minutos)
uv run flashcards-cli generate -i ./input -o ./output --timeout 900
```

### Mesclar arquivos CSV

```bash
# Combinar todos os CSVs em uma pasta
uv run flashcards-cli merge --folder ./output/Tema1

# Combinar e remover duplicatas
uv run flashcards-cli merge --folder ./output/Tema1 --deduplicate
```

### Limpar notebooks do NotebookLM

```bash
# Limpar todos os notebooks criados
uv run flashcards-cli cleanup --all
```

## Estrutura de Diretórios

```
input/                          # Coloque seus PDFs/PPTX aqui
├── Livros/
│   ├── python-basico.pdf
│   └── sqlalchemy.pdf
└── Apostilas/
    └── curso-react.pptx

output/                         # Flashcards serão gerados aqui
├── Livros/
│   ├── python-basico.csv
│   └── sqlalchemy.csv
└── Apostilas/
    └── curso-react.csv
```

## Importação no Anki

### Importação direta via AnkiConnect

Com o Anki aberto e o AnkiConnect habilitado, importe os cards diretamente
para um deck (inclusive decks hierárquicos):

```bash
uv run flashcards-cli generate \
  --input-dir ./input \
  --output-dir ./output \
  --anki-deck "Estácio::Disciplina::Unidade 1"
```

O endpoint padrão é `http://127.0.0.1:8765`. Para uma instalação
customizada, informe a URL e, se necessário, a chave da API:

```bash
uv run flashcards-cli generate \
  --input-dir ./input \
  --anki-deck "Estácio::Disciplina::Unidade 1" \
  --anki-connect-url "http://127.0.0.1:8765" \
  --anki-api-key "$ANKI_API_KEY"
```

O comando cria o deck caso necessário e envia as notas usando o modelo
Cloze. A geração do CSV continua ativa; sem `--anki-deck`, nenhuma chamada
ao AnkiConnect é feita. Se o AnkiConnect falhar, o comando termina com código
não-zero e mantém os CSVs gerados para importação manual.

### Importação manual via CSV

1. Abra o Anki
2. Arquivo → Importar → Selecione o arquivo `.csv` gerado
3. Configure:
   - **Tipo de nota:** Cloze
   - **Delimitador:** Vírgula
   - **Campos:** Frente (coluna 1), Verso (coluna 2)
4. Clique em **Importar**

## Como Funciona

### Chunking de PDFs Grandes

PDFs com mais de 50 páginas são automaticamente divididos em chunks de até 30 páginas:

- **Detecção de capítulos**: Se o PDF tiver bookmarks/outline, os chunks respeitam os limites dos capítulos
- **Filtragem inteligente**: Seções como Copyright, Índice, Prefácio e Índice remissivo são ignoradas
- **Sem overlap**: Quando chunking por capítulos é usado, não há páginas duplicadas entre chunks

### Qualidade dos Flashcards

O sistema aplica filtros automáticos para garantir qualidade:

- Remove cards com apenas palavras triviais (artigos, preposições)
- Remove cards com respostas muito curtas (menos de 2 palavras)
- Remove cards com linguagem subjetiva ("bom", "ruim", "importante")
- Remove cards duplicados ou muito similares (similaridade > 85%)

### Formato Cloze Deletion

Todos os flashcards seguem o formato:

```
Frente: O {{c1::SQLAlchemy}} é um ORM para Python.
Verso: Object-Relational Mapping facilita a interação com bancos de dados.
```

Dicas para melhores resultados:
- Cada card testa **apenas um conceito**
- Contexto é sempre incluído na frente
- Listas usam clozes progressivos: `{{c1::itemA}} {{c2::itemB}}`

## Limitações

- Requer conexão com internet (usa API do NotebookLM)
- PDFs muito grandes podem demorar vários minutos
- Qualidade depende da clareza do texto no PDF
- Imagens e diagramas não são processados (apenas texto)

## Solução de Problemas

### Erro: "No such file or directory: 'notebooklm'"

```bash
# Sincronize o ambiente do projeto e reinstale o Chromium
uv sync
uv run playwright install chromium
uv run notebooklm login
```

### Erro: "Timeout ao gerar flashcards"

```bash
# Aumente o timeout
uv run flashcards-cli generate -i ./input -o ./output --timeout 1800
```

### Flashcards duplicados

O sistema já remove duplicatas automaticamente. Se ainda encontrar duplicados:

```bash
# Use o modo deduplicate ao importar no Anki
# Ou remova manualmente após a importação
```

## Desenvolvimento

```bash
# Executar testes
uv run pytest

# Executar testes com cobertura
uv run pytest --cov=flashcards_generator --cov-report=term-missing

# Linting
uv run ruff check .
uv run ruff format .
```

## Arquitetura

O projeto segue a **Clean Architecture** com as seguintes camadas:

- **domain/**: Entidades, objetos de valor e portas (protocolos)
- **application/**: Casos de uso, DTOs e lógica de orquestração
- **infrastructure/**: Implementações de serviços externos (PDF, NotebookLM)
- **interfaces/**: CLI (linha de comando)
- **adapters/**: Wrappers para APIs externas

## Licença

[MIT License](LICENSE)
